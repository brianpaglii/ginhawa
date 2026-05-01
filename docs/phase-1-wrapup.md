# Phase 1 wrap-up

**Status:** complete (Phase 1 + Phase 1.5)
**Last commit at wrap-up:** `85e2fb0` (`docs(verification): phase 1.5 smoke test results`)
**Date:** 2026-05-01

## Scope of Phase 1

Phase 1 delivered the cloud backend's authoritative API surface — the
data plane that the BHW portal will consume in Phase 2 and that the
kiosk syncs into. It is FastAPI on Python 3.12, PostgreSQL 16 (TEXT/
REAL → VARCHAR/DOUBLE PRECISION via Alembic), JWT auth with argon2id
password hashing, and an append-only `audit_log` enforced by Postgres
triggers.

Phase 1.5 was a tightly-scoped follow-up that closed the kiosk-sync
gap left N/A in the Phase 1 (Path A) smoke test: kiosk authentication
via Bearer API key, three idempotent batch-upload endpoints, and the
self-service registration audit attribution required by ADR-0014
Option A.

What is **not** in Phase 1: the React/Vite portal, the PyQt6 kiosk
application, the ESP32 firmware, BLE pairing, MQTT wiring, the
SQLCipher kiosk DB, and the printer pipeline. Those land in Phases
2–4.

## Delivered

### Schema and migrations

`schema.sql` is the single source of truth; both `cloud/alembic/` and
`kiosk/alembic/` mirror it with dialect-specific type substitutions.
Cloud migrations applied (head: `b3f7a92c0d8e`):

| #   | Revision       | Purpose                                                                                                                                          |
| --- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `c62cbc71004c` | Initial schema — citizens, sessions, measurements, audit_log, device_config, users, schema_version row, indexes, audit-log append-only triggers. |
| 2   | `20bbb05e1da1` | Drop redundant audit triggers on patient-data tables (ADR-0005: app-layer `record_audit` is the only sanctioned writer).                         |
| 3   | `f566915403f9` | Add `device_credentials` table (kiosk API-key hashes, soft-revocation pathway).                                                                  |
| 4   | `a8c1e3d27f5a` | Extend `audit_log.actor_type` CHECK to include `'kiosk'` (needed for self-service attribution).                                                  |
| 5   | `b3f7a92c0d8e` | Add `updated_at` to `sessions` and `measurements` (idempotency anchor for kiosk sync).                                                           |

All migrations are downgrade-tested. `op.execute` is used for the
audit-log triggers and for raw DDL the autogenerator can't produce.

### BHW / admin API surface

| Endpoint group               | Operations                          | Auth                                                    |
| ---------------------------- | ----------------------------------- | ------------------------------------------------------- |
| `/api/v1/auth/login`         | POST (issues JWT)                   | password (argon2id)                                     |
| `/api/v1/users`              | CRUD + `/me`                        | `users:admin`                                           |
| `/api/v1/citizens`           | POST/GET/list/PATCH/soft-DELETE     | `citizens:read` / `citizens:write`, BHW barangay-scoped |
| `/api/v1/sessions`           | POST/GET/list/PATCH                 | `sessions:read` / `sessions:write`, BHW barangay-scoped |
| `/api/v1/measurements`       | POST/GET/list + PATCH `/invalidate` | `measurements:read` / `measurements:write`              |
| `/api/v1/audit-log`          | GET/list                            | `audit_log:read` (admin only)                           |
| `/api/v1/device-credentials` | POST/GET/list/PATCH (revoke)        | `device_credentials:admin`                              |

ADR-relevant behaviour:

- **ADR-0007 (timing-leak mitigation):** `/auth/login` runs a
  dummy-hash verification on the unknown-username path so response
  time is independent of whether the username exists.
- **ADR-0008 (BHW indistinguishability):** cross-barangay reads return
  404 (not 403); cross-barangay POSTs return 403 (the only path where
  the client can already see the barangay it picked).
- **Soft-delete is uniform:** GETs against soft-deleted citizens return
  404 (byte-equivalent to never-existed).

### Kiosk-to-cloud sync

