i# ADR 0XXX: Move MLX90640BAB from ESP32-B to ESP32-A

- **Status:** Accepted, supersedes ADR-0004's sensor-cluster pairing
- **Date:** 2026-05-09
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

ADR-0004 paired the MLX90640BAB thermal imager with the VL53L0X
height sensor on ESP32-B (the stand node), grouping them under
"anthropometric measurements." That decision was based on
computational bandwidth analysis: the thermal imager's frame-rate
needs were judged compatible with VL53L0X's discrete-shot triggering
but not with the MAX30100's continuous 100 Hz sampling on the same
microcontroller.

Subsequent physical layout work for the kiosk surfaced a constraint
ADR-0004 did not consider: the thermal imager must be aimed at the
citizen's forehead at a 25–30 cm working distance (per its emissivity
calibration and centre-ROI peak-detection design). The vertical
pillar housing ESP32-B and the VL53L0X has no mounting surface
suitable for the thermal imager — VL53L0X looks down at the
citizen's head from above to measure height, but the thermal camera
needs to look at the forehead from the front at a controlled
distance. That viewing geometry is naturally satisfied at the
kiosk-console platform, alongside the BP cuff and SpO2 shroud, where
the citizen sits with their face at the right distance.

Cable run measurements for the two configurations:

- MLX90640BAB → ESP32-A (kiosk console): ≈ 40–50 cm
- MLX90640BAB → ESP32-B (vertical pillar): > 1 m

The MLX90640BAB communicates over I²C at 400 kHz (Fast Mode). The
I²C specification permits up to 400 pF total bus capacitance, which
practically caps reliable cable length at ~1 m for unshielded
twisted-pair at 400 kHz. The > 1 m run to ESP32-B would operate at
or beyond that limit, with thermal frames containing 768 16-bit
words per frame and any line corruption translating to faulty
forehead-temperature readings. Adding I²C bus extenders or shielding
is possible but adds cost, complexity, and points of failure.

## Decision

Move the MLX90640BAB from ESP32-B to **ESP32-A** (the kiosk-console
node). The new sensor pairing is:

- **ESP32-A** (kiosk-console node): MAX30100 (SpO2, heart rate) +
  MLX90640BAB (forehead temperature)
- **ESP32-B** (vertical-pillar node): VL53L0X (height)

ESP32-A drives the two sensors on **two separate I²C buses** to
preserve the bandwidth isolation that ADR-0004 sought:

- Wire (default I²C0, GPIO 21/22): MAX30100 only
- Wire1 (I²C1, alternate GPIO pins, e.g. GPIO 25/26): MLX90640BAB

This addresses ADR-0004's original concern: the MLX90640BAB's
~250 ms frame-read no longer blocks the MAX30100's 100 Hz sampling,
because the two sensors share no bus.

## Alternatives considered

- _Keep ADR-0004's pairing; run > 1 m I²C cable to ESP32-B_ —
  rejected. Bus capacitance approaches I²C spec limit; thermal
  frames are noise-sensitive (each pixel is a 16-bit word) and
  silent corruption would be hard to detect from the kiosk side.
- _Add an I²C bus extender (e.g. P82B96) for the long cable run_ —
  rejected. Adds cost (~$3-5/board), increases assembly complexity,
  introduces another active component subject to failure, and is
  unnecessary once the thermal camera is moved to ESP32-A.
- _Mount the MLX90640BAB on the vertical pillar pointing forward at
  forehead height_ — rejected. The pillar's purpose is height
  measurement (VL53L0X looking down); a forward-facing thermal
  camera mid-pillar would interfere with citizen positioning and
  fall outside the 25–30 cm controlled working distance.
- _Single I²C bus on ESP32-A for both MAX30100 and MLX90640BAB_ —
  rejected for the same bandwidth reasons that motivated ADR-0004.
  The MLX90640's frame reads would drop MAX30100 samples and degrade
  pulse-detection accuracy. The dual-bus approach (Wire + Wire1)
  preserves isolation without requiring a second microcontroller.

## Consequences

- **Paper Section 3.4 must be updated** to reflect the new sensor
  pairing. ADR-0004's narrative remains valid for the original
  decision; this ADR documents the revision.
- **ESP32-A firmware complexity increases:** two I²C buses must be
  managed, the dual-sensor task structure is more involved, and
  bandwidth-isolation testing under both sensors active becomes a
  required bench item.
- **ESP32-B firmware simplifies:** single sensor, single I²C bus,
  smaller footprint.
- **MQTT topic structure is unaffected** if the kiosk subscribes by
  topic name rather than by source MCU. The topics themselves
  (`vitals/spo2`, `vitals/temperature`, `anthro/height`) keep their
  semantic meaning; only the publishing source for
  `vitals/temperature` changes from ESP32-B to ESP32-A.
- **Bench testing must verify:** (1) MAX30100 pulse readings remain
  accurate under concurrent MLX90640BAB frame reads; (2) thermal
  frames arrive at the kiosk on the expected topic regardless of
  publishing source.
- **Future hardware revisions** should reconsider the placement
  decision if the kiosk's physical layout changes (e.g., if the
  vertical pillar gains a forward-facing mount surface).

## References

- ADR-0004 (Two ESP32 microcontrollers, one per sensor cluster) —
  superseded with respect to sensor pairing; the two-MCU
  architecture itself remains unchnged.
- I²C specification UM10204, NXP. Section 7.1, bus capacitance.
- Datasheet: MLX90640BAB, Melexis NV. Operating bus configuration.
