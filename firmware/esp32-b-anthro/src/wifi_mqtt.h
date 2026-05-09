// WiFi + MQTT plumbing for ESP32-B.
//
// Auth-required broker (per the kiosk's mosquitto.conf:
// allow_anonymous false) — we pass username/password from
// secrets.h on every connect attempt. Auto-reconnect lives in
// mqtt_loop(); main.cpp only calls mqtt_connect() once at boot.
#pragma once

#include <Arduino.h>
#include <stdint.h>

void wifi_connect(const char* ssid, const char* pass);

// Records broker host/port/credentials and attempts the first
// connect. Returns true on success; mqtt_loop() retries on its
// own afterwards if the connection drops or this initial attempt
// fails.
bool mqtt_connect(const char* host, uint16_t port,
                  const char* user, const char* pass);

// Publish ``payload`` on ``topic``. Returns false when not
// connected (caller may log; the next reading will publish).
// Note: PubSubClient's Arduino API is at-most-once even with
// QoS 1 in the v2 protocol layer — see the cpp comment.
bool mqtt_publish_qos1(const char* topic, const char* payload);

// Drive PubSubClient's keep-alive + auto-reconnect. Call on every
// loop() iteration.
void mqtt_loop();
