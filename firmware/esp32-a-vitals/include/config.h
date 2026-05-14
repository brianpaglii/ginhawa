// Compile-time tunables for the ESP32-A vitals node.
//
// Per ADR-0004 + ADR-0018, ESP32-A drives two sensors on SEPARATE
// I²C buses to keep the MAX30100's continuous 100 Hz sampling from
// starving the MLX90640's 0.5 Hz frame reads (and vice versa):
//   - Wire  (I²C0) on GPIO 21/22  → MAX30100 pulse oximeter
//   - Wire1 (I²C1) on GPIO 25/26  → MLX90640BAB thermal imager
#pragma once

#include <stdint.h>

// I²C buses
constexpr int I2C0_SDA = 21;
constexpr int I2C0_SCL = 22;
constexpr int I2C1_SDA = 25;
constexpr int I2C1_SCL = 26;

// MAX30100 pulse oximeter
// SAMPLE_INTERVAL_MS sets how often we call PulseOximeter::update()
// — the library's beat-detection algorithm needs frequent calls (its
// docs say "as fast as possible"; 10 ms is the practical floor that
// also gives the MLX90640 frame read room on the same loop). We
// only PUBLISH every REPORT_INTERVAL_MS so the kiosk doesn't see a
// flicker of values per second.
constexpr unsigned long MAX30100_SAMPLE_INTERVAL_MS = 10;
constexpr unsigned long MAX30100_REPORT_INTERVAL_MS = 30000;

// SpO2 / heart rate plausibility ranges. Values outside these are
// dropped pre-buffer; the kiosk prefers no reading over a wrong one
// for clinical-significance reasons. The library returns 0.0 until
// it has a stable reading, which is also rejected here as below the
// physiological floor.
constexpr float SPO2_MIN = 70.0f;
constexpr float SPO2_MAX = 100.0f;
constexpr float HR_MIN = 30.0f;
constexpr float HR_MAX = 220.0f;

// Minimum stable-sample count required before a publish is allowed.
// 16 samples × ~10 ms tick spacing ≈ 160 ms of in-range readings,
// which roughly aligns with the library's internal stabilisation
// window after first finger contact.
constexpr int MAX30100_MIN_BUFFERED_SAMPLES = 16;

// Rolling buffer size for the median filter at consume time. 64 is
// comfortably below ESP32 RAM and large enough that a few outliers
// don't tilt the median.
constexpr int MAX30100_SAMPLE_BUFFER = 64;

// --- Finger-presence gate (ADR-0022) ---------------------------------
// The OXullo PulseOximeter library memoises its last computed SpO2 —
// once stable, getSpO2() keeps returning that value even after the
// finger is removed, and it also produces plausible 90-98% readings
// from ambient light reflecting off the M5Stack shroud. Without a
// finger-presence gate the firmware silently re-publishes session 1's
// reading into session 2 (audit:
// docs/audits/2026-05-14-spo2-stale-readings-audit.md).
//
// The gate uses the raw IR DC level from a direct I²C FIFO_DATA peek
// — the only finger-evidence the chip exposes through the library
// boundary, mirroring the accessor the diagnostic dump uses. Three
// layered checks: IR threshold, warmup window, post-publish cooldown.
//
// FIFO peeks are destructive (each read pops a sample the library
// would have consumed). We throttle the gate to CHECK_INTERVAL_MS
// cadence so the library still receives ~90% of the chip's 100 Hz
// stream — its beat detector tolerates this rate easily, and the
// warmup remains a 500 ms wall-clock budget against finger-arrival.

// Minimum raw IR sample level (16-bit ADC counts) that the chip
// reports when a citizen's finger is on the sensor. Bench-calibrate
// per fixture: with no finger expect ≤ 15000; with finger pressed
// expect 50000-200000. 30000 sits comfortably between the two on
// the bench breadboard with the current M5Stack shroud — re-tune for
// future deployments if needed.
constexpr float MAX30100_FINGER_IR_THRESHOLD = 30000.0f;

// Throttle for the gate's destructive FIFO_DATA read. The library's
// update() also reads the FIFO, at every 10 ms tick. Reading at 10 ms
// cadence here would fight the library for every sample; 100 ms gives
// the library ~9 of every 10 samples and keeps gate sample-loss at
// 10%, well inside the beat detector's tolerance.
constexpr unsigned long MAX30100_FINGER_CHECK_INTERVAL_MS = 100;

// Number of consecutive above-threshold finger checks required to
// declare the finger "warmed up" and admit SpO2 samples to the
// buffer. 5 × 100 ms = 500 ms — matches the audit's recommended
// warmup wall-clock and absorbs the few-tick transients the OXullo
// library emits during finger-on settling.
constexpr uint16_t MAX30100_FINGER_WARMUP_CHECKS = 5;

// Lock-out after a successful publish. Prevents a borderline
// finger-on / finger-off / finger-on sequence from publishing two
// rapid values off residual library state. 5 s is short enough that
// a citizen who wants a second reading (after legitimate finger
// repositioning) still gets one inside their MEASURING_VITALS budget,
// long enough to discriminate from intentional re-takes.
constexpr unsigned long MAX30100_POST_PUBLISH_COOLDOWN_MS = 5000;

// MLX90640 thermal imager
// 0.5 Hz frame rate — the sensor itself is configured to match
// (refresh-rate enum 0x02). Higher rates are noisier; lower rates
// would make the kiosk wait too long for a temperature publish.
constexpr unsigned long MLX90640_SAMPLE_INTERVAL_MS = 2000;

// Skin emissivity per CLAUDE.md ("emissivity 0.98"). Used by
// Adafruit_MLX90640::getFrame's per-pixel temperature correction.
constexpr float THERMAL_EMISSIVITY = 0.98f;

// Forehead ROI within the 24×32 frame (768 pixels, row-major).
// Centre-biased: rows 8–15 and cols 12–19 — an 8×8 block around the
// frame centre (12, 16). Calibrate per fixture if the imager is
// mounted off-axis from the citizen's expected stance.
constexpr int MLX_FRAME_ROWS = 24;
constexpr int MLX_FRAME_COLS = 32;
constexpr int MLX_ROI_ROW_MIN = 8;
constexpr int MLX_ROI_ROW_MAX = 15;
constexpr int MLX_ROI_COL_MIN = 12;
constexpr int MLX_ROI_COL_MAX = 19;

// Plausibility window for forehead temp in °C. Outside this is
// rejected (sensor mis-pointed, citizen too far, ambient too cold,
// etc.). The kiosk prefers an offline placeholder over a wrong
// reading.
constexpr float TEMP_MIN_C = 30.0f;
constexpr float TEMP_MAX_C = 42.0f;

// MQTT topic suffixes. heart_rate is intentionally absent — the
// kiosk receives HR from the Omron BP cuff over BLE as part of the
// BP triple; the MAX30100 publishes only spo2.
#define MQTT_TOPIC_SPO2 "spo2"
#define MQTT_TOPIC_TEMPERATURE "temperature"
