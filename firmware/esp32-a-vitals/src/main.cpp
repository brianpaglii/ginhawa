// GINHAWA ESP32-A — vitals node skeleton.
//
// This file is the boot stub. F1 (MAX30100 driver) and F2 (MLX90640
// centre-ROI temperature) replace the loop body with the real
// sampling logic; for now main.cpp only proves that the toolchain
// flashes a working binary that can talk on the serial console.
//
// Sensors and topics this node will own once F1/F2 land:
//   - spo2          → ginhawa/kiosk/<device_id>/sensors/spo2
//   - heart_rate    → ginhawa/kiosk/<device_id>/sensors/heart_rate
//   - temperature   → ginhawa/kiosk/<device_id>/sensors/temperature
//                     (per ADR-0018, MLX90640BAB lives here, not on
//                     ESP32-B)
//
// Wi-Fi + MQTT credentials live in include/secrets.h, copied from
// secrets.h.example. secrets.h is gitignored.

#include <Arduino.h>

void setup() {
    Serial.begin(115200);
    // Give the host serial a moment to attach so the boot banner
    // isn't lost.
    delay(200);
    Serial.println();
    Serial.println("GINHAWA ESP32-A vitals — boot OK");
}

void loop() {
    // Placeholder. F1/F2 replace this with the MAX30100 +
    // MLX90640 sampling + MQTT publish loop.
    delay(1000);
}
