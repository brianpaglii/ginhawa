// MAX30100 pulse-oximeter implementation.
//
// Library: oxullo/Arduino-MAX30100 (PulseOximeter wrapper around
// the MAX30100 register set; identical to the chip on the M5Stack
// Mini Heart Rate Unit per the bench wiring). update() must be
// called at high frequency to drive the library's internal beat
// detector — that's what makes getSpO2() converge on a stable
// reading. Heart rate is read from the chip only by the diagnostic
// dump (chip-behavior signal); the production tick collects only
// SpO2.
//
// I²C bus: oxullo's library reaches Wire directly via the global
// instance (no TwoWire setter), so this sensor MUST live on Wire
// (I²C0, GPIO 21/22). The MLX90640 — which CAN take a Wire1 ptr
// via Adafruit's library — owns I²C1.
#include "sensor_max30100.h"

#include <Arduino.h>
#include <MAX30100_PulseOximeter.h>

#include "config.h"
#include "pulse_math.h"

namespace {
PulseOximeter g_pox;

// Rolling buffer of in-range SpO2 samples; consume_stable() drains
// and resets every 30 s reporting window.
float g_spo2_buf[MAX30100_SAMPLE_BUFFER];
int g_spo2_count = 0;

// Finger-presence gate state (ADR-0022 / audit
// docs/audits/2026-05-14-spo2-stale-readings-audit.md). The OXullo
// library memoises last SpO2 across finger-off; without a presence
// gate the firmware silently re-publishes session 1's reading into
// session 2. State is module-scope so a single-tick "no finger" check
// can both reset the warmup counter and clear the accumulating SpO2
// buffer — brief finger removal must invalidate any in-progress
// accumulation, not just stop adding to it.
unsigned long g_last_finger_check_ms = 0;
uint16_t g_finger_check_count = 0;
bool g_finger_warmed_up = false;
unsigned long g_last_spo2_publish_ms = 0;

// MAX30100 I²C address + FIFO_DATA register (same constants the
// diagnostic dump hard-codes inline). Hoisted to namespace scope so
// the production gate's raw-IR read shares them with the diagnostic.
constexpr uint8_t kMax30100Addr = 0x57;
constexpr uint8_t kFifoDataReg = 0x05;

// Append-with-overwrite: when full, drop the oldest sample so the
// median tracks recent state instead of an old fixed window.
void _push(float* buf, int& count, float value) {
    if (count < MAX30100_SAMPLE_BUFFER) {
        buf[count++] = value;
        return;
    }
    for (int i = 0; i < MAX30100_SAMPLE_BUFFER - 1; ++i) {
        buf[i] = buf[i + 1];
    }
    buf[MAX30100_SAMPLE_BUFFER - 1] = value;
}

// Read one raw IR sample directly from the chip's FIFO_DATA register.
// Mirrors the I²C accessor the diagnostic dump uses
// (sensor_max30100_diagnostic_dump). Returns -1 on I²C failure,
// otherwise the 16-bit IR value (0–65535).
//
// DESTRUCTIVE: this pops a sample from the chip's FIFO that the
// library's update() would otherwise consume. Throttled by the
// caller (sensor_max30100_tick) to ~100 ms cadence so the library
// retains ~90 % of the 100 Hz sample stream — enough headroom for
// the beat detector to stay reliable.
int _read_raw_ir_destructive() {
    Wire.beginTransmission(kMax30100Addr);
    Wire.write(kFifoDataReg);
    if (Wire.endTransmission(false) != 0) return -1;
    // Each FIFO sample is 4 bytes: IR_MSB, IR_LSB, RED_MSB, RED_LSB.
    // We need only IR; discard the red bytes so the FIFO read pointer
    // advances by exactly one sample.
    if (Wire.requestFrom(static_cast<int>(kMax30100Addr),
                         static_cast<int>(4)) != 4) {
        return -1;
    }
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    (void)Wire.read();  // RED_MSB
    (void)Wire.read();  // RED_LSB
    return (static_cast<int>(hi) << 8) | lo;
}
}  // namespace