| Endpoint                         | Idempotency anchor | Per-record reject paths                                                                                                                                 |
| -------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/sync/citizens`     | `(id, updated_at)` | `rejected` (validation), `conflict_constraint` (RFID collision), `conflict_stale`                                                                       |
| `POST /api/v1/sync/sessions`     | `(id, updated_at)` | `rejected` with `error='citizen_not_found'` or `'device_id_mismatch'`, `conflict_stale`                                                                 |
| `POST /api/v1/sync/measurements` | `(id, updated_at)` | `rejected` with `error='session_not_found'`, `conflict_stale`. Out-of-range readings are STORED with `is_valid=0` and `validation_notes`, not rejected. |

All three: ≤500 records per batch (HTTP 413 otherwise); a single
real DB error rolls back the whole batch (HTTP 500); per-record
validation/conflict errors do not poison sibling records. Auth via
`get_current_kiosk` (Bearer API key, argon2id-hashed); the lookup
runs `verify_password` against every active credential to keep work
constant in population size, mirroring the dummy-hash pattern.

ADR-0014 Option A (self-service registration) is implemented in
`/sync/citizens`: a record with `registered_by=NULL` produces an
audit row attributed to `actor_type='kiosk'`,
`actor_id=<device_credentials.device_id>`, with
`details.registration_type='self_service'`. BHW-assisted records
attribute to `actor_type='bhw'` with the kiosk device_id captured in
details for traceability.

### Seed script

`uv run python -m ginhawa_cloud.scripts.seed_dev_data` is idempotent
and produces:

- 1 admin + 3 BHWs (one per seeded barangay)
- 20 citizens distributed 8/7/5 across Tibagan / Pinaglabanan / Corazon de Jesus
- 5 sessions, 15 measurements
- 1 device credential (`seed_kiosk_001`) with deterministic plaintext
  API key for smoke-test reproducibility — clearly marked DEV ONLY in
  both source comments and stdout output.
- ≥45 audit_log rows (one create row per seeded entity).

## Tests and coverage

- **132 tests pass** (`uv run pytest`).
- **97% line coverage** on the cloud package (1430 statements, 46
  missing). Every gap is a real unexercised branch, not a deletion.

Coverage gaps under 95% (tracked as Phase 2 follow-ups, see "Known
gaps" below):

| Module                      | Coverage | Why                                                                                                                           |
| --------------------------- | -------: | ----------------------------------------------------------------------------------------------------------------------------- |
| `api/_authz.py`             |      83% | Cross-barangay POST 403 path, BHW PATCH cross-barangay path.                                                                  |
| `api/sessions.py`           |      86% | `?status=`, `?started_after=`/`?started_before=`, BHW barangay override on list, PATCH-on-unknown, empty-PATCH short-circuit. |
| `api/users.py`              |      90% | Duplicate-username 409, PATCH-on-unknown, empty-PATCH short-circuit.                                                          |
| `api/device_credentials.py` |      91% | Description-collision 409 path, GET-on-unknown 404, PATCH-on-unknown 404.                                                     |
| `core/security.py`          |      95% | Token-missing-subject, token-scopes-wrong-type, token-after-user-deleted, token-after-user-deactivated.                       |

`api/sync_*` are all 95–96%; the residual misses are the
`# pragma: no cover` real-DB-error branches.

Full snapshots are durable under
[docs/verification/](verification/):

- [`2026-04-30-phase1-coverage-snapshot.md`](verification/2026-04-30-phase1-coverage-snapshot.md)
- [`phase1_smoke_test_path_a.md`](verification/phase1_smoke_test_path_a.md)
- [`2026-05-01-phase1-5-smoke-test.md`](verification/2026-05-01-phase1-5-smoke-test.md)

## Smoke test outcomes

### Phase 1 (Path A) — kiosk-sync deferred

Run against a freshly seeded local Postgres + uvicorn. All in-scope
scenarios PASS; the kiosk-sync rows were marked N/A and explicitly
deferred to Phase 1.5.

### Phase 1.5 — kiosk sync paths

