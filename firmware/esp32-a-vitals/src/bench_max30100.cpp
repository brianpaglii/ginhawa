// Bench-only sketch for the MAX30100 pulse oximeter.
//
// Strips away WiFi, MQTT, the MLX90640, and the production firmware's
// publish-window thresholds so a developer can see exactly what the
// oxullo library reports per tick. Useful when "no oximeter logs"
// symptoms appear in main.cpp: this binary lets you confirm wiring,
// I²C presence, init success, finger detection, and stabilisation
// behaviour without touching the rest of the src tree.
//
// Wiring matches the production firmware — same I²C bus, same pins
// (SDA=GPIO21, SCL=GPIO22 from include/config.h). Power and pull-up
// guidance lives in README.md to keep one source of truth; do not
// re-wire to test this sketch.
//
// Build + flash:
//   pio run -e bench_max30100 -t upload
//   pio device monitor
#include <Arduino.h>
#include <MAX30100_PulseOximeter.h>
#include <Wire.h>

#include "config.h"

namespace {

PulseOximeter g_pox;

// Throttle raw-value prints so the serial line isn't flooded — the
// loop ticks at ~500 Hz, but humans only need a snapshot every few
// hundred ms to see whether values are moving.
constexpr unsigned long kRawPrintIntervalMs = 250;
constexpr unsigned long kSummaryIntervalMs = 5000;

unsigned long g_last_raw_print_ms = 0;
unsigned long g_last_summary_ms = 0;

unsigned long g_tick_count = 0;
unsigned long g_inrange_spo2 = 0;
unsigned long g_inrange_hr = 0;
unsigned long g_beat_count = 0;

void on_beat_detected() {
    ++g_beat_count;
    Serial.println("BEAT");
}

// Read the MAX3010x PART_ID register (0xFF) directly to identify
// which chip is actually on the bus. The oxullo library's begin()
// returns true on either chip (I²C ack only), but its configuration
// writes are MAX30100-specific — a MAX30102 will report 0.0 SpO2 /
// 0.0 HR forever, which is the M5Stack "Mini Heart Rate Unit"
// failure mode after the MAX30102 hardware revision.
//
//   PART_ID = 0x11 → MAX30100 (oxullo MAX30100lib is correct)
//   PART_ID = 0x15 → MAX30102 (oxullo MAX30100lib will not work;
//                              swap to SparkFun MAX3010x)
constexpr uint8_t kMax3010xAddr = 0x57;
constexpr uint8_t kPartIdReg = 0xFF;

int read_part_id() {
    Wire.beginTransmission(kMax3010xAddr);
    Wire.write(kPartIdReg);
    if (Wire.endTransmission(false) != 0) return -1;
    if (Wire.requestFrom(static_cast<int>(kMax3010xAddr), 1) != 1) return -1;
    return Wire.read();
}

// Returns true iff the chip is a MAX30100 (caller should halt
// otherwise — letting the oxullo library run against a MAX30102
// just reproduces the silent-zeros bug we are trying to diagnose).
bool identify_chip() {
    int part_id = read_part_id();
    Serial.print("PART_ID (reg 0xFF) = ");
    if (part_id < 0) {
        Serial.println("read failed - is anything at 0x57?");
        return false;
    }
    Serial.printf("0x%02X  ", part_id);
    switch (part_id) {
        case 0x11:
            Serial.println("-> MAX30100 (matches oxullo MAX30100lib)");
            return true;
        case 0x15:
            Serial.println("-> MAX30102 (DOES NOT match oxullo MAX30100lib)");
            Serial.println(
                "  The MAX30102 has a different register map than the");
            Serial.println(
                "  MAX30100. The current firmware/library will report");
            Serial.println(
                "  0.0 SpO2 / 0.0 HR forever even with a clean finger");
            Serial.println(
                "  contact. To use this hardware:");
            Serial.println(
                "    1. Replace lib_deps oxullo/MAX30100lib with");
            Serial.println(
                "       sparkfun/SparkFun MAX3010x Pulse and Proximity");
            Serial.println(
                "       Sensor Library");
            Serial.println(
                "    2. Rewrite sensor_max30100.cpp against the SparkFun");
            Serial.println(
                "       MAX30105 driver (covers MAX30100/30101/30102/30105)");
            Serial.println(
                "    3. Add an ADR + update CLAUDE.md hardware section");
            return false;
        default:
            Serial.printf(
                "-> unknown part (neither MAX30100 nor MAX30102)\n");
            Serial.println(
                "  Check the chip's laser-etched markings under a loupe.");
            return false;
    }
}

// Walk every 7-bit address on Wire and report which respond. The
// MAX30100 sits at 0x57; if it doesn't show up here, init will fail
// and the rest of the sketch is moot. Triages "chip absent" vs
// "library refused" before we try begin().
void scan_i2c() {
    Serial.println("I2C scan on Wire (GPIO 21/22):");
    int found = 0;
    for (uint8_t addr = 1; addr < 127; ++addr) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            Serial.printf("  0x%02X responding%s\n", addr,
                          addr == 0x57 ? "  <-- MAX30100" : "");
            ++found;
        }
    }
    if (found == 0) {
        Serial.println(
            "  (no devices) - check wiring, 3V3 power, and SDA/SCL pull-ups");
    } else {
        Serial.printf("  total: %d device(s)\n", found);
    }
}

}  // namespace

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println();
    Serial.println("=================================================");
    Serial.println("  GINHAWA bench: MAX30100 pulse oximeter");
    Serial.printf("  Build: %s %s\n", __DATE__, __TIME__);
    Serial.printf("  Wire pins: SDA=GPIO%d SCL=GPIO%d\n", I2C0_SDA, I2C0_SCL);
    Serial.println("=================================================");

    Wire.begin(I2C0_SDA, I2C0_SCL);
    scan_i2c();

    // Identify the chip BEFORE calling the library's begin() — on a
    // MAX30102, begin() returns true but the device is mis-configured
    // and we'd lose the user's time waiting for samples that never
    // arrive.
    if (!identify_chip()) {
        Serial.println("Halting — chip is not a MAX30100. See guidance above.");
        while (true) {
            delay(1000);
        }
    }

    Serial.print("PulseOximeter::begin() ... ");
    if (!g_pox.begin()) {
        Serial.println("FAILED");
        Serial.println(
            "Halting. If 0x57 was missing in the scan above, the chip is "
            "not on the bus (check wiring/power). If 0x57 was present, the "
            "library rejected initialisation - verify VIN is 3.3 V, not "
            "1.8 V, and that the part isn't a counterfeit MAX30102.");
        while (true) {
            delay(1000);
        }
    }
    Serial.println("OK");

    g_pox.setOnBeatDetectedCallback(on_beat_detected);

    Serial.println();
    Serial.println("Place a finger on the sensor with steady pressure.");
    Serial.println("Stabilisation typically takes 10-15 s of clean contact.");
    Serial.println("Raw values follow (0.0 = library still stabilising):");
    Serial.println();
}

void loop() {
    g_pox.update();
    ++g_tick_count;

    float spo2 = g_pox.getSpO2();
    float hr = g_pox.getHeartRate();

    if (spo2 >= SPO2_MIN && spo2 <= SPO2_MAX) {
        ++g_inrange_spo2;
    }
    if (hr >= HR_MIN && hr <= HR_MAX) {
        ++g_inrange_hr;
    }

    unsigned long now = millis();

    if (now - g_last_raw_print_ms >= kRawPrintIntervalMs) {
        g_last_raw_print_ms = now;
        Serial.printf("raw  spo2=%6.2f  hr=%6.2f\n", spo2, hr);
    }

    if (now - g_last_summary_ms >= kSummaryIntervalMs) {
        g_last_summary_ms = now;
        Serial.printf(
            "[%4lus] ticks=%lu beats=%lu in-range spo2=%lu hr=%lu\n",
            now / 1000, g_tick_count, g_beat_count, g_inrange_spo2,
            g_inrange_hr);
    }

    // 2 ms tail mirrors the production loop so behaviour is comparable
    // — if the bench shows beats and production doesn't, scheduling
    // isn't the difference.
    delay(2);
}
