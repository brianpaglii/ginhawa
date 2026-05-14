# ADR 0022: MAX30100 SpO2 finger-presence gate

- **Status:** Accepted
- **Date:** 2026-05-14
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira
- **Related:** ADR-0019 (ESP32-B height stabilisation gate),
  ADR-0020 (BP cuff session_floor). Motivating audit:
  [`docs/audits/2026-05-14-spo2-stale-readings-audit.md`](../audits/2026-05-14-spo2-stale-readings-audit.md).
  Fourth fix in the cross-session-contamination family alongside
  the path filter (commit `e54a02b`), the BP session_floor
  (ADR-0020), and the Xiaomi MAC filter (commit `243b917`).

## Context

The ESP32-A's MAX30100 SpO2 publish path relies entirely on
oxullo's `PulseOximeter` library. The library memoises its last
computed SpO2 value: once stable, `getSpO2()` keeps returning
that value even after the citizen removes their finger. The
sensor also produces plausible 90–98 % readings from ambient
light reflecting off the M5Stack shroud, hand proximity, or any
non-finger IR source. Bench 2026-05-14 confirmed the kiosk
displaying SpO2 values with no finger placed on the sensor.

Audit Section 4 walks the failure mode. Audit Section 7 places
this fix in the cross-session-contamination family: every prior
member (path filter, BP session_floor, scale MAC filter) gated
at the kiosk's receipt boundary using a signal the sensor
protocol exposed. The MQTT publish from ESP32-A carries
`{value, unit, captured_at}` and nothing else; the only
finger-presence signal lives in chip-level IR DC voltage that
the library never surfaces.

Consequence: the _implementation surface_ of the receipt-boundary
defence moves up the stack to the firmware for this sensor.

## API discovery

