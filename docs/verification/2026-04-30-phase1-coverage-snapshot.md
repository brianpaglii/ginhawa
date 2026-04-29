# Phase 1 Final Coverage Snapshot — 2026-04-30

**Date generated:** 2026-04-30
**Git commit:** `cf67ec4a9f82e8104e7247ed840e3baadd1b66a4`
**Subject:** `docs: add verification/ directory for structured test reports`
**Pytest command:**

```bash
DATABASE_URL=… JWT_SECRET=… uv run pytest \
    --cov=ginhawa_cloud \
    --cov-report=term-missing \
    --cov-report=json
```

(Run from `cloud/` after `rm -rf .pytest_cache .ruff_cache .coverage coverage.json htmlcov` and recursive `__pycache__` cleanup.)

## Headline

**Total project coverage: 97%** (969 statements, 28 missing). 94 tests
pass. No module in `api/`, `services/`, `core/`, or `db/` is below the
80% floor.

## Per-module coverage

Sorted ascending so modules to watch are at the top.

| Module                       | Statements |                                            Coverage |
| ---------------------------- | ---------: | --------------------------------------------------: |
| `api/_authz.py`              |         24 |                                             **83%** |
| `api/sessions.py`            |         98 |                                             **86%** |
| `api/users.py`               |         51 |                                             **90%** |
| `core/security.py`           |         67 |                                             **94%** |
| `db/models.py`               |         94 |                                                 99% |
| `__init__.py` (package root) |         14 |                                                100% |
| `api/__init__.py`            |         17 |                                                100% |
| `api/audit_log.py`           |         47 |                                                100% |
| `api/auth.py`                |         50 |                                                100% |
| `api/citizens.py`            |         83 |                                                100% |
| `api/health.py`              |          5 |                                                100% |
| `api/measurements.py`        |        116 |                                                100% |
| `api/schemas.py`             |        171 |                                                100% |
| `core/__init__.py`           |          0 |                                                100% |
| `core/config.py`             |         12 |                                                100% |
| `db/__init__.py`             |          0 |                                                100% |
| `db/base.py`                 |          3 |                                                100% |
| `db/session.py`              |          7 |        100% (body excluded by `# pragma: no cover`) |
| `scripts/__init__.py`        |          0 |                                                100% |
| `scripts/seed_dev_data.py`   |         99 | 100% (CLI surface excluded by `# pragma: no cover`) |
| `services/__init__.py`       |          0 |                                                100% |
| `services/audit.py`          |         11 |                                                100% |

## Modules under 95% — gap explanations

### `api/_authz.py` — 83% (lines 44, 54-56)

- **Line 44** — the 403 raise inside `assert_barangay_write`. Fires only when a BHW POSTs a `citizen` whose `barangay` differs from their `assigned_barangay`. The cross-barangay POST path is not tested directly; the test suite covers cross-barangay reads (404) and the BHW-list filter, but not the explicit-write 403.
- **Lines 54-56** — the cross-barangay branch of `assert_session_access` (the `if citizen is None or citizen.barangay != user.assigned_barangay` raise). No PATCH/GET-on-cross-barangay-session test exists, so this BHW path of the helper isn't exercised.

### `api/sessions.py` — 86% (lines 55, 140-143, 151, 160-161, 163-164, 166-167, 217, 234)

Six gaps, all in `list_sessions` and `update_session`:

- **Line 55** — 400 raise inside `create_session` for the BHW cross-barangay case ("citizen not found" indistinguishability). Not tested.
- **Lines 140-143** — ISO 8601 try/except for `started_after` / `started_before` query params. No malformed-timestamp test against `/api/v1/sessions`. (The same pattern is tested for `/api/v1/measurements` and `/api/v1/audit-log`, just not sessions.)
- **Line 151** — `barangay = current_user.assigned_barangay` BHW override in `list_sessions`. The BHW listing path isn't exercised.
- **Lines 160-161** — `?status=…` filter clause. No test passes `status` to `GET /api/v1/sessions`.
- **Lines 163-164, 166-167** — `started_after` / `started_before` filter clauses. No test passes either to `GET /api/v1/sessions`.
- **Line 217** — 404 raise in `update_session` for an unknown session id. No PATCH-on-unknown-session test.
- **Line 234** — empty-payload short-circuit (`if not changes: return session`). No PATCH-with-`{}` test.

### `api/users.py` — 90% (lines 50-52, 107, 114)

- **Lines 50-52** — `IntegrityError` rollback + 409 raise in `create_user`, fires on duplicate username. No duplicate-username test (the citizens version is tested but not the user version).
- **Line 107** — 404 raise in `update_user` for an unknown user id. No PATCH-on-unknown-user test.
- **Line 114** — empty-payload short-circuit (`if not changes: return user`). No PATCH-with-`{}` test on users.

### `core/security.py` — 94% (lines 133, 137, 164, 172)

All four are negative-path raises in token decoding / user lookup:

- **Line 133** — `raise CredentialsError("token missing subject")`. Triggered by a JWT that decodes successfully but has no `sub` claim. No test forges such a token.
- **Line 137** — `raise CredentialsError("token scopes must be a list")`. Triggered by a JWT whose `scopes` claim is the wrong type. No test forges this either.
- **Line 164** — `raise _credentials_401("user not found")` in `get_current_user`. Triggered by a token whose `sub` references a deleted user. No test deletes a user mid-session and then reuses the prior token.
- **Line 172** — `raise _credentials_401("inactive user")` in `get_current_active_user`. Same shape: a token issued before the user was deactivated. The login-flow `is_active` check is tested at `/login`, but the post-issue inactivity case isn't.

## Why this is steady state, not a regression

Every gap above is a real-behaviour edge that no test currently triggers, not a deletion of test coverage. The Phase 1 functional smoke test ([phase1_smoke_test_path_a.md](phase1_smoke_test_path_a.md)) exercised these paths empirically (cross-barangay scoping, login flows, audit attribution) but not via the test suite, so the gaps survive into post-smoke-test coverage. They are tracked here as Phase 1.5 follow-ups.

The four "non-negotiable" Phase 1 contracts — citizens CRUD, sessions lifecycle, measurements capture/invalidate, audit-log read with admin scope — are at **100%** coverage on their respective modules.

## Files not committed (per the convention noted in `README.md`)

Generated alongside this snapshot but ignored by `.gitignore`:

- `cloud/.coverage` — coverage tool's binary database
- `cloud/coverage.json` — JSON export from `--cov-report=json`
- `cloud/htmlcov/` — would be created by `--cov-report=html` (we didn't generate it this run, but ignoring preemptively for future runs)

These are local artifacts; only this markdown snapshot is the durable record.
