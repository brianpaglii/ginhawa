// WiFi + MQTT implementation. Uses the ESP32 WiFi STA stack and
// PubSubClient for MQTT — both bundled with the espressif32 board
// support, no extra config needed beyond the lib_dep on
// knolleary/PubSubClient.
#include "wifi_mqtt.h"

#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClient.h>

namespace {
WiFiClient g_wifi_client;
PubSubClient g_mqtt(g_wifi_client);
const char* g_mqtt_user = nullptr;
const char* g_mqtt_pass = nullptr;
char g_client_id[64];

bool _mqtt_reconnect() {
    if (g_mqtt.connected()) return true;
    if (WiFi.status() != WL_CONNECTED) return false;
    // Per-device client id derived from the bottom 24 bits of the
    // efuse MAC. Keeps the journal readable without dragging in
    // the full 12-char MAC string.
    snprintf(g_client_id, sizeof(g_client_id),
             "ginhawa-esp32-b-%06X",
             static_cast<unsigned int>(ESP.getEfuseMac() & 0xFFFFFFu));
    if (g_mqtt.connect(g_client_id, g_mqtt_user, g_mqtt_pass)) {
        Serial.printf("MQTT connected as %s\n", g_client_id);
        return true;
    }
    Serial.printf("MQTT connect failed, state=%d\n", g_mqtt.state());
    return false;
}
}  // namespace

void wifi_connect(const char* ssid, const char* pass) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, pass);
    Serial.printf("WiFi connecting to %s ", ssid);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - start) < 30000) {
        delay(250);
        Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("WiFi connected, IP: %s\n",
                      WiFi.localIP().toString().c_str());
    } else {
        Serial.println("WARN: WiFi connect timeout — will retry in loop");
    }
}

bool mqtt_connect(const char* host, uint16_t port,
                  const char* user, const char* pass) {
    g_mqtt_user = user;
    g_mqtt_pass = pass;
    g_mqtt.setServer(host, port);
    // Default 256-byte buffer is too small for our payload + topic
    // pair plus PubSubClient header overhead; 512 is comfortable.
    g_mqtt.setBufferSize(512);
    g_mqtt.setKeepAlive(60);
    return _mqtt_reconnect();
}

bool mqtt_publish_qos1(const char* topic, const char* payload) {
    if (!_mqtt_reconnect()) return false;
    // PubSubClient's Arduino-API publish() doesn't expose a
    // QoS argument; the wire-level protocol is at-most-once. For
    // the kiosk's purposes a missed packet is filled by the next
    // 500 ms-cadence reading on the device side, and the kiosk's
    // path-completion logic doesn't depend on lossless delivery —
    // weight (the only path-blocking anthro reading) comes from
    // the Xiaomi scale, not this node.
    return g_mqtt.publish(topic, payload, /*retained=*/false);
}

void mqtt_loop() {
    if (!g_mqtt.connected()) {
        // Throttled reconnect: don't hammer the broker if it's
        // down. 5 s is fast enough that a transient bounce
        // (Mosquitto restart on the Pi) recovers within a couple
        // of cycles, slow enough that the BLE scanner / kiosk
        // FSM aren't deluged with connect failures.
        static unsigned long last_attempt = 0;
        if (millis() - last_attempt > 5000) {
            last_attempt = millis();
            _mqtt_reconnect();
        }
    } else {
        g_mqtt.loop();
    }
}
