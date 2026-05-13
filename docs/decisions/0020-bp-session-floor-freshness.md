# ADR 0020: BP cuff freshness uses a session floor

- **Status:** Accepted
- **Date:** 2026-05-13
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira
- **Related:** ADR-0004 (Omron HEM-7155T as BP source). Audit:
  `docs/audits/2026-05-13-bp-stale-readings-audit.md`.

## Context

The Omron HEM-7155T is a store-and-forward BLE device. It holds its
last measurement in internal memory and re-delivers it (via SIG
indicate) on every reconnect; the cuff has no concept of "session"
and no API to clear its stored reading. The kiosk's adapter
(`OmronBpSensor`) connects per request, drains stored notifications,
and accepts the first one that passes `_is_fresh`.

The pre-ADR-0020 freshness predicate compared the cuff-side
`taken_at` against the kiosk's wall clock over a symmetric
±180 s window. This was sufficient for solo sessions, but
back-to-back vitals sessions within the window reproduce a
stale-reading failure mode:

1. Citizen 1's BP is taken and stored on the cuff at `T0`.
2. The kiosk drains it during session 1, validates fresh, publishes
   the triple, finishes the session, returns to `IDLE`.
3. Session 2 starts at `T0 + 90 s`. The kiosk connects to the
   cuff; the cuff dumps the same stored reading.
4. `_is_fresh(T0)` evaluated at `T0 + 90 s` returns `True` —
   90 s ≤ 180 s window.
5. The kiosk publishes citizen 1's BP as citizen 2's. The path
   advances; the citizen-2 reading (taken later) is never retrieved.

The audit (Section 4) identifies this as the only smoking-gun
mismatch on the BP path: every other lifecycle element (BLE
connect-per-request, handler-state hygiene, cancel propagation)
is correct.

## Decision

`_is_fresh` accepts an optional `session_floor: datetime`. The
predicate now requires both gates:

1. **Absolute window** (existing): `abs(taken_at − now) ≤ 180 s`.
2. **Session floor** (new): `taken_at ≥ session_floor − 10 s`.

`session_floor` is the timestamp main_window stamps at the moment
it emits `BpMeasurementRequested` on entry to `MEASURING_VITALS`.
The event carries the floor as an ISO-8601 string; the BP handler
parses it once, stores it on `self._session_floor` for the
lifetime of the request, and clears it in `finally` so a stale
floor cannot bleed into the next session.

The 10 s skew tolerance covers:

- Cuff-RTC drift (typically sub-second per day, observed ≤2 s).
- The small gap between the citizen pressing START on the cuff
  and the kiosk's `MEASURING_VITALS` entry stamp — a citizen who
  presses START a moment before the RFID tap should not have their
  fresh reading rejected.

`session_floor=None` is the legacy single-gate behaviour. The
default is `None` so existing `_is_fresh(taken_at, now=...)`
call sites — primarily the unit tests in
`tests/sensors/test_omron_bp.py` that pin pre-ADR-0020 behaviour —
work unchanged.

The audit logs already emitted on the drain path
(`omron_bp.stored_reading_drained`, `omron_bp.measurement_received`)
gain `session_floor` and `delta_to_floor_s` fields, so an operator
inspecting journalctl can correlate every drop to its session
boundary and read the delta directly without consulting the audit
trail.

## Alternatives considered

- **Tear down BLE between sessions.** Rejected. The cuff's stored
  reading lives on its own RTC, not in any BLE state the kiosk
  controls. Reconnecting would yield the same stale reading from
  the same internal buffer. Additionally, the BleakScanner is
  shared with the Xiaomi scale via `BleAdapterLock`; cycling the
  BP path's connect cadence would add re-discovery latency without
  fixing the underlying mismatch.
- **Read-and-discard a fixed number of indicates on connect.**
  Rejected. The cuff may deliver 0 or N stored indicates depending
  on its post-battery-pull state. A fixed discard count is either
  too aggressive (drops the fresh reading) or too lenient (lets
  the stale one through).