bool sensor_max30100_init(TwoWire& /*bus*/) {
    // oxullo's PulseOximeter::begin() doesn't accept a TwoWire
    // reference — it talks to the global Wire instance. Caller
    // (main.cpp) is responsible for Wire.begin(I2C0_SDA, I2C0_SCL)
    // before getting here. The bus parameter is kept for API
    // symmetry with the MLX90640 wrapper.
    if (!g_pox.begin()) {
        return false;
    }
    // IR LED current: 27.1 mA (the oxullo library default; register
    // value 0x8 in LED_CONFIG[3:0]).
    //
    // We tried 50 mA earlier when finger contact looked silent — that
    // was wrong. Diagnostic captures with a finger actually pressed
    // showed ir=65535 sustained at 50 mA: the IR ADC saturates against
    // the M5Stack shroud's reflective optical path, the ~1–2 % AC
    // modulation from arterial pulsations gets clamped out, and the
    // beat detector finds nothing. 27.1 mA leaves comfortable
    // headroom for finger-pressed light return without saturation.
    // Lower further (e.g. 24 mA, 0x7) only if a future deployment
    // shows IR still pegged at 65535 with a real finger.
    g_pox.setIRLedCurrent(MAX30100_LED_CURR_27_1MA);
    return true;
}

void sensor_max30100_tick() {
    // Finger-presence gate (ADR-0022). Throttled FIFO_DATA peek
    // checks whether the chip is actually seeing a finger, gated on
    // IR DC level and a 5-check warmup. Below-threshold (or I²C
    // failure) resets warmup AND clears the accumulating buffer —
    // brief finger removal must invalidate in-progress accumulation,
    // not just stop adding to it.
    unsigned long now = millis();
    if (now - g_last_finger_check_ms >= MAX30100_FINGER_CHECK_INTERVAL_MS) {
        g_last_finger_check_ms = now;
        int ir = _read_raw_ir_destructive();
        if (ir < 0 || static_cast<float>(ir) < MAX30100_FINGER_IR_THRESHOLD) {
            if (g_finger_warmed_up) {
                Serial.println("[max30100] finger lost, gate reset");
            }
            g_finger_check_count = 0;
            g_finger_warmed_up = false;
            g_spo2_count = 0;
        } else if (!g_finger_warmed_up) {
            g_finger_check_count++;
            if (g_finger_check_count >= MAX30100_FINGER_WARMUP_CHECKS) {
                g_finger_warmed_up = true;
                Serial.println("[max30100] finger warmed up, gate open");
            }
        }
    }

    // Always drive the library — its beat detector must consume
    // samples at the chip's 100 Hz rate to stay locked. Skipping
    // update() between gate checks would discard 10 samples worth
    // of state and break SpO2 calculation.
    g_pox.update();

    if (!g_finger_warmed_up) {
        return;
    }

    float spo2 = g_pox.getSpO2();
    // The library returns 0.0 until it has stabilised; the SPO2_MIN
    // floor culls those startup values plus any grossly out-of-range
    // readings the algorithm emits during finger-on transients. With
    // the finger-presence gate above, this filter now serves as a
    // second line of defence rather than the primary stale-value
    // suppressor.
    if (spo2 >= SPO2_MIN && spo2 <= SPO2_MAX) {
        _push(g_spo2_buf, g_spo2_count, spo2);
    }
}

// Helper: read one register byte from the MAX3010x at `addr`. Returns
// the byte on success, -1 on any I²C failure. Used only by the
// diagnostic dump; isolated here so the dump stays linear.
static int _read_reg(TwoWire& bus, uint8_t addr, uint8_t reg) {
    bus.beginTransmission(addr);
    bus.write(reg);
    if (bus.endTransmission(false) != 0) return -1;
    if (bus.requestFrom(static_cast<int>(addr), 1) != 1) return -1;
    return bus.read();
}

