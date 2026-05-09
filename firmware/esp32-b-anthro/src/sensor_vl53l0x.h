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
