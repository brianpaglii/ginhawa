# GINHAWA — Project Context for Claude Code

## What this is

GINHAWA is an offline-first IoT health monitoring kiosk for Philippine
barangay health centers. It measures blood pressure, oxygen saturation,
body temperature, height, and weight; calculates Body Mass Index; stores
records locally with encryption; and synchronizes to a cloud backend when
internet is available. Citizens authenticate via RFID health cards.
Barangay Health Workers (BHWs) access community-level data through a
web portal hosted on the cloud backend.

The system is a four-year capstone project at Emilio Aguinaldo College.
It is **not a medical diagnostic device** and the printed receipts and
on-screen advisories are explicitly framed as health-monitoring guidance,
not diagnoses.

## Repository layout

This is a monorepo with four top-level packages plus shared support
directories. The packages have intentionally separate dependency trees
because they target different runtimes (microcontroller, Linux SBC,
cloud Linux, browser).Build the kiosk's sensor abstractions: RFID reader, Xiaomi BLE scale, Omron BLE blood-pressure cuff, and MQTT subscriber for ESP32 sensors. Each sensor has a mock implementation (for development on a laptop without hardware) and a real implementation (for the Pi with hardware connected). The MOCK_HARDWARE setting from Prompt 1 selects between them at startup.
Prerequisites — verify before starting:

Phase 2 Prompts 1-5 committed. The integration test passes.
xiaomi-ble>=1.3.0, bleak, home-assistant-bluetooth, paho-mqtt, and evdev are in pyproject.toml. Add them via uv add if missing.
ADR-0017 (BLE library choice for Xiaomi S200) is written and Accepted.
The Xiaomi scale bindkey extraction process is documented in the kiosk's commissioning runbook.

If any prerequisite is unmet, stop and report.
Abstract base class in kiosk/src/ginhawa_kiosk/sensors/base.py:
Define a Sensor abstract base class with:

async def start(self) -> None — begin listening for events from the underlying device
async def stop(self) -> None — clean shutdown
is_running: bool property
The sensor publishes events to the event bus when relevant inputs arrive; it does not return values to its caller. The bus is the integration point.

Sensors are constructed with the event bus as a dependency. They never write directly to the database; that's the persistence layer's responsibility.
RFID reader in kiosk/src/ginhawa_kiosk/sensors/rfid.py:
RFID reader is a USB HID device (typical 125kHz proximity reader, e.g., the kind that costs ~$20 and presents as a keyboard). When a card is tapped, the reader emits the card's UID followed by Enter, as if it were typed.
Mock implementation MockRfidReader: exposes a simulate_tap(uid: str) method that publishes an RfidScanned(uid=uid) event to the bus. This is what the integration test uses.
Real implementation EvdevRfidReader: uses evdev to read from the configured input device path (e.g., /dev/input/event0, configured via env var KIOSK_RFID_DEVICE_PATH). Buffers keystrokes until Enter is received, then publishes RfidScanned(uid=buffered_string). Handles device disconnect by attempting reconnect every 5 seconds with structured-log warnings.
Selection: in sensors/__init__.py, a factory function create_rfid_reader(bus, settings) returns MockRfidReader if settings.mock_hardware else EvdevRfidReader.
Xiaomi BLE scale in sensors/xiaomi_scale.py:
Wraps xiaomi-ble per the spike. Listens to BLE advertisements, decrypts using bindkey from device_config (key xiaomi_scale_bindkey), publishes MeasurementProposed(measurement_type='weight', value=kg, unit='kg', source_device='xiaomi_s200_ble', claimed_is_valid=True) events when weight readings come through.
Critical implementation details:

