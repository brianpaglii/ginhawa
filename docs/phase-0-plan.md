# Phase 0 plan — host requirements, hardware, toolchain

**Status:** Draft (consolidated from CLAUDE.md, kiosk/README.md, and ADRs 0001 / 0002 / 0003 / 0004 / 0017)
**Last updated:** 2026-05-02
**Scope:** what you need installed and configured before any package's
quickstart steps will succeed.

This document is the **single entry point for setting up a development
environment or commissioning a deployment**. It supersedes scattered
install notes in per-package READMEs.

---

## Reading guide

| If you are...                                           | Read                                                         |
| ------------------------------------------------------- | ------------------------------------------------------------ |
| A developer setting up a laptop to write code           | [§Developer-laptop quickstart](#developer-laptop-quickstart) |
| Commissioning a fresh Raspberry Pi for kiosk deployment | [§Raspberry Pi commissioning](#raspberry-pi-commissioning)   |
| Setting up the cloud locally (Postgres + uvicorn)       | [§Cloud quickstart](#cloud-quickstart)                       |
| Setting up the BHW portal                               | [§Portal quickstart](#portal-quickstart)                     |
| Building the ESP32 firmware                             | [§Firmware quickstart](#firmware-quickstart)                 |

The Pi commissioning section is the long one — most other paths
inherit from it.

---

## Hardware

Locked in CLAUDE.md "Tech decisions (locked)". Listed here for
inventory clarity; consult CLAUDE.md before substituting any item.

| Item            | Model / part                                                         | Notes                                                                                                                                            |
| --------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Central hub     | Raspberry Pi 5 (4 GB or 8 GB)                                        | RPi OS trixie (Debian 13). **Debian 12 is unreliable** for our BLE stack — do not use.                                                           |
| Console node    | ESP32 (esp32dev) + MAX30100 via M5Stack Mini Heart Rate Unit         | Pulse oximeter + heart rate. Has a physical finger shroud.                                                                                       |
| Stand node      | ESP32 (esp32dev) + VL53L0X + MLX90640BAB                             | Height (long-range mode, validated 120–185 cm) + thermal imager (110°×75°, centre-ROI peak detection at 25–30 cm, emissivity 0.98).              |
| BP cuff         | Omron HEM-7155T                                                      | BLE, attached **directly to the Pi**, not through ESP32. Clinically validated.                                                                   |
| Body scale      | Xiaomi Smart Scale S200 (`xiaomi.scales.ms111`, product ID `0x4C04`) | BLE, attached **directly to the Pi**. Per-device bindkey required — see ADR-0017 and [§Xiaomi scale commissioning](#xiaomi-scale-commissioning). |
| Thermal printer | Xprinter XP-58IIH (USB, 58 mm, ESC/POS, partial auto-cutter)         | VID:PID typically `0x0416:0x5011` — verify per unit. **Powered by its own 9 V adapter**, never from the Pi.                                      |
| Touchscreen     | 15.6" capacitive, 1920×1080                                          | HDMI + USB-touch.                                                                                                                                |
| RFID reader     | MFRC522 (13.56 MHz MIFARE Classic / NTAG)                            | SPI on the Pi. Pin map below.                                                                                                                    |
| Power           | Centralised 5 V rail                                                 | Pi, ESP32×2, touchscreen, RFID. **Not the printer.**                                                                                             |

### MFRC522 → Pi 5 pin map

| MFRC522 | Pi 5 (BCM)   | Notes                                                                                                                   |
| ------- | ------------ | ----------------------------------------------------------------------------------------------------------------------- |
| SDA     | GPIO 8 (CE0) | SPI chip-select                                                                                                         |
| SCK     | GPIO 11      | SPI clock                                                                                                               |
| MOSI    | GPIO 10      | SPI MOSI                                                                                                                |
| MISO    | GPIO 9       | SPI MISO                                                                                                                |
| IRQ     | GPIO 24      | Edge-triggered card-present (unused by current driver, but wired so the runtime can be flipped to interrupt mode later) |
| RST     | GPIO 25      | Reset                                                                                                                   |
| 3V3     | 3.3 V        | Do NOT use 5 V.                                                                                                         |
| GND     | GND          |                                                                                                                         |

### Hardware safety rules (absolute)

These are mirrored from CLAUDE.md "Never do these"; restated here so
the deployment runbook is self-contained:

- **Never power the thermal printer from the Pi's USB rail or 5 V GPIO.**
  The high-density print lines draw enough current to brown out the
  Pi. The printer's own 9 V adapter is mandatory.
- **Never run more than one BLE operation concurrently.** BlueZ on
  the Pi is not concurrent-safe for our use. The session FSM
  serialises BP → release → weight → release. ADR-0017 covers the
  Xiaomi half; CLAUDE.md "Hardware safety" the Omron half.
- **Never write to the Omron HEM-7155T EEPROM.** Read-only mode only.

---

## Developer-laptop quickstart

Target: Linux (any distro), macOS, or Windows-WSL2. The laptop runs
mock-mode for everything BLE / RFID / MQTT / printer.

### 1. Toolchain

```bash
# Python 3.12 + uv (ADR-0003)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version           # ≥ 0.10.12

# Node 20 (for the portal)
# pick your preferred installer (nvm, fnm, asdf, etc.)
node --version         # ≥ 20.0.0
npm --version          # ≥ 10

# PlatformIO (for firmware desktop unit tests)
pip install --user platformio
pio --version

# Docker + Compose (for local Postgres)
docker --version
docker compose version
```

### 2. Clone + per-package install

```bash
git clone <repo> ginhawa
cd ginhawa

# Cloud
cd cloud && uv sync && cd ..

# Kiosk (the Pi-only deps mfrc522 / RPi.GPIO / spidev are markered;
# uv skips them on x86_64 / arm64-mac.)
cd kiosk && uv sync && cd ..

# Portal
cd portal && npm install && cd ..

# Firmware desktop tests only (no board flashing on a laptop)
cd firmware/esp32-a-vitals && pio pkg install && cd ../..
cd firmware/esp32-b-anthro && pio pkg install && cd ../..
```

### 3. Pre-commit hooks

```bash
uvx pre-commit install
```

### 4. Verify

```bash
cd cloud   && uv run pytest -q && cd ..
cd kiosk   && uv run pytest -q && cd ..
cd portal  && npm test -- --run && cd ..
cd firmware/esp32-a-vitals && pio test -e native && cd ../..
cd firmware/esp32-b-anthro && pio test -e native && cd ../..
```

All five suites pass = the laptop is correctly set up.

---

## Raspberry Pi commissioning

Target: a fresh Raspberry Pi 5, Raspberry Pi OS trixie installed,
network and SSH already configured.

### Choose your starting state

Two supported scenarios — pick one and follow the matching path:

| You logged in as...                             | Path                                                                                                                                         |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| The default `pi` (or another sudo-capable user) | [Path A](#path-a-creating-the-ginhawa-service-user) — create a dedicated `ginhawa` service user, then drop into it for the kiosk install     |
| `ginhawa` directly (the user already exists)    | [Path B](#path-b-running-as-the-ginhawa-user) — most steps run as the current shell; only system-wide ones (apt, systemd, /etc/) need `sudo` |

Both paths produce the same end state: kiosk source under
`/opt/ginhawa/src`, encrypted DB at `/var/lib/ginhawa/kiosk.db`,
credentials at `/etc/ginhawa/kiosk.env`, systemd unit
`ginhawa-kiosk.service` running as the `ginhawa` user.

If you're not sure which path to take:

```bash
whoami                    # → 'pi' = Path A; 'ginhawa' = Path B
id -nG                    # check which groups your user is in
```

If your `ginhawa` user isn't in the `gpio`, `spi`, and `bluetooth`
groups, it cannot drive the MFRC522 over SPI or talk BLE — see
step 2 of either path.

### 1. System packages (both paths)

```bash
sudo apt update
sudo apt install -y \
    git \
    bluez bluez-tools \
    mosquitto mosquitto-clients \
    qt6-base-dev qt6-wayland \
    libqt6gui6 libqt6widgets6 \
    python3-pip pipx \
    sqlcipher \
    libgpiod2

# Enable SPI for the MFRC522
sudo raspi-config nonint do_spi 0

# Mosquitto: bind to localhost only — NOT the LAN.
sudo tee /etc/mosquitto/conf.d/ginhawa-localhost.conf >/dev/null <<'EOF'
listener 1883 127.0.0.1
allow_anonymous true
EOF
sudo systemctl enable --now mosquitto
```

### Path A: creating the `ginhawa` service user

Take this path if you logged in as `pi` (or any sudo-capable user
other than `ginhawa`). Skip if your current user is already
`ginhawa` — go to [Path B](#path-b-running-as-the-ginhawa-user).

#### A.2. Create the user and install uv

```bash
sudo useradd -m -s /bin/bash ginhawa
sudo usermod -aG gpio,spi,bluetooth,input ginhawa
sudo -u ginhawa bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
```

#### A.3. Clone + sync the kiosk package

```bash
sudo install -d -o ginhawa -g ginhawa /opt/ginhawa
sudo -u ginhawa git clone <repo> /opt/ginhawa/src
sudo -u ginhawa bash -lc 'cd /opt/ginhawa/src/kiosk && uv sync'
```

#### A.4. Provision the encrypted local database

```bash
sudo install -d -o ginhawa -g ginhawa -m 0700 /var/lib/ginhawa
sudo -u ginhawa bash -lc '
  cd /opt/ginhawa/src/kiosk
  KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
  uv run python -m ginhawa_kiosk.scripts.provision_db
'
```

Then continue with [step 5: pair the Omron BP cuff](#5-pair-and-capture-the-omron-bp-cuff)
(common to both paths).

### Path B: running as the `ginhawa` user

Take this path if `whoami` already prints `ginhawa`. The user
exists; `sudo -u ginhawa` is redundant and only adds password
prompts.

Quick sanity check first:

```bash
groups                    # must include: gpio spi bluetooth input
```

If any of those groups are missing, add them and re-login (group
membership is only picked up on a fresh shell):

```bash
sudo usermod -aG gpio,spi,bluetooth,input "$USER"
exit                      # log out
# log back in
groups                    # confirm
```

#### B.2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# uv installs to ~/.local/bin; exec a new shell or source the rc
# file to pick it up:
source ~/.bashrc          # or ~/.profile, depending on your shell
uv --version
```

#### B.3. Clone + sync the kiosk package

`/opt/ginhawa` is a system path so `sudo` is needed for the
top-level mkdir; once it's owned by you, the rest runs as your
current shell.

```bash
sudo install -d -o "$USER" -g "$USER" /opt/ginhawa
git clone <repo> /opt/ginhawa/src
cd /opt/ginhawa/src/kiosk
uv sync
```

#### B.4. Provision the encrypted local database

```bash
sudo install -d -o "$USER" -g "$USER" -m 0700 /var/lib/ginhawa
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
  uv run python -m ginhawa_kiosk.scripts.provision_db
```

### After step 4 (both paths)

This:

1. Generates a 32-byte SQLCipher key (hex-encoded, 64 ASCII chars).
2. Prints the key ONCE under a `DO NOT LOSE THIS` banner.
3. Creates the encrypted DB file at `KIOSK_DB_PATH`.
4. Initialises the schema (per ADR-0001).

**Capture the key into the credentials file the systemd unit will
read** (see step 7) before the SSH session ends. There is no
recovery path.

### 5. Pair and capture the Omron BP cuff

```bash
sudo bluetoothctl
[bluetooth]# scan on            # press the cuff's Bluetooth button
[bluetooth]# pair  AA:BB:CC:DD:EE:FF
[bluetooth]# trust AA:BB:CC:DD:EE:FF
[bluetooth]# scan off
[bluetooth]# quit
```

Record the cuff's MAC and store it in `device_config`. The
`run_as_ginhawa` helper below wraps the two paths' shells: on
Path A use `sudo -u ginhawa bash -lc '...'`; on Path B drop the
prefix and just run the body. Both produce the same effect.

```bash
# Path B (logged in as ginhawa):
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-step-4> \
  uv run python -c "
from ginhawa_kiosk.db.models import DeviceConfig
from ginhawa_kiosk.db.session import create_engine_for_kiosk, make_session_factory
from datetime import datetime, timezone
import os, pathlib
factory = make_session_factory(
    create_engine_for_kiosk(
        pathlib.Path(os.environ['KIOSK_DB_PATH']),
        os.environ['KIOSK_DB_KEY'],
    )
)
with factory() as s:
    s.add(DeviceConfig(
        key='omron_cuff_mac',
        value='AA:BB:CC:DD:EE:FF',
        updated_at=datetime.now(timezone.utc).isoformat(),
    ))
    s.commit()
"

# Path A (logged in as pi or another sudoer): wrap the same body —
#   sudo -u ginhawa bash -lc '
#     cd /opt/ginhawa/src/kiosk && \
#     KIOSK_DB_PATH=… KIOSK_DB_KEY=… uv run python -c "…"
#   '
```

**Per CLAUDE.md "Hardware safety": never write to the Omron EEPROM.**
Do not run `omblepy` with `-n` or `-t` flags during commissioning or
afterwards. Read-only access only.

### 6. Pair and capture the Xiaomi scale (per ADR-0017)

The S200 mints its bindkey on first pairing to the **Mi Home app**, not
on first connection to the Pi. Per ADR-0017 the bindkey extraction is
a one-time runbook step:

1. Install Mi Home on a designated commissioning phone (Android
   recommended; the bindkey is extractable via `adb pull` of the app's
   data dir, which iOS forbids).
2. Step on the scale to wake it; pair to Mi Home.
3. Locate the bindkey in the Mi Home app's `device_info` JSON blob.
   The key is a 16-byte hex string (32 ASCII characters). Document
   the exact path in the deployment runbook for your Mi Home version
   — it has moved between releases.
4. Pair the scale to the Pi over BLE (`bluetoothctl scan on`, find
   the device, `pair`, `trust`).
5. Store the bindkey (Path B form below — wrap with
   `sudo -u ginhawa bash -lc '...'` on Path A):

```bash
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-step-4> \
  uv run python -c "
from ginhawa_kiosk.db.models import DeviceConfig
from ginhawa_kiosk.db.session import create_engine_for_kiosk, make_session_factory
from datetime import datetime, timezone
import os, pathlib
factory = make_session_factory(
    create_engine_for_kiosk(
        pathlib.Path(os.environ['KIOSK_DB_PATH']),
        os.environ['KIOSK_DB_KEY'],
    )
)
with factory() as s:
    s.add(DeviceConfig(
        key='xiaomi_scale_bindkey',
        value='<paste-32-char-hex-bindkey-here>',  # pragma: allowlist secret
        updated_at=datetime.now(timezone.utc).isoformat(),
    ))
    s.commit()
"
```

The bindkey inherits SQLCipher at-rest encryption from ADR-0001.

### 7. Wire env vars into systemd

The kiosk reads its config from environment variables (CLAUDE.md
"Kiosk software stack"). Production deployment uses a
**root-only credentials file** consumed by `EnvironmentFile=`:

```bash
sudo install -d -m 0700 /etc/ginhawa
sudo tee /etc/ginhawa/kiosk.env >/dev/null <<EOF
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db
KIOSK_DB_KEY=<the-key-from-step-4>
KIOSK_API_KEY=<plaintext-from-cloud-admin>
KIOSK_DEVICE_ID=<uuid-matching-cloud-device_credentials.device_id>
CLOUD_API_URL=https://cloud.ginhawa.example
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
LOG_LEVEL=INFO
MOCK_HARDWARE=false
EOF
sudo chmod 0600 /etc/ginhawa/kiosk.env
sudo chown root:root /etc/ginhawa/kiosk.env
```

`KIOSK_API_KEY` is the plaintext device API key. The cloud admin
issues it via `POST /api/v1/device-credentials` and shows it once;
capture it during commissioning. `KIOSK_DEVICE_ID` is the UUID the
admin endpoint returned alongside the API key.

### 8. systemd unit

```bash
sudo tee /etc/systemd/system/ginhawa-kiosk.service >/dev/null <<'EOF'
[Unit]
Description=GINHAWA kiosk
After=network-online.target mosquitto.service bluetooth.service
Wants=network-online.target

[Service]
User=ginhawa
Group=ginhawa
EnvironmentFile=/etc/ginhawa/kiosk.env
WorkingDirectory=/opt/ginhawa/src/kiosk
ExecStart=/home/ginhawa/.local/bin/uv run python -m ginhawa_kiosk
Restart=on-failure
RestartSec=5
# Do NOT auto-restart on credential failures — see ADR-0010 / sync
# daemon's CloudCredentialError handling. The ExitCode=2 / Restart=
# rule should be tightened once we have a stable list of fatal codes.

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now ginhawa-kiosk
```

### 9. Verification

```bash
# Check the unit started cleanly:
sudo systemctl status ginhawa-kiosk
journalctl -u ginhawa-kiosk -f --since "5 min ago"

# Confirm SQLCipher engagement (file exists, is encrypted):
file /var/lib/ginhawa/kiosk.db          # → "data" (not "SQLite database")
# As the ginhawa user (drop the sudo prefix on Path B):
sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
PRAGMA key = '<the-key-from-step-4>';
SELECT name FROM sqlite_master WHERE type='table';
SQL

# Confirm Mosquitto is up and bound localhost-only:
sudo ss -tlnp | grep 1883             # should bind 127.0.0.1, not 0.0.0.0

# Confirm BlueZ sees both BLE peers:
bluetoothctl devices                    # both Omron + Xiaomi listed
```

---

## Cloud quickstart

The cloud package targets Linux/macOS dev. Postgres runs in Docker
locally.

### Local Postgres

```bash
cd ginhawa
docker compose up -d postgres
docker compose ps                       # status: healthy
```

### Sync deps + apply migrations + seed

`POSTGRES_PASSWORD` is the dev-only password from `docker-compose.yml`
(or `.env` if you've copied `.env.example`). Source it into your shell
before running the commands below; never commit a `.env` with the
real value.

```bash
cd cloud
uv sync
DATABASE_URL="postgresql+psycopg://ginhawa:${POSTGRES_PASSWORD}@localhost:5432/ginhawa" \
JWT_SECRET=local-dev-only \
  uv run alembic upgrade head

DATABASE_URL="postgresql+psycopg://ginhawa:${POSTGRES_PASSWORD}@localhost:5432/ginhawa" \
JWT_SECRET=local-dev-only \
  uv run python -m ginhawa_cloud.scripts.seed_dev_data
```

The seed script prints admin/BHW passwords and one device-credential
plaintext (DEV ONLY — see the dev-credential warning printed at end
of run).

### Run the API

```bash
DATABASE_URL=… JWT_SECRET=… \
  uv run uvicorn ginhawa_cloud:app --reload --port 8000
```

OpenAPI at <http://localhost:8000/openapi.json>; Swagger at
<http://localhost:8000/docs>.

---

## Portal quickstart

```bash
cd portal
npm install
npm run dev          # vite dev server with HMR
npm test             # vitest in watch mode
npm run build        # production build
```

The portal's API client is generated from the cloud's OpenAPI spec
(no hand-written fetch wrappers — see CLAUDE.md "Portal software
stack"). Re-generate after cloud-side schema changes:

```bash
cd portal
npm run generate-client    # alias: openapi-typescript ../cloud/openapi.json -o src/api/types.ts
```

---

## Firmware quickstart

```bash
cd firmware/esp32-a-vitals
pio test -e native            # desktop unit tests, no board needed
pio run -e esp32dev           # compile for ESP32 target
pio run -e esp32dev -t upload # flash via USB
pio device monitor            # serial console
```

Same pattern under `firmware/esp32-b-anthro/`.

The firmware publishes to MQTT topics on the Pi's local broker (see
`kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py` for the topic
taxonomy: `ginhawa/kiosk/<device_id>/sensors/{spo2,heart_rate,temperature,height}`).

---

## Required environment variables (full reference)

| Variable           | Where used | Required? | Notes                                                                              |
| ------------------ | ---------- | :-------: | ---------------------------------------------------------------------------------- |
| `DATABASE_URL`     | cloud      |    yes    | Postgres SQLAlchemy URL (`postgresql+psycopg://...`); see `.env.example`           |
| `JWT_SECRET`       | cloud      |    yes    | Random ≥32 byte string. Never reuse across envs.                                   |
| `KIOSK_DB_PATH`    | kiosk      |    no     | Defaults to `~/.ginhawa/kiosk.db`                                                  |
| `KIOSK_DB_KEY`     | kiosk      |    yes    | 64-char hex (32 byte) SQLCipher passphrase                                         |
| `KIOSK_API_KEY`    | kiosk      |    yes    | Plaintext device API key issued by cloud admin                                     |
| `KIOSK_DEVICE_ID`  | kiosk      |    yes    | UUID matching cloud's `device_credentials.device_id`                               |
| `CLOUD_API_URL`    | kiosk      |    no     | Defaults to `https://cloud.ginhawa.local`                                          |
| `MQTT_BROKER_HOST` | kiosk      |    no     | Defaults to `localhost`                                                            |
| `MQTT_BROKER_PORT` | kiosk      |    no     | Defaults to `1883`                                                                 |
| `LOG_LEVEL`        | kiosk      |    no     | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. Default `INFO`.                       |
| `MOCK_HARDWARE`    | kiosk      |    no     | `true` for laptop dev, unset/`false` for Pi prod. The single switch between modes. |

`.env.example` at the repo root is the canonical template; copy to
`.env` (NEVER commit). Production on the Pi uses
`/etc/ginhawa/kiosk.env` consumed by systemd, NOT `.env`.

---

## Troubleshooting

| Symptom                                        | Likely cause                                        | Fix                                                                                                                                |
| ---------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `import RPi.GPIO` fails on Pi                  | user not in the `gpio` group                        | `sudo usermod -aG gpio,spi,bluetooth,input "$USER"`, then log out and log back in (group membership only refreshes on a new shell) |
| `bleak` / scan returns nothing                 | Bluetooth disabled or BlueZ not running             | `sudo systemctl enable --now bluetooth`                                                                                            |
| Kiosk sync gets `401 invalid kiosk credential` | `KIOSK_API_KEY` mismatched against the cloud's hash | revoke the old credential cloud-side, issue new, update `/etc/ginhawa/kiosk.env`, restart the unit                                 |
| SQLCipher reports "file is not a database"     | wrong key, OR the `PRAGMA key` was never issued     | both fail the same way — verify `KIOSK_DB_KEY` first; if that's correct, see ADR-0001 ("Consequences").                            |
| Printer goes dark mid-print + Pi reboots       | printer being drawn from Pi's 5 V rail              | wire the printer to its 9 V adapter (CLAUDE.md "Hardware safety")                                                                  |
| Two BLE adapters disagree on device state      | concurrent BLE access                               | the FSM serialises these; if you see this, code regression is the cause — file a bug                                               |

---

## Things this plan does NOT cover

- Cloud production deployment (TLS termination, hosting platform,
  rate limiting). Phase 4 work; will land as a separate runbook.
- Backup and disaster-recovery for the kiosk's SQLCipher DB. Phase 3
  work; key escrow is the first decision to make.
- Network architecture for multi-kiosk deployments. The current model
  assumes one kiosk per barangay with its own cloud credential; if a
  health centre runs multiple kiosks, both need separate credentials
  (the cloud's `device_id` mismatch guard rejects shared keys —
  ADR-0014 tangentially covers this).
- Hardware procurement / supplier list. Lives in the project's
  hardware-spec deliverable (separate doc).
