# ADR 0019: Stabilization gate on the ESP32-B height publish path

- **Status:** Accepted
- **Date:** 2026-05-13
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The F3 firmware on ESP32-B publishes a smoothed VL53L0X height
reading every `HEIGHT_SAMPLE_INTERVAL_MS` (500 ms). When a citizen
walks under the pillar, the first one or two smoothed samples catch
a shoulder, hair, or a mid-step pose — pushing the computed height
50–80 cm below the citizen's actual standing height. Bench evidence:
a 172 cm citizen entering the range produced an initial 90 cm
reading, which the kiosk took as the first publish and rendered on
the REPORT screen.

The kiosk-side validator only enforces unit + physiological range
(100–198 cm); a 90 cm reading from a tall citizen is in-range and
therefore plausible. The validator can't distinguish "real 90 cm
child" from "shoulder catch on adult." This has to be solved at the
sensor's source — by the time a value reaches the kiosk, the
context (entering vs. settled) is gone.

Two related observations shaped the response:

1. The session FSM is happy to wait. A citizen scanned into a
   measurement path stands under the pillar for ~10 s on average
   before the BHW advances the screen. We have time budget to
   require stillness before accepting a value.
2. There is no current cross-talk between the kiosk and ESP32-B
   beyond MQTT publish. The firmware is fire-and-forget. Any gating
   must be self-contained on the device.

## Decision

ESP32-B's loop calls a new `sensor_vl53l0x_tick_gate()` instead of
publishing every smoothed read. The gate state machine:

- Anchors on the first in-range smoothed reading and records the
  window start time.
- Every subsequent reading within `±HEIGHT_STABILIZATION_TOLERANCE_CM`
  (1 cm) of the anchor advances a sample count and keeps the
  window alive.
- A reading outside tolerance discards the anchor and starts a
  fresh window with the new reading as the new anchor — strict
  rather than running-mean, so slow drift cannot accumulate the
  window to firing.
- A reading with `has_value=false` (timeout or out-of-range) does
  NOT reset the window — the citizen may have flickered briefly
  out of one of the median-of-3 samples. If the sensor stays bad
  for the whole window, the window expires of its own accord and
  no publish fires.
- When the window age reaches `HEIGHT_STABILIZATION_WINDOW_MS`
  (5 s), the gate fires `fired=true` with the anchor value as the
  publish payload, and arms a `HEIGHT_POST_PUBLISH_COOLDOWN_MS`
  (5 s) cooldown during which every tick returns `fired=false`
  regardless of underlying reads.

Net effect: the citizen must stand still for ~5 s for the kiosk to
receive a single height value. Re-firing for the same standstill is
blocked by the cooldown.

## Alternatives considered

- _Kiosk-side "Capture height" button_ — rejected. The citizen is
  already mostly motionless under the pillar; adding a BHW tap to
  the measuring flow is friction the device-side gate solves
  automatically, and the BHW would need to look at the screen
  rather than at the citizen.
- _Running-mean stabilization_ — rejected as the comparison basis.
  A running mean with a small tolerance permits slow drift to
  build up — if the citizen leans forward gradually over 5 s, the
  mean tracks the lean and the gate fires on a wrong value.
  Anchor-based comparison rejects any deviation > 1 cm from the
  initial sample, which is closer to "did this person stand
  still?" than "is the running average steady?"
- _Wider tolerance (e.g., ±3 cm) with shorter window (e.g., 3 s)_
  — rejected for v1. The shoulder-catch problem produces deltas of
  20–80 cm, so the wide tolerance doesn't help reject it; the
  shorter window makes "still" easier to achieve but also makes
  "still while reaching for the cuff" qualify. 5 s × 1 cm matches
  what the BHWs already verbally coach citizens to do ("stand
  still while we measure").
- _MQTT-driven session reset from the kiosk_ — out of scope for
  v1. ESP32-B currently has no MQTT subscriber. The natural
  re-arm via cooldown is sufficient for the one-citizen-at-a-time
  kiosk session model. The reset hook (`sensor_vl53l0x_reset_gate`)
  is exposed for the day we add an MQTT subscriber on the device.

## Consequences

- **Publish cadence drops from 2 Hz to one-per-stable-standstill.**
  The kiosk's `mqtt_sensors` subscriber sees fewer events; the
  measuring screen's "height pending → height captured" transition
  now waits up to 5 s instead of firing on the first valid read.
  Bench testing should verify the BHW-facing waiting feels
  intentional, not broken.
- **Mid-step / walk-through citizens produce no publish at all.**
  Acceptable for v1 — the BHW will coach a walker to stop. If a
  future deployment supports walk-through measurement, the gate
  becomes the wrong model and we revisit.
- **`HEIGHT_SAMPLE_INTERVAL_MS` still 500 ms.** The gate's window
  consumes ten ticks; lengthening the interval would lengthen the
  window proportionally. Kept at 500 ms so reset behaviour (citizen
  moves, anchor resets to the new reading) feels responsive.
- **The `sensor_vl53l0x_read_smoothed` function and its
  `OptionalHeight` return shape are unchanged** — the gate is
  layered on top. Existing tests / consumers that import that
  function don't need to migrate.
- **Bench-test protocol** (logged in firmware README):
  1. Walk into range and stop. Serial monitor should show
     `STAB: window start`, then `STAB: building` every ~2 s, then
     `STAB: FIRE` after ~5 s, then no further publishes until step
     5 below.
  2. Stay there. No second publish.
  3. After 5 s cooldown, no fresh publish until you step out and
     back in (the citizen has been the anchor the whole time;
     cooldown ended but a fresh window needs a fresh anchor read,
     which requires a new in-range arrival).
  4. Step out, wait 6 s, step back in. Gate fires again ~5 s later.
  5. Walk back and forth through the range without stopping. The
     serial monitor shows `STAB: reset` lines, no publishes.

## References

- ADR-0018 — ESP32-B is height-only since the MLX90640 moved.
- `firmware/esp32-b-anthro/include/config.h` — tunables
  (`HEIGHT_STABILIZATION_WINDOW_MS`,
  `HEIGHT_STABILIZATION_TOLERANCE_CM`,
  `HEIGHT_POST_PUBLISH_COOLDOWN_MS`).
- `firmware/esp32-b-anthro/src/sensor_vl53l0x.cpp` — gate state
  machine in the anonymous namespace at the bottom of the file.
