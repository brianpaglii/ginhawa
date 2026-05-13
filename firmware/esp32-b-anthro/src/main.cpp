// GINHAWA ESP32-B — anthropometric stand node.
//
// Owns the VL53L0X height sensor only (per ADR-0018; the
// MLX90640BAB thermal imager moved to ESP32-A). Publishes one
// JSON-encoded measurement every HEIGHT_SAMPLE_INTERVAL_MS to
//
//   ginhawa/kiosk/<KIOSK_DEVICE_ID>/sensors/height
//
// The kiosk's mqtt_sensors subscriber routes the payload to a
// MeasurementProposed event on the kiosk-side bus.
//
// Wi-Fi + MQTT credentials live in include/secrets.h (gitignored;
// copied from include/secrets.h.example at install time).
#include <Arduino.h>
#include <Wire.h>

#include "config.h"
#include "json_encode.h"
#include "secrets.h"
#include "sensor_vl53l0x.h"
#include "wifi_mqtt.h"

namespace {
unsigned long g_last_height_ms = 0;

// Pre-computed once at boot — the device id never changes at
// runtime, and concatenating into the same fixed buffer on every
// loop iteration would burn cycles for nothing.
char g_topic[160];
}  // namespace

void setup() {
    Serial.begin(115200);
    delay(200);  // Let serial settle before the first print.
    Serial.println();
    Serial.println("GINHAWA ESP32-B anthro booting");
    Serial.printf("Build: %s %s\n", __DATE__, __TIME__);

    Wire.begin(21, 22);

    if (!sensor_vl53l0x_init(Wire)) {
        Serial.println(
            "WARN: VL53L0X init failed - continuing; the kiosk "
            "tolerates absent height readings via offline placeholders");
    } else {
        Serial.println("VL53L0X init OK (long-range mode)");
    }

    wifi_connect(WIFI_SSID, WIFI_PASS);

    if (!mqtt_connect(MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS)) {
        Serial.println("WARN: initial MQTT connect failed; will retry in loop");
    }

    snprintf(g_topic, sizeof(g_topic),
             "ginhawa/kiosk/%s/sensors/%s",
             KIOSK_DEVICE_ID, MQTT_TOPIC_SUFFIX);

    Serial.println("ESP32-B ready");
}

void loop() {
    unsigned long now = millis();
    if (now - g_last_height_ms >= HEIGHT_SAMPLE_INTERVAL_MS) {
        g_last_height_ms = now;
        OptionalHeight reading = sensor_vl53l0x_read_smoothed();
        if (reading.has_value) {
            Serial.printf("Height: %.1f cm\n", reading.value);
            // Pass nullptr for captured_at — the kiosk's mqtt_sensors
            // subscriber stamps capture time on receipt; the ESP32
            // skips NTP entirely (no internet dependency).
            char payload[128];
            if (json_encode_measurement(reading.value, MQTT_TOPIC_UNIT,
                                        nullptr, payload, sizeof(payload))) {
                if (!mqtt_publish_qos1(g_topic, payload)) {
                    Serial.println("WARN: mqtt publish failed");
                }
            } else {
                // Should be impossible at our payload size (~80 bytes
                // of 128-byte buffer) but paranoia is cheap here.
                Serial.println("WARN: payload too long for buffer");
            }
        }
    }
    mqtt_loop();
    delay(10);
}
