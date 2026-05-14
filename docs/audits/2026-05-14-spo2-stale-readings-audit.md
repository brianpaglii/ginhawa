# MAX30100 SpO2 — stale or stuck values across sessions

Date: 2026-05-14
Scope: read-only. No code or firmware changed.

## Symptom

In back-to-back sessions on the same kiosk, session 2's persisted
SpO2 is sometimes either (a) literally session 1's value re-
delivered or (b) a plausibly-in-range value the citizen never
actually produced (out-of-range outliers, stuck readings, or
sporadic jitter). The first-of-the-day session and any session
where the citizen lingers with the finger correctly placed for
the full 30 s reporting window look correct.

Fourth audit in the cross-session-contamination series — see
[scale-prefiring](2026-05-13-scale-prefiring-audit.md),
[bp-stale-readings](2026-05-13-bp-stale-readings-audit.md),
[scale-stale-readings](2026-05-13-scale-stale-readings-audit.md),
[db-lock-contention](2026-05-14-db-lock-contention-audit.md).

---

## Section 1 — ESP32-A SpO2 firmware path

### Library + tick loop

The MAX30100 driver is oxullo's `PulseOximeter` wrapper. The
firmware owns a single module-scope instance:

```cpp
// firmware/esp32-a-vitals/src/sensor_max30100.cpp:25
PulseOximeter g_pox;
```

`sensor_max30100_init`
([sensor_max30100.cpp:46-69](firmware/esp32-a-vitals/src/sensor_max30100.cpp))
calls `g_pox.begin()` once at boot and sets the IR LED current to
the library default (27.1 mA). **There is no other reset path**:
the global `g_pox` retains its full beat-detector + SpO2-estimator
state for the entire kiosk uptime.

