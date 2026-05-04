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

## Printer hardware portability

The kiosk's printer service is built for ESC/POS thermal printers
over USB. Different printer brands and models expose USB
differently; the printer service has four config knobs to handle
this, all read from environment variables and surfaced through
`Settings`. Defaults match the Xprinter XP-58IIH (the project's
reference printer); override per deployment when you wire in a
different unit.

1. **`KIOSK_PRINTER_VENDOR_ID` / `KIOSK_PRINTER_PRODUCT_ID`** —
   USB VID:PID. Find them with `lsusb`. Encoded as integers in the
   environment; pydantic accepts both decimal (`1131`) and
   `0x`-prefixed hex (`0x0483`).

2. **`KIOSK_PRINTER_SUPPORTS_STATUS_QUERY`** — set to `false` if
   your printer doesn't respond to ESC/POS DLE EOT or GS r commands.
   _Symptom:_ paper-status check raises `ValueError` ("not enough
   data" or similar). Default: `true`. Disabling means the kiosk
   skips paper-out detection; the citizen-facing report screen still
   shows the Print button (the service can't tell whether paper is
   present), and a paper-out condition surfaces only as
   `print_failed` mid-print rather than `paper_out_pre`. Operators
   should visually inspect the receipt tray.

3. **`KIOSK_PRINTER_USB_IN_ENDPOINT` / `KIOSK_PRINTER_USB_OUT_ENDPOINT`** —
   override `python-escpos`'s auto-detect when it picks the wrong
   endpoint. _Symptom:_ print fails with
   `ValueError: Invalid endpoint address 0xNN`. Find your printer's
   actual endpoints with:

   ```bash
   lsusb -v -d <vid>:<pid> 2>/dev/null | grep -A 2 bEndpointAddress
   ```

   Look for one IN endpoint (high bit set, e.g., `0x81`, `0x82`) and
   one OUT endpoint (low bit clear, e.g., `0x01`, `0x02`). Default:
   unset (auto-detect).

4. **`KIOSK_PRINTER_PROFILE`** — `python-escpos` profile name (e.g.,
   `"TM-T88III"`). Some profiles trigger device queries on init;
   setting unset (default) or a minimal profile can avoid failures
   on non-spec-compliant hardware. Default: unset.

### Tested deployments

| Printer                 | VID:PID         | IN endpoint | OUT endpoint | Status query | Profile |
| ----------------------- | --------------- | ----------- | ------------ | ------------ | ------- |
| Xprinter XP-58IIH       | `0x0416:0x5011` | auto        | auto         | `true`       | unset   |
| STM-based generic 58 mm | `0x0483:0x070b` | `0x81`      | `0x01`       | `false`      | unset   |

### Commissioning a new printer

1. Plug in the printer (USB to the Pi; power from its own adapter —
   never the Pi's USB rail or 5 V GPIO). Confirm it powers up and
   the paper feeds.

2. Capture VID:PID:

   ```bash
   lsusb
   ```

   Note the bus / device line that appeared after plugging in. Set
   `KIOSK_PRINTER_VENDOR_ID` and `KIOSK_PRINTER_PRODUCT_ID` in the
   kiosk's environment file (or `.env`) accordingly.

3. Bare ESC/POS smoke test (bypasses our service so you can
   distinguish "USB / udev problem" from "kiosk service problem"):

   ```bash
   uv run python -c "
   from escpos.printer import Usb
   p = Usb(0xVID, 0xPID)
   p.text('test\n')
   p.cut()
   p.close()
   "
   ```

   A receipt should print. If not, the issue is below the kiosk
   layer — check `/dev/bus/usb` permissions (the kiosk user needs
   `lp` group, or a udev rule granting access), and make sure CUPS
   isn't grabbing the device (`sudo systemctl stop cups` while
   debugging).

4. Paper-status query test:

   ```bash
   uv run python -c "
   from escpos.printer import Usb
   p = Usb(0xVID, 0xPID)
   print(p.paper_status())
   p.close()
   "
   ```

   - Returns an integer `0`, `1`, or `2` →
     `KIOSK_PRINTER_SUPPORTS_STATUS_QUERY=true` (the default).
   - Raises `ValueError` or hangs →
     `KIOSK_PRINTER_SUPPORTS_STATUS_QUERY=false`.

5. If step 3 worked but a full kiosk print fails with
   `Invalid endpoint address 0xNN`:

   ```bash
   lsusb -v -d 0xVID:0xPID 2>/dev/null | grep -A 2 bEndpointAddress
   ```

   Set `KIOSK_PRINTER_USB_IN_ENDPOINT` and
   `KIOSK_PRINTER_USB_OUT_ENDPOINT` to match the values reported.

6. End-to-end validation through the production service (NOT the
   bare-USB bypass):

   ```bash
   KIOSK_PRINTER_VENDOR_ID=0xVID KIOSK_PRINTER_PRODUCT_ID=0xPID \
     # plus any endpoint / status-query overrides from steps 4-5 \
     uv run python -m ginhawa_kiosk.scripts.bench_printer_full
   ```

   The script prints one English and one Tagalog test receipt
   through `EscPosPrinterService` (same code path the kiosk uses in
   production), reports each `PrintedStatus`, and exits non-zero if
   either receipt failed.

7. Record the working configuration in the "Tested deployments"
   table above, then commit the env values into the deployment's
   environment file so subsequent boots pick them up automatically.
