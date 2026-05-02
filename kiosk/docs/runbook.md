# GINHAWA kiosk runbook

Operational procedures for commissioning, debugging, and demoing
the kiosk. Pre-deploy install steps live in
[`docs/phase-0-plan.md`](../../docs/phase-0-plan.md); this runbook
covers what comes after, while a kiosk is in service.

## Continuous capture mode

A long-running diagnostic CLI that subscribes to the kiosk's event
bus and prints / logs every sensor event it sees, indefinitely,
until you Ctrl-C. Read-only — does not write to the kiosk database
and does not produce session records.

### When to use

- **Commissioning a fresh Pi.** After `provision_db` and BlueZ
  pairing, run continuous-capture once to confirm RFID, Xiaomi
  scale, and Omron BP cuff each deliver readings end-to-end before
  handing the kiosk to the field team.
- **Demos.** Show the full sensor surface in action without spinning
  up the citizen-facing GUI flow.
- **Field debug.** A deployed kiosk is reporting "weight didn't
  capture" / "card scan missed" / "BP wouldn't read"? Stop the
  systemd unit, run continuous-capture against the same hardware,
  and compare what the bus sees against what the operator observed.

### Usage examples

Capture from all sensors, log to the default location
(`~/.ginhawa-continuous-capture.jsonl`):

```bash
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY="$KIOSK_DB_KEY" \
  uv run python -m ginhawa_kiosk.scripts.continuous_capture
```

Only RFID, no log file (stdout only — useful for quick card-reading
checks):

```bash
uv run python -m ginhawa_kiosk.scripts.continuous_capture \
    --sensors rfid --no-log-file
```

On a developer laptop with no hardware:

```bash
MOCK_HARDWARE=true \
  uv run python -m ginhawa_kiosk.scripts.continuous_capture
```

Headless / pipeline (no interactive BP-trigger prompt — useful when
another process publishes `BpMeasurementRequested`):

```bash
uv run python -m ginhawa_kiosk.scripts.continuous_capture \
    --no-bp-prompt --verbose
```

The interactive BP-trigger prompt lets the operator press Enter to
publish a `BpMeasurementRequested` event, mirroring what the GUI
will eventually do. Each press triggers one capture; the next
prompt appears once the cuff disconnects.

### Output format

Each captured event prints as a single line on stdout:

```
[14:23:11] RfidScanned: uid=A3F2C901
[14:23:45] MeasurementProposed: type=systolic_bp value=128.0 unit=mmHg source=omron_hem7155t valid=True
[14:23:45] MeasurementProposed: type=diastolic_bp value=82.0 unit=mmHg source=omron_hem7155t valid=True
[14:23:45] MeasurementProposed: type=heart_rate value=74.0 unit=bpm source=omron_hem7155t valid=True
[14:24:02] MeasurementProposed: type=weight value=61.05 unit=kg source=xiaomi_s200_ble valid=True
```

The JSONL log file uses ISO-8601 UTC timestamps and includes every
field of the event:

```json
{"timestamp": "2026-05-02T14:23:11.412+00:00", "event": "RfidScanned", "uid": "A3F2C901"}
{"timestamp": "2026-05-02T14:23:45.087+00:00", "event": "MeasurementProposed", "measurement_type": "systolic_bp", "value": 128.0, "unit": "mmHg", "source_device": "omron_hem7155t", "claimed_is_valid": true}
```

On Ctrl-C the tool prints a per-event-type summary:

```
[14:35:02] === capture summary ===
  duration_seconds: 712.4
  MeasurementProposed: 17
  RfidScanned: 4
```

### Important warnings

- **This tool does not record measurements to the citizen database.**
  No `Measurement` rows. No `Session` rows. No `audit_log` rows. No
  consent capture. No barangay attribution. The JSONL log is a
  forensic trace of bus traffic, not a clinical record.
- **Do not use this to capture real patient data.** If a citizen
  walks up wanting their BP / weight / RFID profile recorded, run
  the kiosk's normal `ginhawa-kiosk` flow (the citizen-facing GUI),
  not this tool. The normal flow is the only path that produces
  audited, attributed measurements bound to a session.
- **`MOCK_HARDWARE=true` runs against the in-process mocks**,
  exactly like the kiosk itself. Demos on a laptop should set this;
  hardware verification on a Pi must NOT.
- **The systemd `ginhawa-kiosk` unit and continuous-capture cannot
  share BLE adapter time.** BlueZ on the Pi serialises BLE access;
  if the kiosk service is running, stop it before launching
  continuous-capture: `sudo systemctl stop ginhawa-kiosk`. Restart
  the unit when you're done.

### Available CLI options

| Flag                                | Default                               | Effect                                           |
| ----------------------------------- | ------------------------------------- | ------------------------------------------------ |
| `--sensors {rfid,xiaomi,omron,all}` | `all`                                 | Pick which sensors to start                      |
| `--log-file PATH`                   | `~/.ginhawa-continuous-capture.jsonl` | Where the JSONL log goes                         |
| `--no-log-file`                     | (off)                                 | Disable file logging entirely                    |
| `--verbose`                         | (off)                                 | Set the structlog level to DEBUG                 |
| `--no-bp-prompt`                    | (off)                                 | Disable the interactive Enter-to-trigger-BP loop |

Mutually exclusive: `--log-file` and `--no-log-file`.
