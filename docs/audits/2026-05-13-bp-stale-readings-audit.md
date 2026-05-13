# Omron BP cuff — stale readings across consecutive sessions

Date: 2026-05-13
Scope: read-only. No code changed.

## Symptom

In back-to-back `vitals_only` sessions, the BP triple persisted
for session 2 is sometimes session 1's measurement. Three
observed shapes:

1. Session 1's BP value re-appears as session 2's BP value on the
   REPORT and in the DB.
2. Session 2's freshly-taken BP never reaches the kiosk.
3. The kiosk's BP triple is published _before_ the citizen has
   finished pressing START on the cuff for the new session.

All three traces collapse to the same root cause (see Section 4).

---

## Section 1 — BLE connection lifecycle

The OmronBpSensor uses a **one-connect-per-measurement** model.
There is no persistent connection across a session, let alone
across sessions.

- The handler entry is `_handle_request`:
  [omron_bp.py:470-514](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L470-L514).
- The connect retry loop is `_read_notifications_until_fresh`:
  [omron_bp.py:516-659](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L516-L659).
- Each retry constructs a **fresh** `BleakClient(mac)`:
  [omron_bp.py:607](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L607),
  connects ([line 609](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L609)),
  starts notifies ([line 636](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L636)),
  drains ([line 637](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L637)),
  and disconnects in a `finally`
  ([omron_bp.py:638-646](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L638-L646)).
- Once a fresh reading is captured the loop returns and the outer
  `_handle_request` publishes the BP triple and exits
  ([omron_bp.py:514](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L514)).