- **Persistent connection across sessions.** Rejected on privacy
  grounds — a stale BLE handle that survives a session-end means
  citizen-1 notifications could in principle arrive while
  citizen-2's session is live. The single-gate bug is bad; a
  cross-session BLE channel is worse.
- **Track every cuff timestamp the kiosk has ever seen and reject
  exact matches.** Rejected. No clear retention policy; requires
  durable state; failure mode is "kiosk forgets" rather than
  "reading is stale," which is a worse default.

The session-floor approach is the cheapest, most legible fix and
aligns the kiosk's "fresh per session" contract with the citizen-
facing reality: one citizen, one session, one BP, where "the
reading must have been taken during this session" is the
intuitive answer to "why was the previous reading rejected?"

## Trade-offs

- **10 s skew is a magic number.** Justified by the two physical
  sources of skew (cuff RTC drift, citizen-press-vs-kiosk-stamp
  ordering) and the bench-observed maxima for both. Will be
  revisited if a deployment surfaces a wider drift envelope.
- **Floor is keyed off the kiosk's `now()` at request emission,
  not off `session.started_at`.** This is intentional: the FSM
  creates the session row on `PATH_CHOICE` entry, which can be
  seconds-to-minutes before `MEASURING_VITALS`. The relevant
  floor is "BP was requested for this session," not "the session
  began" — a reading taken during CONSENT shouldn't qualify.
- **The handler does not persist the floor.** It lives only in
  memory, in `self._session_floor`, for the lifetime of one
  `_handle_request` invocation. If the kiosk restarts mid-request
  the next session re-stamps its own floor — no recovery path is
  needed.
- **The fix is local to the kiosk.** No firmware or cuff change.
  The cuff continues to behave exactly as before; the kiosk just
  filters more tightly.

## Audit story

Structured logs gain `session_floor` (ISO 8601) and
`delta_to_floor_s` (float seconds, negative = pre-floor) on:

- `omron_bp.stored_reading_drained` — every drop is
  reconstructable from a journalctl entry alone, no DB
  introspection needed.
- `omron_bp.measurement_received` — the accepted reading carries
  its floor so a forensics trail can prove "this reading's
  `taken_at` was after the session that requested it."
- `omron_bp.request_started` — the floor is logged at request
  entry so the lifetime of the in-flight floor is visible in the
  journal trace.

## Defense story

A panel question along the lines of _"how do you prevent citizens
from seeing the previous patient's blood pressure?"_ now has a
two-sentence answer:

> The cuff re-delivers its last stored reading on every reconnect.
> Our freshness gate accepts a reading only if it falls within an
> absolute 180-second window of now AND its cuff-side timestamp
> sits at or after the current session's BP-request timestamp
> (minus a 10-second skew for clock drift). Every dropped reading
> is logged with both deltas so any operator can reconstruct why a
> reading was rejected.

Both halves are visible in code (`_is_fresh` at
`kiosk/src/ginhawa_kiosk/sensors/omron_bp.py`) and in the audit
log fields, and the failure mode was caught on bench rather than
in production.

## References

- `docs/audits/2026-05-13-bp-stale-readings-audit.md` — the audit
  that motivated this ADR.
- `kiosk/src/ginhawa_kiosk/sensors/omron_bp.py` — `_is_fresh`,
  `_handle_request`, `_drain_until_fresh`.
- `kiosk/src/ginhawa_kiosk/fsm/event_bus.py` — `BpMeasurementRequested`
  with `session_floor: str`.
- `kiosk/src/ginhawa_kiosk/gui/main_window.py` — request emission
  with the floor stamped at MEASURING_VITALS entry.
- `kiosk/tests/sensors/test_omron_bp_freshness.py` — pinned
  behaviour for both gates and the handler-state lifecycle.
