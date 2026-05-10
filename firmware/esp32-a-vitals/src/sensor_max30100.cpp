// MAX30100 pulse-oximeter implementation.
//
// Library: oxullo/Arduino-MAX30100 (PulseOximeter wrapper around
// the MAX30100 register set; identical to the chip on the M5Stack
// Mini Heart Rate Unit per the bench wiring). Provides update() +
// getSpO2()/getHeartRate(). update() must be called at high
// frequency for the beat detector to see edges.
//
// I²C bus: oxullo's library reaches Wire directly via the global
// instance (no TwoWire setter), so this sensor MUST live on Wire
// (I²C0, GPIO 21/22). The MLX90640 — which CAN take a Wire1 ptr
// via Adafruit's library — owns I²C1.
#include "sensor_max30100.h"

#include <MAX30100_PulseOximeter.h>

#include "config.h"
#include "pulse_math.h"

namespace {
PulseOximeter g_pox;

// Rolling buffers. We hold up to MAX30100_SAMPLE_BUFFER (64)
// stable samples; consume_stable() drains and resets.
float g_spo2_buf[MAX30100_SAMPLE_BUFFER];
int g_spo2_count = 0;
float g_hr_buf[MAX30100_SAMPLE_BUFFER];
int g_hr_count = 0;

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
    // Default LED current is comfortable for finger pulse-ox.
    // Modes / sample rates are set by the library to 100 Hz red+IR.
    return true;
}

void sensor_max30100_tick() {
    g_pox.update();
    float spo2 = g_pox.getSpO2();
    float hr = g_pox.getHeartRate();
    // The library returns 0.0 until it has stabilised; SPO2_MIN /
    // HR_MIN cull those startup values + grossly out-of-range
    // readings (e.g., the spurious 200+ bpm spikes the algorithm
    // sometimes emits during finger-on transients).
    if (spo2 >= SPO2_MIN && spo2 <= SPO2_MAX) {
        _push(g_spo2_buf, g_spo2_count, spo2);
    }
    if (hr >= HR_MIN && hr <= HR_MAX) {
        _push(g_hr_buf, g_hr_count, hr);
    }
}

VitalsReading sensor_max30100_consume_stable() {
    VitalsReading r{false, 0.0f, false, 0.0f};
    if (g_spo2_count >= MAX30100_MIN_BUFFERED_SAMPLES) {
        r.spo2 = compute_pulse_median(g_spo2_buf, g_spo2_count);
        r.has_spo2 = true;
    }
    if (g_hr_count >= MAX30100_MIN_BUFFERED_SAMPLES) {
        r.heart_rate = compute_pulse_median(g_hr_buf, g_hr_count);
        r.has_heart_rate = true;
    }
    // Reset for the next reporting window — even if the buffer
    // had < MIN_BUFFERED_SAMPLES we drain it so a long initial
    // settling window doesn't poison later windows with stale
    // values.
    g_spo2_count = 0;
    g_hr_count = 0;
    return r;
}
