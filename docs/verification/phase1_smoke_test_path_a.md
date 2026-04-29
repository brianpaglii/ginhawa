# Phase 1 Functional Smoke Test — Path A (kiosk-sync deferred)

Run against seeded local Postgres on `localhost:5432`, API on `localhost:8000`.
Path A means kiosk-sync scenarios are explicitly N/A.

## Prerequisites

| #   | Check                                                     | Result                                                                                                                                                                         |
| --- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `cloud/src/ginhawa_cloud/scripts/seed_dev_data.py` exists | ✅ 21 KB file present                                                                                                                                                          |
| 2   | `cloud/src/ginhawa_cloud/api/audit_log.py` exists         | ✅ 4.5 KB file present                                                                                                                                                         |
| 3   | Cloud package starts and serves OpenAPI on `/docs`        | ✅ `/openapi.json` → 200 (26 KB), `/docs` → 200, 14 paths registered. Required `DATABASE_URL` and `JWT_SECRET` env vars; same operational pattern used throughout the project. |
| 4   | Docker compose configured                                 | ✅ `docker compose ps` recognized the postgres service (was stopped at start of run, restarted as part of teardown step)                                                       |

## Setup

```bash
docker compose down -v          # destroyed volume ginhawa_postgres_data
docker compose up -d postgres   # fresh volume, healthy in ~5s
DATABASE_URL=… JWT_SECRET=… uv run alembic upgrade head
DATABASE_URL=… JWT_SECRET=… uv run python -m ginhawa_cloud.scripts.seed_dev_data
DATABASE_URL=… JWT_SECRET=… uv run uvicorn ginhawa_cloud:app --port 8000 &
```

### Seed-script output (verbatim)

```
Created in this run:
  Users:        4
  Citizens:     20
  Sessions:     5
  Measurements: 15
  Audit rows:   44

Total in database after this run:
  Users:        4
  Citizens:     20
  Sessions:     5
  Measurements: 15
  Audit rows:   44

[CREDENTIALS — DEV ONLY, DO NOT USE IN PROD]
  admin            / seed_admin_password_change_me
  bhw_tibagan      / seed_bhw_password
  bhw_pinaglabanan / seed_bhw_password
  bhw_corazon      / seed_bhw_password

[DEFERRED] Device credential not seeded — device_credentials table
does not exist yet. This will be added when the kiosk sync feature
(originally Phase 1 Prompt 9) is implemented. Tracked in ADR-XXXX.
```

All counts match spec (4 / 20 / 5 / 15 / ≥44). Deferred notice present.

### Citizen UUIDs used in scenarios (from seed script's hardcoded values)

| Barangay                        | UUID                                   |
| ------------------------------- | -------------------------------------- |
| Tibagan (Maria Dela Cruz)       | `00000000-0000-0000-0000-000000000101` |
| Pinaglabanan (Teresa Mendoza)   | `00000000-0000-0000-0000-000000000109` |
| Corazon de Jesus (Cristina Lim) | `00000000-0000-0000-0000-000000000116` |

---

## Authentication scenarios

### A. Admin login (correct password) — **PASS**

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"seed_admin_password_change_me"}'
```

Response: **HTTP 200**, JWT bearer token (387 chars). `ADMIN_TOKEN` captured.

### B. BHW Tibagan login — **PASS**

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"bhw_tibagan","password":"seed_bhw_password"}'
```

Response: **HTTP 200**, JWT bearer token (345 chars). `BHW_TIBAGAN_TOKEN` captured.

### C. Admin with wrong password — **PASS**

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -d '{"username":"admin","password":"wrong-pw"}'
```

Response: **HTTP 401**, body: `{"detail":"incorrect credentials"}`. Generic message as expected.

### D. Wrong-password vs unknown-user timing (5 attempts each) — **PASS**

| Branch                     | Mean wall time |
| -------------------------- | -------------- |
| Known user, wrong password | **110.2 ms**   |
| Unknown username           | **101.9 ms**   |
| **Delta**                  | **8.3 ms**     |

Δ = 8.3 ms, well within the 50 ms window. The dummy-hash equalization in `api/auth.login` is doing its job. Both branches return identical status (401) and body (`{"detail":"incorrect credentials"}`).

---

## Barangay scoping scenarios

### E. BHW Tibagan lists citizens — **PASS**

```bash
curl -H "Authorization: Bearer $BHW_TIBAGAN_TOKEN" \
  http://localhost:8000/api/v1/citizens
