# Xiaomi scale prefiring during MEASURING_VITALS — audit

Date: 2026-05-13
Scope: read-only. No code changed.

## Symptom

A `weight` measurement row lands in the kiosk DB during a
`vitals_only` session, attributed to the active session, with no
citizen having stepped on the scale. The post-fix REPORT filter
(commit `b124968`) hides the row from the citizen-facing screen,
but the row is still persisted and counts toward duplicate-guard
state, with downstream consequences.

---

## Section 1 — Adapter lifecycle

### Where the adapter is constructed and started

- The Xiaomi scale adapter is constructed once at app boot from the
  sensor factory: [**main**.py:130](kiosk/src/ginhawa_kiosk/__main__.py#L130)
  (`sensors = create_all_sensors(bus, settings, db)`).
- `boot_sensors` then calls `await sensor.start()` for every
  registered sensor exactly once:
  [**main**.py:151-162](kiosk/src/ginhawa_kiosk/__main__.py#L151-L162).
- `XiaomiScaleSensor.start` subscribes to `SessionResetForSensors`,
  constructs the `XiaomiBluetoothDeviceData` decoder, builds a
  `BleakScanner` whose detection callback is `_on_advertisement`,
  and starts the scanner: [xiaomi_scale.py:281-318](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L281-L318).
  After this the `BleakScanner` is alive for the rest of the kiosk
  uptime.

### Where it is stopped / paused / resumed

- `stop()` is only invoked at application shutdown via the boot
  helpers; no FSM state-change ever calls it. Implementation:
  [xiaomi_scale.py:333-343](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L333-L343).
- `_pause_scanner` / `_resume_scanner` exist but are only registered
  with the `BleAdapterLock` so that the **BP cuff's** directed
  connect can briefly take exclusive use of `hci0`:
  [xiaomi_scale.py:315-318](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L315-L318)
  and [xiaomi_scale.py:345-378](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L345-L378).
  They have nothing to do with session state.
- `Sensor` abstract base ([base.py:29-49](kiosk/src/ginhawa_kiosk/sensors/base.py#L29-L49))
  exposes only `start`, `stop`, and `is_running`. No state-aware
  hook (`pause_for_state`, `expects_path`, etc.) exists on the
  contract.

### Per-session state — the stability gate

The adapter's only session-scoped state is `_WeightStabilityGate`
([xiaomi_scale.py:84-166](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L84-L166)).
Its contract:

- `accept(value)` buffers `K=3` readings; once the buffer is full
  and stable (`max - min ≤ 0.2 kg`), it publishes the median and
  **locks itself** ([xiaomi_scale.py:117-140](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L117-L140)).
- `unlock()` clears the buffer, releases the lock, and stamps
  `_unlocked_at` for the 8 s warmup window
  ([xiaomi_scale.py:142-146](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L142-L146)).
- The gate is unlocked via the `SessionResetForSensors` event
  ([xiaomi_scale.py:301](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L301)
  subscribe; [xiaomi_scale.py:330-331](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L330-L331)
  handler).
- **Critical:** the gate is the _only_ thing that turns a stream of
  BLE adverts into "a single weight per session". It is **not**
  path-aware. It does not know whether the FSM is in
  `MEASURING_VITALS`, `MEASURING_ANTHRO`, `IDLE`, or anywhere else.

### Kiosk-side gating of incoming weight publishes

The bus subscription that catches a published weight is
[main_window.py:415](kiosk/src/ginhawa_kiosk/gui/main_window.py#L415)
(`self._bus.subscribe(MeasurementProposed, self._on_measurement_proposed_event)`).

The handler at
[main_window.py:868-947](kiosk/src/ginhawa_kiosk/gui/main_window.py#L868-L947)
does **not** check the FSM state, the session's `measurement_path`,
or the sensor's `source_device` against the current path. It only:

- runs the validator,
- drops out-of-flow events when `current_session is None`
  ([line 901](kiosk/src/ginhawa_kiosk/gui/main_window.py#L901)),
- drops duplicates of the same `measurement_type` whose REAL
  reading was already persisted this state
  ([line 917](kiosk/src/ginhawa_kiosk/gui/main_window.py#L917)),
- and otherwise inserts the row
  ([line 941-942](kiosk/src/ginhawa_kiosk/gui/main_window.py#L941-L942)).

There is no kiosk-side path filter on publishes; the only "should
I publish?" gate is the sensor-side `_WeightStabilityGate`, and
that gate is path-blind.

---

## Section 2 — Failure mode trace (vitals_only path)

Pre-conditions: citizen taps RFID, picks Tagalog, picks `vitals`.
A stray Xiaomi advert is in range (cached frame from a previous
session, or someone leans against the scale).

1. FSM enters `LANGUAGE_SELECT` after identification.
   `_on_fsm_state_changed` runs at
   [main_window.py:429-449](kiosk/src/ginhawa_kiosk/gui/main_window.py#L429-L449),
   clears `_captured_types` / `_captured_real_types`
   ([lines 445-446](kiosk/src/ginhawa_kiosk/gui/main_window.py#L445-L446)),
   then calls `_maybe_publish_session_reset`
   ([lines 470-485](kiosk/src/ginhawa_kiosk/gui/main_window.py#L470-L485))
   which publishes `SessionResetForSensors`.

2. The scale's `_on_session_reset` fires
   ([xiaomi_scale.py:330-331](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L330-L331)),
   calling `reset_for_new_session` → `_gate.unlock()`. The gate is
   now armed; `_unlocked_at = monotonic()`. The 8 s warmup is
   running.

3. Citizen taps "Vitals only" on `PATH_CHOICE`. FSM transitions to
   `MEASURING_VITALS`. `_configure_state_specific` at
   [main_window.py:525-534](kiosk/src/ginhawa_kiosk/gui/main_window.py#L525-L534)
   seeds offline placeholders and fires `BpMeasurementRequested`.
   **No `SessionResetForSensors` is published here.** Crucially,
   the scale is also not stopped, paused, or otherwise told that
   anthro is not in play.

4. 8 s after the LANGUAGE_SELECT reset, the gate's warmup window
   expires ([xiaomi_scale.py:130-132](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L130-L132)).
   Any subsequent advert with a `mass` entity is buffered.

5. Three Xiaomi adverts within tolerance arrive (cached frames,
   nearby motion, or actual residual weight on the scale). The
   handler `_on_advertisement` →
   `_on_sensor_update`
   ([xiaomi_scale.py:400-446](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L400-L446))
   feeds each into `_gate.accept`.

6. On the third stable reading, the gate publishes:
   [xiaomi_scale.py:438-446](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L438-L446)
   emits `MeasurementProposed(measurement_type="weight", …)` on the
   bus and locks itself.

7. `_on_measurement_proposed_event`
   ([main_window.py:868-947](kiosk/src/ginhawa_kiosk/gui/main_window.py#L868-L947))
   runs. `current_session` exists (created on `PATH_CHOICE` entry
   by the FSM, see
   [session_fsm.py:566](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L566)
   and [796-825](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L796-L825));
   the validator accepts weight in `kg`; `weight` is not in
   `_captured_real_types` (cleared on state entry); the row is
   persisted at
   [main_window.py:927-942](kiosk/src/ginhawa_kiosk/gui/main_window.py#L927-L942)
   with `session_id = current_session.id` and `is_valid=1`.

8. `_on_measurement_persisted` adds `"weight"` to `_captured_types`
   and `_captured_real_types`
   ([main_window.py:958-960](kiosk/src/ginhawa_kiosk/gui/main_window.py#L958-L960)).
   `_maybe_advance_measurement_path`
   ([main_window.py:975-986](kiosk/src/ginhawa_kiosk/gui/main_window.py#L975-L986))
   checks `_VITALS_TYPES.issubset(_captured_types)` — weight is not
   a vitals type so the path doesn't false-complete. **But the row
   is in the DB tagged to this session.**

The bug manifests at step 7: a `measurements` row with
`type='weight'`, `is_valid=1`, `source_device='xiaomi_s200_ble'` is
persisted against the vitals_only session.

---

## Section 3 — Root cause hypothesis

**Selected: A** (with refinement).

The Xiaomi scale BLE adapter is started once at boot and stays
listening for the entire kiosk uptime. Its sole "should I publish"
gate is `_WeightStabilityGate`, which is purely a _one-weight-per-
session_ rate limiter — it has no concept of the session's chosen
`measurement_path`. The kiosk-side `_on_measurement_proposed_event`
handler likewise has no path filter. Together they form a path-
blind pipeline: any stable run of three in-tolerance Xiaomi adverts
arriving after the LANGUAGE_SELECT reset and after the 8 s warmup
will produce one persisted weight row, regardless of whether the
session is vitals_only, anthropometric_only, or full_check.

Smoking-gun lines:

- The gate's only state checks are `_locked` and the warmup
  timestamp — no path, no FSM coupling:

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

- The kiosk's measurement handler has no path filter — only the
  no-session and duplicate guards:

  ```python
  # main_window.py:898-924 (abbreviated)
  if self._fsm.current_session is None:
      _log.warning("main_window.measurement_without_session", ...)
      return
  if is_valid_int == 1 and event.measurement_type in self._captured_real_types:
      _log.warning("main_window.duplicate_measurement_dropped", ...)
      return
  # ... persist
  ```

- The asymmetry that lets this slip through is in
  `_configure_state_specific`:
  - MEASURING_VITALS branch
    ([main_window.py:525-534](kiosk/src/ginhawa_kiosk/gui/main_window.py#L525-L534))
    fires `BpMeasurementRequested` but does nothing to the scale.
  - MEASURING_ANTHRO branch
    ([main_window.py:535-547](kiosk/src/ginhawa_kiosk/gui/main_window.py#L535-L547))
    explicitly publishes `SessionResetForSensors` to unlock the
    gate — implying the gate is treated as the only state-aware
    seam, but unlock is the wrong primitive for "anthro is not
    happening this session."

In other words: the gate's `unlock` already fires at
`LANGUAGE_SELECT` ([main_window.py:478-485](kiosk/src/ginhawa_kiosk/gui/main_window.py#L478-L485)),
so the gate is **armed and waiting** during the entire
MEASURING_VITALS window. The MEASURING_ANTHRO re-unlock at line
547 exists to handle a known stale-broadcast race within a single
session — not to gate weight by path.

---

## Section 4 — Recommended fix sketch

**No code in this section.** Direction only.

The right primitive is a _path-aware suppression_ at the kiosk
boundary, not at the BLE adapter. The scale should keep doing what
it does today (listen always, stability-gate to one publish per
unlock, lock after publish); the kiosk should decide whether to
honour the publish based on `current_session.measurement_path` at
the moment the `MeasurementProposed` arrives. This is the same
seam the REPORT filter (commit `b124968`) used and would generalise
to every continuously-listening sensor.

Concretely, `_on_measurement_proposed_event` should grow a
path-vs-type check immediately after the `current_session is None`
guard: a real (`is_valid=1`) weight or height arriving when
`measurement_path == "vitals"` is a prefire and should be logged
and dropped before the DB write; vice versa for vitals types
arriving under `measurement_path == "anthropometric"`. Offline
placeholders (`is_valid=0`, `validation_notes="sensor_offline"`)
should remain exempt — they are seeded by the kiosk itself with
full knowledge of state, so the path filter would be redundant.

Tearing down the adapter between sessions is a worse fix: BLE
scanner restart on every state change adds re-discovery latency
to the BP path (which already relies on the scanner being up for
the cuff's directed connect via `BleAdapterLock`), and the failure
mode would be replaced with "first MEASURING_ANTHRO entry after a
state churn loses the first valid weight while the scanner is
warming up." Filtering at receipt is cheaper and keeps the
audit-trail story honest: the DB still records that the scale
emitted, the row just isn't persisted into the session's
measurement set.

Other always-on / mostly-on sensors to audit for the same gap:

- **MQTT subscriber** (`MqttSensorSubscriber`,
  [mqtt_sensors.py:111-296](kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py#L111-L296))
  is symmetrically affected. SpO2 / heart_rate / height all flow
  through `_route_to_event` →
  `MeasurementProposed`
  ([mqtt_sensors.py:330-342](kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py#L330-L342))
  with no state or path check. The ESP32-B can prefire `height`
  during a vitals_only session under the same logic. Temperature
  is exempt because it routes to `LiveTemperatureUpdate` and only
  becomes a `MeasurementProposed` on citizen tap
  ([mqtt_sensors.py:304-327](kiosk/src/ginhawa_kiosk/sensors/mqtt_sensors.py#L304-L327)).

- **Omron BP cuff** (`OmronBpSensor`,
  `kiosk/src/ginhawa_kiosk/sensors/omron_bp.py`) is **not**
  affected: its handler only runs on `BpMeasurementRequested`
  ([omron_bp.py:433](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L433))
  and aborts on `BpMeasurementRequestCancelled` published when
  MEASURING*VITALS is left
  ([main_window.py:464-468](kiosk/src/ginhawa_kiosk/gui/main_window.py#L464-L468)).
  It is the model the scale & MQTT subscribers should emulate at
  \_receipt time* even if they remain always-listening.

A single helper that maps `(measurement_path, measurement_type) →
allowed?` would centralise the rule; both this audit and the
existing `_VITALS_TYPES` / `_ANTHRO_TYPES` sets in
[main_window.py:115-116](kiosk/src/ginhawa_kiosk/gui/main_window.py#L115-L116)
and the same sets duplicated in
[report.py:30-41](kiosk/src/ginhawa_kiosk/gui/screens/report.py#L30-L41)
already encode that mapping — the third use site is the
persistence path.

---

## Section 5 — Related anomalies (flag-only)

1. **Same-type duplicate-drop spans state entry, not session.**
   `_captured_real_types` is cleared on every state change
   ([main_window.py:445-446](kiosk/src/ginhawa_kiosk/gui/main_window.py#L445-L446)).
   In a `full_check` session, a prefire weight persisted during
   MEASURING_VITALS does **not** block the real weight from being
   persisted during MEASURING_ANTHRO — but the DB ends up with
   two `type='weight'` rows for the same session, both
   `is_valid=1`, both attributed to `xiaomi_s200_ble`. The REPORT
   filter currently shows whichever row the query orders first.
   If the kiosk-side path filter (Section 4) is added, this case
   disappears because the prefire is dropped before persistence.

2. **Problem 1 candidate — BP delivery after MEASURING_ANTHRO.**
   The Omron handler is cancelled the instant the FSM leaves
   MEASURING*VITALS
   ([main_window.py:464-468](kiosk/src/ginhawa_kiosk/gui/main_window.py#L464-L468)).
   If the citizen presses the cuff's BT button \_after* the FSM
   has already advanced to MEASURING_ANTHRO (because every vitals
   placeholder seeded and `measurement_path_complete` fired before
   the cuff finished), the cuff's eventual notification reaches a
   handler that has already taken the cancel path
   ([omron_bp.py:586-631](kiosk/src/ginhawa_kiosk/sensors/omron_bp.py#L586-L631)).
   This is a different bug class from the scale prefire but lives
   in the same family — state-coupled sensors with no
   "post-arrival state check." A path-vs-type filter at receipt
   would not fix it (the type is `systolic_bp`, the path is
   `full`, both legal); the fix would be a deferred-cancel or a
   state-extension that keeps MEASURING_VITALS alive while the
   cuff handler is mid-connect.

3. **8 s warmup is silent about its expiry.** Once `_unlocked_at`
   crosses the threshold
   ([xiaomi_scale.py:130-132](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L130-L132))
   the gate has no per-state knob: the next stable triplet wins.
   The 2026-05-09 bench-evidence comment in the source attributes
   the original race to _adapter resume_, but the same warmup gate
   does not protect against "we entered a vitals*only path and a
   weight was always going to be invalid." Logging the gate's
   publish at `info` is fine; what's missing is a state-aware
   suppressor \_upstream* of the gate.

4. **`BleAdapterLock` interaction.** During a BP capture the
   scanner is paused; on resume, the gate restarts its warmup
   ([xiaomi_scale.py:377](kiosk/src/ginhawa_kiosk/sensors/xiaomi_scale.py#L377)).
   If the scale prefire in Section 2 happened _before_ the BP
   triple completes, the gate is locked and the resume's
   `restart_warmup` is moot. If the prefire happens _after_ the
   BP triple (e.g., the citizen wandered toward the scale during
   SpO2), the scale could publish a second weight during
   MEASURING_VITALS, which then races the real
   MEASURING_ANTHRO weight via the duplicate-drop (item 1).