- The handler is invoked exactly once per `BpMeasurementRequested`
  event. `main_window` fires that event on every entry into
  `MEASURING_VITALS`
  ([main_window.py:564](kiosk/src/ginhawa_kiosk/gui/main_window.py#L564)).

So at the **adapter level** the kiosk-side BLE connection is
torn down cleanly between sessions. The contradiction is that the
**cuff itself** is a store-and-forward device whose internal
notification buffer is _not_ cleared by the kiosk disconnecting
— the cuff keeps whatever it most recently measured until the
citizen presses START again to take a new one
([omron_bp.py:29-43](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L29-L43)).

Stop / start path:

- `start()` subscribes to `BpMeasurementRequested` and
  `BpMeasurementRequestCancelled`
  ([omron_bp.py:433-436](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L433-L436)).
- `stop()` only flips `_running=False`
  ([omron_bp.py:451-454](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L451-L454)).
  In production `stop()` is only called at app shutdown.

Cancellation path:

- Main window publishes `BpMeasurementRequestCancelled` on every
  exit from `MEASURING_VITALS`, including REPORT after a
  successful publish:
  [main_window.py:483-498](kiosk/src/ginhawa_kiosk/gui/main_window.py#L483-L498).
- The sensor's handler is
  `_on_cancellation_requested`
  ([omron_bp.py:439-449](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L439-L449))
  which sets `self._cancel_event`.
- The retry loop checks the event before every reconnect and at
  the top of every `_drain_until_fresh` iteration
  ([omron_bp.py:586](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L586),
  [705](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L705)).
- On the next session's `BpMeasurementRequested` the handler
  clears the cancel event before doing anything else
  ([omron_bp.py:490](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L490)),
  so a leftover-from-last-exit cancel does not poison the new
  request. **The handler state is properly reset between
  sessions.**

---

## Section 2 — Freshness gate semantics

The "fresh" predicate is `_is_fresh`
([omron_bp.py:199-233](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L199-L233)):

```python
# omron_bp.py:199-233 (abbreviated)
def _is_fresh(taken_at, *, now=None, window_s=_BP_FRESHNESS_WINDOW_S):
    if taken_at is None:
        return False
    current = (now or (lambda: datetime.now(timezone.utc)))()
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    delta_s = abs((current - taken_at).total_seconds())
    return delta_s <= window_s
```

What drives the comparison:

- `taken_at` is the cuff-side timestamp embedded in the SIG
  `0x2A35` payload, decoded by `_parse_timestamp`
  ([omron_bp.py:236-270](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L236-L270)).
  The SIG Date Time field carries no tz metadata; the parser
  attaches the host's local zone via `.astimezone()` so the
  comparison against `datetime.now(timezone.utc)` reduces to a
  common UTC instant.
- `current` is `datetime.now(timezone.utc)` — **wall-clock,
  absolute, not session-relative**.
- The window is `_BP_FRESHNESS_WINDOW_S = 180.0` (3 minutes), set
  at [omron_bp.py:100](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L100).
- The check is **symmetric** (`abs(...) ≤ window_s`) to tolerate
  cuff-RTC skew; this is irrelevant to the bug.

The unrelated `_BP_FRESH_READ_TIMEOUT_S = 30.0`
([omron_bp.py:115](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L115))
is the _drain phase_ budget — how long the kiosk waits for any
notification after subscribing before giving up the current
connect cycle. It is not part of the freshness predicate. The
two constants do orthogonal jobs and one does not protect the
other.

`_drain_until_fresh`
([omron_bp.py:674-758](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L674-L758))
loops over indicates until one of them passes `_is_fresh`. Stale
indicates are logged as `omron_bp.stored_reading_drained`
([line 745-750](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L745-L750))
and skipped; the loop keeps consuming until either a fresh
indicate, drain-timeout, or cancel.

**There is no session-boundary awareness anywhere on this path.**
`_is_fresh` does not see `current_session.started_at`; the
sensor does not even have access to the FSM or the DB session
that holds it.

---

## Section 3 — Failure-mode traces

Setup for all three traces:

- Session 1's citizen takes BP at wall time `T0`. The cuff stores
  `(systolic, diastolic, pulse, taken_at=T0)` internally.
- The citizen pairs the cuff, the kiosk drains the stored
  reading, validates fresh (delta ≈ 0 s), publishes the BP
  triple. Session 1 completes and goes to END → IDLE.
- The cuff retains the stored reading on its own RTC; the kiosk
  has no API to clear it.
- Session 2 begins at wall time `T0 + Δ` where `Δ < 180 s`.

### Trace A — Session 1's reading is re-delivered as session 2's

1. Session 2's RFID tap, language, path. FSM enters
   `MEASURING_VITALS` at `T0 + Δ` and publishes
   `BpMeasurementRequested`
   ([main_window.py:564](kiosk/src/ginhawa_kiosk/gui/main_window.py#L564)).
2. `_handle_request` clears `_cancel_event` and enters the retry
   loop ([omron_bp.py:484-503](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L484-L503)).
3. The citizen has not yet pressed START on the cuff for session
   2; the cuff is still holding the session-1 reading from `T0`.
   The kiosk-side connect succeeds and `start_notify` fires
   ([line 636](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L636)).
4. The cuff immediately dumps the stored indicate. The payload's
   `taken_at = T0`.
5. `_drain_until_fresh` parses, then calls `_is_fresh(T0)` at
   wall time `T0 + Δ`:
   `abs(Δ) ≤ 180 s` is **True**. The reading is logged as
   `omron_bp.measurement_received`
   ([omron_bp.py:752-758](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L752-L758))
   and returned.
6. `_handle_request` publishes the BP triple
   ([omron_bp.py:514](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L514),
   [\_publish_reading at 760-788](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L760-L788)).
7. `_on_measurement_proposed_event` validates and persists each
   triple member against session 2's `session_id`
   ([main_window.py:927-942](kiosk/src/ginhawa_kiosk/gui/main_window.py#L927-L942)).
   The path-vs-type filter from commit `e54a02b` does **not**
   protect here: the path is `vitals` and BP types ARE in the
   vitals set, so the filter passes the reading through.

The session-1 reading is now session 2's BP triple in the DB.

### Trace B — Session 2's fresh reading fails to push

Same setup as Trace A. After step 6 the handler returns. There
is no further loop iteration to wait for a "newer" indicate.
Even if the citizen now takes a real BP for session 2:

1. The cuff overwrites its stored reading with the new one.
2. The cuff is no longer connected to the kiosk (disconnect ran
   inside the `finally` at
   [omron_bp.py:638-646](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L638-L646)).
3. No new `BpMeasurementRequested` is emitted for session 2 —
   `main_window` only emits one per MEASURING_VITALS entry
   ([main_window.py:564](kiosk/src/ginhawa_kiosk/gui/main_window.py#L564)),
   and the FSM has already advanced past MEASURING_VITALS once
   the (stale) BP triple satisfied `_VITALS_TYPES.issubset(
_captured_types)`.
4. Even if a stray new `BpMeasurementRequested` did arrive, the
   downstream duplicate-drop guard
   ([main_window.py:917](kiosk/src/ginhawa_kiosk/gui/main_window.py#L917))
   would discard the fresh BP triple because `systolic_bp` /
   `diastolic_bp` / `heart_rate` are already in
   `_captured_real_types`.

The fresh reading is never persisted. It dies on the cuff's
internal RTC.

### Trace C — "Previous" reading delivered before session 2's measurement completes

This is just Trace A observed earlier in the wall-clock. The
trigger is the order in which the citizen does things during
session 2:

- If the citizen taps RFID **before** taking the session-2 BP
  on the cuff (typical when the BP cuff is positioned across the
  room from the kiosk console), the BP handler runs immediately,
  finds the still-stored session-1 reading, validates it as
  fresh, and publishes. The "previous reading delivered before
  this session's measurement was even taken" symptom is the
  same path as Trace A, just framed by the citizen's perception
  rather than the wall-clock.

The 30 s drain timeout
([omron_bp.py:115](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L115))
provides no protection here either — the very first indicate the
cuff sends after subscribe IS the stale reading, which `_is_fresh`
rubber-stamps before the drain has a chance to time out.

---

## Section 4 — Root cause hypothesis

**Selected: A** (absolute freshness window, not session-relative).

Supporting evidence:

- The cuff's store-and-forward model means the kiosk routinely
  retrieves a stored reading rather than a "live" one. This is
  by design ([omron_bp.py:29-43](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L29-L43)).
- The kiosk's only stale-vs-fresh discriminator is `_is_fresh`
  ([omron_bp.py:199-233](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L199-L233)),
  which compares against `datetime.now(timezone.utc)` rather
  than the session's `started_at`.
- A session's lifespan from RFID tap to END is comfortably
  within 180 s on the bench (the consent timeout alone is 60 s;
  measurement screens and REPORT add another minute or so).
  Two back-to-back vitals_only sessions are very likely to fall
  inside one 180 s window.
- Once the stale reading passes `_is_fresh`, every downstream
  guard agrees with it: the validator accepts physiological
  values regardless of timestamp, the path filter accepts BP
  types under `vitals` regardless of which session captured
  them, the duplicate-drop guard is per-state-entry rather than
  per-cuff-timestamp.

Hypotheses B / C / D / E / F **are not** the root cause:

- B (stored-reading drain): the drain _is_ present and correctly
  iterates ([omron_bp.py:695-758](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L695-L758)).
  It just trusts `_is_fresh` to discriminate.
- C (handler state carries forward): the handler clears
  `_cancel_event` ([omron_bp.py:490](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L490)),
  releases its lock on exit, and builds a fresh BleakClient
  per attempt ([omron_bp.py:607](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L607)).
  No state leaks across sessions.
- D (BLE connection persists across sessions): falsified — every
  retry connects + disconnects
  ([omron_bp.py:638-646](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L638-L646)).
- E (retry loop from a previous session still running): the
  loop only exits on success or on cancel; on success
  `_handle_request` returns and the lock is released; on cancel
  the loop bails. There is no path that leaves a coroutine
  pending into the next session.

The smoking gun is the conjunction of three lines:

```python
# omron_bp.py:100 — wall-clock-only window
_BP_FRESHNESS_WINDOW_S = 180.0  # 3 minutes
```

```python
# omron_bp.py:229 — comparison against datetime.now, not session start
current = (now or (lambda: datetime.now(timezone.utc)))()
```

```python
# omron_bp.py:743-751 — drain skips stale, accepts the first "fresh"
if not _is_fresh(reading.taken_at):
    stale_count += 1
    self._logger.info("omron_bp.stored_reading_drained", ...)
    continue
self._logger.info("omron_bp.measurement_received", ...)
return reading
```

A reading whose `taken_at` falls in `[now − 180 s, now + 180 s]`
is accepted; the _session's_ boundary is irrelevant.

---

## Section 5 — Recommended fix sketch

**No code in this section.**

The bug is conceptual: "fresh enough" is being measured against
the kiosk's wall clock, but the citizen-facing contract is
"BP taken during _this_ session." The fix is to refine the
predicate so a reading must be fresh **AND** post-dated relative
to the current session's start.

Direction:

- The sensor's `BpMeasurementRequested` payload should carry the
  current session's `started_at` (or `MEASURING_VITALS` entry
  timestamp). The sensor records this when the request arrives
  and clears it on `_handle_request` exit; the comparison in
  `_is_fresh` becomes `taken_at >= session_started_at - skew`
  (a few seconds of skew tolerance for cuff-RTC drift, mirroring
  the existing symmetric `abs` window). The existing 180 s
  window stays as the outer "stored from yesterday" guard.
- Alternatively, the timestamp can be passed via a sensor-side
  setter (`set_session_floor(ts)`) called from main_window on
  MEASURING_VITALS entry. Less event-bus plumbing; same
  semantics.
- BLE teardown changes are unnecessary. The current per-request
  connect/disconnect is the right shape — the bug is the
  predicate, not the connection lifecycle.
- The cuff's stored-reading dump does not need new explicit
  recognition; the existing `_drain_until_fresh` already drains
  stale indicates and continues. With a session-relative
  predicate, every reading from session 1 (and every reading
  taken before session 2's start) is correctly classified as
  stale.
- The handler does **not** need an additional reset on
  `BpMeasurementRequested`; the existing `_cancel_event.clear()`
  pattern at [omron_bp.py:490](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L490)
  already covers handler-state hygiene. The only thing the
  request handler needs to remember newly is the session floor
  for the lifetime of the call.

Edge cases worth pinning down before writing the fix:

- Multi-vitals_only path: the kiosk could in principle re-fire
  `BpMeasurementRequested` while a stale reading is in mid-
  drain. Today the lock at
  [omron_bp.py:484](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L484)
  drops re-fires. With a session-floor passed on the event,
  the in-flight handler must still finish on its original
  floor; re-fire logic doesn't change.
- Cuff-RTC clock drift across hours: with the symmetric
  `abs(...) ≤ 180 s` check today, a 200-second-fast cuff would
  drop fresh readings. The session-floor variant should keep
  the symmetric tolerance for the post-floor side. Concretely:
  `taken_at >= session_started_at - tolerance AND taken_at <= now + tolerance`.
- The MQTT-fed heart_rate from the MAX30100 (which is also a
  member of `_VITALS_TYPES`) is unaffected — it flows over a
  different path entirely.

---

## Section 6 — Defense story implications

The wider design assumes the BP cuff acts as a "stored result
that the kiosk retrieves," which is what the SIG Blood Pressure
Service supports. The Omron HEM-7155T inherits that model and
adds a constraint the paper hasn't called out explicitly:
**pairing mode and measurement mode are mutually exclusive on
the device** ([omron_bp.py:35-36](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L35-L36)),
which means the kiosk only ever reads previously-stored values,
not "live" measurements. That architectural constraint makes
the freshness gate load-bearing: it is the only piece of code
that protects against the kiosk attributing yesterday's reading
to today's citizen.

Implications:

- An ADR is appropriate here. Suggested scope:
  - The store-and-forward protocol assumption (and its
    consequence: the kiosk's BP reading is never "live")
  - The freshness gate as the discriminator
  - The new session-floor refinement and _why_ the wall-clock
    window alone is insufficient (this audit)
  - Why we did **not** choose alternatives (BLE teardown
    changes, persistent connection, polling for new indicates)
- The audit logs the gate already emits
  (`omron_bp.stored_reading_drained`,
  `omron_bp.measurement_received`) are sufficient for forensic
  reconstruction. After the fix, these log lines should also
  carry `session_floor_ts` so an operator can correlate a drop
  to a session boundary.
- The wider "Failure modes — fail loud, fail safe" rule in
  CLAUDE.md is upheld: the kiosk drops stale readings, it does
  not silently substitute a default; the symptom today is the
  kiosk being _too permissive_, not too lenient. The fix
  tightens, doesn't loosen.
- No change to the Data Privacy Act story: BP values do not
  cross devices; the bug attributes the wrong row to the wrong
  citizen within the kiosk's own DB, which the path-filter audit
  already flagged as a class of issue. Both the path filter
  (commit `e54a02b`) and the BP session-floor refinement are
  receipt-boundary defences and would be jointly cited in a
  "how we keep one citizen's data out of another citizen's
  record" section.