Wrap Bleak's AdvertisementData into BluetoothServiceInfoBleak before passing to XiaomiBluetoothDeviceData.update() — the library expects this wrapping per the spike findings.
Filter for events where mass appears in entity_values; ignore signal_strength-only advertisements.
Deduplicate: track the last published (value, timestamp) and only publish a new event when the value differs by ≥0.1 kg or 5 seconds have elapsed since the last publish. The Xiaomi scale rebroadcasts the same measurement multiple times for reception reliability; we want one event per actual measurement.
Ignore the profile_id entirely. Document why in a comment: in a kiosk context the profile_id is meaningless because dozens of unrelated citizens use the same physical scale; citizen identity comes from the RFID tap, not from the scale.

Mock implementation MockXiaomiScale: exposes simulate_weight(kg: float) for tests.
Real implementation XiaomiScaleSensor: the wrapping described above. Loads bindkey from device_config at start(). Raises a clear error if bindkey is missing — the kiosk cannot be commissioned without one.
Omron BLE blood-pressure cuff in sensors/omron_bp.py:
The Omron HEM-7155T uses a different protocol than Xiaomi — it implements the standard Bluetooth SIG Blood Pressure Service (UUID 0x1810), so unlike Xiaomi we don't need a vendor-specific library. We use bleak directly.
The protocol: connect to the cuff, subscribe to notifications on the Blood Pressure Measurement characteristic (0x2A35), receive a single notification per measurement containing systolic, diastolic, MAP, pulse, and timestamp. Disconnect after the measurement.
Mock implementation MockOmronBp: exposes simulate_measurement(systolic, diastolic, pulse) for tests.
Real implementation OmronBpSensor: uses bleak. The kiosk pre-pairs with the cuff at commissioning (the cuff's MAC address is stored in device_config under omron_cuff_mac). On start(), the sensor begins a connection-and-listen loop: connect when measurement is requested by the FSM (subscribing to a BpMeasurementRequested event), wait for one notification, parse it per the SIG specification, publish MeasurementProposed events for both systolic and diastolic and pulse, then disconnect.
Parse the BP measurement characteristic per the Bluetooth SIG specification — flags byte first, then 6 bytes of measurement (systolic, diastolic, MAP as IEEE 11073 SFLOAT-16), optional fields based on flags. The library bleak-retry-connector may help with reliability.
Add bleak-retry-connector to dependencies if you use it.
MQTT subscriber for ESP32 sensors in sensors/mqtt_sensors.py:
The two ESP32s publish sensor readings to MQTT topics on the local broker. Topic taxonomy:

ginhawa/kiosk/<device_id>/sensors/spo2 — published by ESP32-A
ginhawa/kiosk/<device_id>/sensors/heart_rate — published by ESP32-A
ginhawa/kiosk/<device_id>/sensors/temperature — published by ESP32-A
ginhawa/kiosk/<device_id>/sensors/height — published by ESP32-B

Each topic carries a JSON payload: {value: float, unit: str, captured_at: ISO8601}. The kiosk's device_id is loaded from device_config.
Mock implementation MockMqttSensors: exposes simulate_publish(topic_suffix, value, unit) for tests.
Real implementation MqttSensorSubscriber: uses paho-mqtt async API. On start(), connects to the broker (host/port from settings), subscribes to all four topics with QoS 1, publishes a MeasurementProposed event for each received message. Handles broker disconnect with reconnect-and-resubscribe automatically (paho-mqtt's auto-reconnect plus a callback that re-subscribes on on_connect).
Validation: if a topic-suffix doesn't match the four expected, log a warning and drop the message. If the JSON is malformed, log and drop. The kiosk should never crash on a malformed MQTT payload.
Wiring in kiosk/src/ginhawa_kiosk/sensors/__init__.py:
Factory function create_all_sensors(bus, settings, db) -> dict[str, Sensor] that instantiates the four sensor types using mock/real selection based on settings.mock_hardware. Returns a dict keyed by sensor name for the application's lifecycle manager to start/stop in coordinated fashion.
Tests:
Each sensor's mock and real implementations get tests in kiosk/tests/sensors/. The mock tests verify the simulated event publishing; the real tests use pytest-bleak (or similar) and paho-mqtt-mocking to verify the wrapping logic without actual hardware.
Specifically:

test_mock_rfid_reader_publishes_event_on_simulate_tap — mortality: 'Would fail if MockRfidReader did not call bus.publish.'
test_evdev_rfid_reader_buffers_until_enter — feed simulated keystrokes via mock evdev; assert one event per Enter. Mortality: 'Would fail if the buffer were emitted on every keystroke or never flushed.'
test_xiaomi_scale_deduplicates_repeated_readings — feed the same advertisement three times in 1 second; assert one event published. Then feed the same advertisement after 6 seconds; assert a second event. Mortality: 'Would fail if deduplication logic were removed or threshold changed.'
test_xiaomi_scale_ignores_signal_strength_only_advertisements — feed an advertisement with only signal_strength in entity_values; assert no event. Mortality: 'Would fail if the mass-presence check were removed.'
test_xiaomi_scale_raises_on_missing_bindkey — initialize without bindkey in device_config; assert clear error. Mortality: 'Would fail if the kiosk silently accepted a missing bindkey.'
test_omron_bp_parses_sig_blood_pressure_measurement — feed a hardcoded SIG BP measurement payload; assert systolic/diastolic/pulse extracted correctly. Mortality: 'Would fail if the SFLOAT-16 parsing were broken.'
test_mqtt_subscriber_publishes_event_for_each_topic — simulate four MQTT messages; assert four events. Mortality: 'Would fail if topic-to-measurement-type routing were broken.'
test_mqtt_subscriber_drops_malformed_payloads — feed invalid JSON; assert no event, warning logged, no crash. Mortality: 'Would fail if a malformed payload crashed the subscriber.'

Each test self-contained, no shared fixtures except the standard event bus and database fixtures.
Run pytest. Coverage on sensors/ ≥ 90%. Commit message after review: feat(kiosk): sensor abstractions for RFID, Xiaomi BLE scale, Omron BLE BP cuff, and MQTT subscribers.

- `firmware/esp32-a-vitals/` — ESP32 firmware for the console node
  (MAX30100 pulse oximeter + MLX90640BAB thermal imager)
- `firmware/esp32-b-anthro/` — ESP32 firmware for the stand node
  (VL53L0X height sensor)
- `kiosk/` — Raspberry Pi 5 Python application (PyQt6 GUI, session FSM,
  BLE services, MQTT broker client, SQLite, sync daemon, printer)
- `cloud/` — FastAPI + PostgreSQL backend (REST API, BHW portal data,
  audit log mirror)
- `portal/` — React + TypeScript BHW web portal (consumes cloud API)
- `docs/` — research paper, architectural deliverables, ADRs
- `docs/decisions/` — Architecture Decision Records (ADRs); read these
  before proposing major changes
- `scripts/` — setup, deployment, calibration utilities
- `schema.sql` — authoritative database schema; both kiosk and cloud
  mirror this with type substitutions handled by Alembic migrations

## Tech decisions (locked)

These decisions are settled. Do not propose alternatives unless I
explicitly open the topic.

### Hardware

- **Central hub:** Raspberry Pi 5 (4 GB or 8 GB), running Raspberry Pi OS
  trixie (Debian 13-based). Debian 12 is unreliable for our BLE library
  stack and must not be used.
- **Sensor nodes:** Two ESP32 microcontrollers. ESP32-A on the console
  with the MAX30100 pulse oximeter (via M5Stack Mini Heart Rate Unit,
  with a physical finger shroud) and the MLX90640BAB thermal imager
  (wide-angle 110°×75°, centre-ROI peak detection at 25–30 cm working
  distance, emissivity 0.98). The two sensors live on separate I²C
  buses (Wire / Wire1) to preserve the bandwidth isolation per
  ADR-0018. ESP32-B on the stand with the VL53L0X Time-of-Flight
  height sensor (long-range mode, validated range 120–185 cm).
- **BLE commercial devices, attached directly to the Pi (NOT through
  ESP32):**
  - Omron HEM-7155T blood pressure monitor — clinically validated,
    accessed via the omblepy library
  - Xiaomi Smart Scale S200 — accessed via the Bluetooth-Devices/xiaomi-ble
- **Thermal printer:** Xprinter XP-58IIH USB thermal receipt printer
  (58 mm paper, ESC/POS, partial auto-cutter). VID:PID typically
  `0x0416:0x5011`, verify per unit. Driven via `python-escpos`.
- **Peripherals:** capacitive touchscreen (15.6 inch, 1920x1080); MFRC522 RFID reader over SPI; centralized 5 V supply
  for Pi, ESP32 nodes, touchscreen, RFID — but **not** for the printer
  (it has its own 9 V adapter).

### Kiosk software stack

- **Language:** Python 3.12. Managed with `uv`.
- **GUI:** PyQt6 (with Qt6 system libraries on the Pi).
- **Database:** SQLite via SQLCipher (AES-256). Passphrase derived at
  runtime from Pi machine-id + installation-time salt; never stored
  in plaintext.
- **MQTT:** Mosquitto broker on `localhost`, paho-mqtt client. Local-
  network only; not exposed beyond the kiosk.
- **BLE:** `bleak` for active connections (Omron via `omblepy`),
  `bluepy` for passive advertisement scanning (Xiaomi). BlueZ system
  stack underneath, on Raspberry Pi OS trixie.
- **Reverse-engineered libraries (GINHAWA-maintained forks):**
  - `omblepy` — Omron HEM-7155T BLE protocol; **read-only mode** only.
  - `Bluetooth-Devices/xiaomi-ble`— Xiaomi Smart Scale S200
- **Printer:** `python-escpos` over USB.
- **Logging:** `structlog`, JSON output to file, rotated daily.

### Cloud software stack

- **Framework:** FastAPI on Python 3.12, managed with `uv`.
- **Database:** PostgreSQL 16. Schema mirrors `schema.sql` with TEXT→
  VARCHAR, REAL→DOUBLE PRECISION substitutions handled in Alembic.
- **ORM:** SQLAlchemy 2.x with declarative models.
- **Migrations:** Alembic, with separate migration trees for kiosk
  (SQLite) and cloud (PostgreSQL).
- **Auth:** JWT with short-lived tokens, argon2id password hashing
  for BHW portal users.
- **Transport:** HTTPS with TLS 1.3; weaker TLS rejected.

### Portal software stack

- **Framework:** React 18 with Vite as the build tool.
- **Language:** TypeScript with strict mode on. No `any`. No
  type-asserted-as-any escape hatches.
- **Data layer:** TanStack Query (React Query v5).
- **API client:** Generated from the cloud's OpenAPI spec via
  `openapi-typescript`. Do not hand-write fetch wrappers.
- **UI library:** shadcn/ui.
- **Routing:** React Router v6.

### Firmware stack

- **Framework:** Arduino on PlatformIO.
- **Target:** ESP32 (esp32dev board).
- **Native test target:** Configured for desktop unit tests of
  signal-processing and JSON-encoding logic without needing a
  physical board flashed.
- **Libraries:** PubSubClient (MQTT), ArduinoJson (JSON encoding).

### Cross-cutting decisions

- **Identifiers:** All `id` columns are RFC 4122 v4 UUIDs (TEXT in
  SQLite, UUID type in PostgreSQL), except `audit_log.id` which is
  AUTOINCREMENT integer.
- **Timestamps:** All stored as ISO 8601 strings in UTC. Local-time
  display is the UI's responsibility.
- **Sync model:** Per-record `synced` boolean. Kiosk → cloud is
  write-mostly; conflicts are rare. Sync daemon marks `synced = 1`
  only after explicit server confirmation.
- **Bilingual UI:** All user-facing text in English and Tagalog.
  Touchscreen has a language toggle. Receipts print in the language
  selected at session start.

## Never do these

These rules are absolute and override any other consideration. If you
believe a rule must be broken, stop and tell me before doing anything.

### Hardware safety and integrity

- **Never write to the Omron HEM-7155T EEPROM.** Do not use `omblepy`'s
  `-n` (new-record-counter) or `-t` (time-sync) flags. The BP
  measurement sub-flow is read-only by design; writes risk corrupting
  the device's pressure-sensor calibration.
- **Never power the thermal printer from the Pi's USB rail or 5 V
  GPIO.** It must use its own 9 V external adapter. Sharing power
  causes Pi brownout during high-density print lines.
- **Never run more than one BLE operation concurrently.** BlueZ on
  the Pi is not concurrent-safe for our use. The session FSM
  serializes BP, weight, and any BLE access strictly. If you see
  code that opens two BLE connections simultaneously, that is a bug.

### Data privacy and integrity

- **Never store patient data unencrypted at rest.** Local SQLite is
  always opened via SQLCipher with the derived passphrase. Cloud
  Postgres relies on the hosting provider's at-rest encryption.
- **Never bypass the record_audit helper for mutations or sensitive reads**. The application is the sole writer of audit_log for both mutations and sensitive reads (citizen views, exports, login events). Every mutation handler and every sensitive-read handler calls services.audit.record_audit() in the same transaction as its main operation; if the audit write fails, the operation rolls back. The database enforces append-only via the audit_log_no_update and audit_log_no_delete triggers and by revoking UPDATE/DELETE on audit_log from the application's database role.
- **Never hard-delete a citizen who has any sessions.** Soft-delete
  via `is_active = 0`. Hard-delete only after retention period
  expiry, and CASCADE through sessions and measurements per the
  schema's ON DELETE rules.
- **Never log sensitive personal information at INFO level or higher.**
  Names, RFID UIDs, and measurement values may appear in DEBUG logs
  for development; production logs use structured fields with
  hashed identifiers only.
- **Never commit `.env`, secrets, SQLCipher passphrases, BLE pairing
  keys, JWT secrets, or any production credential.** `.env.example`
  is the only environment file that goes in git.
- **Never re-add automatic audit triggers to the patient-data tables (citizens, sessions, measurements).** A previous design used database triggers to write audit rows on mutation; this was removed because triggers cannot capture rich actor context (which BHW, which IP, which session, what reasoning). See ADR-0005 for the full rationale. The application-layer record_audit helper is the only correct path.

### Schema and migrations

- **Never modify `schema.sql` without a matching Alembic migration in
  both `kiosk/alembic/` and `cloud/alembic/`.** The schema is the
  contract between modules.
- **Never write a destructive migration without me reviewing it
  first.** Anything that drops a column, drops a table, or changes
  a column type on data-bearing tables requires explicit human
  approval before commit.

### Xiaomi scale specifics

- **Never use the Xiaomi library's default user-identification-by-
  weight-range logic.** That logic is designed for household
  deployments with two or three users and non-overlapping weight
  ranges. In a barangay kiosk with many users, ranges overlap and
  the logic is wrong. Each captured weight is assigned to the RFID-
  authenticated user active in the current session.
- **Never store the Xiaomi scale's body-composition outputs.** Body
  fat percentage, muscle mass, water content, bone mass, and
  segmental analysis are read from the BLE advertisement and
  immediately discarded. Bioimpedance varies too much with
  hydration, foot contact, and recent activity to be reliable for
  community screening, and they are out of declared scope under
  the Data Privacy Act consent.
- **Never use the Xiaomi scale's heart-rate measurement.** Heart rate
  is obtained from the MAX30100 pulse oximeter. The S200's foot-
  electrode HR is ignored.

## How to run things

### Kiosk

```bash
cd kiosk
uv sync                           # install/update dependencies
uv run pytest                     # run all tests
uv run pytest tests/printers/     # run a specific test directory
uv run python -m ginhawa_kiosk    # run the kiosk app (on Pi)
uv run ruff format .              # format code
uv run ruff check . --fix         # lint and auto-fix
```

### Cloud

```bash
cd cloud
uv sync
uv run pytest
uv run alembic upgrade head       # apply migrations
uv run alembic revision --autogenerate -m "msg"  # create migration
uv run uvicorn ginhawa_cloud:app --reload --port 8000
```

### Portal

```bash
cd portal
npm install
npm test                          # vitest run
npm run dev                       # local dev server
npm run build                     # production build
npm run lint
npm run format
```

### Firmware

```bash
cd firmware/esp32-a-vitals
pio test -e native                # desktop unit tests (no board needed)
pio run -e esp32dev               # compile for device
pio run -e esp32dev -t upload     # flash to connected board
pio device monitor                # serial monitor
```

### Local Postgres for cloud development

```bash
docker compose up -d postgres
docker compose ps                 # verify healthy
docker compose down               # stop (data persists in volume)
```

## Style and conventions

### Python

- Format with `ruff format`. Lint with `ruff check`. Both run in
  pre-commit.
- **Full type hints required** on all function signatures (parameters
  and return types). No `Any` unless interfacing with untyped third-
  party code, in which case isolate the boundary.
- Docstrings on **public** APIs only — module-level functions, public
  class methods. Private helpers (leading underscore) do not need
  docstrings unless their behavior is non-obvious.
- Prefer dataclasses over dicts for structured returns.
- Prefer `pathlib.Path` over `os.path`.
- Prefer `match` statements over long `if/elif/else` chains.
- Logging via `structlog`, structured fields, never f-string-concat
  user data into log messages.

### TypeScript

- Format with `prettier`. Lint with `eslint`.
- Strict mode on (`strict: true` in tsconfig). No `any`. No
  `as unknown as Foo` casts.
- Function components only. No class components.
- Server state via TanStack Query; local state via `useState` or
  `useReducer`. No Redux unless we have a documented reason in an
  ADR.
- File naming: `kebab-case.tsx` for components, `camelCase.ts` for
  utilities.

### C++ (firmware)

- Format with `clang-format` using LLVM style. 100-character line
  width.
- Avoid heap allocation in the main loop. Pre-allocate buffers at
  setup time.
- Always check return values of I²C/SPI/MQTT calls.
- One sensor per file; don't multiplex sensor logic into one big
  cpp file.

### Commits

- Conventional Commits format: `feat:`, `fix:`, `chore:`, `docs:`,
  `refactor:`, `test:`, `perf:`.
- Subject under 72 characters; body explains *why* if non-obvious.
- One logical change per commit; do not bundle unrelated changes.

### Tests

- Tests must accompany every new feature. Do not merge untested
  code.
- For bug fixes: write the failing test first, then the fix.
- Prefer behavior-focused test names: `test_paper_out_hides_print_button`
  over `test_print_button`.
- Mock external boundaries (BLE, MQTT broker, USB printer) at the
  service interface, not deeper.

## When you encounter a decision

1. Check `/docs/decisions/` for an ADR that already covers it.
2. If no ADR exists and the decision has lasting consequences,
   stop and ask before deciding. Do not silently choose.
3. After a non-obvious decision is made, write a new ADR as part
   of the same commit.

## Failure modes — fail loud, fail safe

- Prefer raising exceptions over silent fallback. If the BLE scan
  fails, surface the failure; do not return a default weight.
- The kiosk session continues even if individual measurements fail
  ("unavailable" is recorded). The kiosk **does not crash** because
  one sensor failed.
- Cloud sync failures are non-fatal: the kiosk continues to
  accumulate records locally. The next sync attempt retries with
  exponential backoff.
- The printer is best-effort: a print failure does not affect the
  session record. The session is saved regardless.

## Kiosk-specific rules

These augment the absolute rules in "Never do these" with kiosk-side
implementation details that recur across modules.

- **SQLCipher requires `PRAGMA key = '<key>'` immediately on every
  new connection.** This is the FIRST statement on the connection,
  before any other SQL — otherwise SQLCipher reports "file is not a
  database". The data-access layer (`db/session.py`) hooks
  SQLAlchemy's `connect` event and is the only sanctioned caller of
  `core.security.apply_sqlcipher_pragma`. Do not open raw connections
  elsewhere.
- **SQLAlchemy models in `kiosk/src/ginhawa_kiosk/db/models.py` track
  `/schema.sql` and never diverge from it.** Schema changes require
  matching Alembic migrations in BOTH `kiosk/alembic/` and
  `cloud/alembic/`, plus an update to `/schema.sql`. The kiosk omits
  `users` and `device_credentials` (cloud-only); everything else
  mirrors the cloud structure with TEXT/REAL → String/Float
  substitutions.
- **The kiosk is the SOLE writer to its local `audit_log`,** via
  `services.audit.record_audit`. This mirrors the cloud's pattern
  (ADR-0005). Defence-in-depth on the kiosk: the disk file is
  encrypted, this module is the only writer, and there are no
  UPDATE/DELETE handlers exposed for `audit_log`.
- **BLE operations are never run concurrently.** BlueZ on the Pi is
  not concurrent-safe for our use. The session FSM is the SOLE
  serialiser of BLE access — it holds a lock and owns each BLE
  device's lifecycle (BP measurement → release → weight scan →
  release → ...). If you see code that opens two BLE connections
  simultaneously, that is a bug.
- **MQTT subscriptions handle reconnect-and-resubscribe automatically.**
  Mosquitto on `localhost` may bounce (e.g., during an OS update); the
  paho-mqtt client is configured with `reconnect_delay_set` and the
  on-connect callback re-subscribes to every topic. Application code
  treats subscriptions as durable, not one-shot.
- **`MOCK_HARDWARE` is the SINGLE switch between dev and prod.**
  Subpackages must consult `Settings.MOCK_HARDWARE` through
  `core.config.get_settings()` — never sniff env vars directly,
  never branch on `platform.machine()`, never inspect `/sys`. One
  switch, one truth, one place. The factory in `sensors/factory.py`
  is the only place this flag is read.

## Things I'll often ask Claude Code to do

- Add a new endpoint with tests (cloud)
- Add a new screen with tests (portal)
- Add a new measurement parser (kiosk)
- Refactor a module while preserving behavior
- Generate an Alembic migration from schema changes (with manual
  review)
- Generate a TypeScript client from the cloud's OpenAPI spec
- Write tests for an existing module that lacks coverage

## Things I will NOT ask Claude Code to do

- Write Architecture Decision Records on its own (those reflect
  human reasoning)
- Edit `CLAUDE.md` itself (this file is human-maintained)
- Commit secrets, generate cryptographic keys, or pick passphrases
- Run database migrations against production data
- Make medical or clinical-policy decisions (e.g., what BMI
  threshold counts as "concerning")
- Modify the research paper's prose

## Useful context for the project

- The research paper lives at `docs/paper.pdf`. The architectural
  deliverables (hardware update, schema revision, DPA section,
  printer integration, editorial pass) are in `docs/`.
- Survey data uses N=113. Be aware that the original draft had an
  N=50 vs N=113 inconsistency that has since been reconciled; do
  not reintroduce N=50 references.
- The target deployment context is barangay health centers in the
  Philippines. UI must work for users with limited tech literacy
  and possible visual impairment (large fonts, high contrast,
  bilingual EN/TL).
- Data Privacy Act of 2012 (RA 10173) compliance is structural to
  the design. Do not propose changes that would weaken consent,
  audit logging, or encryption.