void sensor_max30100_diagnostic_dump(TwoWire& bus) {
    // MAX3010x register map (datasheet, MAX30100 specifically):
    //   0x02  FIFO write pointer
    //   0x04  FIFO read pointer
    //   0x05  FIFO data register (4 bytes/sample: IR_MSB, IR_LSB,
    //         RED_MSB, RED_LSB; auto-advances pointer on read)
    //   0x06  MODE_CONFIG    bit7 SHDN, bit6 RESET, bit3 TEMP_EN,
    //                        bits[2:0] MODE (0x00 idle, 0x02 HR only,
    //                        0x03 SPO2+HR)
    //   0x07  SPO2_CONFIG    bit6 HI_RES_EN, bits[4:2] SR (sample rate),
    //                        bits[1:0] LED_PW (pulse width)
    //   0x09  LED_CONFIG     bits[7:4] RED_PA, bits[3:0] IR_PA
    //                        (0x0=0 mA → 0xF=50 mA, datasheet table)
    //   0xFF  Part ID (0x11 = MAX30100, 0x15 = MAX30102)
    constexpr uint8_t kAddr = 0x57;
    constexpr uint8_t kPartIdReg = 0xFF;
    constexpr uint8_t kFifoDataReg = 0x05;
    constexpr uint8_t kFifoWrPtrReg = 0x02;
    constexpr uint8_t kFifoRdPtrReg = 0x04;
    constexpr uint8_t kModeConfigReg = 0x06;
    constexpr uint8_t kSpo2ConfigReg = 0x07;
    constexpr uint8_t kLedConfigReg = 0x09;

    int part_id = _read_reg(bus, kAddr, kPartIdReg);
    if (part_id < 0) {
        Serial.println(
            "DIAG MAX30100: I2C read failed - chip not on Wire (0x57)?");
        return;
    }
    const char* chip = part_id == 0x11   ? "MAX30100"
                       : part_id == 0x15 ? "MAX30102"
                                         : "unknown";

    // Read the three configuration registers up front. If any of
    // these come back as something other than what oxullo's begin()
    // is supposed to write, the begin() call's I2C writes were
    // silently dropped and the chip is sitting in standby — which
    // would explain a permanently-empty FIFO.
    int mode_cfg = _read_reg(bus, kAddr, kModeConfigReg);
    int spo2_cfg = _read_reg(bus, kAddr, kSpo2ConfigReg);
    int led_cfg = _read_reg(bus, kAddr, kLedConfigReg);

    const char* mode_label;
    if (mode_cfg < 0) {
        mode_label = "(read-fail)";
    } else if (mode_cfg & 0x80) {
        mode_label = "(SHDN)";
    } else {
        switch (mode_cfg & 0x07) {
            case 0x00:
                mode_label = "(IDLE - begin() write lost)";
                break;
            case 0x02:
                mode_label = "(HR-only)";
                break;
            case 0x03:
                mode_label = "(SPO2+HR)";
                break;
            default:
                mode_label = "(unknown)";
                break;
        }
    }

    Serial.printf(
        "DIAG MAX30100 cfg: mode=0x%02X%s  spo2_cfg=0x%02X  "
        "led=0x%02X (IR=0x%X RED=0x%X)\n",
        mode_cfg < 0 ? 0 : mode_cfg, mode_label,
        spo2_cfg < 0 ? 0 : spo2_cfg,
        led_cfg < 0 ? 0 : led_cfg,
        led_cfg < 0 ? 0 : (led_cfg & 0x0F),
        led_cfg < 0 ? 0 : ((led_cfg >> 4) & 0x0F));

    // Read FIFO write/read pointers RAW. Printing only the computed
    // queued depth was ambiguous: at 100 Hz sample rate the production
    // tick (sensor_max30100_tick @ 10 ms) drains the FIFO faster than
    // the dump period (500 ms), so queued=0 is the steady state even
    // when the chip is producing healthily. wr_delta (the change in
    // write pointer between consecutive dumps, mod 16) disambiguates:
    // wr_delta>0 = chip is producing; wr_delta=0 sustained = chip is
    // silent regardless of mode_cfg saying SPO2+HR.
    int wr = _read_reg(bus, kAddr, kFifoWrPtrReg);
    int rd = _read_reg(bus, kAddr, kFifoRdPtrReg);
    int queued = -1;
    if (wr >= 0 && rd >= 0) {
        queued = (wr - rd + 16) & 0x0F;
    }
    static int s_last_wr = -1;
    int wr_delta = -1;
    if (wr >= 0 && s_last_wr >= 0) {
        wr_delta = (wr - s_last_wr + 16) & 0x0F;
    }
    if (wr >= 0) {
        s_last_wr = wr;
    }

    // Always attempt one FIFO_DATA read regardless of queued. If the
    // FIFO is genuinely empty the chip returns stale/zero bytes —
    // useful itself: ir/red frozen at the same value across dumps
    // while wr also frozen confirms the chip is silent (no sampling).
    // ir/red varying confirms real photodiode signal flowing.
    // Reads BEFORE g_pox.update() so we get a sample before the
    // library drains; the library loses one sample per 500 ms,
    // negligible vs. its 100 Hz tick consumption.
    uint16_t ir = 0;
    uint16_t red = 0;
    bool fifo_read_ok = false;
    bus.beginTransmission(kAddr);
    bus.write(kFifoDataReg);
    if (bus.endTransmission(false) == 0 &&
        bus.requestFrom(static_cast<int>(kAddr),
                        static_cast<int>(4)) == 4) {
        uint8_t hi_ir = bus.read();
        uint8_t lo_ir = bus.read();
        uint8_t hi_rd = bus.read();
        uint8_t lo_rd = bus.read();
        ir = (static_cast<uint16_t>(hi_ir) << 8) | lo_ir;
        red = (static_cast<uint16_t>(hi_rd) << 8) | lo_rd;
        fifo_read_ok = true;
    }

    g_pox.update();
    float lib_spo2 = g_pox.getSpO2();
    float lib_hr = g_pox.getHeartRate();

    Serial.printf(
        "DIAG MAX30100: part=0x%02X(%s) wr=%d rd=%d wr_delta=%d queued=%d "
        "ir=%u red=%u%s lib spo2=%.2f hr=%.2f window spo2=%d/%d\n",
        part_id, chip, wr, rd, wr_delta, queued, ir, red,
        fifo_read_ok ? "" : " (fifo-read-fail)", lib_spo2, lib_hr,
        g_spo2_count, MAX30100_MIN_BUFFERED_SAMPLES);
}