The main loop calls `sensor_max30100_tick()` every 10 ms
([main.cpp:118-121](firmware/esp32-a-vitals/src/main.cpp#L118-L121)).
The tick advances the library and samples `getSpO2()`:

```cpp
// firmware/esp32-a-vitals/src/sensor_max30100.cpp:71-80
void sensor_max30100_tick() {
    g_pox.update();
    float spo2 = g_pox.getSpO2();
    // The library returns 0.0 until it has stabilised; the SPO2_MIN
    // floor culls those startup values plus any grossly out-of-range
    // readings the algorithm emits during finger-on transients.
    if (spo2 >= SPO2_MIN && spo2 <= SPO2_MAX) {
        _push(g_spo2_buf, g_spo2_count, spo2);
    }
}
```

`SPO2_MIN = 70.0f` and `SPO2_MAX = 100.0f`
([config.h:33-34](firmware/esp32-a-vitals/include/config.h#L33-L34)).
Note the floor is **70**, not the 85 the kiosk's "Problem 6"
discussion called out — this is the firmware-side range, and the
kiosk's validator at
[services/validation.py:43](kiosk/src/ginhawa_kiosk/services/validation.py#L43)
matches it (`spo2: (70.0, 100.0)`).

### Publish gating

Every `MAX30100_REPORT_INTERVAL_MS = 30000` ms
([config.h:26](firmware/esp32-a-vitals/include/config.h#L26)), the
loop drains the buffer via `consume_stable`:

```cpp
// firmware/esp32-a-vitals/src/main.cpp:129-135
if (now - g_last_max30100_report_ms >= MAX30100_REPORT_INTERVAL_MS) {
    g_last_max30100_report_ms = now;
    VitalsReading vitals = sensor_max30100_consume_stable();
    if (vitals.has_spo2) {
        publish_to_topic(g_topic_spo2, vitals.spo2, "%", "spo2");
    }
}
```

`consume_stable`'s gate is **buffer count alone**:

```cpp
// firmware/esp32-a-vitals/src/sensor_max30100.cpp:226-242
VitalsReading sensor_max30100_consume_stable() {
    ...
    VitalsReading r{false, 0.0f};
    if (g_spo2_count >= MAX30100_MIN_BUFFERED_SAMPLES) {
        r.spo2 = compute_pulse_median(g_spo2_buf, g_spo2_count);
        r.has_spo2 = true;
    }
    // Reset every window even if the threshold wasn't met, so a long
    // settling period at session start can't poison later windows
    // with stale values.
    g_spo2_count = 0;
    return r;
}
```

`MAX30100_MIN_BUFFERED_SAMPLES = 16`
([config.h:42](firmware/esp32-a-vitals/include/config.h#L42)). At
the 10 ms tick interval that's **160 ms of in-range library
output** to trigger a publish. The buffer is cleared at the end of
every 30 s window even when no publish fires — good hygiene for
the buffer, but it doesn't reset the _library's internal state_.

### What "stable" actually means here

The 16-sample threshold counts only ticks where
`g_pox.getSpO2()` returned a value in [70, 100]. The threshold
doesn't require:

- **Finger presence.** No IR-DC or red-DC threshold is checked
  before pushing a sample. The oxullo library's "no finger"
  signal (it returns 0.0 until it has stabilised) is the only
  filter — and that filter only fires _while the library has
  not yet stabilised_. Once it has stabilised (i.e. captured a
  first valid reading from a finger), `getSpO2()` keeps
  returning a non-zero value derived from its internal moving
  state, which persists across finger removal.
- **A fresh beat-locked window.** The library's
  `getSpO2()` returns the last computed estimate; it does not
  re-zero between beats.
- **Continuity.** 16 in-range samples can be 160 ms of solid
  finger contact, or they can be 16 transient ticks scattered
  across 30 seconds of intermittent noise.

### No cooldown, no per-session hook

Unlike the height sensor (ADR-0019), the SpO2 path has **no
stabilisation gate**, **no cooldown after publish**, and **no
external reset hook**. The main loop publishes every 30 s,
forever, with whatever the library is willing to hand over in
[70, 100].

There is no MQTT subscriber or other input the kiosk could use to
tell the firmware "new session, reset your state."

---

## Section 2 — Kiosk receipt path

### MQTT route

The subscriber routes the spo2 topic to a `MeasurementProposed`
event with no state-aware filtering:

```python
# kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py:63-68
_TOPIC_ROUTES: dict[str, tuple[str, str, str]] = {
    "spo2": ("spo2", "%", "esp32_a_max30100"),
    ...
}
```

```python
# kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py:304-327
async def _emit_for_payload(bus, topic_suffix, value, unit, captured_at):
    if topic_suffix == "temperature":
        await bus.publish(LiveTemperatureUpdate(...))  # special path
        return
    await _route_to_event(bus, topic_suffix, value, unit)
```

For SpO2 (and everything else except temperature), the kiosk
emits a `MeasurementProposed` immediately. The MQTT layer:

- Does not filter by FSM state. Adverts arriving during IDLE,
  MEASURING_ANTHRO, REPORT, or any other non-MEASURING_VITALS
  state still publish a `MeasurementProposed` — they only get
  dropped at the receipt boundary because
  `_on_measurement_proposed_event` checks `current_session is
None` and the path-vs-type filter (Problem 3 fix /
  [commit e54a02b](kiosk/src/ginhawa_kiosk/gui/main_window.py)).
- Does not consult any session timestamp. Unlike the BP cuff
  fix (ADR-0020), there is no `session_floor` on the
  measurement-proposed event.
- Does check the topic suffix and JSON shape — malformed
  payloads are logged and dropped.

### Receipt-boundary handler

When SpO2 arrives during a vitals-allowed path
(`vitals` or `full`), `_on_measurement_proposed_event`
([main_window.py:868-1009](kiosk/src/ginhawa_kiosk/gui/main_window.py))
runs:

1. Validator
   ([services/validation.py:70-106](kiosk/src/ginhawa_kiosk/services/validation.py#L70-L106))
   accepts the value if `70 ≤ value ≤ 100` and `unit == "%"`. The
   firmware's range maps exactly onto the validator's range —
   anything the firmware buffered is accepted.
2. `current_session` check — present during MEASURING_VITALS.
3. Path filter — `spo2 ∈ _VITALS_TYPES`, accepted.
4. Duplicate guard — `_captured_real_types` is cleared on every
   state-change
   ([main_window.py:445-446](kiosk/src/ginhawa_kiosk/gui/main_window.py#L445-L446)),
   so the first `spo2` in this MEASURING_VITALS visit always
   wins.
5. Persist as `is_valid=1`.
6. `_maybe_advance_measurement_path` — `spo2 ∈ _VITALS_TYPES`,
   contributes to path completion.

**The very first SpO2 message to arrive during MEASURING_VITALS
becomes the citizen's SpO2 for that session.** There is no
session-floor, no Capture button, no second-reading correction
path.

---

## Section 3 — Validator

```python
# kiosk/src/ginhawa_kiosk/services/validation.py:40-49
_RANGES: dict[str, tuple[float, float]] = {
    ...
    "spo2": (70.0, 100.0),
    ...
}
```

```python
# kiosk/src/ginhawa_kiosk/services/validation.py:58-67
_EXPECTED_UNITS: dict[str, frozenset[str]] = {
    ...
    "spo2": frozenset({"%"}),
    ...
}
```

`validate_measurement` returns
`(is_valid=True, validation_notes=None)` for any 70–100 % value.
On failure
([services/validation.py:99-104](kiosk/src/ginhawa_kiosk/services/validation.py#L99-L104))
it returns `is_valid=False` with a descriptive notes string —
which the receipt handler then persists with `is_valid=0` rather
than dropping (see CONTRACT WITH CLOUD comment at
[services/validation.py:21-25](kiosk/src/ginhawa_kiosk/services/validation.py#L21-L25)).
The REPORT screen filters to `is_valid=1`, so out-of-range rows
never reach the citizen — but they still count toward
`_captured_types` and so contribute to path completion, which is
intentional (offline-placeholder semantics).

### What's _not_ checked

- No "Problem 6"-style hard reject at 85 %. The validator's
  floor is the same 70 % the firmware uses. Any reading the
  firmware emitted is in range from the validator's perspective.
- No timestamp / freshness check. The validator sees only
  `(type, value, unit)` — not the message's `captured_at`, not
  any session anchor, not how long the citizen has been on
  the MEASURING_VITALS screen.
- No source-device cross-check (the validator is generic; the
  receipt handler doesn't gate by `source_device`).

---

## Section 4 — Failure-mode traces

### Trace A — Session 1's reading delivered as session 2's

Pre-conditions: a prior session has produced a real SpO2 reading
in the last few minutes. The kiosk and ESP32-A are still running
since boot.

1. Citizen 1 places finger on the MAX30100 during their
   MEASURING_VITALS. The oxullo library's beat detector locks,
   `getSpO2()` returns values that settle around (say) 98 %.
   Hundreds of tick samples land in `g_spo2_buf`.
2. The 30 s reporting window expires (`main.cpp:129`).
   `consume_stable` returns `has_spo2=true, spo2≈98`. The
   firmware publishes one MQTT message to
   `ginhawa/kiosk/<id>/sensors/spo2`. Buffer reset to count=0.
3. Citizen 1 removes finger. Session 1 finishes (kiosk path
   advances; FSM goes END → IDLE).
4. **The firmware's `g_pox` is not reset.** The library's
   internal SpO2 state is still ~98 %. Each subsequent
   `pox.getSpO2()` call returns ~98 (no new beats are arriving,
   but the library does not re-zero — it returns the last
   computed value). The tick at 10 ms keeps pushing those 98 %
   reads into `g_spo2_buf` because 98 ∈ [70, 100]
   ([sensor_max30100.cpp:77](firmware/esp32-a-vitals/src/sensor_max30100.cpp#L77)).
5. The next 30 s window expires. `g_spo2_count` is well past 16. `consume_stable` returns 98 %. The firmware publishes
   another MQTT message — even with no finger on the sensor.
6. Citizen 2's RFID tap arrives shortly after. FSM enters
   MEASURING_VITALS.
7. The next ESP32-A publish (≤ 30 s later) arrives at the
   kiosk. The path-vs-type filter accepts (spo2 ∈ vitals).
   `_captured_real_types` is empty (cleared on state-change).
   The validator accepts (98 ∈ [70, 100]). The kiosk persists
   it as session 2's SpO2 with `is_valid=1`.
8. `_maybe_advance_measurement_path` sees spo2 captured.
   Combined with the other vitals (offline placeholders or real
   readings) the path completes. Citizen 2 transitions to
   REPORT showing session 1's 98 %.

The bug manifests at step 7. Citizen 2 may or may not also place
their finger; even if they do, the duplicate-drop guard at
[main_window.py:937](kiosk/src/ginhawa_kiosk/gui/main_window.py#L937)
prevents the _real_ citizen-2 SpO2 from overwriting the stale
one.

### Trace B — "Random" SpO2 from idle noise

Same setup, except no prior real reading recently — the kiosk
has been idle for hours, or the library has not yet locked onto
a real reading.

1. The library's `getSpO2()` is initially 0 (returned until
   stabilisation). 0 is below `SPO2_MIN=70` so ticks are
   filtered out by
   [sensor_max30100.cpp:77](firmware/esp32-a-vitals/src/sensor_max30100.cpp#L77).
2. A transient — ambient light fluctuation, a hand brushing the
   sensor, the citizen looking at it but not placing the finger
   — produces enough IR/red modulation for the library's beat
   detector to register a few false beats. The estimator
   computes a value the firmware sees as "in range" (perhaps
   72 % from a poorly-modulated red/IR ratio).
3. Ticks during that transient push 16+ samples in the 70–100
   band. The 30 s window expires; the median of those samples
   publishes.
4. Citizen 2's session is now active. The kiosk accepts the
   value with `is_valid=1`. The citizen sees `72 %` on REPORT
   — a value never produced by their actual finger.

This is the "random" form of the symptom: not necessarily
session 1's value but a value the library produced from
non-finger reflectance.

---

## Section 5 — Root cause hypothesis

**Selected: A + B with B as the proximate amplifier.**

The proximate cause is **(A) firmware-side stale library
state**. The library's `getSpO2()` is a memoised last-computed
value, the tick loop accepts it unconditionally as long as it's
in [70, 100], the consume window publishes it unconditionally as
long as 16 such samples accumulated in 30 seconds, and no
firmware path resets `g_pox` between citizens. Smoking-gun pair:

```cpp
// firmware/esp32-a-vitals/src/sensor_max30100.cpp:71-80
void sensor_max30100_tick() {
    g_pox.update();
    float spo2 = g_pox.getSpO2();
    if (spo2 >= SPO2_MIN && spo2 <= SPO2_MAX) {
        _push(g_spo2_buf, g_spo2_count, spo2);
    }
}
```

No finger-presence check. No "is the IR DC level consistent
with tissue contact?" gate. The library's stale memoised SpO2
sails through unchanged.

```cpp
// firmware/esp32-a-vitals/src/sensor_max30100.cpp:233-236
if (g_spo2_count >= MAX30100_MIN_BUFFERED_SAMPLES) {
    r.spo2 = compute_pulse_median(g_spo2_buf, g_spo2_count);
    r.has_spo2 = true;
}
```

Same shape as Hypothesis A in the scale-stale-readings audit:
the library carries forward state the kiosk has no way to know
about; the kiosk-side filter (validator) can't see anything
that lets it tell stale from fresh.

The amplifier is **(B) no kiosk-side session-floor on the SpO2
path**. The kiosk's `_on_measurement_proposed_event` accepts
the first SpO2 to arrive during MEASURING_VITALS regardless of
when the underlying sample was taken on the firmware. A session-
floor of the sort ADR-0020 added to the BP cuff would catch the
specific subset of (A) where the firmware re-publishes a value
captured before the kiosk's MEASURING_VITALS entry — but it
wouldn't catch the "in-MEASURING_VITALS noise" form of (B).

Not the cause:

- **(C) firmware publishes spo2=0 leaking through.** Falsified:
  the 70/100 filter at
  [sensor_max30100.cpp:77](firmware/esp32-a-vitals/src/sensor_max30100.cpp#L77)
  drops 0 values pre-buffer, and `consume_stable` requires the
  buffer to be non-empty. The "stuck at 0" form of the symptom
  isn't reachable through this code path; if it's observed,
  it's likely a kiosk-side display glitch, not a sensor-side
  bug.
- **(D) multiple amplifying interactions** is technically
  accurate (A and B compound) but the _originator_ is A. Fixing
  B alone leaves the in-session noise problem; fixing A alone
  closes both Trace A and Trace B at the source.

---

## Section 6 — Recommended fix sketch

**No code in this section.** Direction with trade-offs.

### Tier 1 (recommended): firmware-side finger-presence gate

Mirror ADR-0019's height stabilisation: require N consecutive
ticks of _evidence the citizen's finger is actually there_ before
admitting the library's SpO2 estimate to the publish buffer.
Cheap, robust signals:

- **IR DC level above a "tissue" threshold.** With no finger,
  the IR LED reflects off the M5Stack shroud / ambient — the
  DC level is either very low or saturated. With a finger,
  the DC level sits in a characteristic mid-range. The
  diagnostic dump already reads IR DC
  ([sensor_max30100.cpp:197-212](firmware/esp32-a-vitals/src/sensor_max30100.cpp#L197-L212));
  the production tick can do the same with `g_pox.getRawIR()`
  (or the equivalent in oxullo's API) once per tick.
- **Red/IR DC ratio matching a perfusion fingerprint** — more
  involved; not needed if the DC level alone discriminates
  reliably.
- **Library "isStabilized" / beat-detector convergence
  signal**, if the library exposes one. Used as _gate_, not
  just a filter: the buffer only accepts values during a
  beat-detected window.

The 30 s publish cadence stays. Add a post-publish cooldown of
~5 s (analogous to ADR-0019's
`HEIGHT_POST_PUBLISH_COOLDOWN_MS`) so a single tap-and-leave
can't immediately re-fire on residual library state.

Trade-off: firmware change requires a reflash, which adds bench
overhead. Worth it because the same fix protects against every
downstream consumer (kiosk, future bench harnesses) without
asking each to add its own gate.

### Tier 2: kiosk-side session-floor (ADR-0020 analog)

The MQTT subscriber stamps `captured_at`
([mqtt_sensors.py:276-280](kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py#L276-L280))
on the message; that timestamp could be carried through to
`MeasurementProposed` and compared against a session*floor set
at MEASURING_VITALS entry. Mirrors the BP cuff fix exactly.
Caveat: the firmware doesn't carry NTP, so `captured_at` is
\_kiosk-receipt time*, not _firmware-sample time_. A session_floor
based on kiosk-receipt time would catch the "stuck at 0 between
sessions" / "publish from before MEASURING_VITALS entry" case but
**not** the "publish at 30 s window aligned with session 2 but
still based on session 1's stale internal state" case.

The session-floor is therefore a useful defence-in-depth layer
(it catches a real subset of the bug) but it is not a complete
fix on its own.

### Tier 3 (not recommended alone): Capture button

A citizen-tap-to-capture flow like the temperature path is the
hardest UX nut — citizens are already holding their finger
still; adding a screen tap mid-measurement is friction. Reject
as the primary fix.

### Tier 4 (definitely not): stricter validator alone

Raising the validator floor from 70 to (say) 90 % would reject
genuine borderline readings as much as it would reject stale
ones — the firmware's library produces 98 % readings when the
citizen actually had a 98 % SpO2 _and_ when the library is
carrying that value forward from before. The validator can't
distinguish. Reject.

### Combined recommendation

**Tier 1 + Tier 2**, in that order.

- Tier 1 (finger-presence gate + post-publish cooldown) is the
  durable, comprehensive fix.
- Tier 2 (session-floor at kiosk) is the cheap defence-in-depth
  that doesn't require a reflash and lands faster. It also
  generalises the architectural pattern — all three of BP, SpO2,
  height, and weight now have receipt-boundary session-relative
  gates.

If only one can land before the defense, Tier 2: it ships in
Python with the existing audit-trail discipline, and the
firmware change can follow in the next sprint.

---

## Section 7 — Cross-references and pattern

This is the **fourth** instance in the cross-session-
contamination family:

| #   | Audit                                                                                   | Sensor / path                     | Mechanism                                                                                            | Fix family                                              |
| --- | --------------------------------------------------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| 1   | [scale-prefiring](2026-05-13-scale-prefiring-audit.md) (commit `e54a02b`)               | Always-on adapters                | No `measurement_path` gate at receipt                                                                | Receipt-boundary path-vs-type filter                    |
| 2   | [bp-stale-readings](2026-05-13-bp-stale-readings-audit.md) (ADR-0020, commit `f7f100a`) | Omron HEM-7155T store-and-forward | Absolute 180 s freshness window with no session anchor                                               | Receipt-boundary `session_floor` + skew                 |
| 3   | [scale-stale-readings](2026-05-13-scale-stale-readings-audit.md) (commit `243b917`)     | Xiaomi S200 BLE adverts           | Stateful `xiaomi_ble.update()` returned cached mass for _any_ device's advert                        | Receipt-boundary **MAC filter**                         |
| 4   | This audit                                                                              | MAX30100 / ESP32-A SpO2 stream    | Library's `getSpO2()` retains last value across citizens, no finger-presence gate, no session anchor | Firmware presence gate + receipt-boundary session-floor |

The architectural rule continues to hold:

> Every always-on, store-and-forward, or broadcast-based sensor
> can deliver data outside the current citizen's capture window.
> The sensor adapter cannot know the session; only the kiosk's
> receipt boundary, gated by something the FSM owns, can stop
> stale data from being attributed.

The SpO2 case is the **first instance where the receipt-boundary
fix alone is incomplete**. The previous three sensors carry a
freshness signal somewhere in their data:

- BP cuff: an embedded `taken_at` timestamp in the SIG payload
  (used by ADR-0020).
- Xiaomi scale: a per-advert MAC (used by commit `243b917`) and
  a discarded advert timestamp (originally suspected, now moot).
- Scale prefiring: the receipt's `measurement_type` itself
  versus the session's `measurement_path` (used by commit
  `e54a02b`).

The MAX30100's MQTT publish carries `value, unit, captured_at`
— and `captured_at` is _kiosk-stamped at receipt_, not firmware-
stamped at sample. So the kiosk has no firmware-side freshness
signal to gate on; the only true freshness signal lives inside
oxullo's library state, which the firmware never surfaces.

The cross-cutting principle is therefore refined for SpO2:

> When the sensor protocol carries no freshness signal the kiosk
> can read, the firmware itself must produce one — either by
> gating its publishes on per-sample sensor evidence
> (finger-presence) or by exposing internal state the kiosk can
> consult.

An umbrella ADR (suggested in audit #3, not yet written) covering
the "receipt-boundary defence for sensor freshness" family should
note this special case: the _implementation surface_ moves up the
stack to the firmware for protocols that don't surface a
freshness primitive.

The existing pattern relations:

- **Audit-trail story.** Same as the other three: structured
  logs at the drop point (e.g., `max30100.no_finger_dropped`
  in the firmware Serial, or
  `mqtt_sensors.spo2_pre_session_floor` on the kiosk side) keep
  forensics readable.
- **No data integrity bug.** Like every prior fix in this
  family, the rejected reading is shed — no row is lost or
  silently mutated. The citizen's session simply doesn't get a
  fresh SpO2 if the firmware never delivered one; the kiosk
  already handles "no SpO2 captured" via the offline-placeholder
  - REPORT-filter mechanism.
- **ADR-0019 prior art.** The height-stabilisation gate is the
  closest template — ESP32-side finger-presence gate would
  largely mirror that file's anchor + window + cooldown
  structure.

---

## Section 8 — Impact and urgency

### Demo readiness

**Visible during defense.** The failure mode is bench-reproducible
by running two back-to-back sessions where citizen 2 does _not_
re-place the finger (or places it briefly and removes). The
oxullo library's last-value memoisation is a deterministic
property of the library, not an intermittent timing race. A
panellist who tries the kiosk twice in succession may surface it
without intending to.

Mitigating factors:

- Within a single session where the citizen does correctly hold
  the finger for the full 30 s window, the value is genuine —
  there is no in-session corruption.
- The MAX30100's 30 s publish cadence + the citizen typically
  needing ≥ 30 s on the MEASURING_VITALS screen means the
  first-of-the-session publish usually arrives mid-finger-on,
  hiding the stale-library-state mode behind a real new value.
- The failure is mostly second-session and beyond. A scripted
  demo with a single demo subject is unlikely to hit it.

### Data integrity

**Wrong SpO2 values are persisted as `is_valid=1`.** The validator
accepts anything in [70, 100] %. A 98 % stale reading from
citizen 1, attributed to citizen 2, looks indistinguishable from
a genuine reading in the DB. The REPORT screen shows it. Sync
pushes it to the cloud as a valid citizen-2 measurement.

Same severity class as the BP cuff bug pre-ADR-0020 and the
scale prefire bug pre-commit `e54a02b`: silently misattributes
one citizen's data to another's session. Unlike a missing reading,
the citizen has no way to know the value is wrong. _This is the
defining harm of the receipt-boundary contamination family._

### Priority

**Fix before next field deployment. Defer just the firmware
component if needed for defense scheduling.**

Order:

1. **Tier 2 (kiosk session-floor + captured_at threading)** —
   ship before defense. Same pattern as ADR-0020; modest code
   change inside the same `_on_measurement_proposed_event`
   handler that already carries the path-vs-type filter and the
   duplicate-drop guard. Catches the cross-session "stuck at
   last value" mode (Trace A) reliably; partially catches
   Trace B.
2. **Tier 1 (firmware finger-presence gate + cooldown)** —
   schedule for the post-defense iteration. Reflash + bench
   verification with a finger-on / finger-off protocol takes
   longer than a Python-side change. Mirrors ADR-0019's
   structure; can pick up its bench-test protocol verbatim.

After both, the SpO2 path joins BP, weight, height, and
temperature in the audit-driven family of receipt-boundary
gated sensors.

---

## Cross-references

- [docs/audits/2026-05-13-scale-prefiring-audit.md](2026-05-13-scale-prefiring-audit.md)
- [docs/audits/2026-05-13-bp-stale-readings-audit.md](2026-05-13-bp-stale-readings-audit.md)
- [docs/audits/2026-05-13-scale-stale-readings-audit.md](2026-05-13-scale-stale-readings-audit.md)
- [docs/audits/2026-05-14-db-lock-contention-audit.md](2026-05-14-db-lock-contention-audit.md)
- ADR-0019 — height stabilisation gate (the firmware template).
- ADR-0020 — BP cuff session_floor (the kiosk template).
- ADR-0021 — SQLite WAL & busy_timeout (most recent infrastructure ADR).
- Future: receipt-boundary-defence umbrella ADR — should note
  SpO2 as the special case where the firmware itself must
  produce the freshness signal.
