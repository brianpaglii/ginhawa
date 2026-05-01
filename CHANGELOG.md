# Changelog

All notable changes to the GINHAWA project are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses semantic versioning for the database schema.

## [Unreleased]

### Added — Phase 1.5 (kiosk sync)

- **Kiosk authentication** — Bearer-API-key dependency
  (`get_current_kiosk`) that authenticates kiosks against
  argon2id-hashed credentials in `device_credentials`. Lookup runs
  `verify_password` against every active credential to keep work
  constant in population size, mirroring the dummy-hash pattern used
  by the BHW login flow.
- **`POST /api/v1/sync/citizens`** — idempotent batch upload (≤500
  records) with `(id, updated_at)` as the sync key. Per-record
  results: `created`, `updated`, `conflict_stale`,
  `conflict_constraint` (RFID collision), or `rejected` (validation).
  ADR-0014 Option A self-service registration: records with
  `registered_by=NULL` produce an audit row attributed to
  `actor_type='kiosk'`, `actor_id=<device_id>`,
  `details.registration_type='self_service'`.
- **`POST /api/v1/sync/sessions`** — same batch/idempotency contract,
  with two extra per-record reject paths: `citizen_not_found` (FK)
  and `device_id_mismatch` (a kiosk cannot upload sessions claiming
  another kiosk's device_id).
- **`POST /api/v1/sync/measurements`** — same batch/idempotency
  contract, with `session_not_found` rejection. Out-of-range readings
  are STORED (not rejected) with `is_valid=0` and `validation_notes`,
  preserving the kiosk's clinical decision to capture.
- **Migrations** — `a8c1e3d27f5a` extends `audit_log.actor_type`
  CHECK to include `'kiosk'`; `b3f7a92c0d8e` adds `updated_at` to
  `sessions` and `measurements` (idempotency anchor).
- **Seed script** — now seeds one device credential
  (`seed_kiosk_001`) with a deterministic plaintext API key,
  reprinted in the stdout summary for smoke-test reproducibility
  (DEV ONLY).

### Added — Phase 1 (cloud backend)

- **Cloud package** — FastAPI on Python 3.12 (managed by `uv`),
  PostgreSQL 16 via SQLAlchemy 2.x, Alembic migrations, JWT auth
  (HS256) with argon2id password hashing.
- **API surface** — `/auth/login`, `/users` (CRUD + `/me`),
  `/citizens` (CRUD + soft-delete), `/sessions` (lifecycle + PATCH),
  `/measurements` (capture + `/invalidate`), `/audit-log` (admin-only
  list), `/device-credentials` (admin-only CRUD with revoke).
- **Authorization** — role/scope tuples for `admin`, `bhw`,
  `data_viewer`. BHW barangay-scoping with the ADR-0008
  indistinguishability pattern: cross-barangay reads return 404
  (not 403); cross-barangay writes return 403.
- **Timing-leak mitigation (ADR-0007)** — `/auth/login` runs a
  hardcoded dummy argon2id hash on the unknown-username path so
  response time is independent of whether the username exists.
- **Append-only audit_log (ADR-0005)** — application-side
  `record_audit` is the only sanctioned writer. Append-only enforced
  at the database by `audit_log_no_update` / `audit_log_no_delete`
  triggers and by revoking UPDATE/DELETE from the application role.
  Patient-data tables no longer carry their own audit triggers.
- **Idempotent dev seeder** — admin, BHWs, citizens, sessions,
  measurements, plus a realistic audit history.
- **Verification artifacts** — `docs/verification/` directory with
  the Phase 1 final coverage snapshot, the Path A smoke-test report,
  and the Phase 1.5 smoke-test report.

### Added — Phase 0

- **Schema v1.0.0** — initial authoritative database schema at `schema.sql`.
  Defines the core patient-data hierarchy (`citizens` → `sessions` →
  `measurements`), the append-only `audit_log` with enforcement triggers,
  per-kiosk `device_config` key-value store, cloud-only BHW portal `users`,
  audit triggers on patient-data mutations, retention-task reference SQL,
  and the `schema_version` row pinned at `1.0.0`. Both `kiosk/alembic/`
  and `cloud/alembic/` mirror this schema with dialect-specific type
  substitutions; future changes must land in this file together with
  matching migrations on both sides.
