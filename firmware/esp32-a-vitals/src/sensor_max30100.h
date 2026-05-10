// MAX30100 pulse-oximeter wrapper.
//
// Lives on the default Wire (I²C0, GPIO 21/22). The oxullo
// Arduino-MAX30100 library hardcodes the default Wire bus
// internally — that's why the MLX90640 (which CAN take a TwoWire
// pointer) is the one we move to Wire1.
//
// The library exposes a smoothed PulseOximeter class with a
// "stable enough" detector. We sample its outputs at every tick
// (10 ms cadence), drop physiologically implausible values
// (treated as the library's "still settling" signal), and median
// over a rolling 64-element buffer to absorb the residual jitter.
#pragma once

#include <Arduino.h>
#include <Wire.h>

struct VitalsReading {
    bool has_spo2;
    float spo2;          // % SaO2
    bool has_heart_rate;
    float heart_rate;    // beats per minute
};

// Probe + configure the MAX30100 on the supplied I²C bus. Returns
// false if the sensor isn't on the bus or didn't ack init. Non-
// fatal upstream — main.cpp logs and continues; the kiosk seeds
// offline placeholders for spo2 / heart_rate when this node is
// silent.
bool sensor_max30100_init(TwoWire& bus);

// Drive the library's internal beat-detection algorithm by one
// step. Should be called every MAX30100_SAMPLE_INTERVAL_MS (10 ms).
// Reads the library's current SpO2 / HR estimate; if either is
// inside the plausibility window, appends to the rolling buffer.
void sensor_max30100_tick();

// Drain the rolling buffers and return the median of each. Resets
// the buffers so the next 30 s window starts clean. Returns
// has_value=false for a channel that didn't accumulate the minimum
// number of stable samples.
VitalsReading sensor_max30100_consume_stable();

// Diagnostic: read the chip's PART_ID register and a raw IR/red
// FIFO sample directly off the bus, then print everything plus the
// library's current SpO2 / HR estimate and rolling-buffer counts.
// Intended to be called every ~500 ms from main.cpp's loop() under
// `#ifdef DIAGNOSTIC_MAX30100` (built via [env:esp32dev_diag]).
// Linker dead-code elimination drops it from production builds.
void sensor_max30100_diagnostic_dump(TwoWire& bus);
