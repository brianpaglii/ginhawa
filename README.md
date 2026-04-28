# GINHAWA

GINHAWA is an offline-first IoT health monitoring kiosk for Philippine barangay
health centers. It measures blood pressure, oxygen saturation, body temperature,
height, and weight; computes Body Mass Index; stores records locally with
SQLCipher AES-256 encryption; and synchronizes to a cloud backend whenever
internet is available. Citizens authenticate with RFID health cards, and
Barangay Health Workers (BHWs) consult community-level data through a web
portal hosted on the cloud backend. GINHAWA is **not a medical diagnostic
device** — printed receipts and on-screen advisories are framed as
health-monitoring guidance, not diagnoses.

## Research paper

The full research paper and supporting architectural deliverables live in
[docs/paper.pdf](docs/paper.pdf). Architecture Decision Records are tracked in
[docs/decisions/](docs/decisions/).

## Prerequisites

Before setting up a development environment, read
[docs/phase-0-plan.md](docs/phase-0-plan.md) for the full list of host
requirements, hardware dependencies, and toolchain versions.

## Quickstart

### Clone

```bash
git clone https://github.com/<org>/ginhawa.git
cd ginhawa
```

### Install

Each package has its own dependency tree because they target different
runtimes. Install only what you need.

Cloud backend (FastAPI + PostgreSQL):

```bash
cd cloud
uv sync
```

Kiosk application (Raspberry Pi 5, PyQt6):

```bash
cd kiosk
uv sync
```

BHW portal (React + Vite + TypeScript):

```bash
cd portal
npm install
```

ESP32-A firmware (console node, MAX30100):

```bash
cd firmware/esp32-a-vitals
pio pkg install
```

ESP32-B firmware (stand node, VL53L0X + MLX90640BAB):

```bash
cd firmware/esp32-b-anthro
pio pkg install
```

### Run tests

```bash
# Cloud
cd cloud && uv run pytest

# Kiosk
cd kiosk && uv run pytest

# Portal
cd portal && npm test

# Firmware (desktop unit tests, no board required)
cd firmware/esp32-a-vitals && pio test -e native
cd firmware/esp32-b-anthro && pio test -e native
```

A local PostgreSQL instance for cloud development is provided via Docker:

```bash
docker compose up -d postgres
```

## Repository layout

```
ginhawa/
├── cloud/                      # FastAPI + PostgreSQL backend (Python 3.12, uv)
│   ├── alembic/                # PostgreSQL migrations
│   ├── src/ginhawa_cloud/      # API, models, services
│   └── tests/
├── kiosk/                      # Raspberry Pi 5 application (Python 3.12, uv)
│   ├── alembic/                # SQLite (SQLCipher) migrations
│   ├── scripts/
│   ├── src/ginhawa_kiosk/      # GUI, FSM, BLE, MQTT, sync, printer
│   └── tests/
├── portal/                     # BHW web portal (React 18, Vite, TypeScript)
│   ├── public/
│   └── src/
├── firmware/
│   ├── esp32-a-vitals/         # Console node firmware (PlatformIO)
│   └── esp32-b-anthro/         # Stand node firmware (PlatformIO)
├── docs/
│   └── decisions/              # Architecture Decision Records
├── scripts/                    # Setup, deployment, calibration utilities
├── schema.sql                  # Authoritative database schema (kiosk + cloud)
├── docker-compose.yml          # Local Postgres for cloud development
├── .env.example                # Template for local environment variables
├── CHANGELOG.md
└── CLAUDE.md                   # Project context for Claude Code
```

`schema.sql` is the contract between the kiosk and cloud schemas; both
Alembic trees mirror it with type substitutions (TEXT → VARCHAR,
REAL → DOUBLE PRECISION) handled in migrations.

## Contribution workflow

1. **Branch off `main`.** Use a descriptive branch name prefixed by the
   package or area, e.g. `kiosk/session-fsm-retry`, `cloud/audit-log-mirror`,
   `portal/bhw-dashboard`, `firmware/vl53l0x-long-range`.
2. **Commit using [Conventional Commits](https://www.conventionalcommits.org/).**
   Allowed types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`.
   Subject lines stay under 72 characters; the body explains _why_ if the
   change is non-obvious. One logical change per commit — do not bundle
   unrelated work.
3. **Write tests with every change.** Bug fixes start with a failing test.
   New features land with coverage. See per-package `tests/` directories
   for conventions.
4. **Run formatters and linters before pushing.**
   - Python: `uv run ruff format .` and `uv run ruff check . --fix`
   - TypeScript: `npm run format` and `npm run lint`
   - C++: `clang-format` (LLVM style, 100-char width)
5. **Open a Pull Request against `main`.** Fill in the PR template, link
   any related issue, and describe the testing performed. Schema changes
   require migrations in **both** `kiosk/alembic/` and `cloud/alembic/`,
   and any destructive migration must be flagged for human review before
   merge.
6. **At least one reviewer approval is required before merge.** Squash
   merge is the default.

Refer to [CLAUDE.md](CLAUDE.md) for project conventions, locked technical
decisions, and absolute rules (hardware safety, data privacy, schema
discipline).

## Team contact

> _TODO: replace with team contact details before public release._
>
> - **Project lead:** _name — email_
> - **Technical lead:** _name — email_
> - **Institution:** Emilio Aguinaldo College
> - **Issue tracker:** _link_