VitalsReading sensor_max30100_consume_stable() {
    // Heartbeat per reporting window — silent windows otherwise look
    // identical to a dead chip on the serial monitor, since the
    // publish-path Serial.printf only fires when the threshold is met.
    Serial.printf("MAX30100 window: spo2=%d/%d warm=%d\n", g_spo2_count,
                  MAX30100_MIN_BUFFERED_SAMPLES,
                  g_finger_warmed_up ? 1 : 0);
    VitalsReading r{false, 0.0f};

    // Post-publish cooldown (ADR-0022 gate 3). After a successful
    // publish, suppress the next one for COOLDOWN_MS regardless of
    // buffer contents. Drops any samples accumulated during the
    // cooldown so a fresh window starts clean after it elapses.
    unsigned long now = millis();
    if (g_last_spo2_publish_ms > 0 &&
        (now - g_last_spo2_publish_ms) < MAX30100_POST_PUBLISH_COOLDOWN_MS) {
        g_spo2_count = 0;
        return r;
    }

    if (g_spo2_count >= MAX30100_MIN_BUFFERED_SAMPLES) {
        r.spo2 = compute_pulse_median(g_spo2_buf, g_spo2_count);
        r.has_spo2 = true;
        g_last_spo2_publish_ms = now;
        Serial.printf("[max30100] published spo2=%.1f\n",
                      static_cast<double>(r.spo2));
    }
    // Reset every window even if the threshold wasn't met, so a long
    // settling period at session start can't poison later windows
    // with stale values.
    g_spo2_count = 0;
    return r;
}
