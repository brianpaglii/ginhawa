# Xiaomi S200 — stale weight delivered before the citizen steps on the scale

Date: 2026-05-13
Scope: read-only. No code changed.

## Symptom

In a session whose path allows weight (`anthropometric_only` or
`full_check`), a weight value appears in the kiosk **before the
citizen has placed any weight on the scale for this session**. The
value matches session 1's prior reading. The path filter
(commit `e54a02b`) doesn't catch it because weight is legal for the
current path; the value is persisted against session 2, the path
advances, and any later "real" reading is duplicate-dropped.

This is the broadcast-protocol analog of the cuff store-and-forward
bug already documented in
[2026-05-13-bp-stale-readings-audit.md](2026-05-13-bp-stale-readings-audit.md).
Same shape ("prior session's reading attributed to current
session"); different mechanism.

---

## Section 1 — Xiaomi S200 advert protocol

### What the kiosk sees (today)

The detection callback at
[xiaomi_scale.py:400-424](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L400-L424)
wraps the bleak advert into a `BluetoothServiceInfoBleak` and feeds
it to `XiaomiBluetoothDeviceData.update()`. The kiosk consumes the
resulting `entity_values` mapping via
[`extract_mass_kg`](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L174-L194):

```python
# xiaomi_scale.py:184-194
for key, value in entity_values.items():
    key_str = (getattr(key, "key", None) if not isinstance(key, str) else key) or str(key)
    if "mass" in key_str.lower():
        native = getattr(value, "native_value", value)
        try:
            return float(native)
        except (TypeError, ValueError):
            return None
return None
```

This is the **only** signal the kiosk extracts from the advert. The
mass value is the entire payload as far as the gate is concerned.
There is no timestamp, no sequence number, no "stabilized" flag, no
"removed" flag.

### What's actually in the advert

The S200's MiBeacon-decoded payload is parsed in xiaomi-ble's
`obj4e16` at
`.venv/lib/python3.12/site-packages/xiaomi_ble/parser.py:1722-1747`:

```python
# .venv/lib/python3.12/site-packages/xiaomi_ble/parser.py:1722-1747
def obj4e16(xobj, device, device_type):
    """Parser for Xiaomi Smart Scale S200 MJTZC02YM"""
    if len(xobj) != 9:
        return {}
    profile_id, data, timestamp = struct.unpack("<BII", xobj)
    if data == 0:
        return {}
    weight_kg = data / 100.0
    device.update_predefined_sensor(SensorLibrary.MASS__MASS_KILOGRAMS, weight_kg)
    device.update_sensor(
        key=ExtendedSensorDeviceClass.PROFILE_ID, ...
    )
    return {}
```

**Critical:** the S200 advertisement carries a 4-byte
`timestamp` field. The library unpacks it (line 1733) but
**never surfaces it** — only the mass and profile id flow into
`entity_values`. The kiosk's `extract_mass_kg` therefore cannot
see the timestamp; only the mass.

For comparison, the older Mi Scales (`_parse_scale_v1` /
`_parse_scale_v2` at
[parser.py:2229-2321](kiosk/.venv/lib/python3.12/site-packages/xiaomi_ble/parser.py))
emit two distinct sensor entities — `MASS_NON_STABILIZED` for every
in-flight reading and `MASS` only when the device's
`mass_stabilized` bit is set and `mass_removed` is clear. The S200's
obj4e16 has no equivalent flags in the parsed payload; the advert
seems to carry only a finalised value with its capture timestamp.

### Broadcast cadence and persistence (what the comments document)

[xiaomi_scale.py:23-32](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L23-L32)
encodes the kiosk's prior understanding of the device:

- "Xiaomi advertises signal-strength-only frames between
  measurements; those are not a reading." → the mass-only filter
  was intended to be the sole separator between "scale idle" and
  "scale has a reading."
- "The S200 broadcasts mass roughly every 5 s while a user stands
  on it" → the comment asserts mass is broadcast only while
  weight is on the scale.

[xiaomi_scale.py:56-76](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L56-L76)
adds the bench-evidence finding from 2026-05-09:

```python
# xiaomi_scale.py:65-75
# Bench evidence (2026-05-09): weight publishes were observed
# within 50 ms – 1 s of the gate_unlocked event, far faster than
# 3 stable readings × 5 s broadcast cadence allows. Either the
# BleAdapterLock pause/resume cycle re-delivers cached
# advertisements when the scanner resumes, or xiaomi-ble caches
# state and emits on next event regardless of the gate's buffer
# history.
```

Translation: the 5 s "only when user is on the scale" model does
not match observed behaviour. Either the scale itself rebroadcasts
the last measurement after the user steps off, or BlueZ / bleak /
xiaomi-ble caches and replays. The kiosk's response was to add an
8 s warmup window — but as shown below that only covers the first
8 s after unlock, not arbitrary re-broadcasts later in the
session.

---

## Section 2 — Gate lifecycle and timing

`_WeightStabilityGate`
([xiaomi_scale.py:84-166](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L84-L166))
maintains four pieces of state: buffer (`deque(maxlen=K)`),
`_locked` bool, `_unlocked_at` timestamp, and the constants `_k`
(buffer size), `_tolerance` (kg spread), `_warmup_seconds`.

### `accept(value)` — the publish predicate

```python
# xiaomi_scale.py:117-140
def accept(self, value: float) -> float | None:
    if self._locked:
        return None
    if self._unlocked_at is not None:
        if time.monotonic() - self._unlocked_at < self._warmup_seconds:
            return None
    self._buffer.append(value)
    if len(self._buffer) < self._k:
        return None
    if max(self._buffer) - min(self._buffer) > self._tolerance:
        return None
    published = float(median(self._buffer))
    self._locked = True
    return published
```

Three gates, in order:

1. **Lock gate.** Once a reading is published the gate locks and
   every subsequent `accept` is `None` until `unlock()`. One
   weight per unlock cycle.
2. **Warmup gate.** Drops readings for `_GATE_WARMUP_SECONDS`
   (8 s) after the most recent `unlock()`. Was added in May 2026
   to absorb the cached-advert replay observed at scanner-resume
   ([xiaomi_scale.py:65-75](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L65-L75)).
3. **Stability gate.** Once the warmup expires, the buffer fills
   to K=3 readings; the gate publishes the median when
   `max − min ≤ 0.2 kg`.

### `unlock()` — when and from where

```python
# xiaomi_scale.py:142-146
def unlock(self) -> None:
    self._locked = False
    self._buffer.clear()
    self._unlocked_at = time.monotonic()
```

Called from two paths:

- Bus handler: `_on_session_reset`
  ([xiaomi_scale.py:330-331](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L330-L331))
  fires on `SessionResetForSensors`. The main window publishes
  that event on entry to **`IDLE` and `LANGUAGE_SELECT`**
  ([main_window.py:500-515](kiosk/src/ginhawa_kiosk/gui/main_window.py#L500-L515))
  AND again on entry to **`MEASURING_ANTHRO`**
  ([main_window.py:565-578](kiosk/src/ginhawa_kiosk/gui/main_window.py#L565-L578)).
  The second one re-unlocks defensively to close a known race
  inside a single session — but it also restarts the 8 s warmup.
- `restart_warmup()` after a `BleAdapterLock` resume
  ([xiaomi_scale.py:148-163](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L148-L163)):
  restamps `_unlocked_at` without clearing buffer or lock so a
  pause-after-publish doesn't lose the just-captured reading.

Net: by the time the citizen reaches `MEASURING_ANTHRO`, the
warmup window has typically expired (the citizen passed through
`LANGUAGE_SELECT` → `CONSENT/PATH_CHOICE` → `MEASURING_VITALS`
→ `MEASURING_ANTHRO`, which is many tens of seconds). The
MEASURING_ANTHRO re-unlock re-arms warmup for another 8 s but
the citizen still typically hasn't stepped on the scale for
that window. After it expires, the gate is hot.

---

## Section 3 — Failure-mode trace

Pre-conditions for a `full_check` or `anthropometric_only`
session 2 less than ~5 minutes after session 1:

- Session 1 captured a weight at wall time `T0`. The scale
  broadcasted `(profile_id, data=mass*100, timestamp=T0)` adverts
  about every 5 s while the citizen stood on it, and continues
  to rebroadcast the same `(mass, T0)` tuple after they step off
  (per
  [xiaomi_scale.py:65-75](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L65-L75)
  bench evidence; the alternative — that BlueZ replays the cached
  advert — yields the same kiosk-side effect).
- Session 1 finished, FSM passed through END → IDLE. On the END
  → IDLE transition `SessionResetForSensors` published. The
  gate unlocked, buffer cleared, 8 s warmup started, `_unlocked_at`
  refreshed.
- LANGUAGE_SELECT for session 2 published a second
  `SessionResetForSensors` (idempotent — gate already unlocked,
  but `_unlocked_at` re-stamped → another 8 s warmup).

Step-by-step:

1. Session 2's citizen taps RFID and selects a path that admits
   weight. The FSM walks the citizen through CONSENT / PATH_CHOICE
   into `MEASURING_VITALS` (if `full_check`) or directly into
   `MEASURING_ANTHRO`. On entry to `MEASURING_ANTHRO`,
   `_configure_state_specific` at
   [main_window.py:565-578](kiosk/src/ginhawa_kiosk/gui/main_window.py#L565-L578)
   publishes `SessionResetForSensors` once more — gate unlocks
   again, buffer cleared, warmup restarted.

2. ~10 s later (citizen is en-route to the scale, has not yet
   stepped on), the 8 s warmup expires. The gate is now hot.

3. The scale's BLE advert hits
   `BleakScanner.detection_callback`
   ([xiaomi_scale.py:395-398](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L395-L398)).
   `_on_advertisement` wraps it as `BluetoothServiceInfoBleak`
   ([xiaomi_scale.py:400-424](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L400-L424))
   and passes it to `XiaomiBluetoothDeviceData.update()`.

4. The library decodes `obj4e16`
   ([parser.py:1722-1747](kiosk/.venv/lib/python3.12/site-packages/xiaomi_ble/parser.py#L1722-L1747)),
   unpacks `(profile_id, data=session-1-mass×100, timestamp=T0)`,
   updates `MASS__MASS_KILOGRAMS` in `entity_values`. **The
   `timestamp=T0` is discarded.**

5. `_on_sensor_update`
   ([xiaomi_scale.py:426-446](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L426-L446))
   calls `extract_mass_kg` → returns session 1's mass.
   `self._gate.accept(kg)` is called.

6. The buffer receives the value. Two more identical adverts
   arrive over the next ~10 s (the scale's rebroadcast cadence).
   Buffer is now `[mass_T0, mass_T0, mass_T0]`. `max − min = 0`
   so it passes the 0.2 kg tolerance trivially. The gate
   publishes the median — which is session 1's mass — and locks
   itself.

7. `_on_advertisement` → `_publish_reading` →
   `bus.publish(MeasurementProposed(measurement_type="weight",
value=mass_T0, ...))`.

8. `main_window._on_measurement_proposed_event`
   ([main_window.py:868-1006](kiosk/src/ginhawa_kiosk/gui/main_window.py#L868-L1006))
   runs. The session's path is `anthropometric` (or `full`) so
   the path-vs-type filter
   ([main_window.py:119-145](kiosk/src/ginhawa_kiosk/gui/main_window.py#L119-L145))
   admits the row. `_captured_real_types` doesn't yet contain
   `"weight"`. The row is persisted with `session_id =
current_session.id` and `is_valid=1`.

9. `_maybe_advance_measurement_path` adds `"weight"` to
   `_captured_types`. For an `anthropometric_only` path this is
   the only outstanding piece (height being offline-placeholdered
   if MQTT is down, else captured separately) — the path advances
   to REPORT, the gate is locked for the rest of the session, and
   a citizen who _then_ steps on the scale produces no further
   publish (gate locked) and would be duplicate-dropped if it
   somehow leaked through.

The bug manifests at step 6 — the _moment_ three identical
rebroadcasts fill the buffer, which is exactly the time the
stability gate was designed to bless ("three identical readings
in tolerance" was the heuristic for "the citizen is standing
still"). Re-broadcasts of a finalised stored value are
indistinguishable from a still-standing citizen at this signal
level.

---

## Section 4 — Root cause hypothesis

**Selected: A** — "The Xiaomi scale's BLE advertisement contains a
stale weight in its payload (the scale continues broadcasting its
last finalised measurement after the citizen steps off). The
kiosk's gate buffers three identical adverts and passes them as
stable."

Supporting evidence:

- **The protocol carries a freshness signal that the kiosk does
  not see.** `obj4e16` decodes a 4-byte `timestamp` per advert
  ([parser.py:1733](kiosk/.venv/lib/python3.12/site-packages/xiaomi_ble/parser.py#L1733))
  but the library discards it. The kiosk's `extract_mass_kg`
  ([xiaomi_scale.py:174-194](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L174-L194))
  can only see mass.
- **The S200 has no `mass_stabilized` / `mass_removed` decode
  path on this code branch** — the v1 and v2 scales do
  ([parser.py:2245-2270](kiosk/.venv/lib/python3.12/site-packages/xiaomi_ble/parser.py#L2245-L2270)),
  but the S200's `obj4e16` emits only one `MASS` entity with no
  flags. The kiosk has no way to know whether an advert is
  "live" or "rebroadcast of finalised."
- **The 2026-05-09 bench note (xiaomi_scale.py:65-75) already
  identified the failure pattern** as cached / rebroadcast
  adverts being delivered post-unlock. The 8 s warmup is a
  partial defence — it covers the _transient_ burst after a
  scanner resume — but does nothing about adverts that arrive
  after the warmup expires.
- **The K=3 stability check is the worst possible heuristic for
  this failure mode.** A re-broadcast of a finalised measurement
  yields perfectly identical values — `max − min = 0` ≤ 0.2 kg.
  The gate's "stable" predicate fires on the _quietest_ signal
  the bug can produce.

Hypotheses B / C are subsidiary mechanisms, not the root cause:

- **B** (gate state across sessions) is largely false: the gate's
  `unlock()`
  ([xiaomi_scale.py:142-146](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L142-L146))
  fully resets buffer + lock + warmup-stamp, and `MEASURING_ANTHRO`
  entry publishes another `SessionResetForSensors` to close even
  the within-session race
  ([main_window.py:565-578](kiosk/src/ginhawa_kiosk/gui/main_window.py#L565-L578)).
  Gate state is properly reset between sessions; the bug is not
  the gate forgetting to reset, it's the gate accepting a
  rebroadcast post-reset.
- **C** (BleakScanner advert cache delivering stale adverts
  on restart) is plausible as a _contributing_ mechanism but not
  the load-bearer. Even without any cache replay, the scale's
  own rebroadcast (point A) is sufficient to populate the gate.
  The scanner is never restarted between sessions — only paused
  / resumed for BP capture (BleAdapterLock) — so the cache
  surface only applies to within-session pause/resume cycles,
  which `restart_warmup()`
  ([xiaomi_scale.py:148-163](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L148-L163))
  already handles.

The smoking-gun pair of lines:

```python
# .venv/lib/python3.12/site-packages/xiaomi_ble/parser.py:1733-1738
profile_id, data, timestamp = struct.unpack("<BII", xobj)
if data == 0:
    return {}
weight_kg = data / 100.0
device.update_predefined_sensor(SensorLibrary.MASS__MASS_KILOGRAMS, weight_kg)
# (timestamp is unpacked into a local but never used)
```

```python
# kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py:435-446
kg = extract_mass_kg(entity_values)
if kg is None:
    return  # signal-strength-only or other non-mass advertisement
published = self._gate.accept(kg)
if published is None:
    return
await self._bus.publish(MeasurementProposed(...))
```

The library decodes a timestamp and discards it; the kiosk
decides freshness on a stability heuristic that the re-broadcast
trivially satisfies.

---

## Section 5 — Recommended fix sketch

**No code in this section.** Direction and trade-offs.

The shape of the fix mirrors ADR-0020 (BP cuff session_floor):
keep the always-on adapter exactly as it is, and add a session-
relative check at the receipt boundary. The implementation surface
differs because the freshness signal lives in different places.

### Best direction: surface and consult the S200 advert timestamp

The S200 already broadcasts a per-measurement timestamp in every
advert. The xiaomi-ble library decodes it but discards it. The
kiosk's options for getting at it:

- **Parse the timestamp from the raw advert ourselves.** The
  `service_info.service_data` / `manufacturer_data` blobs are
  already available in `_on_advertisement`
  ([xiaomi_scale.py:400-424](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L400-L424));
  re-parsing the 4 bytes after the mass field is a few lines of
  struct unpacking. The kiosk then keeps a "last seen advert
  timestamp" and only feeds the gate when the timestamp advances,
  OR (mirroring ADR-0020) requires the advert timestamp to be at
  or after a session floor stamped on entry to MEASURING_ANTHRO.
- **Fork / replace the xiaomi-ble parser** to surface the
  timestamp through `entity_values`. More invasive than a
  receipt-side re-parse and creates an upstream fork to maintain.
  Recommend against unless the library is patched upstream.

Of the two, the receipt-side re-parse is the cheaper, more
auditable change. It also keeps xiaomi-ble at the upstream
revision pinned in ADR-0017.

### Session floor analog

Direct analog of ADR-0020:

- Main window publishes `SessionResetForSensors` with a session
  timestamp on entry to `MEASURING_ANTHRO` (today the event has
  no fields; this would extend it). The scale stores the floor on
  `self._session_floor` for the lifetime of the session and
  clears it on next reset.
- `_on_sensor_update` compares the advert's parsed timestamp
  against the floor; readings whose advert-side timestamp
  predates the floor minus a skew tolerance are logged as
  `xiaomi_scale.stale_broadcast_dropped` and skipped before the
  gate sees them.

The advert timestamp's origin (scale RTC vs. relative seconds-
since-power-on vs. broadcast counter) needs verification on the
bench — the floor comparison only makes sense if both timestamps
are on the same monotonic axis. If the scale uses a relative
counter that resets on battery pull, the equivalent fix is
"timestamp must be strictly greater than the last accepted
timestamp" — a sequence guard rather than a wall-clock floor.
Either way, the receipt boundary gates on a sensor-supplied
freshness signal the gate currently ignores.

### Alternative ideas (rejected or marginal)

- **Tie the warmup to MEASURING_ANTHRO entry only, not to
  LANGUAGE_SELECT.** Helps marginally: the warmup is short
  enough (8 s) that the citizen's walk to the scale already
  exceeds it. Wouldn't fix the bug, just shorten the failure
  window.
- **Lengthen the warmup until the citizen physically reaches
  the scale.** No reliable signal for "citizen has arrived"
  without a separate sensor. Reject.
- **Restart the BleakScanner between sessions.** The scanner is
  shared with the Omron BP cuff via `BleAdapterLock` — a stop /
  start cycle adds re-discovery latency on the BP path and BlueZ
  may still cache. The audit's earlier finding
  ([2026-05-13-bp-stale-readings-audit.md](2026-05-13-bp-stale-readings-audit.md))
  also recommended against BLE teardown for the BP fix; the
  argument is symmetric here. Reject.
- **Require N consecutive _seconds_ of mass adverts before
  accepting.** Doesn't help. The scale's rebroadcasts produce
  identical adverts every ~5 s indefinitely; "N seconds" still
  fires on the rebroadcast.
- **Require N consecutive _different_ mass values** (i.e., detect
  the on-step transient where mass climbs from zero through the
  citizen's full weight). Could in principle distinguish a
  finalised value from a live step-on, but assumes the scale
  emits mass during the climb. The current
  [xiaomi_scale.py:23-25](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L23-L25)
  comment claims it doesn't ("signal-strength-only frames between
  measurements"). If that comment is right, the kiosk never sees
  the transient and "different values" never happens. Reject as
  unreliable.
- **Drop the K=3 stability check entirely.** Worse: removes the
  defence against legitimate scale jitter while leaving the
  stale-rebroadcast bug untouched. Reject.

### Recommended posture

A two-part receipt-boundary check (advert timestamp + session
floor) is the cheapest fix and matches ADR-0020's shape exactly.
The implementation lives in the same file as the gate, runs
before the gate, and adds one structured log event
(`xiaomi_scale.stale_broadcast_dropped`) for journalctl
forensics.

---

## Section 6 — Defense story implications

### Pattern: three audits, one principle

This is the third bug in a row whose fix lives at the **kiosk's
receipt boundary**, not in the sensor adapter:

1. **Scale prefiring across paths**
   ([2026-05-13-scale-prefiring-audit.md](2026-05-13-scale-prefiring-audit.md)
   / commit `e54a02b`). Always-on sensors don't know the
   session's `measurement_path`. Fix: receipt-boundary path-vs-
   type filter.
2. **BP cuff stale readings across sessions** (this audit's
   sibling,
   [2026-05-13-bp-stale-readings-audit.md](2026-05-13-bp-stale-readings-audit.md)
   / ADR-0020). Store-and-forward devices re-deliver prior
   sessions' readings. Fix: receipt-boundary session-floor on the
   freshness predicate.
3. **Xiaomi scale stale broadcast** (this audit). Broadcast
   adverts re-deliver prior sessions' readings while the scanner
   stays up. Same fix shape: receipt-boundary check against a
   freshness signal the protocol already carries.

The cross-cutting principle: **every always-on, broadcast, or
store-and-forward sensor needs a session-relative gate at the
receipt boundary, not just a time-relative or stability-relative
one.** The sensor adapter cannot know the session — its lifecycle
is the kiosk's whole uptime, not the citizen's session. The
session is owned by the FSM; receipt-side filtering is the only
seam that has both views.

The wider design rule:

> The kiosk's BLE / MQTT adapters do not — and should not — know
> about sessions, but every adapter that can deliver data outside
> of a citizen's active capture window must be gated at the
> kiosk-side receipt boundary by something the FSM owns.

### Suggested ADR work

- **ADR-0021 (new): "Receipt-boundary defence for sensor
  freshness."** Documents the principle and points at the three
  current instances (path filter, BP session_floor, scale advert
  timestamp). Future always-on sensors (e.g., a continuous
  glucose meter, an SpO2 finger sensor that streams) inherit the
  pattern.
- **Update ADR-0017** (xiaomi-ble library choice) with a note
  that the library decodes-but-discards the S200's advert
  timestamp, and the kiosk re-parses for freshness.

### Defense panel framing

If a panellist asks, _"how do you guarantee one citizen doesn't
see another citizen's weight?"_:

> The S200 broadcasts its last finalised measurement repeatedly
> after the citizen steps off. We can't ask the scale to stop —
> it has no off switch we can reach over BLE — and tearing down
> the scanner between sessions would slow the BP path that shares
> the same adapter. Instead, every advert carries a timestamp
> that the off-the-shelf library decodes but discards; the kiosk
> re-parses that timestamp at the receipt boundary and rejects
> any advert whose timestamp predates the session's
> MEASURING_ANTHRO entry. The same principle covers the BP cuff
> (ADR-0020 session floor) and the path filter (commit `e54a02b`).
> Three different sensor protocols, one architectural rule:
> receipt-boundary freshness against a session-owned timestamp.

The full forensic story lives in
[2026-05-13-scale-prefiring-audit.md](2026-05-13-scale-prefiring-audit.md),
[2026-05-13-bp-stale-readings-audit.md](2026-05-13-bp-stale-readings-audit.md),
and this audit; the rule lives in ADR-0021 (when written).
