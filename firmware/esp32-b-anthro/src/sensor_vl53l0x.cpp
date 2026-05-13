// VL53L0X long-range height sensor implementation.
//
// Configures the Pololu VL53L0X driver for the 100–198 cm citizen
// height window (widened from CLAUDE.md's original 120–185 cm to
// match the deployed pillar's 198 cm height and the broader
// citizen-population spec). The VCSEL pulse-period values +
// signal-rate limit follow the Pololu example for the long-range
// profile; the timing budget is exposed via include/config.h.
//
// Beyond the sensor-level smoothing (median-of-3) and bounds
// rejection, this TU also owns the stabilization gate (ADR-0019)
// that downstream main.cpp uses to decide WHEN to publish — the
// gate state machine lives in the anonymous namespace at the
// bottom of this file.
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

namespace {
// Stabilization gate state (ADR-0019). Anonymous namespace so the
// gate's mutable globals don't leak into other translation units.
unsigned long g_gate_window_start_ms = 0;
unsigned long g_gate_cooldown_until_ms = 0;
float g_gate_anchor_cm = 0.0f;
int g_gate_sample_count = 0;
}  // namespace

void sensor_vl53l0x_reset_gate() {
    g_gate_window_start_ms = 0;
    g_gate_cooldown_until_ms = 0;
    g_gate_anchor_cm = 0.0f;
    g_gate_sample_count = 0;
}

GatedHeight sensor_vl53l0x_tick_gate() {
    GatedHeight out = {false, 0.0f};
    unsigned long now = millis();

    // Cooldown: ignore everything until the deadline passes. The
    // citizen is presumed to still be under the pillar; their
    // height already published.
    if (now < g_gate_cooldown_until_ms) {
        return out;
    }

    OptionalHeight reading = sensor_vl53l0x_read_smoothed();

    if (!reading.has_value) {
        // No valid reading this tick (timeout or out-of-range). The
        // window is intentionally NOT reset — a citizen standing
        // still can briefly drop out of the smoothed window if one
        // of the three median samples fails, but they're still
        // there. If the sensor stays bad for ~5 s the window
        // expires of its own accord without firing.
        return out;
    }

    if (g_gate_sample_count == 0) {
        // First in-range reading of a fresh window. Anchor on it.
        g_gate_anchor_cm = reading.value;
        g_gate_window_start_ms = now;
        g_gate_sample_count = 1;
        Serial.printf("STAB: window start anchor=%.1fcm\n", g_gate_anchor_cm);
        return out;
    }

    float delta = reading.value - g_gate_anchor_cm;
    if (delta < 0) delta = -delta;

    if (delta > HEIGHT_STABILIZATION_TOLERANCE_CM) {
        // Citizen moved (or read drifted) outside tolerance. Start
        // a fresh window with this reading as the new anchor —
        // simpler and stricter than a running-mean approach, which
        // would let slow drift accumulate without ever resetting.
        Serial.printf(
            "STAB: reset (anchor=%.1f, last=%.1f, delta=%.1f)\n",
            g_gate_anchor_cm, reading.value, delta
        );
        g_gate_anchor_cm = reading.value;
        g_gate_window_start_ms = now;
        g_gate_sample_count = 1;
        return out;
    }

    // Reading is within tolerance — accumulate and check whether
    // the window has aged enough to fire.
    g_gate_sample_count += 1;
    unsigned long elapsed = now - g_gate_window_start_ms;

    if (elapsed >= HEIGHT_STABILIZATION_WINDOW_MS) {
        out.fired = true;
        out.value_cm = g_gate_anchor_cm;
        Serial.printf(
            "STAB: FIRE value=%.1fcm elapsed=%lums samples=%d\n",
            out.value_cm, elapsed, g_gate_sample_count
        );
        // Cooldown blocks a fresh window until the citizen has had
        // a chance to step out; ~5 s is enough that the kiosk's
        // session FSM will have moved on if it consumed the value.
        g_gate_cooldown_until_ms = now + HEIGHT_POST_PUBLISH_COOLDOWN_MS;
        g_gate_anchor_cm = 0.0f;
        g_gate_window_start_ms = 0;
        g_gate_sample_count = 0;
        return out;
    }

    // Window still building. Verbose heartbeat every ~1 s for bench
    // debug — at 500 ms tick cadence, every 4th sample = ~2 s.
    if ((g_gate_sample_count % 4) == 0) {
        Serial.printf(
            "STAB: building anchor=%.1f reading=%.1f elapsed=%lums samples=%d\n",
            g_gate_anchor_cm, reading.value, elapsed, g_gate_sample_count
        );
    }
    return out;
}
