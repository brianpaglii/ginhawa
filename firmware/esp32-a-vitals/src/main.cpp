// GINHAWA ESP32-A — vitals node.
//
// Drives two sensors on SEPARATE I²C buses so the MAX30100's 100 Hz
// continuous sampling doesn't starve the MLX90640's 0.5 Hz frame
// reads (and vice versa):
//   - Wire  (I²C0, GPIO 21/22)  → MAX30100   spo2 + heart_rate
//   - Wire1 (I²C1, GPIO 25/26)  → MLX90640   forehead temperature
//
// Per ADR-0018, MLX90640 lives on this node (console platform)
// rather than on ESP32-B (stand pillar). The forehead-distance
// constraint (25–30 cm at emissivity 0.98) is naturally satisfied
// next to the BP cuff and SpO2 shroud.
//
// Topics published:
//   ginhawa/kiosk/<KIOSK_DEVICE_ID>/sensors/spo2          (every 30 s)
//   ginhawa/kiosk/<KIOSK_DEVICE_ID>/sensors/heart_rate    (every 30 s)
//   ginhawa/kiosk/<KIOSK_DEVICE_ID>/sensors/temperature   (every  2 s)
//
// Wi-Fi + MQTT credentials live in include/secrets.h (gitignored;
// copy from include/secrets.h.example at install time).
#include <Arduino.h>
#include <Wire.h>

#include "config.h"
#include "json_encode.h"
#include "secrets.h"
#include "sensor_max30100.h"
#include "sensor_mlx90640.h"
#include "timestamp.h"
#include "wifi_mqtt.h"

namespace {
unsigned long g_last_max30100_tick_ms = 0;
unsigned long g_last_max30100_report_ms = 0;
unsigned long g_last_mlx90640_ms = 0;

// Pre-built once at boot — KIOSK_DEVICE_ID never changes at runtime
// and rebuilding the topic string per loop iteration would burn
// cycles for nothing. Three buffers since each topic ends in a
// different suffix; PubSubClient requires the topic to live for the
// duration of the publish call, hence statics.
char g_topic_spo2[160];
char g_topic_heart_rate[160];
char g_topic_temperature[160];

void publish_to_topic(const char* topic, float value, const char* unit,
                      const char* label) {
    char ts[64];
    timestamp_now_iso8601(ts, sizeof(ts));
    char payload[128];
    if (!json_encode_measurement(value, unit, ts, payload, sizeof(payload))) {
        Serial.printf("WARN: %s payload encode failed\n", label);
        return;
    }
    if (!mqtt_publish_qos1(topic, payload)) {
        Serial.printf("WARN: %s mqtt publish failed\n", label);
    } else {
        Serial.printf("Published %s=%.1f %s\n", label, value, unit);
    }
}
}  // namespace

void setup() {
    Serial.begin(115200);
    delay(200);  // Let serial settle before the first print.
    Serial.println();
    Serial.println("GINHAWA ESP32-A vitals booting");
    Serial.printf("Build: %s %s\n", __DATE__, __TIME__);

    Wire.begin(I2C0_SDA, I2C0_SCL);
    Wire1.begin(I2C1_SDA, I2C1_SCL);

    if (!sensor_max30100_init(Wire)) {
        Serial.println(
            "WARN: MAX30100 init failed - the kiosk will treat spo2 "
            "and heart_rate as offline for this session");
    } else {
        Serial.println("MAX30100 init OK");
    }
    if (!sensor_mlx90640_init(Wire1)) {
        Serial.println(
            "WARN: MLX90640 init failed - the kiosk will treat "
            "temperature as offline for this session");
    } else {
        Serial.println("MLX90640 init OK");
    }

    wifi_connect(WIFI_SSID, WIFI_PASS);
    timestamp_sync_ntp();

    if (!mqtt_connect(MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS)) {
        Serial.println("WARN: initial MQTT connect failed; will retry in loop");
    }

    snprintf(g_topic_spo2, sizeof(g_topic_spo2),
             "ginhawa/kiosk/%s/sensors/%s",
             KIOSK_DEVICE_ID, MQTT_TOPIC_SPO2);
    snprintf(g_topic_heart_rate, sizeof(g_topic_heart_rate),
             "ginhawa/kiosk/%s/sensors/%s",
             KIOSK_DEVICE_ID, MQTT_TOPIC_HEART_RATE);
    snprintf(g_topic_temperature, sizeof(g_topic_temperature),
             "ginhawa/kiosk/%s/sensors/%s",
             KIOSK_DEVICE_ID, MQTT_TOPIC_TEMPERATURE);

    Serial.println("ESP32-A ready");
}

void loop() {
    unsigned long now = millis();

    // MAX30100 — drive the library's beat detector at ~100 Hz.
    if (now - g_last_max30100_tick_ms >= MAX30100_SAMPLE_INTERVAL_MS) {
        g_last_max30100_tick_ms = now;
        sensor_max30100_tick();
    }

    // MAX30100 — emit one stable median per 30 s reporting window.
    if (now - g_last_max30100_report_ms >= MAX30100_REPORT_INTERVAL_MS) {
        g_last_max30100_report_ms = now;
        VitalsReading vitals = sensor_max30100_consume_stable();
        if (vitals.has_spo2) {
            publish_to_topic(g_topic_spo2, vitals.spo2, "%", "spo2");
        }
        if (vitals.has_heart_rate) {
            publish_to_topic(g_topic_heart_rate, vitals.heart_rate,
                             "bpm", "heart_rate");
        }
    }

    // MLX90640 — one frame every 2 s.
    if (now - g_last_mlx90640_ms >= MLX90640_SAMPLE_INTERVAL_MS) {
        g_last_mlx90640_ms = now;
        OptionalTemp temp = sensor_mlx90640_read_forehead_temp();
        if (temp.has_value) {
            publish_to_topic(g_topic_temperature, temp.value, "C",
                             "temperature");
        }
    }

    mqtt_loop();
    // 2 ms tail keeps the WiFi / MQTT stacks from being starved
    // while still allowing the 10 ms MAX30100 tick budget.
    delay(2);
}
