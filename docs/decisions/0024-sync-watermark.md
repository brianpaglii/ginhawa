# ADR 0024: Kiosk-to-cloud sync watermark via `last_synced_at`

- **Status:** Accepted
- **Date:** 2026-05-14
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira
- **Related:** ADR-0016 (kiosk audit_log forensic-only),
  ADR-0021 (SQLite WAL + busy_timeout). Motivating audit:
  [`docs/audits/2026-05-14-session-sync-create-update-gap-audit.md`](../audits/2026-05-14-session-sync-create-update-gap-audit.md).
  First sync-layer fix in the cross-system data-integrity series
  (audits 1–5 covered sensor receipt-boundary contamination at
  the kiosk's input boundary).

## Context

The kiosk sync daemon's row-selection used `WHERE synced=0`
exclusively. After first successful upload the daemon flipped
`synced=1` and never reconsidered the row. The kiosk's four
session-mutation sites (FSM `_after_aborted`,
`_after_path_selected`, `_after_error`,
`_finalise_session_completed`) bumped `updated_at` but didn't
re-flag for sync. Result: every UPDATE was invisible to the
daemon, and every session on the cloud was frozen at create-time
state.

Bench evidence on 2026-05-14 showed 58 of 58 completed kiosk
sessions still displaying `status='in_progress'` on the cloud,
with cloud `updated_at` 75 s behind the kiosk's local mutation
time. The BHW portal — which renders `status` verbatim — showed
the entire deployment as a wall of in-progress sessions.

The audit confirmed:

- The cloud already supports upsert-by-id with `conflict_stale`
  rejection by `updated_at` (`POST /api/v1/sync/sessions`,
  [`cloud/src/ginhawa_cloud/api/sync_sessions.py:99-139`](../../cloud/src/ginhawa_cloud/api/sync_sessions.py#L99-L139)).
  No cloud change is needed.
- The kiosk daemon's HTTP method (always POST) is correct —
  POST against the cloud's upsert endpoint is the right call.
- The gap lives purely in the kiosk's row-selection contract.

## Decision

Each synced table on the kiosk (`citizens`, `sessions`,
`measurements`) gains a nullable `last_synced_at` column
recording the row's `updated_at` value at the moment of the most
recent successful upload. The sync daemon's row-selection
becomes:

```sql
WHERE last_synced_at IS NULL
   OR last_synced_at < updated_at
```

After a successful upload, the daemon stamps
`last_synced_at = the updated_at value the row had AT FETCH
TIME`. Critically NOT the row's current `updated_at` at stamp
time — that value may have been mutated by concurrent FSM
activity between fetch and stamp.

The legacy `synced` integer column stays in the schema for
backward compatibility but is no longer consulted by the
daemon's row-selection. `_apply_results` still sets `synced=1`
alongside the watermark stamp. The column is deprecated; a
follow-up PR will remove it.

## Race-free property

The fetch returns `(row, updated_at_at_fetch)` tuples and the
stamp captures the fetch-time value rather than re-reading the
DB at stamp time. Concurrent FSM mutation across the upload
round-trip is the case the watermark closes:

1. Daemon fetches row R; captures `updated_at = T1`.
2. Daemon POSTs the row to the cloud.
3. **Concurrent FSM mutates R, bumping `updated_at` to T2
   (T2 > T1).**
4. Cloud accepts the upload, returns `created`/`updated`.
5. `_apply_results` stamps `R.last_synced_at = T1`. (Not T2 —
   that's the load-bearing detail.)
6. Next daemon cycle: predicate `last_synced_at < updated_at`
   reduces to `T1 < T2` → row is re-selected. ✓

If step 5 had stamped `R.updated_at` (reading the current DB
value), the FSM's T2 mutation would have been silently
consumed and the row would never resync. Capturing-at-fetch is
the load-bearing detail.

## Alternatives considered

- **Path C: flip `synced=0` at every UPDATE site.** Surgical —
  four lines added to four FSM transitions. Cheap to ship,
  brittle to maintain: any future UPDATE site must remember the
  rule, and one forgotten line silently reintroduces the gap.
  Rejected as the long-term answer; usable as a stopgap.
- **Path B: separate `dirty` flag.** Cleaner separation of
  concerns than C (decouples "known to cloud" from "needs
  resync") but has a subtle race: if a row is dirtied during a
  sync round-trip, naively resetting `dirty=0` after success
  loses the second mutation. Fixing the race requires a version
  counter or timestamp — at which point the design converges to
  Path A. Rejected.
- **Cloud-side UPSERT only.** Already done; the cloud's
  `_apply_update` path handles updates idempotently. Not a
  fix alone — the kiosk still has to re-send.

Path A is race-free by construction (the stamp captures the
fetch-time value), requires no rules at mutation sites (any
code path that bumps `updated_at` is automatically eligible
for resync), and the schema change is one nullable column.

## Migration behavior

The Alembic migration
[`c8a7e93d4f12_add_last_synced_at_watermark.py`](../../kiosk/alembic/versions/c8a7e93d4f12_add_last_synced_at_watermark.py)
adds `last_synced_at` as nullable with no backfill. Existing
rows have `last_synced_at=NULL`, which the new predicate
treats as "needs sync." On the first daemon cycle after
deployment, every existing kiosk row is re-pushed. The cloud's
existing upsert path handles them via `_apply_update`.

This is intentional: 58 already-completed sessions on the
bench kiosk currently have stale cloud state; the migration's
NULL backfill triggers a one-shot resync that brings the
cloud current. The cloud's `conflict_stale` check by
`updated_at` ensures the operation is idempotent if it runs
twice (a row whose cloud-side `updated_at` already matches the
kiosk's will be reported as `conflict_stale`, which
`_apply_results` treats as a terminal-OK and stamps anyway).

Estimated backfill volume per kiosk: ~172 sessions + N
citizens + M measurements. At the daemon's default batch size
(100) and 30 s cycle interval, full backfill completes in 1–2
cycles.

## Trade-offs

- **One new nullable column on three tables.** Mild migration,
  no data backfill needed.
- **Legacy `synced` column remains.** Briefly creates two ways
  to read "was this synced?" The daemon no longer uses it but
  any external reader (a SQL inspection script, a future BHW
  ops tool) might. Deprecated, removal in a follow-up PR keeps
  this commit minimal.
- **Fetch function signature changes** from `list[Model]` to
  `list[tuple[Model, str]]`. Three internal call sites in
  `daemon.py` updated. No external callers (verified via grep
  for `_fetch_unsynced_*` and `_fetch_pending_*`).
- **Append-only tables (citizens, measurements) gain a column
  they technically don't need.** Their `last_synced_at` is set
  once and the column never re-fires after first sync. Kept
  for schema uniformity — and for forward-compat with any
  future kiosk-side edit path on those tables, which would
  inherit the watermark behaviour automatically.
- **Existing rows are re-pushed once on first cycle after
  deployment.** Expected backfill flurry of 1–2 cycles. The
  cloud accepts these as `updated` (or `conflict_stale` if the
  cloud's `updated_at` already matches), so no data harm.

## Data integrity

Unchanged at the semantic level; strengthened operationally.

- **INSERT path** (unchanged shape): row created with
  `last_synced_at=NULL`, daemon picks up, cloud creates,
  daemon stamps `last_synced_at = fetched_updated_at`.
- **UPDATE path** (newly correct): FSM mutation bumps
  `updated_at`, daemon picks up on next cycle, cloud upserts
  (or returns `conflict_stale` if the kiosk somehow regressed
  `updated_at` — that's the cloud's invariant), daemon stamps
  the new `last_synced_at`.
- **Failed cycle path** (unchanged): `_apply_results` only
  stamps on `_TERMINAL_OK_STATUSES`; a `rejected` or
  `conflict_constraint` result leaves `last_synced_at`
  untouched, and the row is picked up again next cycle (which
  is the existing operational-visibility behaviour for stuck
  rows).

The daemon's `OperationalError` defence-in-depth from ADR-0021
remains — a SQLite write lock from a concurrent main-session
commit rolls back the failed cycle, leaving every row in its
pre-cycle state (`last_synced_at` not stamped) so the same
rows are re-attempted next cycle.

## Cross-references and pattern

This is the **first sync-layer fix** in the cross-system
data-integrity audit series. Audits 1–5 (ADRs 0019–0023)
addressed sensor receipt-boundary contamination — data
flowing from sensors into the kiosk. This ADR addresses
kiosk-to-cloud propagation correctness — data flowing from
the kiosk to the cloud.

The two pattern families:

- **Receipt-boundary defence for sensor freshness** — every
  always-on or store-and-forward sensor that can deliver data
  outside the citizen's capture window needs a session-relative
  gate at the kiosk's input boundary (audits 1, 2, 3, 5;
  ADRs 0019, 0020, 0022, 0023).
- **Eventual-consistency watermarks for partial-connectivity
  deployments** — every kiosk-to-cloud mutation must propagate
  via a freshness predicate the daemon can reliably evaluate
  (this ADR; future portal-driven invalidate flows; future
  kiosk-side citizen edits).

Both are paper-section-worthy on their own axes. The umbrella
ADRs are suggested as follow-up.

## Verification

Unit tests at
[`kiosk/tests/sync/test_resync_on_update.py`](../../kiosk/tests/sync/test_resync_on_update.py)
pin the watermark contract across 11 cases: never-synced
row, synced row with no update, synced row with subsequent
update, fetch-time snapshot, race-free stamp, FSM finalise /
abort / error triggers, append-only citizens, append-only
measurements, migration backfill, audit-row preservation.
The tests use the existing `pytest-httpx` mock to drive the
cloud transport without a real broker; `sqlcipher3` is the
DB driver on the Pi (tests skip on dev boxes that don't have
it installed).

mypy strict clean on
[`kiosk/src/ginhawa_kiosk/sync/daemon.py`](../../kiosk/src/ginhawa_kiosk/sync/daemon.py),
[`kiosk/src/ginhawa_kiosk/db/models.py`](../../kiosk/src/ginhawa_kiosk/db/models.py),
and the new test file.

Bench (on the Pi):

```bash
cd /opt/ginhawa/src/kiosk
uv run alembic upgrade head
sudo systemctl restart ginhawa-kiosk
sudo journalctl -u ginhawa-kiosk -f | \
  grep -iE "sync_attempt|sync.cycle_complete"
```

Expect within 1–2 minutes after restart:

- A flurry of `sync_attempt` and `sync.cycle_complete` events.
- Counter detail showing `sessions: {"updated": N}` for the
  backfill cycle (where N is the count of previously-frozen
  completed sessions).
- After backfill settles, normal "create on session start,
  update on session finish" cadence as new sessions complete.

Verify a specific session on the cloud:

```bash
sudo docker exec ginhawa-postgres psql -U ginhawa -d ginhawa -c \
  "SELECT id, status, ended_at, updated_at FROM sessions \
   WHERE id='29e697f9-5e1f-40e6-8707-c9817dd044c7';"
```

Expected: `status=completed`, `ended_at` populated,
`updated_at` matches the kiosk's local value.

Open the BHW portal: completed sessions should now show as
"Completed" with end times.

## References

- Audit: [`docs/audits/2026-05-14-session-sync-create-update-gap-audit.md`](../audits/2026-05-14-session-sync-create-update-gap-audit.md)
- Models: [`kiosk/src/ginhawa_kiosk/db/models.py`](../../kiosk/src/ginhawa_kiosk/db/models.py)
  (`Citizen.last_synced_at`, `Session.last_synced_at`,
  `Measurement.last_synced_at`).
- Migration: [`kiosk/alembic/versions/c8a7e93d4f12_add_last_synced_at_watermark.py`](../../kiosk/alembic/versions/c8a7e93d4f12_add_last_synced_at_watermark.py).
- Daemon: [`kiosk/src/ginhawa_kiosk/sync/daemon.py`](../../kiosk/src/ginhawa_kiosk/sync/daemon.py)
  (`_fetch_pending_citizens` / `_fetch_pending_sessions` /
  `_fetch_pending_measurements`, `_apply_results`).
- Cloud upsert (unchanged): [`cloud/src/ginhawa_cloud/api/sync_sessions.py:99-139`](../../cloud/src/ginhawa_cloud/api/sync_sessions.py#L99-L139).
- Tests: [`kiosk/tests/sync/test_resync_on_update.py`](../../kiosk/tests/sync/test_resync_on_update.py).
