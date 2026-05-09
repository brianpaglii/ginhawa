// GINHAWA ESP32-B — anthropometric stand node skeleton.
//
// This file is the boot stub. F3 (VL53L0X height sampling + MQTT
// publish) replaces the loop body; for now main.cpp only proves the
// toolchain flashes a working binary.
//
// Sensors and topics this node owns once F3 lands:
//   - height        → ginhawa/kiosk/<device_id>/sensors/height
//
// Wi-Fi + MQTT credentials live in include/secrets.h, copied from
// secrets.h.example. secrets.h is gitignored.

#include <Arduino.h>

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println();
    Serial.println("GINHAWA ESP32-B anthro — boot OK");
}

void loop() {
    // Placeholder. F3 replaces this with the VL53L0X long-range
    // sampling + MQTT publish loop.
    delay(1000);
}
