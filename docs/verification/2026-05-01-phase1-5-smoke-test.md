# Phase 1.5 smoke test results

Generated: 2026-05-01T13:54:44+00:00

## Setup

```
docker compose down -v && docker compose up -d postgres
uv run alembic upgrade head
uv run python -m ginhawa_cloud.scripts.seed_dev_data
DATABASE_URL=... JWT_SECRET=... uv run uvicorn ginhawa_cloud:app \
    --host 127.0.0.1 --port 8765
```

Captured from seed output:

- `device_id`: `00000000-0000-0000-0000-000000000401`
- `api_key`: `seed_kiosk_api_key_DO_NOT_USE_IN_PROD` (DEV ONLY)

Migrations applied (head): `b3f7a92c0d8e — add updated_at to sessions
and measurements`. Five migrations total (initial schema → drop
audit triggers → device_credentials → kiosk actor_type → updated_at).

This run covers the sync scenarios that were N/A in the Phase 1 (Path
A) run because the kiosk-side endpoints did not yet exist. The Path A
report stays in place for the BHW/admin CRUD coverage.

## PS1 — sync citizens, new (two new citizens)

status: `200`
results: `{'results': [{'id': 'd5e29be0-3506-4760-a5b9-1c9eacf67976', 'status': 'created', 'error': None}, {'id': 'ae8fc2bf-4195-4f99-82fa-c7605f6213be', 'status': 'created', 'error': None}]}`

verdict: **PASS**

DB rows in citizens for the two ids: `2` (expected `2`)
DB audit rows actor_type=kiosk for the two ids: `2` (expected `2`)

DB verdict: **PASS**

## PS2 — sync citizens, idempotent replay

status: `200`
results: `{'results': [{'id': 'd5e29be0-3506-4760-a5b9-1c9eacf67976', 'status': 'conflict_stale', 'error': 'incoming updated_at ... is not newer than stored ...'}, {'id': 'ae8fc2bf-4195-4f99-82fa-c7605f6213be', 'status': 'conflict_stale', 'error': 'incoming updated_at ... is not newer than stored ...'}]}`

DB rows after replay: `2` (expected `2`, unchanged)

verdict: **PASS**

## PS3 — sync citizens, self-service registration

status: `200`
results: `{'results': [{'id': 'a415d8ce-eab8-494f-a55c-c60d60c0b587', 'status': 'created', 'error': None}]}`

audit row (actor_type|actor_id|details):

```
kiosk|00000000-0000-0000-0000-000000000401|{"registration_type": "self_service", "rfid_uid": "SMOKE_91012642", "barangay": "Tibagan"}
```

verdict: **PASS**

## PS4 — sync sessions, valid citizen

status: `200`
results: `{'results': [{'id': '21ee668d-bcc8-4c29-a9f1-c4467a0e2a18', 'status': 'created', 'error': None}]}`

verdict: **PASS**

## PS5 — sync sessions, FK violation (unknown citizen)

status: `200`
results: `{'results': [{'id': '05608ab0-d164-4536-9249-adcf93553391', 'status': 'rejected', 'error': 'citizen_not_found'}]}`

verdict: **PASS**

## PS6 — sync measurements, valid session

status: `200`
results: `{'results': [{'id': '9973311a-660f-4654-8d09-2141dfcd99e8', 'status': 'created', 'error': None}]}`

verdict: **PASS**

## PS7 — sync measurements, out-of-range (systolic_bp=300)

status: `200`
results: `{'results': [{'id': '6987e599-7f4c-47ff-9acd-0e2940f0b1e9', 'status': 'created', 'error': None}]}`

DB row (is_valid|validation_notes):

```
0|systolic_bp value 300.0 outside physiological range [70.0, 250.0]
```

The kiosk POSTed `is_valid=1`; the cloud overrode to `0` because the
value is outside the physiological range. The row is preserved (not
rejected) — the kiosk's clinical decision to capture the reading is
honoured, and the cloud's validity decision is recorded alongside.

verdict: **PASS**

## PS8 — revoked credential returns 401

revoke status: `200`
sync attempt status: `401`
sync attempt body: `{'detail': 'invalid kiosk credential'}`

`get_current_kiosk` filters on `revoked_at IS NULL` before the
verify_password loop, so a revoked credential cannot authenticate
even with the correct plaintext key.

verdict: **PASS**

## PS9 — constant-time auth

A fresh credential was created via the admin POST endpoint to recover
a usable key after PS8's revocation.

```
recreated credential device_id: de4a27b0-54a9-436b-a00d-ee1f2df8f717
```

5 valid-key requests vs 5 invalid-key requests:

```
mean valid:   94.9 ms (samples: 97.2, 82.3, 94.7, 99.6, 101.0)
mean invalid: 83.6 ms (samples: 86.8, 67.3, 89.7, 78.9, 95.3)
delta:        11.3 ms (target: within ~50 ms)
```

The valid path runs verify_password against every active credential
(including the matching one); the invalid path also runs against
every active credential (no early exit on first match), so the work
done is determined by the population size, not by whether or where a
match is found.

The 11.3 ms gap is well inside the 50 ms target. Note: the valid path
also performs a `SELECT ... last_seen_at = ...; COMMIT` after a
successful match, which adds a small fixed cost — that's why the
valid mean is slightly _higher_ than the invalid mean rather than
lower (the post-auth bookkeeping is not expensive enough to widen the
gap meaningfully).

verdict: **PASS**

## PS10 — oversize batch (501 records)

status: `413`
detail: `batch size 501 exceeds maximum of 500 records per request`

verdict: **PASS**

## Summary

| Scenario                                       | Result |
| ---------------------------------------------- | ------ |
| PS1 — sync citizens, new                       | PASS   |
| PS2 — sync citizens, idempotent                | PASS   |
| PS3 — sync citizens, self-service registration | PASS   |
| PS4 — sync sessions, valid                     | PASS   |
| PS5 — sync sessions, FK violation              | PASS   |
| PS6 — sync measurements, valid                 | PASS   |
| PS7 — sync measurements, out-of-range          | PASS   |
| PS8 — revoked credential 401                   | PASS   |
| PS9 — constant-time auth                       | PASS   |
| PS10 — oversize batch 413                      | PASS   |

**Overall: PASS (10/10)**
