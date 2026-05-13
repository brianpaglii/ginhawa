// VL53L0X long-range height sensor wrapper.
//
// Arduino-bound API (consumed by main.cpp on the device). The
// pure-function median lives in include/height_math.h so it can
// be unit-tested under platform=native without dragging in
// Arduino headers or the Pololu library.
#pragma once

#include <Arduino.h>
#include <Wire.h>

#include "height_math.h"

struct OptionalHeight {
    bool has_value;
    float value;  // citizen height in cm; valid only when has_value=true
};

// Output of one tick of the stabilization gate (ADR-0019). ``fired``
// is true exactly once per stable 5 s window; ``value_cm`` is the
// anchor reading the window converged on. The gate is layered on
// top of ``sensor_vl53l0x_read_smoothed`` — the same min/max range
// check still applies underneath; the gate only filters whether to
// PUBLISH, never what to publish.
struct GatedHeight {
    bool fired;
    float value_cm;
};

// Initialise the VL53L0X over the supplied I²C bus. Returns true
// on successful init + long-range configuration; false otherwise.
// The kiosk tolerates absent height readings via offline
// placeholders, so a failed init is non-fatal — main.cpp logs and
// continues.
bool sensor_vl53l0x_init(TwoWire& bus);

// One distance read, converted to citizen height via PILLAR_HEIGHT_CM
// and bounds-checked against [MIN_HEIGHT_CM, MAX_HEIGHT_CM]. Returns
// has_value=false on timeout, out-of-range distance, or out-of-range
// computed height. Exposed for the smoothed reader's internal use.
OptionalHeight sensor_vl53l0x_read_height_cm();

// Median-of-3 smoothed read. Returns has_value=false if any of the
// three underlying reads returned has_value=false.
OptionalHeight sensor_vl53l0x_read_smoothed();

// One tick of the stabilization gate. Internally calls
// ``sensor_vl53l0x_read_smoothed``, accumulates a 5-second
// ±TOLERANCE window, and returns ``fired=true`` exactly once when
// the window converges. After firing, enters a 5-second cooldown
// during which subsequent ticks return ``fired=false`` regardless
// of the underlying reads. Stateful — the caller does not track the
// buffer or window timestamps.
GatedHeight sensor_vl53l0x_tick_gate();

// Discard any in-progress stabilization window AND clear the post-
// publish cooldown. ESP32-B has no MQTT subscriber today, so for
// v1 the gate naturally re-arms after cooldown and the kiosk does
// not call this. Exposed for the eventual session-driven reset
// path (per ADR-0019 future work) and to keep the gate testable.
void sensor_vl53l0x_reset_gate();
