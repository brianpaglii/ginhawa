# GINHAWA ESP32-B — anthropometric stand node

Drives the VL53L0X long-range Time-of-Flight height sensor mounted at
the top of the kiosk's stand pillar. Computes citizen height as
`PILLAR_HEIGHT_CM − measured_distance_cm`, smooths a median-of-three
window, and publishes one JSON-encoded measurement every 500 ms to
`ginhawa/kiosk/<KIOSK_DEVICE_ID>/sensors/height`.

Per ADR-0018, this node is height-only. The MLX90640BAB thermal imager
moved to ESP32-A because its viewing geometry (forehead at 25–30 cm)
is naturally satisfied at the console platform, not at the stand
pillar.

See the [root README](../../README.md) for project-wide context.

## Bench-test instructions

1. Wire VL53L0X to ESP32-B per [ADR-0018](../../docs/decisions/0018-mlx90640-on-esp32a-vitals.md):
   - GPIO 21 (SDA), GPIO 22 (SCL), 3V3, GND.
   - XSHUT not connected (single sensor on the bus, default I²C
     address 0x29). Most VL53L0X breakouts include 4.7 kΩ pullups;
     verify with a multimeter (~5 kΩ between SDA and 3V3) if I²C
     errors appear at boot.

2. Copy `include/secrets.h.example` to `include/secrets.h` and fill
   in `WIFI_SSID` / `WIFI_PASS`, `MQTT_HOST` (the Pi's LAN IP),
   `MQTT_PASS` (from `/etc/mosquitto/passwd` for user `esp32_b`),
   and `KIOSK_DEVICE_ID` (from the kiosk's `device_config.kiosk_id`
   row). `secrets.h` is gitignored.

3. Run unit tests:

   ```
   cd firmware/esp32-b-anthro
   pio test -e native -f test_desktop
   ```

   Expect 8 tests passing (median-of-three: 5; JSON encoder: 2;
   ISO 8601 placeholder: 1).

4. Build and flash:

   ```
   pio run -e esp32dev -t upload
   pio device monitor
   ```

5. Expected serial output on a successful boot:

   ```
   GINHAWA ESP32-B anthro booting
   Build: <date> <time>
   VL53L0X init OK (long-range mode)
   WiFi connecting to <SSID> ...
   WiFi connected, IP: <ip>
   NTP synced: 2026-05-09T...
   MQTT connected as ginhawa-esp32-b-XXXXXX
   ESP32-B ready
   Height: 165.0 cm
   ```

6. On the Pi, watch the kiosk pick up the publishes:

   ```
   sudo journalctl -u ginhawa-kiosk -f | grep -E "mqtt|height"
   ```

   Expect `mqtt.message_routed` events with `measurement_type=height`
   roughly every 500 ms while the citizen is under the sensor.

7. End-to-end with a kiosk session:
   - Tap RFID → English → Anthropometric path
   - Stand under the sensor (or hold a hand at a known distance)
   - The measured height appears on the REPORT screen

## Calibration

`PILLAR_HEIGHT_CM` in [`include/config.h`](include/config.h) defaults
to `200.0` (matches a 2 m pillar). Update the constant for each
deployment by measuring the sensor mount-point-to-floor distance with
a tape, then re-flash. The validated citizen-height window is
120–185 cm; readings outside `[MIN_HEIGHT_CM, MAX_HEIGHT_CM]` (defaults
100–200 cm) are dropped silently — the kiosk seeds an offline
placeholder via `mqtt_sensors` if no real reading arrives.

## Layout

```
firmware/esp32-b-anthro/
├── platformio.ini            # board, libs, native test env
├── include/
│   ├── config.h              # compile-time tunables (pillar height, etc.)
│   ├── height_math.h         # pure median (test-shared)
│   ├── json_encode.h         # snprintf-based encoder (test-shared)
│   └── secrets.h.example     # template; copy to secrets.h (gitignored)
├── src/
│   ├── main.cpp              # orchestration: I²C bring-up, WiFi, NTP, MQTT, sample loop
│   ├── sensor_vl53l0x.{h,cpp} # Pololu VL53L0X driver wrapper (long-range)
│   ├── wifi_mqtt.{h,cpp}     # WiFi STA + PubSubClient with auth + auto-reconnect
│   └── timestamp.{h,cpp}     # NTP sync + ISO 8601 UTC formatter
└── test/test_desktop/
    ├── test_height_smoothing.cpp  # main() runner + median tests
    ├── test_json_encode.cpp       # JSON encoder tests
    └── test_iso8601.cpp           # placeholder (strftime is bench-validated)
```

`include/` is on the build path automatically (PlatformIO convention)
so the desktop unity tests can link the same `height_math.h` /
`json_encode.h` the firmware uses, without dragging in Arduino or
Pololu headers.
