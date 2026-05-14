# ADR 0023: SpO2 receipt-boundary session-floor

- **Status:** Accepted
- **Date:** 2026-05-14
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira
- **Related:** ADR-0019 (height stabilisation gate),
  ADR-0020 (BP cuff session_floor), ADR-0022 (MAX30100
  finger-presence gate). Motivating audit:
  [`docs/audits/2026-05-14-spo2-stale-readings-audit.md`](../audits/2026-05-14-spo2-stale-readings-audit.md).
  Defence-in-depth follow-up to ADR-0022.

## Context

The SpO2 stale-readings audit identified two complementary fix
layers for the bench-observed phantom SpO2 bug:

- **Tier 1 (ADR-0022, firmware).** Finger-presence gate on the
  ESP32-A: don't publish unless the chip's raw IR DC signal
  confirms tissue contact. This is the primary fix; it closes the
  in-session phantom case the bench reproduced.
- **Tier 2 (this ADR, kiosk).** Receipt-boundary session_floor: a
  second-line gate against in-flight MQTT messages crossing a
  session boundary, QoS-1 retry / reorder edge cases, and any
  future firmware regression. Structurally identical to ADR-0020
  (BP cuff session_floor), implemented at the kiosk's receipt
  boundary in `main_window._on_measurement_proposed_event` because
  the MQTT subscriber is a generic transport and the session state
  lives on the FSM / main window.

The two layers together complete the architectural symmetry of
the cross-session-contamination series — every sensor that can
deliver data outside the citizen's capture window now has a
session-relative gate.

## API discovery

