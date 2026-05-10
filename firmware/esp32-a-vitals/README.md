# GINHAWA ESP32-A — vitals console node

Drives two sensors on **separate I²C buses** so the MAX30100's
continuous 100 Hz sampling doesn't starve the MLX90640's 0.5 Hz frame
reads (and vice versa):

| Bus          | GPIO    | Sensor            | Topic                |
| ------------ | ------- | ----------------- | -------------------- |
| Wire (I²C0)  | 21 / 22 | MAX30100 pulse ox | `spo2`, `heart_rate` |
| Wire1 (I²C1) | 25 / 26 | MLX90640 thermal  | `temperature`        |

Per ADR-0018, MLX90640 lives on this node (console platform) rather
than the stand pillar — the forehead-distance constraint
(25–30 cm at emissivity 0.98) is naturally satisfied next to the
BP cuff and SpO2 shroud.

See the [root README](../../README.md) for project-wide context.

## Hardware wiring

ESP32-A (esp32dev):

```
MAX30100 (Wire / I²C0)          MLX90640BAB (Wire1 / I²C1)
  GPIO 21 (SDA0) ─ SDA            GPIO 25 (SDA1) ─ SDA
  GPIO 22 (SCL0) ─ SCL            GPIO 26 (SCL1) ─ SCL
  3V3            ─ VIN            3V3            ─ VIN  (≤3.6 V)
  GND            ─ GND            GND            ─ GND
```

**Do not put 5 V on either sensor's VIN even if the breakout has a
5 V pin.** Both parts are 3.3 V devices; the MLX90640's I²C lines
are not 5 V tolerant on the bare die. Most VL53L0X / MAX30100 /
MLX90640 breakouts include 4.7 kΩ pullups on SDA/SCL; if I²C errors
appear at boot, verify with a multimeter (~5 kΩ between SDA and 3V3
indicates pullup present).

## Bench-test instructions

1. **Wire** the sensors per the table above.

2. **Copy** `include/secrets.h.example` to `include/secrets.h` and
   fill in:
   - `WIFI_SSID` / `WIFI_PASS` — your network
   - `MQTT_HOST` — the Pi's LAN IP (`hostname -I` on the Pi)
   - `MQTT_PASS` — from `/etc/mosquitto/passwd` for user `esp32_a`
   - `KIOSK_DEVICE_ID` — from the kiosk's `device_config.kiosk_id`
     row

   `secrets.h` is gitignored.

3. **Run unit tests** on the desktop (no hardware needed):

   ```
   cd firmware/esp32-a-vitals
   pio test -e native -f test_desktop
   ```

   Expect 9 tests passing (5 pulse-median, 2 ROI peak, 2 JSON
   encoder).

4. **Build and flash**:

   ```
   pio run -e esp32dev -t upload
   pio device monitor
   ```

5. **Expected serial output**:

   ```
   GINHAWA ESP32-A vitals booting
   Build: <date> <time>
   MAX30100 init OK
   MLX90640 init OK
   WiFi connecting to <SSID> ...
   WiFi connected, IP: <ip>
   NTP synced: 2026-...
   MQTT connected as ginhawa-esp32-a-XXXXXX
   ESP32-A ready
   Published spo2=97.0 %
   Published heart_rate=72.0 bpm
   Published temperature=36.7 C
   ```

   `spo2` and `heart_rate` publish every 30 s once a finger has
   been on the MAX30100 long enough to accumulate
   `MAX30100_MIN_BUFFERED_SAMPLES`. `temperature` publishes every
   2 s while the forehead ROI is in physiological range.

6. **On the Pi**, watch the kiosk pick up the publishes:

   ```
   sudo journalctl -u ginhawa-kiosk -f | grep -E "mqtt|spo2|heart_rate|temperature"
   ```

   Expect `mqtt.message_routed` events with the corresponding
   `measurement_type=`.

7. **End-to-end with a kiosk session**:
   - Tap RFID → English → Vitals (or Full)
   - Place finger in the SpO2 shroud
   - Aim head at the thermal imager (~25–30 cm)
   - Captured readings appear on the REPORT screen alongside the
     BP triple from the Omron cuff

## Calibration / tunables

[`include/config.h`](include/config.h) holds the MAX30100 / MLX90640
plausibility windows, sample cadences, and the forehead ROI. The
ROI defaults to an 8×8 block centred near (12, 16) on the 24×32
frame; if your fixture is mounted off-axis from the citizen's
expected stance, adjust `MLX_ROI_*` and re-flash.

### Emissivity caveat

The MLX90640 datasheet specifies skin emissivity of 0.98. The
Adafruit `Adafruit_MLX90640` wrapper does NOT expose
`setEmissivity()` in its public API (the library's underlying
vendor driver call uses ~0.95 internally). At body-temperature
(~36.5 °C) and our reflected-ambient (~22 °C) the theoretical
delta from the 0.98 ideal is ~0.3 °C — inside the sensor's ±1 °C
intrinsic noise floor at 25–30 cm. If a stricter correction
becomes load-bearing later, swap to the Melexis vendor driver
(`MLX90640_API` + a vendored I²C driver patched for `Wire1`)
which exposes the emissivity argument on `CalculateTo()`.

## Layout

```
firmware/esp32-a-vitals/
├── platformio.ini                # board, libs (incl. MAX30100lib, Adafruit MLX90640)
├── include/
│   ├── config.h                  # I²C pins, sample cadences, ROI, plausibility
│   ├── pulse_math.h              # pure median (test-shared)
│   ├── mlx90640_roi.h            # pure ROI peak extractor (test-shared)
│   ├── json_encode.h             # snprintf encoder (test-shared)
│   └── secrets.h.example         # template; copy to secrets.h (gitignored)
├── src/
│   ├── main.cpp                  # boot + tick / report / frame loops
│   ├── sensor_max30100.{h,cpp}   # oxullo MAX30100lib wrapper, Wire bus
│   ├── sensor_mlx90640.{h,cpp}   # Adafruit_MLX90640 wrapper, Wire1 bus
│   ├── wifi_mqtt.{h,cpp}         # WiFi STA + PubSubClient w/ auth + reconnect
│   └── timestamp.{h,cpp}         # NTP sync + ISO 8601 UTC formatter
└── test/test_desktop/
    ├── test_pulse_smoothing.cpp  # main() runner + median tests
    ├── test_mlx90640_roi.cpp     # ROI peak extractor tests
    └── test_json_encode.cpp      # JSON encoder tests
```
