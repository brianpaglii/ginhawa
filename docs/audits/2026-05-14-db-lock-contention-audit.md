# Kiosk SQLite write contention — "sync.db_locked_retrying"

Date: 2026-05-14
Scope: read-only. No code changed.

## Symptom

Bench journalctl on the kiosk shows the structured warning:

```json
{"error_type": "OperationalError",
 "error": "(sqlcipher3.dbapi2.OperationalError) database is locked
   [SQL: INSERT INTO audit_log (timestamp, actor_type, actor_id,
   action, object_type, object_id, ip_address, details, synced) ...",
 "event": "sync.db_locked_retrying", "level": "warning"}
```

Fires occasionally — never on every cycle, never bursty. No
follow-up "failed" or "gave up" event ever appears: the daemon
just sleeps the cycle interval and tries again next round.

---

## Section 1 — Database setup

### Engine + PRAGMAs

The engine is built once at boot via
[create_engine_for_kiosk](kiosk/src/ginhawa_kiosk/db/session.py#L33-L71):

```python
# kiosk/src/ginhawa_kiosk/db/session.py:49-54
engine = create_engine(
    f"sqlite:///{db_path}",
    module=sqlcipher3,
    connect_args={"check_same_thread": False},
    future=True,
)
```

The `connect` event applies just two pragmas per new connection
([db/session.py:56-69](kiosk/src/ginhawa_kiosk/db/session.py#L56-L69)):

- `PRAGMA key = '<key>'` (via
  [apply_sqlcipher_pragma at core/security.py:46](kiosk/src/ginhawa_kiosk/core/security.py#L46))
- `PRAGMA foreign_keys = ON`

**Not set anywhere in the codebase:**

- `journal_mode` — default is `DELETE` (rollback journal). Writers
  take an EXCLUSIVE lock that blocks all other readers and writers
  until commit. WAL mode would let readers continue while a writer
  is active and is the standard fix for SQLITE_BUSY contention.
- `busy_timeout` — default is `0`. SQLite returns `SQLITE_BUSY`
  immediately instead of internally retrying. A value of e.g.
  5000 ms would let SQLite absorb most transient contention
  without surfacing an exception to Python at all.
- `synchronous` — default `FULL`. Not relevant to the bug.

### Session factory + connection pool

```python
# kiosk/src/ginhawa_kiosk/db/session.py:86-91
return sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)
```

`autoflush=False` + `autocommit=False` means every implicit flush
on first DML opens a transaction that stays open until an explicit
`commit()` or `rollback()`.

### How many connections, where

The kiosk has **two** SQLAlchemy `Session` lifecycles backed by
the same engine pool:

- **Main-app session, long-lived.** Created at boot in
  [**main**.py:108](kiosk/src/ginhawa_kiosk/__main__.py#L108):

  ```python
  # __main__.py:107-108
  session_factory = make_session_factory(engine)
  db = session_factory()
  ```

  This single session is then handed to the FSM
  ([**main**.py:132](kiosk/src/ginhawa_kiosk/__main__.py#L132)),
  the main window
  ([**main**.py:142](kiosk/src/ginhawa_kiosk/__main__.py#L142)),
  and the citizen-lookup closure
  ([**main**.py:134-137](kiosk/src/ginhawa_kiosk/__main__.py#L134-L137)).
  It lives for the whole kiosk uptime.

- **Sync-daemon session, per cycle.** Built inside `run_once`
  ([sync/daemon.py:333](kiosk/src/ginhawa_kiosk/sync/daemon.py#L333)):

  ```python
  # sync/daemon.py:333
  with self._session_factory() as session:
      ...
      record_audit(session, action="sync_attempt", ...)
      session.commit()
  ```

  Created at the top of every 30 s cycle, closed at the bottom.

Both sessions share the same `Engine` (and therefore the same
SQLAlchemy connection pool) but use distinct ORM `Session`
instances. With `check_same_thread=False` on the SQLite driver
the pool can hand them distinct connections — i.e. two open
file-level handles into the same encrypted SQLite database.
Multiple connections + DELETE journal mode + busy_timeout=0 is
the exact recipe for the observed `SQLITE_BUSY`.

---

## Section 2 — Concurrent writers

### Main-app side (single shared session)

The shared session writes from many places. Almost every path
commits immediately after, so the open-transaction window is
short — _except_ for path-complete and report-screen entry; see
Section 4. All paths land in the same single session, so they
serialise _within_ the main app; the contention is exclusively
with the sync daemon.

- **Citizen registration**
  ([main_window.py:745-758](kiosk/src/ginhawa_kiosk/gui/main_window.py#L745-L758))
  — INSERT into `citizens` + audit row, commit at end.
- **Measurement persistence**
  ([main_window.py:991-1009](kiosk/src/ginhawa_kiosk/gui/main_window.py#L991-L1009))
  — INSERT into `measurements` + audit row (via
  `fsm.measurement_captured` →
  [session_fsm.py:773-778](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L773-L778)),
  commit at line 1009.
- **FSM transitions**
  ([session_fsm.py:\_record_audit at 842-858](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L842-L858))
  — every transition writes one audit row via `record_audit`,
  which `db.flush()`es. The caller (main window's user-action
  handler) is responsible for the commit. The handlers at
  [main_window.py:760-834](kiosk/src/ginhawa_kiosk/gui/main_window.py#L760-L834)
  commit after each trigger.
- **Sessions row create / update**
  ([session_fsm.py:796-825](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L796-L825),
  [827-834](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L827-L834))
  — INSERT on path entry, UPDATE on finalisation. Same caller-
  commits-it pattern.

Audit-log writes are documented at
[services/audit.py:80-110](kiosk/src/ginhawa_kiosk/services/audit.py#L80-L110)
— `record_audit` only flushes, never commits:

```python
# services/audit.py:97-110
entry = AuditLog(
    timestamp=datetime.now(timezone.utc).isoformat(),
    actor_type=actor_type, actor_id=actor_id, action=action,
    object_type=object_type, object_id=object_id,
    ip_address=ip_address,
    details=json.dumps(dict(details)) if details is not None else None,
    synced=0,
)
db.add(entry)
db.flush()
return entry
```

The caller MUST commit. Every kiosk-side caller does — the contract
is upheld — but the gap between flush and commit is where the
write lock lives.

### Sync-daemon side (per-cycle session)

Every 30 s, on the same asyncio loop, the daemon's `run_once`:

1. Reads pending rows (`_fetch_unsynced_citizens` /
   `_sessions` / `_measurements`,
   [daemon.py:142-178](kiosk/src/ginhawa_kiosk/sync/daemon.py#L142-L178)).
2. Sends to cloud (network I/O — releases the asyncio loop).
3. UPDATEs `synced=1` on accepted rows
   ([daemon.py:208-209](kiosk/src/ginhawa_kiosk/sync/daemon.py#L208-L209)).
4. INSERTs one `audit_log` row with `action="sync_attempt"`
   ([daemon.py:350-361](kiosk/src/ginhawa_kiosk/sync/daemon.py#L350-L361)).
5. `session.commit()`.

Step 4 is the INSERT in the failing log message.

### No other writers

There is no background timer that writes (the FSM's auto-timer at
[main_window.py:629-640](kiosk/src/ginhawa_kiosk/gui/main_window.py#L629-L640)
fires user-action handlers which commit in their own paths). No
subprocess. No second python interpreter. **The only contenders
for the write lock are the main session and the sync session.**

---

## Section 3 — Sync worker analysis

### Where it runs

`SyncDaemon.run`
([sync/daemon.py:266-305](kiosk/src/ginhawa_kiosk/sync/daemon.py#L266-L305))
is launched as a `loop.create_task(...)` in
[**main**.py:192](kiosk/src/ginhawa_kiosk/__main__.py#L192)
on the qasync event loop. **Same thread, same loop, as the GUI
and the FSM.** It is not a subprocess and not a worker thread.

This matters: although main-session and sync-session are distinct
ORM `Session` objects, their `db.flush()` / `db.commit()` calls
are serialised by the single-threaded asyncio scheduler at the
_Python_ layer. They only race at the _SQLite_ layer, when both
sides hold their own file connection with an open transaction.

### The retry logic

The `db_locked_retrying` log is emitted at
[sync/daemon.py:297-301](kiosk/src/ginhawa_kiosk/sync/daemon.py#L297-L301):

```python
# sync/daemon.py:284-301 (abbreviated)
except OperationalError as exc:
    # The contention is transient — the next cycle (interval
    # seconds away) is the retry, so we just log + continue here.
    self._logger.warning(
        "sync.db_locked_retrying",
        error_type=type(exc).__name__,
        error=str(exc)[:200],
    )
# ... falls through to wait_for(_stop, timeout=self._interval) ...
```

So the retry is:

- **Not** exponential backoff. **Not** capped. **Not** logging a
  give-up event.
- The "retry" is literally the next 30 s cycle. If contention
  clears before then (it always has in observed runs), the next
  cycle just succeeds and no further trace is left.
- Each `run_once` call rolls back any partial work implicitly
  when the `with self._session_factory() as session:` context
  manager exits via exception
  ([daemon.py:333](kiosk/src/ginhawa_kiosk/sync/daemon.py#L333)
  uses SQLAlchemy 2.x context manager semantics: exception →
  rollback + close). So the cycle that failed leaves no
  half-committed state behind; the next cycle picks the same
  unsynced rows up cleanly.

**The observed log line is the retry-attempt log, not the
give-up log.** There is no give-up log because there is no
give-up: the daemon retries forever every 30 s until the kiosk
shuts down.

### Data-loss assessment

The failing INSERT was the daemon's own `sync_attempt` audit row.
That row records what the daemon was trying to do; if it fails to
write, **the lost information is "the daemon attempted a cycle at
T."** The actual sync work is unaffected:

- If the failure happened before the cloud calls (step 1 above) —
  no data left the kiosk; the next cycle re-reads the same
  unsynced rows.
- If it happened after the cloud calls but before the local
  `UPDATE synced=1` — the cloud has the records but the kiosk
  thinks it doesn't, so they re-upload next cycle; the cloud's
  sync endpoints handle that idempotently (status
  `conflict_stale` →
  [sync/daemon.py:73-75](kiosk/src/ginhawa_kiosk/sync/daemon.py#L73-L75)
  marks them synced=1 anyway, no loop).
- If the failure happened at the audit INSERT (the actual error
  in the log) — the data writes ahead of it in the same
  transaction also roll back because they're all in one
  transaction up to `session.commit()` at
  [daemon.py:361](kiosk/src/ginhawa_kiosk/sync/daemon.py#L361).
  Same recovery: next cycle re-reads the unsynced rows.

So no row of `audit_log` is lost. The information that "a sync
cycle attempted but couldn't write its audit at time T" is
shed — but the cloud-side audit (written at sync-receipt time on
the cloud, see
[services/audit.py:31-41](kiosk/src/ginhawa_kiosk/services/audit.py#L31-L41))
fills that gap whenever the kiosk does succeed. The local kiosk
audit_log is forensic-only (ADR-0016) and the cloud's audit row
is the canonical sync record.

---

## Section 4 — Failure-mode trace

The plausible window where the main session holds an open write
transaction long enough to collide with a 30 s sync cycle:

1. Citizen has just finished MEASURING_VITALS / MEASURING_ANTHRO.
   The last measurement's `_on_measurement_proposed_event`
   committed at
   [main_window.py:1009](kiosk/src/ginhawa_kiosk/gui/main_window.py#L1009).
2. `_on_measurement_persisted`
   ([main_window.py:1014](kiosk/src/ginhawa_kiosk/gui/main_window.py#L1014))
   calls `_maybe_advance_measurement_path`
   ([main_window.py:1020-1030](kiosk/src/ginhawa_kiosk/gui/main_window.py#L1020-L1030))
   which fires `fsm.measurement_path_complete()`.
3. The FSM transition writes one audit row via
   `record_audit` → `db.flush()`
   ([session_fsm.py:842-858](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L842-L858)).
   The flush opens an implicit transaction on the main session's
   connection.
4. **No commit fires after the FSM transition.** The state-change
   handler `_on_fsm_state_changed`
   ([main_window.py:429-450](kiosk/src/ginhawa_kiosk/gui/main_window.py#L429-L450))
   updates the screen and fires sensor-related events, but never
   calls `self._db.commit()`. The audit row sits in the open
   transaction, holding the EXCLUSIVE write lock.
5. The kiosk is now on the REPORT screen. The next commit will be
   when the citizen taps Print or Finish (handlers at
   [main_window.py:778-813](kiosk/src/ginhawa_kiosk/gui/main_window.py#L778-L813)
   — both call `self._db.commit()`).
6. The REPORT auto-timeout is 60 s
   ([main_window.py:82](kiosk/src/ginhawa_kiosk/gui/main_window.py#L82)).
   So the open transaction can last anywhere from a few seconds
   (citizen taps immediately) to a full minute (citizen reads the
   report, then auto-timeout fires).
7. The sync daemon's 30 s cycle fires inside that window. Its
   `_apply_results` UPDATE on `citizens` / `sessions` /
   `measurements` (line 209) or its `sync_attempt` audit INSERT
   (line 350) hits SQLITE_BUSY — `busy_timeout=0` means it
   surfaces immediately.
8. The `except OperationalError` catch
   ([daemon.py:284-301](kiosk/src/ginhawa_kiosk/sync/daemon.py#L284-L301))
   logs `sync.db_locked_retrying`. The `with session_factory()`
   context exits with rollback; no half-write persists.
9. 30 s later the next cycle starts. The citizen has by then
   tapped Print/Finish (or REPORT timed out, which calls
   `_on_finish_without_printing` →
   [main_window.py:803-813](kiosk/src/ginhawa_kiosk/gui/main_window.py#L803-L813)
   → commit). The main session has released the lock; the
   daemon's next cycle commits cleanly.

That matches the observed pattern: occasional, transient,
self-healing.

The same window opens (more briefly) on MEASURING_VITALS →
MEASURING_ANTHRO transitions: `measurement_path_complete` fires
the same audit-row-without-commit. But that interval is bounded
by the citizen walking from the cuff to the scale (seconds, not
a minute), so the contention is much rarer there. The REPORT
window is the main culprit.

---

## Section 5 — Root cause hypothesis

**Selected: C with A as the structural amplifier.**

The proximate cause is **(C) busy_timeout is not set**. SQLite
returns `SQLITE_BUSY` the first instant it sees the lock held,
even though the contention would clear in tens of milliseconds in
the no-citizen-on-screen case and within seconds in the REPORT
case. A `busy_timeout` of 5000 ms would let SQLite's own retry
absorb almost every collision before the Python layer ever sees
an exception.

The structural amplifier is **(A) WAL not enabled**. In WAL mode,
readers don't block writers and one writer doesn't block readers
from other transactions; the kind of brief overlap the kiosk
produces would not raise `SQLITE_BUSY` at all in many cases —
only write-vs-write needs the lock, and SQLite uses a
short-lived locking handshake rather than the EXCLUSIVE lock of
rollback journal mode. WAL is the standard recommended mode for
multi-connection SQLite applications and would compose cleanly
with `busy_timeout`.

Secondary contributors:

- **(B) Open transaction across state changes.** The main session
  flushes an audit row on every FSM transition but does not commit
  until the next user-action handler runs. On the REPORT screen
  that's a 0-60 s gap during which the connection holds the
  EXCLUSIVE write lock under DELETE-mode SQLite. Smoking gun:
  [session_fsm.py:\_record_audit at 842-858](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L842-L858)
  flushes; no caller commits after `measurement_path_complete`
  fires from `_maybe_advance_measurement_path`
  ([main_window.py:1020-1030](kiosk/src/ginhawa_kiosk/gui/main_window.py#L1020-L1030)).
  This is the _duration_ of the lock; (C) and (A) together
  determine whether that duration causes a _visible_ error.
- **(E)** is borderline: there ARE two SQLAlchemy sessions, and
  they do not coordinate transactions. But that's by design — the
  sync daemon's session lifecycle is correct, and the main app's
  single shared session is correct. The collision is at the
  SQLite layer, not the SQLAlchemy layer.

**Not the cause:**

- **(D)** Sync worker fighting itself: the daemon's single
  per-cycle session writes one transaction; the audit row is
  added to that same session, not a separate one. No
  self-contention. The error sits in the same transaction as the
  `_apply_results` UPDATEs and commits or rolls back as one unit.
- **(F)** FK cascades: only relevant if the daemon's UPDATE
  triggered a cascade write into a parent table, which schema
  doesn't. The audit_log INSERT in particular has no FK that
  cascades back to anything.

Smoking-gun configuration lines (what's _missing_ matters here):

```python
# kiosk/src/ginhawa_kiosk/db/session.py:56-69 — entire connect hook
@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, _connection_record):
    apply_sqlcipher_pragma(dbapi_conn, key)
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()
# No PRAGMA journal_mode = WAL.
# No PRAGMA busy_timeout = <N>.
```

---

## Section 6 — Recommended fix sketch

**No code in this section.**

### Posture

This is a **performance / log-noise issue, not a data-integrity
bug.** Section 3's data-loss assessment shows the retry succeeds
on the next 30 s cycle in every observed case, and the
`with session_factory()` context cleanly rolls back the failed
cycle. Nothing of value is lost. But the warning is noisy and
the panel-defense story is weaker without a deliberate fix.

### Direction

The cheap, durable, well-understood fix is two pragmas in the
existing `_on_connect` hook
([db/session.py:56-69](kiosk/src/ginhawa_kiosk/db/session.py#L56-L69)):

- `PRAGMA journal_mode = WAL` — concurrent reader/writer
  semantics. The only operational concern is that WAL produces
  side files (`-wal`, `-shm`) alongside the main `.db`; backup
  and provisioning need to keep them together. SQLCipher
  supports WAL natively (it's the recommended mode upstream for
  SQLCipher ≥ 3.x and our `sqlcipher3` driver respects it). The
  Pi's filesystem is local ext4, not NFS, so WAL is safe.
- `PRAGMA busy_timeout = 5000` — gives SQLite 5 s to absorb a
  collision internally before surfacing `SQLITE_BUSY` to Python.
  Across the kiosk's actual contention windows (sub-second
  except during REPORT), 5 s comfortably absorbs everything; the
  daemon's `OperationalError` catch becomes a defence-in-depth
  guard rather than a routine path.

With both in place, the observed warning should disappear in
normal operation. The `except OperationalError` block in the
daemon should stay — it's the right defence-in-depth — but a
new follow-up event (`sync.db_locked_after_busy_timeout`) could
distinguish "real" contention from the routine sub-second
collisions, and an ADR would document the choice.

### Alternatives considered

- **Restructure transactions** so the FSM commits after every
  transition. Reasonable but invasive: every audit-row-only
  transition would commit, increasing SQLite write count
  several-fold per session. Out of proportion to the symptom.
- **Suppress the log.** Tempting since the retry succeeds, but
  it hides the underlying contention pattern from operators who
  rely on journalctl for kiosk health.
- **Force the main session to commit on REPORT entry.** Targeted
  at the worst window. Doable, but treats the symptom; the
  pragmas treat the cause and benefit every other transition
  too.
- **Tear down + recreate the main session for each user action.**
  Would force commits but adds connection-churn cost and
  complicates the FSM's mid-transition state. Reject as
  overkill.

### Worth-an-ADR factors

Both pragmas are commissioning-level decisions worth a brief
ADR (sibling to the ADR-0008-ish key derivation choice). Notes
to include:

- WAL's filesystem side files and the backup implications.
- `busy_timeout=5000` justification (long enough to absorb the
  REPORT-screen window, short enough to not mask a real deadlock).
- The daemon's existing `OperationalError` catch remains as
  defence-in-depth; the structured warning becomes the
  rare-event marker rather than a routine cycle marker.
- An audit-trail story note that all current data-integrity
  guarantees (single-writer-of-audit_log application, encrypted
  at rest, ADR-0016 forensic-only local audit) are preserved.

---

## Section 7 — Impact and urgency

- **Data integrity:** ✅ intact. No row of `audit_log` lost.
  No data-bearing row lost. Section 3 walks the rollback /
  re-read mechanics.
- **Citizen-facing UX:** ✅ no visible effect. The warning is
  daemon-side; the GUI is unaffected because the main session's
  commit is what holds the lock and the daemon is the one that
  gets blocked.
- **Cloud sync correctness:** ✅ intact. A failed cycle's rows
  stay `synced=0` and re-upload on the next pass; the cloud's
  idempotent `conflict_stale` path handles double-uploads.
- **Operator noise:** ⚠️ moderate. The journalctl warning fires
  several times per busy bench day. Operators tracing a real
  problem have to mentally filter these out.
- **Defense readiness:** ⚠️ a panel question along the lines of
  _"how do you handle concurrent DB access?"_ currently has no
  documented answer. A two-line pragma fix plus a brief ADR
  changes that to a clean answer.

### Suggested priority

**Fix post-defense, but before any field deployment.** The
warning is benign on the bench (no data lost, retry succeeds)
and the demo / defense doesn't require the fix to be in place.
But a field deployment with the kiosk running for weeks at a
time will accumulate enough log noise to mask real issues, and
the WAL pragma is the kind of thing that's much cheaper to ship
before commissioning than after (the journal-mode change
affects the on-disk format; doing it on a populated database is
trivial but takes a checkpoint).

The fix is small enough (one `PRAGMA journal_mode = WAL`, one
`PRAGMA busy_timeout = 5000`, both inside the existing
`_on_connect` hook at
[db/session.py:56-69](kiosk/src/ginhawa_kiosk/db/session.py#L56-L69))
to land in a single PR with an ADR and one unit test that
verifies the pragmas are issued on a fresh connection.

---

## Cross-references

- [services/audit.py:80-110](kiosk/src/ginhawa_kiosk/services/audit.py#L80-L110)
  — `record_audit` flush-only contract.
- [sync/daemon.py:266-305](kiosk/src/ginhawa_kiosk/sync/daemon.py#L266-L305)
  — `run` loop + `OperationalError` catch.
- [session_fsm.py:842-858](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L842-L858)
  — FSM `_record_audit` (flush-only, caller commits).
- [main_window.py:429-450](kiosk/src/ginhawa_kiosk/gui/main_window.py#L429-L450)
  — `_on_fsm_state_changed` (no commit after FSM-internal
  transitions).
- ADR-0016 — kiosk audit_log is local forensic-only.
- Previous audits in this series:
  [2026-05-13-scale-prefiring-audit.md](2026-05-13-scale-prefiring-audit.md),
  [2026-05-13-bp-stale-readings-audit.md](2026-05-13-bp-stale-readings-audit.md),
  [2026-05-13-scale-stale-readings-audit.md](2026-05-13-scale-stale-readings-audit.md).
