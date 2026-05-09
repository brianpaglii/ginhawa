// VL53L0X long-range height sensor implementation.
//
// Configures the Pololu VL53L0X driver for the ~120–185 cm citizen
// height window per CLAUDE.md ("validated range 120–185 cm"). The
// VCSEL pulse-period values + signal-rate limit follow the Pololu
// example for the long-range profile; the timing budget is exposed
// via include/config.h.
#include "sensor_vl53l0x.h"

#include <VL53L0X.h>

#include "config.h"

namespace {
// Single static driver instance — the bus pointer is bound at
// init() and the pillar setup uses one sensor (default I²C address
// 0x29; XSHUT not connected per ADR-0018 wiring).
VL53L0X g_sensor;
}  // namespace

bool sensor_vl53l0x_init(TwoWire& bus) {
    g_sensor.setBus(&bus);
    g_sensor.setTimeout(500);
    if (!g_sensor.init()) {
        return false;
    }
    // Long-range configuration (Pololu library convention). The
    // VCSEL pulse-period tuning trades short-range precision for
    // long-range sensitivity — exactly the trade we want at the
    // ~50–80 cm sensor-to-head distance the pillar produces.
    g_sensor.setSignalRateLimit(0.1);
    g_sensor.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 18);
    g_sensor.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 14);
    g_sensor.setMeasurementTimingBudget(VL53L0X_TIMING_BUDGET_US);
    return true;
}

OptionalHeight sensor_vl53l0x_read_height_cm() {
    uint16_t distance_mm = g_sensor.readRangeSingleMillimeters();
    if (g_sensor.timeoutOccurred()) {
        return {false, 0.0f};
    }
    // The VL53L0X reports values up to 8190 mm to flag "out of
    // range" / no target. Anything over 2 m is implausible for our
    // pillar setup (citizen would have to be >2 m below the
    // sensor), and 0 typically indicates a measurement error.
    if (distance_mm == 0 || distance_mm > 2000) {
        return {false, 0.0f};
    }
    float distance_cm = static_cast<float>(distance_mm) / 10.0f;
    float height_cm = PILLAR_HEIGHT_CM - distance_cm;
    if (height_cm < MIN_HEIGHT_CM || height_cm > MAX_HEIGHT_CM) {
        return {false, 0.0f};
    }
    return {true, height_cm};
}

OptionalHeight sensor_vl53l0x_read_smoothed() {
    float samples[3];
    for (int i = 0; i < 3; ++i) {
        OptionalHeight one = sensor_vl53l0x_read_height_cm();
        if (!one.has_value) {
            // One bad read poisons the whole window. The kiosk
            // re-reads at HEIGHT_SAMPLE_INTERVAL_MS so dropping the
            // window is cheaper than publishing a half-confident
            // value.
            return {false, 0.0f};
        }
        samples[i] = one.value;
        delay(VL53L0X_INTER_SAMPLE_DELAY_MS);
    }
    float median = compute_median_of_three(samples[0], samples[1], samples[2]);
    return {true, median};
}