```

Response: **HTTP 200**, `total=8`, `barangays=['Tibagan']`. Matches the seed's 8-citizen Tibagan cohort exactly. No row from another barangay appeared.

### F. BHW Tibagan reads a Pinaglabanan citizen — **PASS**

```bash
curl -H "Authorization: Bearer $BHW_TIBAGAN_TOKEN" \
  http://localhost:8000/api/v1/citizens/00000000-0000-0000-0000-000000000109
```

Response: **HTTP 404**, body: `{"detail":"citizen 00000000-0000-0000-0000-000000000109 not found"}`. Generic not-found, no leak that the citizen exists in another barangay.

### G. Admin lists citizens — **PASS**

Response: **HTTP 200**, `total=20`, `barangays=['Corazon de Jesus', 'Pinaglabanan', 'Tibagan']`. All 20 seeded citizens visible across all 3 barangays.

### H. BHW reads audit-log — **PASS**

Response: **HTTP 403**, body: `{"detail":"missing required scope: audit_log:read"}`. The `audit_log:read` scope is admin-only and BHW lacks it.

### I. Admin reads audit-log — **PASS**

Response: **HTTP 200**, `total=59`. Total reflects: 44 seed rows + login/list/login-fail rows accumulated up to scenario I. Paginated response with `items` and `total`.

---

## Mutation scenarios

### J. BHW creates a Tibagan citizen — **PASS**

```bash
curl -X POST http://localhost:8000/api/v1/citizens \
  -H "Authorization: Bearer $BHW_TIBAGAN_TOKEN" \
  -d '{"rfid_uid":"SMOKE_TEST_001","full_name":"Smoke Test","dob":"1990-01-01","sex":"M","barangay":"Tibagan","consent_version":"1.0"}'
```

Response: **HTTP 201**, new UUID = `b413c7d8-99b4-4e76-971a-3fd3e51dc424`. Captured as `NEW_CITIZEN_UUID`.

### K. BHW retries with the same RFID — **PASS**

Response: **HTTP 409**, body: `{"detail":"rfid_uid 'SMOKE_TEST_001' already exists"}`. RFID uniqueness enforced; the conflict is surfaced clearly.

### L. BHW PATCH `full_name` — **PASS**

Response: **HTTP 200**, body shows `full_name: "Smoke Test (renamed)"`.

### M. BHW attempts to PATCH `rfid_uid` — **PASS (silent ignore)**

```bash
curl -X PATCH http://localhost:8000/api/v1/citizens/$NEW_UUID \
  -H "Authorization: Bearer $BHW_TIBAGAN_TOKEN" \
  -d '{"rfid_uid":"SHOULD_NOT_CHANGE"}'
```

Response: **HTTP 200**, response body still has `rfid_uid: "SMOKE_TEST_001"`. **Observed contract: silent ignore**, not 422. The Pydantic `CitizenUpdate` schema doesn't declare `rfid_uid`, so Pydantic's default `extra="ignore"` drops the field at parse time before the handler ever sees it. See `Findings` below.

> **Follow-up after this run:** scenario M's "silent ignore" was tightened in commit `99fab29` — `CitizenUpdate` (and the other update schemas) now use `extra="forbid"`, so the same PATCH today returns **HTTP 422** with the offending field named in `loc`. The test suite was updated in the same commit; smoke-test re-verification of this scenario in a future Path A run should expect 422.

### N. BHW DELETE — **PASS**

Response: **HTTP 204** (empty body, as expected for soft-delete confirmation).

### O. BHW GET the soft-deleted citizen — **FAIL**

```bash
curl -H "Authorization: Bearer $BHW_TIBAGAN_TOKEN" \
  http://localhost:8000/api/v1/citizens/$NEW_UUID
