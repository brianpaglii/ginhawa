# ADR 0021: SQLite WAL mode and busy_timeout for the kiosk DB

- **Status:** Accepted
- **Date:** 2026-05-14
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira
- **Related:** ADR-0008 (SQLCipher encryption at rest),
  ADR-0016 (kiosk audit_log is local forensic-only). Motivating
  audit: [`docs/audits/2026-05-14-db-lock-contention-audit.md`](../audits/2026-05-14-db-lock-contention-audit.md).

## Context

The kiosk runs two SQLAlchemy `Session` lifecycles backed by the
same SQLCipher-encrypted SQLite engine:

- A **long-lived main-app session** attached to the FSM and the
  `KioskMainWindow`, created at boot in
  `kiosk/src/ginhawa_kiosk/__main__.py`.
- A **per-cycle sync-daemon session** opened inside
  `SyncDaemon.run_once` every ~30 s in
  `kiosk/src/ginhawa_kiosk/sync/daemon.py`.

Both live on the same qasync event loop (the daemon is launched
as a `loop.create_task(...)`), but at the SQLite layer they hold
two distinct file connections.

Bench observation 2026-05-14: the sync daemon occasionally raised
`sqlcipher3.dbapi2.OperationalError: database is locked` while
issuing `INSERT INTO audit_log`. The audit traced the collision
to two missing PRAGMAs in
[`db/session.py`'s `_on_connect`](../../kiosk/src/ginhawa_kiosk/db/session.py)
hook:

- **No `journal_mode = WAL`.** SQLite defaulted to `DELETE`
  rollback-journal mode, where a writing transaction takes an
  EXCLUSIVE lock that blocks every other reader and writer until
  commit.
- **No `busy_timeout`.** SQLite defaulted to `0`, surfacing
  `SQLITE_BUSY` to Python the instant the lock was contended
  rather than retrying internally.

The collision window is widest on the REPORT screen. The FSM
flushes one audit row per transition via `services.audit.record_audit`,
which only flushes — the GUI commits at the next user-action
handler. After `measurement_path_complete` fires, the connection
holds an open write transaction until the citizen taps Print or
Finish (up to 60 s, bounded by the REPORT auto-timeout). Any sync
cycle that fires inside that window collided with the open
transaction.

The audit confirmed no data loss: the daemon's `with
session_factory()` context rolls back the failed cycle cleanly,
the same `synced=0` rows re-upload on the next cycle, and the
cloud's idempotent `conflict_stale` path absorbs any double
uploads. The symptom was log noise and an unmet defense-story
expectation, not a correctness issue.

## Decision

`db/session.py`'s `_on_connect` event hook now applies two
additional PRAGMAs after the existing `PRAGMA key` (via
`apply_sqlcipher_pragma`) and `PRAGMA foreign_keys = ON`:

```
PRAGMA journal_mode = WAL
PRAGMA busy_timeout = 5000
```

- **WAL** enables reader-writer concurrency. Readers see a
  consistent snapshot while a writer is active; only write-vs-
  write needs a brief locking handshake on commit, not the
  long-held EXCLUSIVE lock of rollback mode. SQLCipher supports
  WAL natively per upstream guidance and the kiosk's pinned
  `sqlcipher3` driver respects the setting.
- **busy_timeout = 5000** lets SQLite internally retry on
  `SQLITE_BUSY` for up to 5 s before surfacing the exception. The
  longest observed contention window (REPORT screen, sub-second
  to 60 s) is comfortably absorbed for the routine sub-second
  case; the `SyncDaemon.run`'s `except OperationalError` block
  remains as defence-in-depth for genuine multi-second deadlocks.

## Alternatives considered

- **Restructure FSM transitions to commit immediately.**
  Reasonable but invasive: every audit-row-only transition would
  open and close its own SQLite write transaction, multiplying
  write count several-fold per session. Out of proportion to the
  symptom. Reject.
- **Commit on REPORT entry only.** Treats the worst case but
  leaves the smaller MEASURING_VITALS → MEASURING_ANTHRO window
  unaddressed and adds a special-case commit in the GUI layer
  that has to be remembered on every screen refactor. The PRAGMA
  approach benefits every transition uniformly. Reject as
  weaker.
- **Tear down the main session per user action.** Excessive
  connection churn for no application-level benefit. Reject.
- **Suppress the `sync.db_locked_retrying` log.** Hides the
  contention pattern from operators who rely on journalctl for
  kiosk health monitoring. Reject.

The PRAGMA approach is the standard SQLite remediation,
well-understood, and the cheapest change that addresses both the
contention window's duration (WAL) and the routine collision's
visibility (busy_timeout).

## Trade-offs

- **WAL side files.** WAL produces `kiosk.db-wal` (transaction
  log) and `kiosk.db-shm` (shared memory index) alongside the
  main `kiosk.db`. Backup scripts must copy all three, or call
  `PRAGMA wal_checkpoint(TRUNCATE)` to flatten pending
  transactions into the main file first. Captured in
  [`kiosk/docs/runbook.md`](../../kiosk/docs/runbook.md) under
  "Backing up the encrypted database."
- **Filesystem requirements.** WAL requires the SQLite database
  to live on a local filesystem with proper mmap/lock support.
  The Pi's ext4 root meets this. WAL is unsafe on networked
  filesystems (NFS, SMB) — the kiosk doesn't use either, so this
  is a forward-looking constraint, not a current one.
- **Deadlock-vs-routine-contention distinction.** With
  `busy_timeout=5000` a genuine deadlock surfaces after 5 s
  rather than instantly. The 5 s budget is large enough to
  absorb every observed contention window (which clears in
  sub-seconds outside of REPORT, and the REPORT case clears at
  the citizen's tap or the auto-timeout) but small enough that
  a real deadlock isn't masked indefinitely. The sync daemon's
  existing `except OperationalError` block is the safety net.
- **In-place format change.** Switching to WAL on an existing
  database is a no-op on the next connect — SQLite handles the
  format flip transparently. No migration script needed; no
  downtime needed beyond the systemd service restart.

## Data integrity

Unchanged. The audit's Section 3 walk-through of the rollback
mechanics still applies — failed cycles roll back cleanly,
unsynced rows re-upload on the next cycle, and the cloud's
idempotent `conflict_stale` path absorbs duplicates. WAL +
busy*timeout \_reduce* the rate of failed cycles; they don't
change the rollback guarantees.

The encryption story (ADR-0008) is preserved: `PRAGMA key`
remains the first statement on every new connection, before any
journal-mode or busy-timeout PRAGMA executes. SQLCipher's WAL
support encrypts the `-wal` and `-shm` side files identically to
the main `.db`; no plaintext leaks to disk.

The audit-log immutability story (ADR-0016) is also preserved:
the application is still the sole writer of `audit_log` via
`services.audit.record_audit`, and the table has no UPDATE/DELETE
handlers in the application layer. WAL changes the _concurrency_
of writes, not their _origin_ or their _retention_.

## Verification

- Unit tests at
  [`kiosk/tests/db/test_pragmas.py`](../../kiosk/tests/db/test_pragmas.py)
  assert the three PRAGMAs (`foreign_keys=1`, `journal_mode=wal`,
  `busy_timeout=5000`) are issued on a fresh connection.
- Bench check: after restarting the kiosk service, the data
  directory contains `kiosk.db`, `kiosk.db-wal`, and
  `kiosk.db-shm`; the runbook's `sqlcipher`-CLI one-liner
  confirms `wal` / `5000` at runtime.
- Operational check: after several back-to-back vitals + anthro
  sessions, `journalctl -u ginhawa-kiosk | grep db_locked` should
  emit at most a single line in cases of genuine multi-second
  contention, and the steady-state warning observed before the
  fix should be absent.

## References

- Audit: [`docs/audits/2026-05-14-db-lock-contention-audit.md`](../audits/2026-05-14-db-lock-contention-audit.md)
- Code: [`kiosk/src/ginhawa_kiosk/db/session.py`](../../kiosk/src/ginhawa_kiosk/db/session.py)
  (`_on_connect` event hook).
- Test: [`kiosk/tests/db/test_pragmas.py`](../../kiosk/tests/db/test_pragmas.py).
- Runbook backup note: [`kiosk/docs/runbook.md`](../../kiosk/docs/runbook.md)
  "Backing up the encrypted database."