10/10 PASS (PS1–PS10): batch upload, idempotent replay, self-service
attribution, FK and device-id-mismatch rejection paths, out-of-range
storage with `is_valid=0`, revoked-credential 401, oversize-batch 413. Constant-time auth: 11.3 ms valid-vs-invalid delta, well inside
the 50 ms target.

## Locked decisions and invariants

These are settled and govern Phase 2's design space:

- **Identifiers:** RFC 4122 v4 UUIDs for every `id` column except
  `audit_log.id` (autoincrement integer).
- **Timestamps:** ISO 8601 strings in UTC at the storage layer; local-
  time display is the UI's responsibility.
- **Audit:** application-side `record_audit` is the only sanctioned
  writer for both mutations and sensitive reads. Append-only is
  enforced by `audit_log_no_update` / `audit_log_no_delete` triggers
  and by revoking UPDATE/DELETE on `audit_log` from the application's
  database role.
- **Sync model:** kiosk → cloud is write-mostly; per-record `synced`
  marker is set to 1 only after the cloud confirms.
- **Idempotency:** `(id, updated_at)` is the universal sync key.
  Lexicographic comparison on ISO 8601 UTC strings is correct for
  ordering.
- **Self-service registration (ADR-0014 Option A):** kiosk is a first-
  class actor in `audit_log` (`actor_type='kiosk'`).

## Known gaps and Phase 2 follow-ups

### Coverage tightening (carryover from Phase 1)

The five modules listed above all have well-documented unexercised
branches. They are not blockers — every gap was reasoned about during
the Phase 1 final coverage snapshot — but they are good first tickets
for Phase 2.

### BHW-side `updated_at` bumping

`update_session`, `update_user`, and a few related handlers do not
currently bump `updated_at` on PATCH. The kiosk sync's stale-write
check uses string comparison, so a BHW edit that doesn't bump
`updated_at` could be silently overwritten by a stale kiosk push.
This is **not exercised in production today** because the BHW portal
doesn't exist yet — but it should be fixed before any portal handler
goes live in Phase 2.

### Documentation

- ADRs are not yet written. The text body of `docs/decisions/` is
  empty except for `.gitkeep`. ADR-0005 (audit triggers removed),
  ADR-0007 (timing-leak), ADR-0008 (BHW indistinguishability), and
  ADR-0014 (self-service registration) are referenced in code and
  docs but the formal records remain to be authored. ADRs are
  human-written by policy.

### Operational hardening (deliberately deferred)

- TLS 1.3 termination is the deployment platform's responsibility;
  the FastAPI app does not negotiate it.
- Rate limiting on `/auth/login`, `/sync/*`, and admin endpoints is
  not implemented. Phase 2 should land at least a per-IP token bucket
  on `/auth/login` to make password-guessing impractical.
- Structured logs are configured (`structlog`) but no log aggregation
  pipeline is set up. The Phase 0 plan calls for journald → remote
  syslog; that's a deployment-time task.

## Phase 2 entry criteria — what should be true before starting

These are what Phase 2's first commit can assume without re-checking:

1. The `cloud/` package exposes a stable OpenAPI document at
   `/openapi.json`. The portal can generate its TypeScript client from
   that document via `openapi-typescript`.
2. JWT login works against the seeded admin/BHW credentials.
3. Kiosk sync endpoints are stable contracts; the portal does not
   touch them, but the kiosk team in Phase 3 can build against them.
4. `schema.sql` is the contract; any portal-facing change that
   requires schema additions lands a migration in **both** `cloud/`
   and `kiosk/` Alembic trees, gated by human review on destructive
   ones.
5. `record_audit` remains the only writer to `audit_log`. Portal
   read endpoints that surface citizen data (export, view) MUST chain
   through it. A future portal-side download endpoint should not be
   merged without auditing.

## What Phase 2 is

Phase 2 is the React/Vite/TypeScript BHW portal: dashboard, citizen
list with barangay-scoping, session detail, measurement trends,
audit-log viewer (admin), credential admin (admin). It consumes the
cloud's OpenAPI spec via a generated client; it does not re-implement
business rules. The portal is the second authenticated client; the
kiosk (Phase 3) is the third.