```

**Expected:** HTTP 404 (soft-deleted; should be invisible).
**Actual:** **HTTP 200** with full body, `is_active: 0` visible.

```json
{
  "id":"b413c7d8-…",
  "rfid_uid":"SMOKE_TEST_001",
  "full_name":"Smoke Test (renamed)",
  "is_active": 0,
  …
}
```

The handler `api/citizens.get_citizen` looks up by id and returns the row regardless of `is_active`. Only the **list** endpoint filters on `is_active=true` by default. See `Findings` for the implication.

> **Follow-up after this run:** scenario O's FAIL was fixed in commit `7490ac5` — `get_citizen` now applies the `is_active=1` filter, returning a byte-equivalent 404 for soft-deleted rows. Behaviour test added in commit `597ba0a`. Smoke-test re-verification should expect 404.

### P. BHW default list does not show the deleted citizen — **PASS**

Response: **HTTP 200**, `total=8` (back to baseline; the new + deleted citizen netted out), `has_deleted_uuid=False`. The list endpoint correctly hides the soft-deleted row from default queries.

---

## Audit-log verification

### Q. Audit rows for the new citizen — **PASS** (and stronger than spec)

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8000/api/v1/audit-log?object_id=b413c7d8-…"
```

Response: **HTTP 200**, `total=4`. All four rows have `actor_type='bhw'` and `actor_id='00000000-0000-0000-0000-000000000010'` (bhw_tibagan's user ID). **None are `actor_type='system'`.**

| #   | action                                                                   | actor_type | actor_id    |
| --- | ------------------------------------------------------------------------ | ---------- | ----------- |
| 1   | `create` (from J)                                                        | bhw        | bhw_tibagan |
| 2   | `update` (from L+M; M was effectively a no-op so it appears as `update`) | bhw        | bhw_tibagan |
| 3   | `soft_delete` (from N)                                                   | bhw        | bhw_tibagan |
| 4   | `read` (from O — the unexpected 200 GET on the soft-deleted record)      | bhw        | bhw_tibagan |

The spec expected 3 rows (create, update, soft_delete). Got 4 because scenario O's unexpected-200 GET wrote a `read` audit row. Attribution on all four is correct.

### Q-meta. Meta-audit on the audit-log read — **PASS**

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8000/api/v1/audit-log?action=read_audit_log&limit=5"
```

Response: **HTTP 200**, `total=2` rows of `action='read_audit_log'`. Both have `actor_type='admin'` and `actor_id` matching the seeded admin (`00000000-0000-0000-0000-000000000001`). The first read was scenario I; the second was scenario Q itself. The Q-meta read's own meta-audit isn't yet visible because record*audit commits \_after* the count is computed (documented behavior).

---

## Audit-log tampering (DB-level append-only)

### R. UPDATE on `audit_log` — **PASS**

```bash
docker compose exec postgres psql -U ginhawa -d ginhawa \
  -c "UPDATE audit_log SET action='hacked' WHERE id=1"
```

Output:

```
ERROR:  audit_log is append-only
CONTEXT:  PL/pgSQL function audit_log_no_modify() line 3 at RAISE
```

Update was rejected by the `audit_log_no_update` trigger; the trigger's function `audit_log_no_modify()` raised the exception. Verified by re-reading row 1: `action` is still `'create'`, unchanged.

### S. DELETE on `audit_log` — **PASS**

```bash
docker compose exec postgres psql -U ginhawa -d ginhawa \
  -c "DELETE FROM audit_log WHERE id=1"
```

Output:

```
ERROR:  audit_log is append-only
CONTEXT:  PL/pgSQL function audit_log_no_modify() line 3 at RAISE
```

Delete rejected by the `audit_log_no_delete` trigger.

---

## Skipped scenarios — Path A

The original Pass 3 prompt's four kiosk-sync scenarios (`POST /api/v1/sync/citizens`, `POST /api/v1/sync/sessions`, `POST /api/v1/sync/measurements`, idempotency verification) are **N/A** in this run. Both the sync endpoints and the device-credentials authentication that gates them are deferred to Phase 1.5 ("Prompt 9" in the original plan). They will be covered in a follow-up smoke test once that work lands.

---

## Summary

| Result   | Count | Scenarios                                                         |
| -------- | ----- | ----------------------------------------------------------------- |
| **PASS** | 19    | A, B, C, D, E, F, G, H, I, J, K, L, M, N, P, Q, Q-meta, R, S      |
| **FAIL** | 1     | O                                                                 |
| **N/A**  | 4     | sync_citizens, sync_sessions, sync_measurements, sync_idempotency |

19 + 1 + 4 = 24 total scenarios scored.

---

## Findings — items a human should look at

### 1. Scenario O — soft-deleted GET-by-id returns 200, not 404 (FAIL) — **FIXED**

`GET /api/v1/citizens/{id}` does not apply the `is_active` filter that the list endpoint applies. A BHW (or anyone with `citizens:read` and the right barangay) can still fetch a soft-deleted citizen by direct UUID lookup and see `is_active: 0` in the body.

**Resolution:** fixed in commit `7490ac5` ("fix(cloud): return 404 for GET on soft-deleted citizens"). The handler now applies `is_active=1` filter and returns a byte-equivalent 404 for soft-deleted rows (ADR-0008 indistinguishability pattern). Behaviour test in commit `597ba0a`.

### 2. Scenario M — `rfid_uid` change is silently ignored, not rejected — **FIXED**

The contract was "silent ignore" — the field is stripped at the Pydantic parse step because `CitizenUpdate` doesn't declare it. Returns 200 with the original `rfid_uid` unchanged.

**Resolution:** tightened in commit `99fab29` ("fix(cloud): reject unknown fields on update schemas with HTTP 422"). All four update schemas (Citizen / Session / Measurement / User) now use `extra="forbid"`. The same PATCH today returns HTTP 422 with the offending field named in `loc`.

### 3. Scenario Q — extra audit row from scenario O

Q showed **4** audit rows for the new citizen, not the 3 the spec anticipated. The fourth is a `read` row from scenario O's unexpected-200 GET. Attribution is still correct (`actor_type='bhw'`), so this isn't a regression — but it does mean the count in the Q assertion will be unstable until O's behaviour is decided.

After the O fix in commit `7490ac5`, the soft-deleted GET returns 404 before `record_audit` runs, so a re-run of Q would see exactly 3 rows.

### 4. PATCH-update audit aggregation — L and M produced one row, not two

Looking at Q's output, there's **one** `update` row covering both PATCH operations (L set `full_name`; M tried `rfid_uid` and was silently ignored). Inspection of the code: `update_citizen()` only appends to `applied` if a non-protected field changes. M dropped `rfid_uid` at the Pydantic layer, so the handler saw `payload.model_dump(exclude_unset=True) == {}` and returned the citizen without writing an audit row. Two PATCH calls, one audit row — that's because M was effectively a no-op at the data layer. **PASS but worth knowing.**

After the M fix in commit `99fab29`, the PATCH with a forbidden field returns 422 before the handler runs, so it produces no audit row at all.

### 5. Audit row count vs. expected at scenario I

I returned `total=59`. Math: 44 seed + 1 admin login (A) + 1 bhw login (B) + 1 admin-fail (C) + 5×wrong_pw (D) + 5×unknown_user (D) + 1 BHW list (E) + 1 admin list (G) = 59. Scenario F's cross-barangay 404 wrote nothing (raises before `record_audit`); scenario H's 403 wrote nothing (`require_scope` rejects before the handler runs). That's the expected behaviour, and the math checks out.

### 6. R / S error message format

Both errors come back as `audit_log is append-only` raised by `audit_log_no_modify()` — the shared trigger function used by both the `audit_log_no_update` and `audit_log_no_delete` triggers. The CONTEXT line names the function but not the trigger. If you ever want the error to say which operation was blocked, give each trigger its own function. Cosmetic.

---

## Cleanup

- Uvicorn process killed (PID 116632).
- Postgres container left running (`Up 4 minutes (healthy)`); volume `ginhawa_postgres_data` not destroyed. Seeded data is intact for any follow-up manual investigation.
- No source files modified during the run.
- No commits made during the smoke test itself.
