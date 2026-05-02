# Phase 2 (Prompt 6) Sensor Abstractions — Bench-Test Verification

**Date:** 2026-05-02
**Hardware tested on:** Raspberry Pi 5 (Raspberry Pi OS trixie), real
sensor kit per [docs/phase-0-plan.md](../../../docs/phase-0-plan.md)
"Hardware".
**Code under test:** `kiosk/src/ginhawa_kiosk/sensors/` after commit
`67f2fab` (`refactor(kiosk): omron BP cuff connects via plain
bleak.BleakClient(mac), drop bleak-retry-connector`).
**Verdict:** **PASS — RFID, Xiaomi scale, and Omron BP cuff all
deliver readings to the kiosk's event bus on the Pi.**

## Scope

This is a real-hardware verification of the three direct-attached
sensors built in Phase 2 Prompt 6:

| Sensor                  | Connection                                                                                                                                                                          | Driver (kiosk-side)                                                                              |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| RFID reader             | MFRC522 over SPI on Pi GPIO 8/9/10/11/24/25 + 3V3/GND (ADR-0017 referenced for the Xiaomi half; pin map per [phase-0-plan.md](../../../docs/phase-0-plan.md#mfrc522--pi-5-pin-map)) | `Mfrc522RfidReader` ([sensors/rfid.py](../../src/ginhawa_kiosk/sensors/rfid.py))                 |
| Xiaomi Smart Scale S200 | BLE advertisements decrypted with per-scale bindkey from `device_config.xiaomi_scale_bindkey` (ADR-0017)                                                                            | `XiaomiScaleSensor` ([sensors/xiaomi_scale.py](../../src/ginhawa_kiosk/sensors/xiaomi_scale.py)) |
| Omron HEM-7155T BP cuff | BLE Blood Pressure Service 0x1810; `BleakClient(mac).connect()` with 5×2 s retry, omblepy-pattern                                                                                   | `OmronBpSensor` ([sensors/omron_bp.py](../../src/ginhawa_kiosk/sensors/omron_bp.py))             |

The MQTT subscriber (`MqttSensorSubscriber`) was **not** end-to-end
tested in this run — the ESP32 firmware that publishes to those
topics lands in Phase 4. Its wire-format / topic-routing / malformed-
payload resilience is covered by the unit tests in
[tests/sensors/test_mqtt_sensors.py](../../tests/sensors/test_mqtt_sensors.py).

## Setup

Per [phase-0-plan.md "Raspberry Pi commissioning"](../../../docs/phase-0-plan.md#raspberry-pi-commissioning):

- Pi 5, RPi OS trixie, SPI enabled (`raspi-config nonint do_spi 0`).
- `ginhawa` user in groups `gpio`, `spi`, `bluetooth`, `input`.
- `apt install bluez bluez-tools mosquitto sqlcipher` in place.
- Kiosk source synced to `/opt/ginhawa/src`; `uv sync` pulled the
  ARM-only deps (`mfrc522`, `rpi-lgpio`, `spidev`).
- SQLCipher DB provisioned at `/var/lib/ginhawa/kiosk.db` via
  `provision_db`.
- BlueZ pair + trust completed for both BLE peers (Omron cuff and
  Xiaomi scale); MAC + bindkey rows present in `device_config`.

Test harness for this run: a small CLI invoking each sensor's
`start()` directly with `MOCK_HARDWARE=false` and a captured event
bus that prints every `RfidScanned` / `MeasurementProposed` event to
stdout.

## Sensor 1 — MFRC522 RFID reader

### Outcome

Tap of a 13.56 MHz MIFARE Classic card produces an `RfidScanned`
event on the bus with the UID normalised to uppercase hex. The
reader's polling thread keeps running until `stop()` is called.

### Behaviours observed

| Behaviour                                     | Result                                                                    |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| First tap of a fresh card                     | One `RfidScanned(uid="A3F2C901")` event                                   |
| Card held in field for 5 s after first tap    | Exactly one event (debounce kept it from re-firing within the 2 s window) |
| Same card tapped again 3 s later              | Second event fires (debounce window cleared)                              |
| Different card tapped within 1 s of the first | Event fires immediately (debounce is per-UID, not global)                 |
| `stop()` called from main thread              | Polling thread joins within ~100 ms; `GPIO.cleanup()` runs                |

The lazy-import invariant (`RPi.GPIO` / `spidev` / `mfrc522` are
imported inside `Mfrc522RfidReader.__init__`, not at module top
level) was already verified by the unit-test
`test_rfid_module_does_not_import_pi_specific_dependencies_at_top_level`;
the Pi-side run confirms that on the deployment environment those
imports succeed (the ARM-only platform-markered deps are present).

### Pin verification

`bluetoothctl info <mac>` is irrelevant here — the MFRC522 is SPI,
not BLE. Wiring confirmed against the [phase-0-plan.md pin map](../../../docs/phase-0-plan.md#mfrc522--pi-5-pin-map).
3.3 V (header pin 1) used; **never 5 V** — the MFRC522 logic is 3.3 V.
`/dev/spidev0.0` was present at test time, confirming SPI was
enabled.

## Sensor 2 — Xiaomi Smart Scale S200

### Outcome

Stepping on the scale produces one `MeasurementProposed(weight, kg,
xiaomi_s200_ble)` event, even though the scale rebroadcasts the
same advertisement many times per second.

### Behaviours observed

| Behaviour                                                          | Result                                                                                                                                                     |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| First weight reading after stepping on                             | One `MeasurementProposed(measurement_type="weight", value=kg, unit="kg", source_device="xiaomi_s200_ble", claimed_is_valid=True)` event                    |
| Scale stays under load for ~10 s with the value flat               | Dedup suppressed re-publish; no flood of duplicate events                                                                                                  |
| Citizen shifts position and the reading changes by ≥0.1 kg         | Second event fires (value-threshold path of dedup)                                                                                                         |
| Citizen steps off and steps back on after 6 s with same reading    | Second event fires (time-threshold path of dedup, ≥5 s elapsed)                                                                                            |
| Signal-strength-only advertisements between measurements           | Silently ignored (mass-only filter held)                                                                                                                   |
| Body fat / muscle mass / water content fields in the advertisement | Discarded — never published, never persisted (CLAUDE.md "Xiaomi scale specifics")                                                                          |
| Foot-electrode heart rate from the scale                           | Discarded (heart rate comes from the MAX30100, not here)                                                                                                   |
| `start()` with no `xiaomi_scale_bindkey` row in `device_config`    | Raises `SensorUnavailable("xiaomi_scale_bindkey missing from device_config; the kiosk cannot be commissioned without a per-scale bindkey (see ADR-0017)")` |
| `start()` with a non-hex bindkey value                             | Raises `SensorUnavailable("…not valid hex")`                                                                                                               |

The bindkey was extracted via the [ADR-0017](../../../docs/decisions/0017-xiaomi-ble-library-for-smart-scale-s200.md)
runbook step (Mi Home `device_info` → 32-char hex string → stored
in `device_config`).

## Sensor 3 — Omron HEM-7155T BP cuff

### Outcome

Once the citizen takes a measurement on the cuff and presses the
Bluetooth button, the kiosk connects directly via
`bleak.BleakClient(mac)`, the cuff sends the stored measurement
over notification 0x2A35, and the parser produces a
`BpReading(systolic, diastolic, MAP, pulse)` that fans out into
three `MeasurementProposed` events.

### Behaviours observed

| Behaviour                                                               | Result                                                                                                                                                  |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User takes measurement on the cuff alone (no Pi connection)             | Cuff stores the reading internally — no kiosk-side action                                                                                               |
| Bluetooth button pressed → cuff in pairing mode → kiosk connects        | `BleakClient.connect()` succeeded on attempt 1 (typical 1–3 s)                                                                                          |
| BT button pressed late — cuff still warming up at first connect attempt | Retry loop absorbed the transient `BleakError`; succeeded on attempt 2 of 5                                                                             |
| BT button NOT pressed within ~8 s                                       | All 5 retries fail; `RuntimeError("Omron HEM-7155T at <mac> did not connect after 5 attempts — put the cuff into pairing mode and try again …")` raised |
| Connection succeeds, notification arrives                               | Three events published in order: `systolic_bp`, `diastolic_bp`, `heart_rate`                                                                            |
| Notification timeout (cuff disconnected mid-handshake)                  | `client.disconnect()` runs in `finally`; BLE handle released                                                                                            |
| `_handle_request` exception (any)                                       | Logged as `omron_bp.connect_failed`; FSM does NOT crash; user can retry                                                                                 |

### Notes on the connection model

This is the omblepy pattern. Lessons re-applied during bench
testing:

1. **`bleak.BleakClient(mac).connect()` works** — BlueZ's known-
   devices cache (set up by the `pair` + `trust` runbook steps)
   resolves the MAC to a connection without an explicit
   `BleakScanner.find_device_by_address` call. Saves ~20 s per
   measurement vs the previous `bleak-retry-connector` design.
2. **`trust` is load-bearing.** A device that's paired but not
   trusted forces BlueZ to re-confirm on each connection, which
   defeats the cache fast-path. We hit this once during the
   verification run on a freshly re-paired cuff; running
   `bluetoothctl trust <mac>` resolved it. Phase 0 plan already
   includes the `trust` step; this run confirms it's mandatory in
   practice, not just defence-in-depth.
3. **Pairing mode and measurement mode are mutually exclusive on
   the cuff** (per the architectural note in
   [sensors/omron_bp.py](../../src/ginhawa_kiosk/sensors/omron_bp.py)).
   Pressing START while the cuff is in pairing mode exits pairing
   and discards the stored reading. The GUI flow (Phase 2 Prompt 8)
   must keep the two modes ordered: measure → tap Done → press BT
   button → connect.
4. **`finally: await client.disconnect()`** — explicit disconnect,
   not `async with`. Re-confirmed on the bench: calling `__aenter__`
   on an already-connected `BleakClient` raises "Client is already
   connected". The `try`/`finally` pattern released the handle
   cleanly after both successful and timed-out runs.

### CLAUDE.md hardware-safety rules upheld

- Never wrote to the cuff's EEPROM. The implementation only
  subscribes to notifications; no `omblepy -n` / `-t`-equivalent
  write command was issued.
- Only one BLE operation at a time. The kiosk did not run the
  Xiaomi scanner concurrently with an Omron `connect()` — the FSM
  serialises BP → release → weight (per CLAUDE.md "no concurrent
  BLE"). This wasn't formally exercised in this run because the
  GUI orchestration lands in Phase 2 Prompt 8; manual sequencing
  during the bench session matched what the FSM will eventually
  enforce.

## Sensor 4 — MQTT subscriber for ESP32 sensors (NOT end-to-end tested)

The kiosk-side subscriber (`MqttSensorSubscriber` →
`MockMqttSensors`) is unit-tested for topic routing, malformed-
payload resilience, and the four expected topic suffixes (`spo2`,
`heart_rate`, `temperature`, `height`). The end-to-end thread
requires the ESP32-A and ESP32-B firmware (Phase 4) to actually
publish to those topics on the Pi's local Mosquitto broker.

`mosquitto` is configured (per [phase-0-plan.md step 1](../../../docs/phase-0-plan.md#1-system-packages-both-paths))
to bind `127.0.0.1` only and is running on the Pi. A manual
`mosquitto_pub` test confirmed the subscriber receives JSON on the
expected topics and emits `MeasurementProposed` events; that's
documented here as a smoke check, not a hardware-end-to-end test:

```bash
$ mosquitto_pub -h localhost -t \
    "ginhawa/kiosk/00000000-0000-0000-0000-000000000401/sensors/spo2" \
    -m '{"value": 98.0, "unit": "%", "captured_at": "2026-05-02T08:30:00Z"}' \
    -q 1
# kiosk log: sensor.mqtt.message_received topic=…/spo2 value=98.0
# event bus: MeasurementProposed(spo2, 98.0, %, esp32_a_max30100)
```

End-to-end MQTT verification (real ESP32 publishing real readings)
is deferred to a Phase 4 verification report.

## Findings carried forward

None blocking. The following were noted during the run and are
already addressed in code or runbook:

| Finding                                                                            | Disposition                                                                                                                                                                     |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pi without `trust` flag had slow first-connect                                     | Phase 0 plan already requires `trust`; this run confirms it's mandatory. No code change.                                                                                        |
| Cuff-not-in-pairing-mode UX confusion                                              | Architectural note added to [sensors/omron_bp.py](../../src/ginhawa_kiosk/sensors/omron_bp.py) docstring; GUI flow (Phase 2 Prompt 8) will surface a clearer prompt.            |
| Mid-print printer brownout (not a BP issue but observed during the same bench day) | Pre-existing CLAUDE.md "Hardware safety" rule about the printer's 9 V external adapter — confirmed as the fix when the printer's USB power was tried first. Already documented. |

## How to re-run

On a commissioned Pi:

```bash
sudo systemctl stop ginhawa-kiosk        # if it's running

cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key>                   \
KIOSK_API_KEY=<the-key>                  \
KIOSK_DEVICE_ID=<the-uuid>               \
MOCK_HARDWARE=false                      \
LOG_LEVEL=DEBUG                          \
  uv run python -m ginhawa_kiosk         # or bench-test entry point
```

Tap a card (RFID), step on the scale (Xiaomi), take a BP and press
the BT button (Omron) — the kiosk's stdout will log each
`MeasurementProposed` event.

## Verdict

**PASS.** The three direct-attached sensors (RFID + Xiaomi + Omron)
all deliver readings to the kiosk's event bus on real hardware.
Phase 2 Prompt 6's sensor-abstraction layer is functional; Phase 2
Prompt 8 (GUI / FSM-to-sensor wiring) can proceed against this
foundation.
