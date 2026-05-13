// Compile-time tunables for the ESP32-B anthropometric node.
//
// Per ADR-0018, this node drives only the VL53L0X height sensor —
// the MLX90640 thermal imager moved to ESP32-A because its
// viewing geometry (forehead at 25–30 cm) is naturally satisfied
// at the console platform, not at the top of the stand pillar.
#pragma once

#include <stdint.h>

// Distance from the VL53L0X mount-point down to the floor, in cm.
// Calibrate per installation. The bench setup before stand
// fabrication may use a placeholder value (e.g., 200.0); update
// before deployment. Citizen height is computed as
// PILLAR_HEIGHT_CM - measured_distance_cm.
constexpr float PILLAR_HEIGHT_CM = 198.0f;

// Validated citizen-height range. Sensor distances outside this
// imply either no citizen present or the sensor is misaligned;
// readings outside the range are dropped silently. The kiosk
// tolerates absent height readings via offline placeholders, so
// dropping is preferable to publishing a nonsense value.
constexpr float MIN_HEIGHT_CM = 100.0f;
constexpr float MAX_HEIGHT_CM = 198.0f;

// Sample cadence and smoothing.
constexpr unsigned long HEIGHT_SAMPLE_INTERVAL_MS = 500;
constexpr int HEIGHT_MEDIAN_WINDOW = 3;

// Stabilization gate (see ADR-0019). Citizens walking into the
// sensor range catch shoulders, hair, or mid-step poses on the
// first few smoothed reads — without this gate the kiosk received
// wildly low values (e.g., 90 cm for a 172 cm citizen). The gate
// requires WINDOW_MS of consecutive readings within ±TOLERANCE_CM
// of an anchor before firing exactly one publish, then enters a
// COOLDOWN to block re-firing for the same standstill.
constexpr unsigned long HEIGHT_STABILIZATION_WINDOW_MS = 5000;
constexpr float HEIGHT_STABILIZATION_TOLERANCE_CM = 1.0f;
constexpr unsigned long HEIGHT_POST_PUBLISH_COOLDOWN_MS = 5000;

// VL53L0X timing budget for long-range mode (μs). Longer = more
// accurate at distance, slower per-sample. 200 ms matches the
// Pololu library's "long range" example.
constexpr uint32_t VL53L0X_TIMING_BUDGET_US = 200000;

// Pause between consecutive VL53L0X reads inside a median window.
// The sensor needs time between reads; 50 ms is a safe floor.
constexpr unsigned long VL53L0X_INTER_SAMPLE_DELAY_MS = 50;

// MQTT publish topic suffix and unit (used to construct the
// per-kiosk topic in main.cpp and the JSON payload's unit field).
#define MQTT_TOPIC_SUFFIX "height"
#define MQTT_TOPIC_UNIT "cm"