The `PulseOximeter` class
([MAX30100_PulseOximeter.h:49-83](../../firmware/esp32-a-vitals/.pio/libdeps/esp32dev/MAX30100lib/src/MAX30100_PulseOximeter.h#L49-L83))
exposes only `begin / update / getHeartRate / getSpO2 /
setOnBeatDetectedCallback / setIRLedCurrent / shutdown / resume`.
There is **no public IR getter**, **no samples-ready callback**,
and the underlying private `MAX30100 hrm` member (which does
expose `getRawValues`) is not friend-accessible without
modifying the library — explicitly disallowed by the
constraints.

The existing diagnostic at
[`sensor_max30100.cpp:197-212`](../../firmware/esp32-a-vitals/src/sensor_max30100.cpp#L197-L212)
already reads raw IR via direct I²C of FIFO_DATA register
`0x05` on the global Wire. This is the only viable accessor for
the production gate.

Caveat: FIFO_DATA reads are **destructive** — each read pops one
sample from the chip's FIFO that the library's `update()` would
otherwise consume. The diagnostic compensates by reading only
every 500 ms; at the 10 ms production tick a read-per-tick would
steal every sample and break the library's beat detector.

## Decision

Three layered gates in `firmware/esp32-a-vitals/src/sensor_max30100.cpp`:

### Gate 1 — IR DC threshold (`MAX30100_FINGER_IR_THRESHOLD`, 30000)

Direct I²C FIFO_DATA peek returns the chip's raw IR ADC counts.
With no finger the M5Stack shroud / ambient produces ≤ 15000
counts; with finger pressed the ADC sits in 50000–200000.
30000 is comfortably between, calibrated against the bench
breadboard. The threshold is a `constexpr float` in `config.h`
so future fixtures can re-tune without code changes.

### Gate 2 — Warmup window (`MAX30100_FINGER_WARMUP_CHECKS`, 5)

The presence check runs at `MAX30100_FINGER_CHECK_INTERVAL_MS`
cadence (100 ms — chosen so the gate's destructive FIFO reads
steal only ~10 % of the chip's 100 Hz samples, leaving the
library's beat detector with 90 samples / second, well above the
threshold its algorithm needs). 5 consecutive above-threshold
checks = 500 ms wall-clock matches the audit's recommended
warmup.

A single below-threshold check resets BOTH the warmup counter
AND the SpO2 accumulation buffer (`g_spo2_count = 0`). The
buffer reset is the load-bearing detail: without it, an in-
flight 5 s of buildup + 1 s drop + 1 s reattach could publish
on the strength of stale buffer content the citizen never
produced.

### Gate 3 — Post-publish cooldown (`MAX30100_POST_PUBLISH_COOLDOWN_MS`, 5000)

After `consume_stable` publishes a value, it stamps
`g_last_spo2_publish_ms`. For the next 5 s, subsequent
`consume_stable` calls drop their buffer and return `has_spo2=false`
regardless of accumulation. Prevents a borderline finger-on /
finger-off / finger-on sequence from publishing two rapid values
off residual library state.

## Cadence deviation from the audit's sketch

The audit's Section 6 sketch proposed every-tick FIFO reads with
a `MAX30100_FINGER_WARMUP_SAMPLES = 50` constant (= 50 × 10 ms =
500 ms). API discovery showed every-tick reads would consume
100 % of the library's samples — the library's `update()` and
the gate's I²C peek both target the same FIFO_DATA register, and
the chip produces one sample per 10 ms tick. The cadence
deviation (100 ms gate-check interval, 5 checks for warmup) keeps
the same 500 ms warmup wall-clock budget while sharing the FIFO
~10:90 between the gate and the library. Library beat detection
on the bench breadboard remains reliable at 90 Hz effective
sampling.

## Alternatives considered

- **Kiosk-side session_floor (Tier 2 in the audit).** Catches the
  narrow subset of cases where the firmware publishes a value
  captured _before_ MEASURING*VITALS entry. Doesn't catch the
  bench-observed in-session phantom case (firmware emits 95 % off
  ambient light \_during* the citizen's session). Recommended as
  defence-in-depth follow-up, not primary fix.
- **Raise validator threshold to 85 % or 90 %.** The library
  produces values squarely in this range from noise. Validator
  cannot discriminate.
- **Citizen-tap Capture button** (analog to temperature).
  Friction the height / SpO2 paths already designed away. The
  citizen is holding their finger still; a tap-to-confirm forces
  them to look at the screen mid-measurement. Reject.
- **`setOnBeatDetectedCallback` as the gate.** Possible, but
  callbacks fire only after a beat is detected — too sparse for
  the 500 ms warmup we want, and a citizen with a weak / late
  pulse would false-negative on finger-presence for several
  seconds. The direct IR check is faster and more discriminative.
- **Modify the library to expose `getIR()` or a samples-ready
  callback.** Cleanest API but requires forking, which the
  constraints rule out.

## Architectural pattern note

This is the **first** instance in the
cross-session-contamination family where the implementation
surface moves up the stack to the firmware:

| #     | Audit                   | Sensor                            | Mechanism                                  | Fix surface                         |
| ----- | ----------------------- | --------------------------------- | ------------------------------------------ | ----------------------------------- |
| 1     | scale-prefiring         | Always-on adapters                | no path gate at receipt                    | kiosk receipt boundary              |
| 2     | bp-stale-readings       | Omron HEM-7155T store-and-forward | absolute freshness window                  | kiosk receipt boundary (ADR-0020)   |
| 3     | scale-stale-readings    | Xiaomi S200 BLE                   | stateful library cache                     | kiosk receipt boundary (MAC filter) |
| **4** | **spo2-stale-readings** | **MAX30100 / ESP32-A**            | **library memoised state + ambient noise** | **firmware (this ADR)**             |

The cross-cutting principle, refined:

> Every always-on, store-and-forward, or broadcast-based sensor
> needs a session-relative gate. When the sensor protocol carries
> a freshness signal the kiosk can read (BP timestamp, scale
> MAC, measurement-type-vs-path), the gate lives at the kiosk
> receipt boundary. When the protocol exposes no such signal,
> the firmware itself must produce one — gating publishes on
> per-sample sensor evidence before the publish ever leaves the
> device.

An umbrella ADR for the family pattern is suggested as a
follow-up; this ADR is its first firmware-surface instance.

## Trade-offs

- **IR threshold is empirical.** 30000 counts works on the bench
  breadboard with the current shroud. Field deployments may
  require re-calibration via the diagnostic build. The constant
  is exposed in `config.h` so re-tuning is a one-line edit.
- **~10 % library sample loss.** The gate's destructive FIFO
  peek steals ~10 sample / second from the chip's 100 Hz
  stream. Library beat detection works at this rate on the
  bench; if a future deployment shows degraded SpO2 quality the
  cadence can be relaxed to 200 ms (5 % loss, 1 s warmup).
- **500 ms warmup latency.** A citizen who places and removes
  their finger inside 500 ms gets no publish. Intentional — that
  pattern is indistinguishable from a probe of the sensor surface.
- **5 s cooldown after publish.** A citizen wanting a second
  reading inside 5 s gets no publish; the next opportunity is
  the publish window 30 s later. Bench-observed citizen sessions
  don't try this; if a real workflow needs it the cooldown can
  be relaxed.
- **No firmware reset between citizens.** The library still
  retains state across sessions; the gate makes that state
  inaccessible to the publish path rather than clearing it.
  Simpler and harder-to-break than a `g_pox.begin()` re-init
  (which would also bounce the LED current and re-cost a
  stabilisation period).

## Data integrity

Unchanged at the kiosk layer. The kiosk's validator
([`services/validation.py:40-49`](../../kiosk/src/ginhawa_kiosk/services/validation.py#L40-L49))
and receipt-boundary filters
([`gui/main_window.py:119-145`](../../kiosk/src/ginhawa_kiosk/gui/main_window.py#L119-L145))
remain. Phantom readings now never reach the kiosk at all;
rejected readings are shed at the firmware (no MQTT publish
fires). The existing offline-placeholder + REPORT-filter
machinery handles "no SpO2 captured this session" exactly the
same way as before — silently substitutes a placeholder so the
path can complete, hides the placeholder from the citizen-facing
REPORT.

## Verification

Bench protocol:

1. Build + flash: `pio run -t upload`.
2. Open serial monitor (`pio device monitor -b 115200`).
3. Do NOT place finger. Wait 60 s. Expected: no
   `[max30100] published spo2=…` lines. Optionally see
   `[max30100] finger lost, gate reset` if the library briefly
   stabilises off ambient.
4. Place finger. Expected: `[max30100] finger warmed up, gate
open` within ~500 ms. Within the next 30 s publish window, a
   `[max30100] published spo2=95.x` (or similar).
5. Remove finger. Expected: `[max30100] finger lost, gate reset`
   within ~100 ms. No further publishes even though the library's
   `getSpO2()` still returns the old value.
6. Wait ≥ 30 s (one publish cycle) then place a finger again.
   Expected: fresh warmup, fresh publish with the new citizen's
   actual SpO2.
7. Two back-to-back kiosk sessions through the GUI: each shows
   that session's actual SpO2; no carry-over.

Native tests at
`firmware/esp32-a-vitals/test/test_desktop/test_pulse_smoothing.cpp`
exercise the median + JSON encoding logic; this ADR's gate logic
is hardware-bound and intentionally not in the desktop test
surface. `pio test -e native` still passes (11/11) — no
regression in the testable layers.

## References

- Audit: [`docs/audits/2026-05-14-spo2-stale-readings-audit.md`](../audits/2026-05-14-spo2-stale-readings-audit.md)
- Firmware: [`firmware/esp32-a-vitals/src/sensor_max30100.cpp`](../../firmware/esp32-a-vitals/src/sensor_max30100.cpp)
- Tunables: [`firmware/esp32-a-vitals/include/config.h`](../../firmware/esp32-a-vitals/include/config.h)
  (`MAX30100_FINGER_IR_THRESHOLD`,
  `MAX30100_FINGER_CHECK_INTERVAL_MS`,
  `MAX30100_FINGER_WARMUP_CHECKS`,
  `MAX30100_POST_PUBLISH_COOLDOWN_MS`).
- Diagnostic build env: `[env:esp32dev_diag]` in
  [`firmware/esp32-a-vitals/platformio.ini`](../../firmware/esp32-a-vitals/platformio.ini).
- Sibling firmware-side gate: ADR-0019 (height stabilisation).
