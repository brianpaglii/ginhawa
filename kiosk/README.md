# GINHAWA kiosk

The PyQt6 kiosk application for the GINHAWA health-monitoring kiosk.
Captures vital signs over BLE / MQTT / RFID, persists to a SQLCipher-
encrypted SQLite database, and syncs to the cloud backend whenever the
internet is available.

This README is for the kiosk team. Project-wide context lives in the
[root README](../README.md) and [`CLAUDE.md`](../CLAUDE.md).

## Prerequisites

- **Python 3.12** (managed via `uv` — see the [root README](../README.md)
  for monorepo install steps).
- **Qt6 system libraries.** PyQt6 ships its own Qt runtime; on Linux
  desktops install `qt6-base` and `qt6-wayland` (or X11 equivalents).
  On Raspberry Pi OS trixie these are already in the deployment image.
- **SQLCipher.** `sqlcipher3-binary` ships with a bundled SQLCipher
  build, so no system package is required.
- **For BLE on Linux:** BlueZ (`bluez`, `bluez-tools`). Bleak talks to
  it. Mock mode does not need BlueZ.
- **For the on-screen keyboard on the touchscreen kiosk** (production
  deployment only): `qml-module-qtquick-virtualkeyboard` +
  `qt6-virtualkeyboard-plugin`. Without these the `QT_IM_MODULE`
  selector set in [`__main__.py`](src/ginhawa_kiosk/__main__.py)
  resolves to nothing and a citizen with no hardware keyboard can't
  fill in the register form. Not required for laptop / mock-mode
  development. Install command lives in
  [`docs/phase-0-plan.md`](../docs/phase-0-plan.md) §1.

## Install

From the repo root:

```bash
cd kiosk
uv sync
```

That installs runtime + dev deps from [`pyproject.toml`](pyproject.toml)
and resolves the lockfile in [`uv.lock`](uv.lock).

## Configuration

Settings come from environment variables, with `.env` fallback. The
required ones (no defaults — the package refuses to start without them)
are marked `required` in the table below.

| Variable           | Default                       | Description                                                                                                                                    |
| ------------------ | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `KIOSK_DB_PATH`    | `~/.ginhawa/kiosk.db`         | Path to the SQLCipher-encrypted SQLite file.                                                                                                   |
| `KIOSK_DB_KEY`     | _required_                    | SQLCipher AES-256 passphrase. Derived from machine-id + install-time salt on a deployed Pi.                                                    |
| `CLOUD_API_URL`    | `https://cloud.ginhawa.local` | Base URL of the GINHAWA cloud backend.                                                                                                         |
| `KIOSK_API_KEY`    | _required_                    | Device API key issued by the cloud admin (`POST /api/v1/device-credentials`). Plaintext — see below.                                           |
| `KIOSK_DEVICE_ID`  | _required_                    | UUID matching `device_credentials.device_id` in the cloud. Sessions claiming a different device id are rejected by the cloud's sync endpoints. |
| `MQTT_BROKER_HOST` | `localhost`                   | Local Mosquitto broker. Not exposed beyond the kiosk.                                                                                          |
| `MQTT_BROKER_PORT` | `1883`                        | Standard MQTT port.                                                                                                                            |
| `LOG_LEVEL`        | `INFO`                        | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.                                                                                        |
| `MOCK_HARDWARE`    | `false`                       | **The single switch between dev-on-laptop and prod-on-Pi.** See "Running in mock mode" below.                                                  |

The `KIOSK_API_KEY` plaintext is shown _once_ at credential creation
on the cloud admin endpoint — never persisted on the cloud, only its
argon2id hash. If it is lost, revoke and re-create on the cloud side.

## Running in mock mode (laptop development)

```bash
export KIOSK_DB_KEY=dev-only-not-a-real-key      # pragma: allowlist secret
export KIOSK_API_KEY=dev-only-not-a-real-api-key # pragma: allowlist secret
export KIOSK_DEVICE_ID=00000000-0000-0000-0000-000000000401
export MOCK_HARDWARE=true

uv run pytest                       # full test suite
uv run python -m ginhawa_kiosk      # launch the GUI (when implemented)
```

In mock mode every BLE / MQTT / RFID / printer integration resolves to
the deterministic mocks under `sensors/` and `services/`. No physical
hardware is needed; logs use `structlog`'s human-readable console
renderer.

## Running on the Pi (production)

The deployment image installs the kiosk under a dedicated `ginhawa`
user with a systemd unit that injects the env vars from a root-only
credentials file derived at install time:

```bash
# /etc/systemd/system/ginhawa-kiosk.service (excerpt)
EnvironmentFile=/etc/ginhawa/kiosk.env  # 0600 root:root
ExecStart=/usr/bin/uv run python -m ginhawa_kiosk
```

`MOCK_HARDWARE` is unset (defaults to `false`), so the production
sensor adapters and the JSON `structlog` renderer engage automatically.
Logs flow into systemd journal; remote forwarding is configured at the
host level, not by the kiosk.

## Tests and lint

```bash
uv run pytest               # smoke + full test suite
uv run mypy src/ginhawa_kiosk
uv run ruff check . --fix
uv run ruff format .
```

Pre-commit hooks at the repo root run `mypy --strict` on the kiosk
source and the smoke test on every commit that touches `kiosk/`.