`MeasurementProposed` previously carried `(measurement_type,
value, unit, source_device, claimed_is_valid, validation_notes)`
but **not** `captured_at`. The MQTT subscriber at
[`mqtt_sensors.py:271-296`](../../kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py)
already stamps `captured_at` (kiosk-receipt time when the
firmware doesn't supply NTP-stamped value) and logs it as
`mqtt.message_routed`, but the field is dropped on event
construction at the existing `_route_to_event` helper.

The gate requires threading `captured_at` through the event chain:

1. `MeasurementProposed` gets a new optional field
   `captured_at: str | None = None`. Default `None` preserves
   compatibility with every existing call site (offline
   placeholders, mock sensors, BP/scale adapters).
2. `mqtt_sensors._emit_for_payload` now forwards `captured_at`
   into `_route_to_event`, which passes it to the
   `MeasurementProposed` constructor.
3. Other adapters (Omron BP, Xiaomi scale, mocks, offline
   placeholders) leave the field unset; the SpO2 gate skips them
   by `measurement_type` and `is_valid_int` checks.

## Decision

`KioskMainWindow` holds a `_spo2_session_floor: datetime | None`
attribute, initialised to `None` and managed by a new helper
`_update_spo2_session_floor(prev_state, new_state)` invoked from
`_on_fsm_state_changed`:

- **Entry** (any state → `MEASURING_VITALS`): stamp the floor with
  `datetime.now(timezone.utc)` and log
  `main_window.spo2_session_floor_set`.
- **Exit** (`MEASURING_VITALS` → any other state): clear the floor
  to `None` and log `main_window.spo2_session_floor_cleared`.

Inside `_on_measurement_proposed_event`, after the path-vs-type
filter and before the duplicate-drop guard / persistence:

```
if event.measurement_type == "spo2" and is_valid_int == 1 and
   self._spo2_session_floor is not None:
    parse captured_at
    if captured_at is missing or malformed:
        log + drop  (fail-closed)
    if captured_at < floor - _SPO2_SESSION_FLOOR_SKEW_S:
        log + drop  (the bug class)
```

`_SPO2_SESSION_FLOOR_SKEW_S = 10.0` is the symmetric tolerance for
the gap between MQTT-subscriber stamp time and main_window state-
change handler time. Larger than any in-loop scheduling overhead
observed; smaller than the firmware's 30 s publish cadence, so a
publish stamped 11+ s before the floor is decisively pre-session.

Gate scope:

- **Only `measurement_type == "spo2"`.** BP has its own
  session_floor in the sensor adapter (ADR-0020); weight has the
  MAC filter + path filter; height has its firmware-side
  stabilisation gate (ADR-0019); temperature is Capture-button-
  gated. Applying the gate to those types would either duplicate
  existing protection or break legitimate event flows that don't
  carry `captured_at`.
- **Only `is_valid_int == 1`.** Offline placeholders
  (`claimed_is_valid=False`, `validation_notes="sensor_offline"`)
  pass through. Without this exemption the path-completion
  machinery would hang on a placeholder spo2 that the gate just
  dropped.
- **Only when a floor is set.** The floor is `None` outside
  MEASURING_VITALS — a publish that arrives during IDLE or REPORT
  is dropped by the existing `current_session is None` check
  (or the path-vs-type filter), and the floor gate becomes a
  no-op there.

## Alternatives considered

- **Gate inside `mqtt_sensors`.** Rejected — `mqtt_sensors` is a
  generic transport. Coupling it to the FSM / main_window's
  session state would force every test that exercises the
  subscriber to mock a state machine. The receipt boundary at
  `main_window._on_measurement_proposed_event` already has the
  state visibility for the path-vs-type filter; adding the
  session_floor there is the layered-equivalent of the BP fix
  in ADR-0020.
- **Use firmware-stamped captured_at.** The ESP32-A has no NTP
  and its onboard clock drifts. Kiosk-stamped receipt time is
  the only reliable timestamp.
- **Skip Tier 2 entirely.** Tempting since the firmware fix closes
  the bench-observed case. Rejected because the defence-in-depth
  principle has demonstrated value across the audit series (BP
  cuff, scale MAC, path filter) and a session_floor closes the
  in-flight / QoS-retry / future-regression edge cases the
  firmware gate doesn't see.
- **Fail-open on captured_at parse error.** A malformed timestamp
  would slip through to persistence. Fail-closed (drop + log)
  trades a one-off lost reading for visibility into the malformed
  payload via journalctl. The kiosk's "fail loud, fail safe" rule
  (CLAUDE.md) favours fail-closed for citizen-attribution paths.

## Trade-offs

- **10 s skew is a magic number.** Chosen to bound the worst
  observed in-loop scheduling gap and the firmware's typical
  100 ms publish-vs-subscriber latency, with comfortable margin
  on both sides. A different deployment with a busier MQTT
  broker or laggier asyncio loop may need re-tuning. Constant
  exposed at module scope in
  [`main_window.py`](../../kiosk/src/ginhawa_kiosk/gui/main_window.py)
  for one-line edits.
- **`captured_at` is an optional field on `MeasurementProposed`.**
  Adapters that don't stamp it leave it unset. This is fine —
  the gate's `is None` short-circuits via the
  `event.measurement_type == "spo2"` check, since BP/scale/etc.
  never have `measurement_type == "spo2"` and never trigger the
  parse path. A future SpO2-emitting adapter that doesn't stamp
  `captured_at` would hit `spo2_captured_at_missing` and drop —
  the fail-closed posture surfaces the wiring bug rather than
  silently bypassing the gate.
- **Floor lives on `main_window`, not the FSM.** Symmetrical to
  the existing `_captured_types` / `_captured_real_types` state
  that's also state-coupled but lives on the GUI window. Keeps
  the FSM single-purpose (state graph + audit emission) and
  avoids adding receipt-time concerns to it.

## Architectural pattern note

| ADR / commit        | Sensor                    | Fix surface                                            |
| ------------------- | ------------------------- | ------------------------------------------------------ |
| commit `e54a02b`    | All MQTT-fed types        | kiosk receipt boundary (path-vs-type filter)           |
| ADR-0019            | ESP32-B height            | firmware (stabilisation + cooldown)                    |
| ADR-0020            | Omron HEM-7155T BP cuff   | kiosk sensor adapter (session_floor on cuff timestamp) |
| commit `243b917`    | Xiaomi S200 scale         | kiosk receipt boundary (MAC filter)                    |
| ADR-0022            | MAX30100 SpO2 (firmware)  | firmware (finger-presence gate)                        |
| **ADR-0023 (this)** | **MAX30100 SpO2 (kiosk)** | **kiosk receipt boundary (session_floor)**             |

SpO2 is the **first sensor to receive a fix in BOTH layers** —
firmware _and_ kiosk receipt boundary. The refined principle:

> When the sensor protocol carries no freshness signal the kiosk
> can read natively, the firmware itself must produce one
> (ADR-0022). When defence-in-depth matters — phantom publishes
> from library regressions, QoS-1 retries crossing a session
> boundary, future protocol changes — the kiosk's receipt
> boundary holds an independent session-relative gate
> (this ADR).

The receipt-boundary defence pattern (path filter, BP
session_floor, scale MAC filter, SpO2 session_floor) deserves an
umbrella ADR as a follow-up; this ADR is its fourth instance.

## Data integrity

Unchanged. Rejected readings are dropped at the receipt boundary
(no DB write attempted). The existing offline-placeholder +
REPORT-filter machinery handles "no SpO2 captured this session"
exactly the same way as before. The cloud sync path is
unaffected — only persisted rows reach the sync daemon, and a
dropped reading was never persisted.

The captured_at field is now part of the audit-trail picture: a
forensic reconstruction can show, for any dropped SpO2, the
exact stamping time and the floor it failed against
(`spo2_pre_session_floor_dropped` carries `captured_at`,
`session_floor`, `delta_to_floor_s`, and `source_device`).

## Verification

Unit tests in
[`kiosk/tests/gui/test_spo2_session_floor.py`](../../kiosk/tests/gui/test_spo2_session_floor.py)
pin the nine behaviours:

- Floor set on MEASURING_VITALS entry.
- Floor cleared on MEASURING_VITALS exit.
- Accept when captured_at is at/after floor.
- Accept when captured_at is within skew before floor.
- Drop when captured_at is well before floor (the bug class).
- Fail-closed on malformed captured_at.
- Fail-closed on missing captured_at for a real spo2 event.
- Non-spo2 types unaffected by the gate.
- Offline placeholders bypass the gate (is_valid=0 exemption).

84/84 GUI tests pass; mypy strict clean.

Bench protocol (with the firmware fix from ADR-0022 deployed):

1. Run two back-to-back vitals sessions through the kiosk GUI.
2. Watch journalctl for the structured events:
   ```
   sudo journalctl -u ginhawa-kiosk -f |
     grep -iE "spo2_session_floor|spo2_pre_session_floor|spo2_captured_at"
   ```
3. Expect `spo2_session_floor_set` on each MEASURING_VITALS
   entry and `spo2_session_floor_cleared` on each exit.
4. Expect NO `spo2_pre_session_floor_dropped` events under
   normal operation — the firmware fix prevents the class from
   arising in steady state. If one fires, it indicates either an
   in-flight MQTT message crossing a session boundary
   (legitimate, rare) or a firmware regression
   (worth investigating).

## References

- Audit: [`docs/audits/2026-05-14-spo2-stale-readings-audit.md`](../audits/2026-05-14-spo2-stale-readings-audit.md)
- Code: [`kiosk/src/ginhawa_kiosk/gui/main_window.py`](../../kiosk/src/ginhawa_kiosk/gui/main_window.py)
  (`_SPO2_SESSION_FLOOR_SKEW_S`, `_update_spo2_session_floor`,
  `_on_measurement_proposed_event`).
- Event schema: [`kiosk/src/ginhawa_kiosk/fsm/event_bus.py`](../../kiosk/src/ginhawa_kiosk/fsm/event_bus.py)
  (`MeasurementProposed.captured_at`).
- Adapter: [`kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py`](../../kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py)
  (`_route_to_event` now threads captured_at).
- Test: [`kiosk/tests/gui/test_spo2_session_floor.py`](../../kiosk/tests/gui/test_spo2_session_floor.py).
- Companion firmware ADR: ADR-0022.
