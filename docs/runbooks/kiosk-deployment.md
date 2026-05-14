# GINHAWA Kiosk Deployment Runbook

| Field        | Value                                                              |
| ------------ | ------------------------------------------------------------------ |
| Version      | 1.0                                                                |
| Last updated | 2026-05-14                                                         |
| Owner        | GINHAWA kiosk team                                                 |
| Audience     | Deployment technician (Linux CLI literate; not a Python developer) |

## Purpose

This is the document a technician follows to provision a brand-new
kiosk in a barangay deployment, or to recover an existing kiosk
that has lost its OS or credentials. It is self-contained — every
command you need is here. Deeper architectural context lives in
the ADRs and audits cross-referenced at the end of each phase.

## Time budget

- **Fresh deployment:** ~3 hours wall-clock, ~2 hours hands-on.
- **Recovery / re-deployment** (hardware intact, OS reinstall):
  ~30 minutes once you have the secrets archive from the original
  commissioning.

## Required tools

- Laptop with SSH client and a recent web browser.
- microSD card reader (USB).
- Two USB-C cables to flash the ESP32 DevKit boards.
- One Android phone (for Mi Home pairing of the Xiaomi scale —
  per ADR-0017).
- A printed copy of [Section 2: Per-Kiosk Deployment Worksheet](#section-2--per-kiosk-deployment-worksheet)
  and a pen.
- Tamper-evident envelope to seal the worksheet after deployment
  (the worksheet holds the SQLCipher key — there is no recovery
  path).

## Cross-references

ADRs that inform this runbook:

- **ADR-0017** — Xiaomi BLE library for Smart Scale S200.
- **ADR-0019** — Height stabilisation gate (firmware).
- **ADR-0020** — BP cuff session-floor freshness.
- **ADR-0021** — SQLite WAL + busy_timeout.
- **ADR-0022** — MAX30100 finger-presence gate (firmware).
- **ADR-0023** — SpO2 receipt-boundary session-floor.
- **ADR-0024** — Kiosk-to-cloud sync watermark.

Audits referenced for the troubleshooting paths:

- [2026-05-13 scale-prefiring](../audits/2026-05-13-scale-prefiring-audit.md)
- [2026-05-13 bp-stale-readings](../audits/2026-05-13-bp-stale-readings-audit.md)
- [2026-05-13 scale-stale-readings](../audits/2026-05-13-scale-stale-readings-audit.md)
- [2026-05-14 db-lock-contention](../audits/2026-05-14-db-lock-contention-audit.md)
- [2026-05-14 spo2-stale-readings](../audits/2026-05-14-spo2-stale-readings-audit.md)
- [2026-05-14 session-sync-create-update-gap](../audits/2026-05-14-session-sync-create-update-gap-audit.md)

A full ADR/audit ↔ phase cross-reference table is in
[Appendix B](#appendix-b--adr-and-audit-cross-reference).

---

## Table of contents

1. [Hardware Bill of Materials](#section-1--hardware-bill-of-materials)
2. [Per-Kiosk Deployment Worksheet](#section-2--per-kiosk-deployment-worksheet)
3. [Phase 1 — Pi OS preparation](#section-3--phase-1--pi-os-preparation)
4. [Phase 2 — Network setup](#section-4--phase-2--network-setup)
5. [Phase 3 — System dependencies install](#section-5--phase-3--system-dependencies-install)
6. [Phase 4 — Source checkout and build](#section-6--phase-4--source-checkout-and-build)
7. [Phase 5 — Configuration](#section-7--phase-5--configuration-env-vars-secrets-paths)
8. [Phase 6 — Database initialisation](#section-8--phase-6--database-initialisation)
9. [Phase 7 — MQTT broker setup](#section-9--phase-7--mqtt-broker-setup)
10. [Phase 8 — ESP32 firmware flashing](#section-10--phase-8--esp32-firmware-flashing)
11. [Phase 9 — Sensor pairing and MAC discovery](#section-11--phase-9--sensor-pairing-and-mac-discovery)
12. [Phase 10 — Cloud + portal setup (brief)](#section-12--phase-10--cloud--portal-setup-brief)
13. [Phase 11 — First-run smoke test](#section-13--phase-11--first-run-smoke-test)
14. [Phase 12 — Production cutover](#section-14--phase-12--production-cutover)
15. [Recovery / Troubleshooting / FAQ](#section-15--recovery--troubleshooting--faq)
16. [Maintenance](#section-16--maintenance)
17. [Appendix A — Post-deployment checklist](#appendix-a--post-deployment-checklist)
18. [Appendix B — ADR and audit cross-reference](#appendix-b--adr-and-audit-cross-reference)

---

## Section 1 — Hardware Bill of Materials

Locked in CLAUDE.md "Tech decisions" and [docs/phase-0-plan.md](../phase-0-plan.md#hardware).
Substitutions require a new ADR. Verify per-unit VID:PID where
noted.

| Item               | Model / part                                                         | Notes                                                                                                               |
| ------------------ | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Central hub        | Raspberry Pi 5 (4 GB or 8 GB)                                        | **Raspberry Pi OS trixie (Debian 13)**. Debian 12 is unreliable for our BLE stack — do not use.                     |
| microSD            | 32 GB+ Class 10 (UHS-I or better)                                    | A1/A2-rated cards last longer under DB writes.                                                                      |
| Power              | Official Pi 5 27 W USB-C PSU                                         | Centralised 5 V rail also powers ESP32s, touchscreen, RFID. **NOT the printer.**                                    |
| Console node       | ESP32 DevKit + M5Stack Mini Heart Rate Unit (MAX30100)               | I²C on Wire (GPIO 21/22). Has physical finger shroud.                                                               |
| Console node (2)   | MLX90640BAB thermal imager                                           | I²C on Wire1 (GPIO 25/26), per ADR-0018. 110°×75° FOV, centre-ROI peak, 25-30 cm working distance, emissivity 0.98. |
| Stand node         | ESP32 DevKit + VL53L0X ToF distance sensor                           | Long-range mode, validated 120-185 cm range.                                                                        |
| BP cuff            | Omron HEM-7155T                                                      | BLE, **directly to Pi** (not through ESP32). Clinically validated. 4× AA batteries (alkaline).                      |
| Body scale         | Xiaomi Smart Scale S200 (`xiaomi.scales.ms111`, product ID `0x4C04`) | BLE, directly to Pi. Per-device bindkey required — ADR-0017.                                                        |
| Thermal printer    | Xprinter XP-58IIH (USB, 58 mm, ESC/POS, partial auto-cutter)         | VID:PID typically `0x0416:0x5011` — **verify per unit**. **Powered by its own 9 V adapter**, never from the Pi.     |
| Touchscreen        | 15.6" capacitive 1920×1080                                           | HDMI + USB-touch.                                                                                                   |
| RFID reader        | MFRC522 (13.56 MHz MIFARE Classic / NTAG)                            | SPI on the Pi. Pin map below.                                                                                       |
| Router             | TP-Link Archer A5 v6.0 or equivalent 2.4 GHz capable                 | See [Phase 2](#section-4--phase-2--network-setup) for required radio settings.                                      |
| USB hub (optional) | Powered 4-port USB 3 hub                                             | Needed if the Pi's USB ports get crowded by printer + RFID + ESP32 cables.                                          |

### MFRC522 → Pi 5 pin map

| MFRC522 | Pi 5 BCM     | Pi 5 physical pin | Notes                                                                 |
| ------- | ------------ | ----------------- | --------------------------------------------------------------------- |
| SDA     | GPIO 8 (CE0) | pin 24            | SPI0 chip-select                                                      |
| SCK     | GPIO 11      | pin 23            | SPI0 clock                                                            |
| MOSI    | GPIO 10      | pin 19            | SPI0 MOSI                                                             |
| MISO    | GPIO 9       | pin 21            | SPI0 MISO                                                             |
| IRQ     | GPIO 24      | pin 18            | Edge-triggered card-present (unused by current driver)                |
| RST     | GPIO 25      | pin 22            | Reset                                                                 |
| 3V3     | 3.3 V        | pin 1 (or pin 17) | **Do NOT use 5 V.** The MFRC522 is a 3.3 V part; 5 V will destroy it. |
| GND     | GND          | pin 25            | Any GND pin works.                                                    |

### Absolute hardware safety rules

These rules are mirrored from CLAUDE.md and are not optional:

- **Never power the thermal printer from the Pi's USB or 5 V GPIO.**
  Printer brown-out kills the Pi. Use the printer's own 9 V adapter.
- **Never run more than one BLE operation concurrently.** The kiosk
  serialises BP cuff and Xiaomi scale automatically; if you write
  custom test scripts, do not bypass this.
- **Never write to the Omron HEM-7155T EEPROM.** Read-only access
  only. Do not run `omblepy` with `-n` or `-t` flags.

---

## Section 2 — Per-Kiosk Deployment Worksheet

Print this section. Fill it in BEFORE starting Phase 1. Keep it
beside you for every subsequent phase. Seal it in a tamper-evident
envelope after Phase 12 — it contains the SQLCipher key, which has
no recovery path.

```
GINHAWA Kiosk Deployment Worksheet
==================================

Date:                  ______________________
Deployment technician: ______________________
Target barangay:       ______________________

Pi hostname (suggested: kiosk-<barangay>):  ______________________

Network:
  WiFi SSID:           ______________________
  WiFi password:       ______________________
  Static IP (optional): ______________________
  Cloud backend URL:   ______________________

Identifiers (generate with the commands below):
  Kiosk device_id (UUID v4):
    _________________________________________________________________
    # uuidgen

  Kiosk API key (issued by the cloud admin, see Phase 10):
    _________________________________________________________________

  SQLCipher encryption key (64 hex chars):
    _________________________________________________________________
    # Generated automatically by provision_db in Phase 6.
    # Printed ONCE; record it here verbatim before the SSH session ends.

  Mosquitto kiosk password (ginhawa_kiosk user):
    _________________________________________________________________
    # python3 -c "import secrets; print(secrets.token_urlsafe(32))"

  Mosquitto esp32_a password:
    _________________________________________________________________
    # python3 -c "import secrets; print(secrets.token_urlsafe(32))"

  Mosquitto esp32_b password:
    _________________________________________________________________
    # python3 -c "import secrets; print(secrets.token_urlsafe(32))"

Sensor MAC addresses (discover during Phase 9 pairing):
  Xiaomi S200 scale MAC:   __:__:__:__:__:__
  Xiaomi scale bindkey (32 hex chars):
    _________________________________________________________________
  Omron HEM-7155T BP MAC:  __:__:__:__:__:__

ESP32 chip MAC addresses (read off `pio device list` post-flash):
  ESP32-A (vitals):  __:__:__:__:__:__
  ESP32-B (anthro):  __:__:__:__:__:__

Sign-off:
  All smoke tests passed:    [ ] yes
  Technician signature:      ______________________
  Date sealed:               ______________________
```

### Generation commands (run these on your laptop, not the Pi)

```bash
# UUID v4 for the kiosk device_id (also use for hostname suffix)
uuidgen

# 32-byte URL-safe random for each MQTT user password
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# (The 64-hex SQLCipher key is generated by provision_db in Phase 6;
# do NOT generate it yourself.)
```

---

## Section 3 — Phase 1 — Pi OS preparation

### 3.1. Flash the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- OS: **Raspberry Pi OS (64-bit) — Bookworm/trixie**. The kiosk
  requires the trixie BLE stack; do not pick Debian 12.
- Configure SSH on, set username `ginhawa`, set the WiFi SSID and
  password from the worksheet, set locale `en_PH.UTF-8` and
  timezone `Asia/Manila`. Set hostname to `kiosk-<barangay>`.

### 3.2. First boot and SSH in

Insert the SD card, connect HDMI + power + ethernet (or wait for
the configured WiFi), wait ~60 seconds, then SSH from your laptop:

```bash
ssh ginhawa@<pi-ip-address>
```

### 3.3. System updates

```bash
sudo apt update
sudo apt upgrade -y
sudo reboot
```

Reconnect after the reboot.

### Verification

```bash
hostnamectl        # hostname, Operating System: Debian trixie
timedatectl        # Time zone: Asia/Manila
uname -r           # kernel 6.x or higher
```

If hostname or timezone are wrong, fix them with
`sudo hostnamectl set-hostname kiosk-<barangay>` and
`sudo timedatectl set-timezone Asia/Manila`.

---

## Section 4 — Phase 2 — Network setup

### 4.1. WiFi

If you set it during the imager step, it's already connected.
Otherwise:

```bash
sudo nmcli device wifi connect "<WIFI_SSID>" password "<WIFI_PASSWORD>"
nmcli connection show               # confirm "<WIFI_SSID>" is active
```

### 4.2. Static IP (recommended)

A static IP avoids the kiosk losing track of itself if the router
reboots. Edit the active connection:

```bash
sudo nmcli connection modify "<WIFI_SSID>" \
    ipv4.method manual \
    ipv4.addresses "<static-ip>/24" \
    ipv4.gateway "<router-ip>" \
    ipv4.dns "<router-ip> 1.1.1.1"
sudo nmcli connection up "<WIFI_SSID>"
ip addr show wlan0                  # confirm the static IP
```

### 4.3. Router settings

The TP-Link Archer A5 v6.0 (and most 2.4 GHz consumer routers)
ship with defaults that destabilise our BLE-heavy environment.
Log into the router's admin UI and set:

- **SSID**: from worksheet.
- **2.4 GHz channel**: 1, 6, or 11 (not auto — auto roams).
- **Security**: **WPA2-PSK only**. NOT "WPA2/WPA3 mixed", NOT
  "WPA3" alone. Mixed mode caused bench disconnects during May
  2026 testing.
- **Channel width**: **20 MHz**. 40 MHz (the default) caused
  bench disconnects.
- **Disable 5 GHz** if the kiosk and the router are in the same
  small room — 5 GHz is unused, and disabling it reduces RF
  noise.

### Verification

```bash
ping -c 3 8.8.8.8                    # internet reachable
ping -c 3 <cloud-backend-host>       # cloud reachable
ip addr show wlan0                   # static IP active
```

If `ping` to the cloud fails, the cloud URL in the worksheet may
be wrong, or DNS is misconfigured. Test with `nslookup
<cloud-host>` to disambiguate.

---

## Section 5 — Phase 3 — System dependencies install

### 5.1. apt packages

```bash
sudo apt update
sudo apt install -y \
    git \
    bluez bluez-tools \
    mosquitto mosquitto-clients \
    qt6-base-dev qt6-wayland \
    libqt6gui6 libqt6widgets6 \
    qml6-module-qtquick-virtualkeyboard \
    qt6-virtualkeyboard-plugin \
    python3-pip pipx \
    sqlcipher \
    libgpiod2 \
    cups
```

**The qt6-virtualkeyboard-plugin and qml6-module-qtquick-virtualkeyboard
packages are not optional.** Without them the kiosk crashes on the
REGISTER screen because the citizen has no way to type their name.

### 5.2. Enable SPI for the MFRC522 RFID reader

```bash
sudo raspi-config nonint do_spi 0
```

### 5.3. Unblock Bluetooth (one-time, persists)

```bash
sudo rfkill unblock bluetooth
```

### 5.4. Install `uv` (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc       # or log out and back in
uv --version           # should print 0.10.x or higher
```

### Verification

```bash
git --version          # 2.x
mosquitto -h | head -1 # version 2.x
python3 --version      # 3.12.x
sqlcipher --version    # 4.x
bluetoothctl --version # 5.x
ls /usr/lib/aarch64-linux-gnu/qt6/plugins/platforminputcontexts/ | grep virtual
# → libqtvirtualkeyboardplugin.so
```

If `libqtvirtualkeyboardplugin.so` is missing, re-run the apt
install — the keyboard plugin is the most common skipped step.

---

## Section 6 — Phase 4 — Source checkout and build

The kiosk source lives under `/opt/ginhawa/src` so it's owned by
the deployment, not by any one user's home directory.

```bash
sudo install -d -o "$USER" -g "$USER" /opt/ginhawa
git clone <repo-url> /opt/ginhawa/src
cd /opt/ginhawa/src/kiosk
uv sync
```

`uv sync` downloads PyQt6, sqlcipher3, bleak, paho-mqtt, and the
rest of the kiosk's pinned dependencies into `.venv`. First run
takes 5-10 minutes.

### Verification

```bash
cd /opt/ginhawa/src/kiosk
uv run python -c "import ginhawa_kiosk; print('ok')"
# → ok
```

If the import fails, check `uv sync` output for build failures
(common: PyQt6 wheel download timeout — re-run).

---

## Section 7 — Phase 5 — Configuration (env vars, secrets, paths)

The kiosk reads its runtime configuration from `/etc/ginhawa/kiosk.env`,
which is loaded by the systemd unit via `EnvironmentFile=`.

### 7.1. Create the config directory and file

```bash
sudo install -d -m 0700 /etc/ginhawa
sudo tee /etc/ginhawa/kiosk.env >/dev/null <<EOF
# ===== Critical paths =====
# Database lives at /var/lib/ginhawa/kiosk.db. THIS MUST MATCH the
# path you pass to alembic in Phase 6, or alembic will create a
# second empty database in ~/.ginhawa/ and the kiosk will read
# from a third location. See Phase 6 troubleshooting.
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db

# ===== Secrets (from the worksheet) =====
KIOSK_DB_KEY=<paste 64-hex SQLCipher key from worksheet — Phase 6 generates it>
KIOSK_API_KEY=<API key issued by cloud admin>
KIOSK_DEVICE_ID=<UUID v4 from worksheet>

# ===== Cloud backend =====
CLOUD_API_URL=<https or http URL to cloud backend>

# ===== MQTT broker (local) =====
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
MQTT_USERNAME=ginhawa_kiosk
MQTT_PASSWORD=<MQTT kiosk password from worksheet>

# ===== Xiaomi scale MAC filter (ADR-0023 / 2026-05-13 scale-stale-readings audit) =====
# Populated after Phase 9. Leave empty for now; the kiosk will run
# in legacy "accept all adverts" mode and warn once on first advert.
KIOSK_SCALE_MAC=

# ===== Printer =====
KIOSK_PRINTER_VENDOR_ID=0x0416
KIOSK_PRINTER_PRODUCT_ID=0x5011

# ===== Operational =====
LOG_LEVEL=INFO
MOCK_HARDWARE=false
EOF

sudo chmod 0600 /etc/ginhawa/kiosk.env
sudo chown root:root /etc/ginhawa/kiosk.env
```

### 7.2. Why these permissions

`KIOSK_DB_KEY` decrypts the SQLCipher database. If someone with
shell access (but not root) can read the env file, they can decrypt
the citizen data on disk. The file is `0600 root:root`; the systemd
unit reads it as root before dropping to the `ginhawa` user
(per `EnvironmentFile=` semantics — the file is parsed by systemd
itself, not by the service process).

### 7.3. About `KIOSK_DB_PATH`

This is the lesson from the 2026-05-14 bench debugging session.
**If you run `alembic upgrade head` without `KIOSK_DB_PATH` set,
alembic falls back to `~/.ginhawa/kiosk.db` and creates a second
empty database** — separate from the one the kiosk actually reads.
The migration appears to succeed, but the kiosk still complains
about missing columns. Always set `KIOSK_DB_PATH` explicitly when
running alembic, even though it duplicates what's in `kiosk.env`.
See Phase 6 troubleshooting.

### Verification

```bash
sudo cat /etc/ginhawa/kiosk.env | grep -v '^#' | grep '='
# Confirm every required variable is set to a non-placeholder value.
# Watch for accidentally pasted angle brackets like "<paste...>" —
# those would crash boot.
```

Cross-references: ADR-0021 (the WAL pragma reads KIOSK_DB_KEY for
the engine), ADR-0023 (KIOSK_SCALE_MAC, populated in Phase 9).

---

## Section 8 — Phase 6 — Database initialisation

### 8.1. Create the database directory

```bash
sudo install -d -o "$USER" -g "$USER" -m 0700 /var/lib/ginhawa
ls -la /var/lib/ginhawa
# Expected: drwx------ ginhawa ginhawa
```

### 8.2. Generate the SQLCipher key + create the DB

```bash
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
  uv run python -m ginhawa_kiosk.scripts.provision_db
```

The script prints a banner with the SQLCipher key. **Copy the key
to the worksheet RIGHT NOW.** It is shown ONCE. There is no
recovery path; if you lose it, the citizen data on this kiosk is
unrecoverable.

Then go back to `/etc/ginhawa/kiosk.env` and substitute the key
into `KIOSK_DB_KEY=...`:

```bash
sudo nano /etc/ginhawa/kiosk.env     # replace the placeholder
```

### 8.3. Stamp the database for alembic

The `provision_db` script created the schema directly via SQLAlchemy
metadata, not via alembic migrations. Tell alembic the initial
schema is already applied, then apply the watermark migration
(ADR-0024).

```bash
cd /opt/ginhawa/src/kiosk

# Stamp the initial schema as applied — does no DDL, just records
# the revision in alembic_version.
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-the-worksheet> \
  uv run alembic stamp 9a4b1c5d2e7f

# Apply every migration since then. Currently just c8a7e93d4f12
# (the last_synced_at watermark column).
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-the-worksheet> \
  uv run alembic upgrade head
```

### Verification

```bash
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-the-worksheet> \
  uv run alembic current
# → c8a7e93d4f12 (head)
```

Then verify the schema directly with sqlcipher:

```bash
sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
PRAGMA key='<the-key-from-the-worksheet>';
.tables
SELECT name FROM pragma_table_info('sessions') WHERE name='last_synced_at';
PRAGMA journal_mode;
PRAGMA busy_timeout;
.quit
SQL
```

Expected output:

- `.tables` lists at minimum: `alembic_version audit_log citizens
device_config measurements sessions`.
- The `last_synced_at` query returns one row (ADR-0024 applied).
- `PRAGMA journal_mode` returns `wal` (ADR-0021 applied).
- `PRAGMA busy_timeout` returns `5000` (ADR-0021 applied).

### Common errors and fixes

**Error: `KIOSK_DB_KEY env var must be set`**

You didn't set `KIOSK_DB_KEY` in the shell before running alembic.
Re-run with the explicit env vars on the command line:

```bash
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key> \
  uv run alembic upgrade head
```

**Error: `table citizens already exists`**

You ran `alembic upgrade head` without first running
`alembic stamp 9a4b1c5d2e7f`. The initial-schema migration tried
to CREATE TABLE on tables that `provision_db` already created.
Run the stamp command (Section 8.3 step 1), then re-run upgrade.

**Error: `no such column: citizens.last_synced_at` (in kiosk logs)**

Alembic upgraded a different database file than the one the kiosk
service reads. This is the 2026-05-14 lesson:

1. Check which file alembic touched:
   ```bash
   ls -la ~/.ginhawa/kiosk.db
   ```
   If this exists, alembic was run without `KIOSK_DB_PATH` set.
   Delete it: `rm ~/.ginhawa/kiosk.db`.
2. Re-run with the explicit path:
   ```bash
   KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
   KIOSK_DB_KEY=<the-key> \
     uv run alembic upgrade head
   ```
3. Re-verify via `alembic current` with the path explicit.

Cross-references:
[2026-05-14 session-sync-create-update-gap audit](../audits/2026-05-14-session-sync-create-update-gap-audit.md),
[ADR-0021](../decisions/0021-sqlite-wal-and-busy-timeout.md),
[ADR-0024](../decisions/0024-sync-watermark.md).

---

## Section 9 — Phase 7 — MQTT broker setup

The kiosk and both ESP32s talk to a single Mosquitto broker
running on the Pi. The broker binds to `127.0.0.1` only — it is
**not exposed to the LAN**.

### 9.1. Localhost-only bind

```bash
sudo tee /etc/mosquitto/conf.d/ginhawa-localhost.conf >/dev/null <<'EOF'
listener 1883 127.0.0.1
EOF
```

### 9.2. Password file for the three roles

```bash
# Create the passwd file and add the kiosk user. The -c flag
# (re)creates the file; subsequent users use the same command
# without -c.
sudo mosquitto_passwd -c /etc/mosquitto/passwd ginhawa_kiosk
# Paste the MQTT kiosk password from the worksheet when prompted.

sudo mosquitto_passwd /etc/mosquitto/passwd esp32_a
sudo mosquitto_passwd /etc/mosquitto/passwd esp32_b

sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
sudo chmod 0640 /etc/mosquitto/passwd
```

### 9.3. ACL — per-user topic restrictions

The kiosk subscribes to `ginhawa/kiosk/<device_id>/sensors/+`; each
ESP32 publishes only its own topics. Tight ACLs prevent a rogue
device from spoofing readings.

```bash
sudo tee /etc/mosquitto/acl >/dev/null <<EOF
# Substitute <device-id> with KIOSK_DEVICE_ID from the worksheet.
user ginhawa_kiosk
topic readwrite ginhawa/kiosk/<device-id>/sensors/#

user esp32_a
topic write ginhawa/kiosk/<device-id>/sensors/spo2
topic write ginhawa/kiosk/<device-id>/sensors/heart_rate
topic write ginhawa/kiosk/<device-id>/sensors/temperature

user esp32_b
topic write ginhawa/kiosk/<device-id>/sensors/height
EOF
sudo chown mosquitto:mosquitto /etc/mosquitto/acl
sudo chmod 0640 /etc/mosquitto/acl
```

### 9.4. Wire passwd + acl into the broker config

```bash
sudo tee -a /etc/mosquitto/conf.d/ginhawa-localhost.conf >/dev/null <<'EOF'
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
EOF

sudo systemctl enable --now mosquitto
sudo systemctl restart mosquitto
```

### Verification

```bash
# 1. Broker is up and bound to localhost only.
sudo ss -tlnp | grep 1883
# → LISTEN 127.0.0.1:1883 (NOT 0.0.0.0:1883)

# 2. Authenticated subscribe + publish round-trip.
mosquitto_sub -h 127.0.0.1 -u ginhawa_kiosk -P "<kiosk-pass>" \
    -t 'ginhawa/kiosk/+/sensors/+' &
SUB_PID=$!
sleep 1

mosquitto_pub -h 127.0.0.1 -u esp32_a -P "<esp32-a-pass>" \
    -t "ginhawa/kiosk/<device-id>/sensors/spo2" \
    -q 1 \
    -m '{"value":97.0,"unit":"%","captured_at":"2026-05-14T00:00:00+00:00"}'

# Confirm the mosquitto_sub printed the message, then clean up:
kill $SUB_PID
```

If the publish fails with `connection refused` the broker isn't
running (`sudo systemctl status mosquitto`). If it fails with
`not authorised`, double-check the ACL file's `<device-id>`
substitution.

The repo also ships `/opt/ginhawa/src/scripts/bench_mqtt_publish.sh`
— a wrapper for simulating ESP32 publishes without real hardware.
Use it to drive a kiosk smoke test before flashing the ESP32s.

Cross-reference: [2026-05-13 scale-prefiring audit](../audits/2026-05-13-scale-prefiring-audit.md)
for why the per-topic ACL matters.

---

## Section 10 — Phase 8 — ESP32 firmware flashing

Two ESP32 boards: **ESP32-A** (vitals: SpO2 + temperature) and
**ESP32-B** (anthro: height). Flash them one at a time so you can
identify each board's chip MAC for the worksheet.

### 10.1. Install PlatformIO on the Pi

```bash
pipx install platformio
pio --version       # 6.x
```

(You can also flash from a developer laptop and skip this step.
The instructions below assume flashing happens on the Pi; adapt
the working directory paths if you flash from a laptop.)

### 10.2. Per-ESP32 — Configure secrets

For each ESP32, create `secrets.h` from the template
(`secrets.h.example` is the gitignored placeholder):

```bash
cd /opt/ginhawa/src/firmware/esp32-a-vitals/include
cp secrets.h.example secrets.h
nano secrets.h
```

Set:

- `WIFI_SSID` and `WIFI_PASSWORD` from the worksheet.
- `MQTT_BROKER_HOST` = the Pi's IP on the WiFi network (not
  `localhost` — the ESP32 talks to the Pi over WiFi).
- `MQTT_USERNAME` = `esp32_a` (ESP32-A) or `esp32_b` (ESP32-B).
- `MQTT_PASSWORD` from the worksheet.
- `KIOSK_DEVICE_ID` = the UUID from the worksheet.

Repeat for `/opt/ginhawa/src/firmware/esp32-b-anthro/include/secrets.h`.

### 10.3. Connect, build, flash, monitor — ESP32-A

```bash
# Plug ESP32-A into a USB port. Confirm it's enumerated:
pio device list
# Note the /dev/ttyUSB* port and the chip MAC printed in the
# device description — RECORD THE MAC ON THE WORKSHEET.

cd /opt/ginhawa/src/firmware/esp32-a-vitals
pio run -t upload

# Watch the serial log:
pio device monitor -b 115200
```

Expected log output (the kiosk-side audits validate these):

- Boot banner with WiFi connect, MQTT connect.
- `MAX30100 PART_ID=0x11` (chip detected, per ADR-0022 diagnostic
  trail).
- `MLX90640 init ok` (thermal imager detected).
- During citizen contact: `[max30100] finger warmed up, gate open`
  (ADR-0022 finger-presence gate engaged).
- On finger removal: `[max30100] finger lost, gate reset`.
- ~Every 30 s with a finger pressed: `[max30100] published spo2=98.5`
  or similar.

Press Ctrl+C to exit the monitor (the firmware keeps running).

### 10.4. Connect, build, flash, monitor — ESP32-B

```bash
# Unplug ESP32-A, plug ESP32-B. RECORD THE MAC ON THE WORKSHEET.
cd /opt/ginhawa/src/firmware/esp32-b-anthro
pio run -t upload
pio device monitor -b 115200
```

Expected log output:

- WiFi + MQTT connect.
- `VL53L0X init ok` (distance sensor detected).
- During citizen-under-pillar: `STAB: window start` followed by
  ~5 s of `STAB: building` then `STAB: FIRE` (ADR-0019
  stabilisation gate fired) and an MQTT publish.
- Citizen steps out: `STAB: reset` or window expiry.

### 10.5. Diagnostic build (only if SpO2 is silent or noisy)

If on first smoke test the kiosk shows no SpO2 readings, reflash
ESP32-A with the diagnostic build to dump raw MAX30100 register
state every 500 ms:

```bash
cd /opt/ginhawa/src/firmware/esp32-a-vitals
pio run -e esp32dev_diag -t upload
pio device monitor -b 115200
```

Look for `DIAG MAX30100: part=0x11 ... wr_delta>0 ir=<value>`.
A `wr_delta=0` sustained for several seconds means the chip is
not producing samples (check wiring, swap the M5Stack unit). An
`ir` value of 65535 sustained means the IR LED is saturating
(check the IR LED current setting in
`firmware/esp32-a-vitals/src/sensor_max30100.cpp:67`).

**Switch back to the production build before going live**:

```bash
pio run -e esp32dev -t upload      # production, no diagnostic spam
```

Cross-references: [ADR-0019](../decisions/0019-height-stabilization-gate.md),
[ADR-0022](../decisions/0022-spo2-finger-presence-gate.md),
[2026-05-14 spo2-stale-readings audit](../audits/2026-05-14-spo2-stale-readings-audit.md).

### Verification

In a second SSH session, subscribe to the broker and confirm the
ESP32s publish:

```bash
mosquitto_sub -h 127.0.0.1 -u ginhawa_kiosk -P "<kiosk-pass>" \
    -t 'ginhawa/kiosk/+/sensors/+' -v
```

Press a finger on ESP32-A; within 30 s a `spo2` payload should
appear. Stand under the ESP32-B pillar; within 5 s a `height`
payload should appear.

---

## Section 11 — Phase 9 — Sensor pairing and MAC discovery

### 11.1. Omron HEM-7155T BP cuff

Insert 4 alkaline AA batteries. **Low batteries silently prevent
pairing** — if BLE scan can't see the cuff, swap batteries first.

```bash
sudo bluetoothctl
[bluetooth]# scan on
# Press the cuff's Bluetooth button — the BT indicator flashes
# while it's in pairing mode (~30 s window).
# Watch the scan output for a device starting with "BLESmart_" or
# the cuff's MAC.
[bluetooth]# pair <cuff-mac>
[bluetooth]# trust <cuff-mac>
[bluetooth]# scan off
[bluetooth]# quit
```

**RECORD THE CUFF MAC ON THE WORKSHEET.** Then store it in the
kiosk's `device_config` table:

```bash
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-the-worksheet> \
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
        value='<cuff-mac>',
        updated_at=datetime.now(timezone.utc).isoformat(),
    ))
    s.commit()
"
```

#### Workflow note for the BHW

The HEM-7155T is store-and-forward. The citizen's flow during
MEASURING_VITALS:

1. Citizen takes their BP using the cuff (cuff alone, no Pi
   contact yet).
2. Cuff displays the reading and stores it internally.
3. Citizen presses the cuff's Bluetooth button — the cuff enters
   pairing mode (~30 s window).
4. Kiosk connects, reads the stored reading, validates freshness
   (ADR-0020 session-floor: rejects if `taken_at` is before
   MEASURING_VITALS entry), publishes, disconnects.

If the citizen presses the BT button before taking the BP, the
cuff delivers whatever it stored last (possibly from a different
citizen). ADR-0020's session_floor catches this — see the
[BP stale-readings audit](../audits/2026-05-13-bp-stale-readings-audit.md).

### 11.2. Xiaomi Smart Scale S200 — bindkey extraction

The S200 mints its bindkey on first pairing to the Mi Home app,
not on first BLE connection to the Pi. ADR-0017 is the canonical
reference. Summary:

1. Install Mi Home on the designated commissioning Android phone.
2. Step on the scale to wake it; pair to Mi Home.
3. Locate the bindkey in Mi Home's `device_info` JSON blob.
   Path varies by Mi Home version — current method documented in
   [ADR-0017](../decisions/0017-xiaomi-ble-library-for-smart-scale-s200.md).
   The bindkey is 16 bytes / 32 ASCII hex characters.
4. **RECORD THE BINDKEY ON THE WORKSHEET.**
5. Pair the scale to the Pi:
   ```bash
   sudo bluetoothctl
   [bluetooth]# scan on
   [bluetooth]# pair <scale-mac>
   [bluetooth]# trust <scale-mac>
   [bluetooth]# scan off
   [bluetooth]# quit
   ```
   **RECORD THE SCALE MAC ON THE WORKSHEET.**
6. Store the bindkey:

```bash
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key-from-the-worksheet> \
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
        value='<32-hex-bindkey>',
        updated_at=datetime.now(timezone.utc).isoformat(),
    ))
    s.commit()
"
```

### 11.3. Set KIOSK_SCALE_MAC

The kiosk filters BLE adverts by the scale's MAC to prevent
library state pollution
([2026-05-13 scale-stale-readings audit](../audits/2026-05-13-scale-stale-readings-audit.md)).
Edit the env file:

```bash
sudo nano /etc/ginhawa/kiosk.env
# Set:
#   KIOSK_SCALE_MAC=<scale-mac-from-worksheet>
```

If you don't know the MAC yet, leave it empty and use the
diagnostic procedure in the audit:

1. Start the kiosk with `MOCK_HARDWARE=false` and `KIOSK_SCALE_MAC=`.
2. Watch `journalctl -u ginhawa-kiosk -f | grep advert_diagnostic`.
3. Step on the scale. The MAC whose `mass_kg` value transitions
   first is the scale.
4. Stop, set `KIOSK_SCALE_MAC` in the env file, restart.

### 11.4. RFID reader

The MFRC522 is on SPI (no pairing). To verify it responds, plug a
known-working MIFARE card or NTAG sticker into the reader and run
the kiosk's RFID tester (covered by the smoke test in Phase 11).

### 11.5. Thermal printer

Connect via USB. Load 58 mm thermal paper (loading instructions
on the printer body — paper unrolls from underneath, not over the
top). Print a CUPS test page to confirm USB connectivity:

```bash
sudo cupsenable Xprinter-XP-58IIH 2>/dev/null || true
echo "GINHAWA test print" | lp -d Xprinter-XP-58IIH 2>/dev/null
```

If CUPS doesn't know about the printer, the kiosk talks to it
directly via `python-escpos` using the VID:PID from the env file
— CUPS configuration is optional.

### Verification

Start the kiosk service (Phase 13 will run the full smoke test;
this is a sensor-only sanity check):

```bash
sudo systemctl restart ginhawa-kiosk
sudo journalctl -u ginhawa-kiosk -f
```

Run the GUI flow from the touchscreen:

- Tap RFID — citizen-lookup screen appears within ~1 s.
- Step on the scale — within ~15 s, a weight reading appears.
- Press the cuff's BT button (after taking a BP) — within ~30 s,
  a BP triple appears.
- Place a finger on the SpO2 sensor for 5+ seconds — within 30 s,
  an SpO2 reading appears.
- Aim the thermal imager at a forehead — the live temperature
  preview updates; tap **Capture Temperature** to commit.
- Stand under the height pillar for 5 s — a height reading appears.

If any sensor doesn't respond, see [Section 15](#section-15--recovery--troubleshooting--faq).

Cross-references: [ADR-0017](../decisions/0017-xiaomi-ble-library-for-smart-scale-s200.md),
[ADR-0019](../decisions/0019-height-stabilization-gate.md),
[ADR-0020](../decisions/0020-bp-session-floor-freshness.md),
[ADR-0022](../decisions/0022-spo2-finger-presence-gate.md),
[ADR-0023](../decisions/0023-spo2-session-floor.md),
[2026-05-13 bp-stale-readings audit](../audits/2026-05-13-bp-stale-readings-audit.md),
[2026-05-13 scale-stale-readings audit](../audits/2026-05-13-scale-stale-readings-audit.md).

---

## Section 12 — Phase 10 — Cloud + portal setup (brief)

The cloud backend and BHW portal have their own deployment
runbooks (TODO: docs/runbooks/cloud-deployment.md). For the kiosk
deployment, you only need to know:

- **Self-hosted on the same Pi:** run the cloud's Docker Compose
  stack from `/opt/ginhawa/src/cloud/`. See
  [cloud/README.md](../../cloud/README.md) for the canonical
  steps. Confirm Postgres, the cloud API, and the portal are all
  up before proceeding.
- **Separate cloud server:** the cloud admin will give you the
  `CLOUD_API_URL` and `KIOSK_API_KEY` for this kiosk. Put them in
  `/etc/ginhawa/kiosk.env`.

### Verification

```bash
# Health check the cloud is reachable.
curl -fsS "$(grep ^CLOUD_API_URL /etc/ginhawa/kiosk.env | cut -d= -f2)/api/v1/health"
# → 200 OK
```

If the cloud is on the same Pi at `http://127.0.0.1:8000`, that's
correct — the kiosk talks to it via loopback.

---

## Section 13 — Phase 11 — First-run smoke test

This is the end-to-end test. **Run it after every commissioning,
re-deployment, or major firmware update.**

### 13.1. Install the systemd unit (if not already)

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
Environment="QT_QPA_PLATFORM=xcb"
Environment="QT_IM_MODULE=qtvirtualkeyboard"  # pragma: allowlist secret
Environment="QT_PLUGIN_PATH=/usr/lib/aarch64-linux-gnu/qt6/plugins"  # pragma: allowlist secret
WorkingDirectory=/opt/ginhawa/src/kiosk
ExecStart=/home/ginhawa/.local/bin/uv run python -m ginhawa_kiosk
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ginhawa-kiosk
```

### 13.2. Watch the boot log

```bash
sudo journalctl -u ginhawa-kiosk -f --since "1 min ago"
```

Look for:

- No `qt.core.plugin.factoryloader` errors loading
  `libqtvirtualkeyboardplugin.so` — if there are, see
  [Section 15](#section-15--recovery--troubleshooting--faq).
- `kiosk.boot.sync_daemon_started` with the cloud URL.
- No `no such column` errors — if you see them, Phase 6 ran
  alembic against the wrong DB; re-do that phase.
- `mqtt` events showing the broker connected.
- Within 30-60 s: a `sync.cycle_complete` event (the daemon's
  first heartbeat).

### 13.3. Drive a full session through the GUI

1. **Tap RFID** with a card that doesn't yet correspond to a
   citizen.
2. The kiosk shows the REGISTER screen. Confirm the virtual
   keyboard appears when the Name field is focused. Register a
   test citizen named "Smoke Test".
3. Tap "Full check" on the path-choice screen.
4. **Vitals capture:**
   - Press a finger on the SpO2 sensor, wait ~30 s — value
     appears.
   - Take a BP on the cuff, press the BT button — triple
     appears.
   - Aim the thermal sensor at a forehead, tap **Capture
     Temperature** — value commits.
5. **Anthro capture:**
   - Step on the scale, wait — weight appears.
   - Stand under the height pillar for 5 s — height appears.
6. Reach the REPORT screen. Confirm:
   - All captured values match what the sensors actually saw.
   - No SpO2 reading appears when no finger was placed
     (ADR-0022).
   - No weight reading appears when no one stepped on
     (ADR-0023).
7. Tap **Print**. Confirm the receipt prints correctly (paper
   loaded, no jam).
8. Tap **Finish**. The kiosk returns to IDLE.

### 13.4. Verify sync

Within 30-60 s of finishing the session, check that the cloud
received it:

```bash
# Get the session ID from the kiosk DB.
sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
PRAGMA key='<the-key>';
SELECT id, status, ended_at, last_synced_at FROM sessions
  ORDER BY started_at DESC LIMIT 1;
.quit
SQL
# Expected: status='completed', ended_at populated,
#          last_synced_at populated.

# If cloud is self-hosted on this Pi:
sudo docker exec ginhawa-postgres psql -U ginhawa -d ginhawa -c \
  "SELECT id, status, ended_at, updated_at FROM sessions \
   WHERE id='<session-id>';"
# Expected: status='completed', ended_at populated, updated_at
# matches the kiosk's value.
```

If the cloud's `status` stays as `in_progress` even after several
sync cycles, see [Section 15 troubleshooting](#section-15--recovery--troubleshooting--faq)
item 6.

### 13.5. Open the BHW portal

Log into the BHW portal (separate machine or `http://<pi-ip>:5173`
if hosted on the same Pi). The test session should appear as
**Completed**. The status pill drives directly off the cloud
DB's `status` column — if it shows "In progress" while the
kiosk DB shows `completed`, ADR-0024 didn't apply
([2026-05-14 session-sync audit](../audits/2026-05-14-session-sync-create-update-gap-audit.md)).

### 13.6. Cleanup

Delete the test citizen and its session from the kiosk DB (the
BHW portal's admin UI can also do this once the sync has
propagated):

```bash
sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
PRAGMA key='<the-key>';
DELETE FROM measurements WHERE session_id='<session-id>';
DELETE FROM sessions WHERE id='<session-id>';
DELETE FROM citizens WHERE rfid_uid='<test-rfid-uid>';
.quit
SQL
```

The deletions sync to the cloud on the next cycle if the cloud
supports DELETE (currently it does not — manual cleanup on the
cloud side is needed too).

Cross-references: [ADR-0020](../decisions/0020-bp-session-floor-freshness.md),
[ADR-0023](../decisions/0023-spo2-session-floor.md),
[ADR-0024](../decisions/0024-sync-watermark.md).

---

## Section 14 — Phase 12 — Production cutover

### 14.1. Enable the kiosk on boot

```bash
sudo systemctl enable ginhawa-kiosk
```

(If you already ran `enable --now` in Phase 11, this is a no-op.)

### 14.2. Kiosk-mode display

The default Pi OS desktop shows a taskbar and lets the user
escape to the launcher. For production, run the kiosk in
fullscreen-no-decorations.

The kiosk's systemd unit already sets `QT_QPA_PLATFORM=xcb` to
force X11. Ensure X11 is the session at boot:

```bash
# raspi-config → Advanced Options → Wayland → choose "X11"
sudo raspi-config nonint do_wayland W1
sudo reboot
```

(`W1` is the X11 option in trixie's raspi-config; if that nonint
constant changes, run interactive `sudo raspi-config` and pick
X11 manually.)

### 14.3. Disable screen blanking / screensaver

```bash
# For LightDM-managed X sessions:
sudo tee /etc/lightdm/lightdm.conf.d/99-no-blank.conf >/dev/null <<'EOF'
[Seat:*]
xserver-command=X -s 0 -dpms
EOF
```

### 14.4. Reboot test

```bash
sudo reboot
```

After the Pi comes back:

- The GUI should be fullscreen, no taskbar visible.
- The IDLE screen should display the GINHAWA splash.
- `sudo systemctl status ginhawa-kiosk` should show "active
  (running)" with no recent restart.

### 14.5. Lock down SSH (optional but recommended)

```bash
# Disable password auth — copy your laptop's public key first!
ssh-copy-id ginhawa@<pi-ip>           # from your laptop

# On the Pi:
sudo sed -i 's/^#*PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

Test from a fresh terminal that key auth still works before
closing your current SSH session.

---

## Section 15 — Recovery / Troubleshooting / FAQ

### 1. Kiosk won't start after boot

**Symptom:** `sudo systemctl status ginhawa-kiosk` shows "failed"
or stuck in restart loop.

**Diagnose:**

```bash
sudo journalctl -u ginhawa-kiosk --since "5 min ago" --no-pager | tail -100
```

**Common causes:**

| Error in log                                               | Fix                                                                 |
| ---------------------------------------------------------- | ------------------------------------------------------------------- |
| `Could not load the Qt platform plugin "xcb"`              | Install `libxcb-cursor0`. Or you booted Wayland — see Phase 12.2.   |
| `libqtvirtualkeyboardplugin.so: cannot open shared object` | `sudo apt install qt6-virtualkeyboard-plugin`.                      |
| `KIOSK_DB_KEY env var must be set`                         | `/etc/ginhawa/kiosk.env` missing or has a placeholder. Fix Phase 5. |
| `file is not a database`                                   | Wrong KIOSK_DB_KEY for this DB file. Re-check the worksheet.        |
| `no such column: ...last_synced_at`                        | Migration not applied. Re-do Phase 6 with `KIOSK_DB_PATH` set.      |

### 2. MQTT not connecting

**Symptom:** Kiosk logs `mqtt.connect_failed` repeatedly.

**Diagnose:**

```bash
sudo systemctl status mosquitto
sudo journalctl -u mosquitto --since "5 min ago" | grep -iE "error|not authorised"
mosquitto_pub -h 127.0.0.1 -u ginhawa_kiosk -P "<kiosk-pass>" \
    -t test -m hello
```

**Fixes:**

- Broker not running: `sudo systemctl restart mosquitto`.
- Password mismatch: `sudo mosquitto_passwd /etc/mosquitto/passwd ginhawa_kiosk`
  (re-set the password to what's in `kiosk.env`).
- ACL too tight: confirm `<device-id>` in `/etc/mosquitto/acl`
  matches `KIOSK_DEVICE_ID` in the env file.

### 3. Scale shows wrong readings (last citizen's weight)

**Symptom:** Citizen 2's session displays citizen 1's weight, or
a weight appears before anyone steps on.

**Cause:** `KIOSK_SCALE_MAC` is empty or wrong — the xiaomi-ble
library is reporting cached values from non-scale BLE devices'
adverts. See
[2026-05-13 scale-stale-readings audit](../audits/2026-05-13-scale-stale-readings-audit.md).

**Fix:**

1. Identify the real scale MAC via the diagnostic procedure in
   Phase 9.3 (or by `bluetoothctl devices` cross-referenced with
   `journalctl -u ginhawa-kiosk | grep advert_diagnostic`).
2. Update `/etc/ginhawa/kiosk.env`:
   `KIOSK_SCALE_MAC=<scale-mac>`.
3. Restart the kiosk: `sudo systemctl restart ginhawa-kiosk`.

### 4. BP cuff not connecting

**Symptom:** Kiosk's MEASURING_VITALS screen waits indefinitely
for the BP triple.

**Checklist:**

1. Are the cuff's batteries fresh? Low batteries silently
   prevent BLE pairing.
2. Press the BT button on the cuff — the BT indicator should
   flash. If it doesn't, batteries are dead or the cuff is
   stuck; remove and reinsert batteries.
3. `bluetoothctl devices` should list the cuff. If it doesn't,
   re-pair (Phase 9.1).
4. Verify the kiosk's DB has the right MAC:
   ```bash
   sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
   PRAGMA key='<key>';
   SELECT * FROM device_config WHERE key='omron_cuff_mac';
   .quit
   SQL
   ```

### 5. SpO2 reading 90-98% with no finger

**Should be impossible per ADR-0022.** If observed:

1. Confirm ESP32-A is running the production build (not stale
   firmware from before ADR-0022):
   ```bash
   pio device monitor -b 115200
   # Expect to see "[max30100] finger warmed up" / "finger lost"
   # log lines. If absent, the finger-presence gate isn't in the
   # firmware — reflash ESP32-A.
   ```
2. If gate logs are present but phantom readings still come
   through, the IR threshold may need re-tuning for this
   physical fixture. Edit
   `firmware/esp32-a-vitals/include/config.h` and lower
   `MAX30100_FINGER_IR_THRESHOLD` if no-finger reads above the
   threshold, or raise it if real fingers fall below. Reflash.

### 6. Sessions stuck as in_progress on BHW portal

**Should be impossible per ADR-0024.** If observed:

1. Kiosk DB shows the right state:

   ```bash
   sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
   PRAGMA key='<key>';
   SELECT id, status, ended_at, last_synced_at, updated_at
   FROM sessions ORDER BY started_at DESC LIMIT 3;
   .quit
   SQL
   ```

   - `last_synced_at` should be populated and ≥ `updated_at`.
   - If `last_synced_at` is NULL or behind `updated_at`, the
     daemon hasn't synced yet — wait 30 s, re-check.

2. Daemon is alive:
   ```bash
   sudo journalctl -u ginhawa-kiosk --since "2 min ago" \
     | grep -iE "sync.cycle_complete|sync_attempt"
   ```
   Expect a `sync.cycle_complete` event roughly every 30 s.
3. If you see `no such column ... last_synced_at`, the migration
   didn't run on the kiosk's actual DB. See Phase 6 troubleshooting.

### 7. "database is locked" warnings

**Should be rare per ADR-0021.** If frequent:

1. Confirm WAL mode is active:
   ```bash
   sqlcipher /var/lib/ginhawa/kiosk.db <<'SQL'
   PRAGMA key='<key>';
   PRAGMA journal_mode;
   PRAGMA busy_timeout;
   .quit
   SQL
   ```
   Expect `wal` and `5000`. If `journal_mode` is `delete`, the
   PRAGMA isn't being applied — confirm the kiosk source is up
   to date (`git log --oneline -5` in `/opt/ginhawa/src` should
   show ADR-0021's commit).
2. Confirm side files exist:
   ```bash
   ls -la /var/lib/ginhawa/kiosk.db*
   # Expect: kiosk.db, kiosk.db-wal, kiosk.db-shm
   ```

### 8. Printer not printing

**Checklist:**

- Power: own 9 V adapter plugged in (NOT from Pi USB).
- Paper: loaded with the thermal-coated side facing down. If
  the print is blank, paper is upside down — flip the roll.
- USB: `lsusb | grep -i 0416:5011` should show the printer
  (substitute the actual VID:PID from the worksheet).
- Print head: hold a finger near the head while a print is
  attempted — should feel slight heat. No heat = power issue.

### 9. WiFi keeps disconnecting

**Cause:** Router defaults that don't agree with the Pi 5's WiFi
firmware. See Phase 4.3.

**Fix:** force WPA2-only (not mixed) and 20 MHz channel width on
the router. Reboot the router after the change.

### 10. "alembic upgrade head" reports no migrations and the kiosk still complains about missing columns

**Cause:** alembic ran against `~/.ginhawa/kiosk.db` instead of
`/var/lib/ginhawa/kiosk.db`. See the lesson box in Phase 6.

**Fix:**

```bash
rm ~/.ginhawa/kiosk.db        # delete the alembic-only stray DB
cd /opt/ginhawa/src/kiosk
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key> \
  uv run alembic current      # should now report c8a7e93d4f12 OR nothing
# If "nothing", run the stamp + upgrade from Phase 6.3.
```

### Escalation

If after the above you still can't get the kiosk live, capture
the following and send to the kiosk team:

```bash
sudo journalctl -u ginhawa-kiosk --since "10 min ago" > /tmp/kiosk-journal.log
sudo systemctl status ginhawa-kiosk > /tmp/kiosk-status.log
cd /opt/ginhawa/src && git log --oneline -10 > /tmp/kiosk-version.log
```

---

## Section 16 — Maintenance

### Backups

The kiosk DB is at `/var/lib/ginhawa/kiosk.db` with WAL side
files (ADR-0021). Two backup approaches:

**Option A — copy all three files (kiosk service must be stopped):**

```bash
sudo systemctl stop ginhawa-kiosk
sudo cp /var/lib/ginhawa/kiosk.db{,-wal,-shm} <backup-location>/
sudo systemctl start ginhawa-kiosk
```

**Option B — flatten WAL first, then copy single file:**

```bash
echo "PRAGMA key='<key>'; PRAGMA wal_checkpoint(TRUNCATE);" \
  | sudo -u ginhawa sqlcipher /var/lib/ginhawa/kiosk.db
sudo cp /var/lib/ginhawa/kiosk.db <backup-location>/
```

Option B is safer to run while the kiosk is live — the checkpoint
takes a brief lock, copies the result, and the kiosk resumes.

### Log rotation

systemd-journald rotates `journalctl` automatically; no kiosk-
specific config needed unless you mount `/var/log/journal/` on a
small volume. To cap journal size:

```bash
sudo nano /etc/systemd/journald.conf
# Set:
#   SystemMaxUse=500M
sudo systemctl restart systemd-journald
```

### Kiosk software updates

```bash
cd /opt/ginhawa/src
git fetch origin
git log --oneline HEAD..origin/main         # review what's new
git pull origin main

cd kiosk
uv sync                                      # apply dependency changes

# Apply any new migrations. ALWAYS pass KIOSK_DB_PATH explicitly.
KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \
KIOSK_DB_KEY=<the-key> \
  uv run alembic upgrade head

sudo systemctl restart ginhawa-kiosk
sudo journalctl -u ginhawa-kiosk -f         # watch for clean boot
```

### Firmware updates (ESP32-A or ESP32-B)

```bash
cd /opt/ginhawa/src/firmware/esp32-a-vitals    # or esp32-b-anthro
git pull
# Plug the ESP32 in via USB.
pio run -t upload
pio device monitor -b 115200                  # confirm boot logs
```

### Encryption key rotation

**Not supported in the current release.** Rotating
`KIOSK_DB_KEY` would require decrypting + re-encrypting the
entire database; provision_db.py does not have a rotate mode.
If a key is suspected compromised, the safest action is to
back up the encrypted DB (which is useless without the key),
provision a new kiosk DB, and re-register citizens from the
cloud's view of the data. This gap is documented for a future
release.

---

## Appendix A — Post-deployment checklist

Print and sign before declaring the kiosk live:

```
GINHAWA Kiosk Deployment — Post-Deployment Checklist
=====================================================

Kiosk hostname: ______________________
Barangay:       ______________________
Date:           ______________________

[ ] Hardware fully assembled (Pi, touchscreen, RFID, printer,
    both ESP32s, BP cuff, scale)
[ ] Printer powered from its OWN 9 V adapter, NOT the Pi
[ ] Network connected; static IP active; cloud URL reachable
[ ] System dependencies installed (qt6-virtualkeyboard-plugin
    explicitly verified)
[ ] /opt/ginhawa/src checked out at the deployment branch
[ ] uv sync completed without errors
[ ] /etc/ginhawa/kiosk.env populated with worksheet values
    (no placeholder strings left)
[ ] /etc/ginhawa/kiosk.env mode 0600, owner root:root
[ ] /var/lib/ginhawa created, mode 0700, owner ginhawa:ginhawa
[ ] SQLCipher key generated by provision_db, recorded on the
    worksheet
[ ] alembic stamped + upgraded to head AT /var/lib/ginhawa/kiosk.db
    (alembic current shows c8a7e93d4f12)
[ ] sqlcipher verifies journal_mode=wal and busy_timeout=5000
[ ] Mosquitto bound to 127.0.0.1:1883 (not 0.0.0.0)
[ ] Mosquitto authenticated for ginhawa_kiosk + esp32_a + esp32_b
[ ] ACL prevents cross-user topic writes
[ ] ESP32-A flashed; serial log shows MAX30100 PART_ID=0x11 and
    MLX90640 init OK
[ ] ESP32-A "finger warmed up" / "finger lost" log lines fire on
    finger contact / removal (ADR-0022)
[ ] ESP32-B flashed; serial log shows VL53L0X init OK and
    STAB:FIRE on standstill (ADR-0019)
[ ] Both ESP32s' chip MACs recorded on the worksheet
[ ] Xiaomi scale MAC discovered, KIOSK_SCALE_MAC set,
    recorded on worksheet
[ ] Xiaomi bindkey stored in device_config, recorded on
    worksheet
[ ] Omron BP cuff MAC stored in device_config, recorded on
    worksheet
[ ] Printer test print succeeded
[ ] Full end-to-end smoke test passed (Phase 11)
[ ] Kiosk DB shows status=completed for the smoke session
[ ] Cloud DB shows status=completed for the smoke session
    within 60 s (ADR-0024)
[ ] BHW portal displays the smoke session as Completed
[ ] Smoke-test data cleaned up
[ ] Systemd autostart enabled (sudo systemctl is-enabled
    ginhawa-kiosk → enabled)
[ ] X11 session forced (not Wayland) — virtual keyboard works
[ ] Reboot test passed — kiosk auto-starts fullscreen
[ ] Worksheet sealed in tamper-evident envelope
[ ] Sign-off complete

Technician signature: ______________________
Date:                 ______________________
```

---

## Appendix B — ADR and audit cross-reference

| Reference                                                                                                       | Subject                              | Runbook phase(s)          |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------ | ------------------------- |
| [ADR-0017](../decisions/0017-xiaomi-ble-library-for-smart-scale-s200.md)                                        | Xiaomi BLE library / bindkey         | Phase 9                   |
| [ADR-0019](../decisions/0019-height-stabilization-gate.md)                                                      | Height stabilisation gate (firmware) | Phase 8, Phase 11         |
| [ADR-0020](../decisions/0020-bp-session-floor-freshness.md)                                                     | BP cuff session-floor                | Phase 9, Phase 11         |
| [ADR-0021](../decisions/0021-sqlite-wal-and-busy-timeout.md)                                                    | WAL + busy_timeout                   | Phase 6, Phase 16         |
| [ADR-0022](../decisions/0022-spo2-finger-presence-gate.md)                                                      | MAX30100 finger-presence (firmware)  | Phase 8, Phase 11, Sec 15 |
| [ADR-0023](../decisions/0023-spo2-session-floor.md)                                                             | SpO2 receipt-boundary floor          | Phase 11                  |
| [ADR-0024](../decisions/0024-sync-watermark.md)                                                                 | Sync watermark (last_synced_at)      | Phase 6, Phase 11, Sec 15 |
| [2026-05-13 scale-prefiring audit](../audits/2026-05-13-scale-prefiring-audit.md)                               | Path-vs-type filter                  | Phase 11                  |
| [2026-05-13 bp-stale-readings audit](../audits/2026-05-13-bp-stale-readings-audit.md)                           | BP cuff freshness                    | Phase 9                   |
| [2026-05-13 scale-stale-readings audit](../audits/2026-05-13-scale-stale-readings-audit.md)                     | Xiaomi MAC filter                    | Phase 9, Sec 15           |
| [2026-05-14 db-lock-contention audit](../audits/2026-05-14-db-lock-contention-audit.md)                         | DB contention                        | Phase 6, Sec 15           |
| [2026-05-14 spo2-stale-readings audit](../audits/2026-05-14-spo2-stale-readings-audit.md)                       | SpO2 firmware fix                    | Phase 8, Sec 15           |
| [2026-05-14 session-sync-create-update-gap audit](../audits/2026-05-14-session-sync-create-update-gap-audit.md) | Sync create/update gap               | Phase 6, Phase 11, Sec 15 |

### Out of scope

Documented elsewhere; not covered here:

- Developer workflow (uv add, pre-commit, dev mode, tests). See
  [docs/phase-0-plan.md](../phase-0-plan.md).
- Cloud + portal deployment in detail. See
  [cloud/README.md](../../cloud/README.md) and (future)
  `docs/runbooks/cloud-deployment.md`.
- DPA compliance procedures. Covered in the research paper.
- Hardware procurement contracts.
