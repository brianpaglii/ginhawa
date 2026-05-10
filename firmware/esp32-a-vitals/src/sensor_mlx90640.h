// MLX90640BAB thermal imager wrapper.
//
// Lives on Wire1 (I²C1, GPIO 25/26) per ADR-0018. Adafruit's
// MLX90640 library accepts a TwoWire pointer via begin(addr, &Wire1)
// so we can keep the MAX30100 (which hardcodes the global Wire
// instance) on I²C0 without bandwidth contention.
//
// Frame rate set to 0.5 Hz to match MLX90640_SAMPLE_INTERVAL_MS;
// the on-chip refresh-rate enum 0x02 is "0.5 Hz" per the Melexis
// datasheet.
#pragma once

#include <Arduino.h>
#include <Wire.h>

struct OptionalTemp {
    bool has_value;
    float value;  // °C; the forehead-ROI peak after emissivity correction
};

bool sensor_mlx90640_init(TwoWire& bus);

// Captures one frame, applies the configured emissivity correction
// during getFrame(), runs forehead-ROI peak extraction, and
// validates the result against the [TEMP_MIN_C, TEMP_MAX_C]
// physiological window. Returns has_value=false on any sensor /
// I²C error or out-of-range result.
OptionalTemp sensor_mlx90640_read_forehead_temp();
