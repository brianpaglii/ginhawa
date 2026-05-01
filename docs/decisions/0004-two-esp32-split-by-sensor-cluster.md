# ADR 0004: Two ESP32 microcontrollers, one per sensor cluster

- **Status:** Accepted
- **Date:** 04-20-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The kiosk includes sensors that the Raspberry Pi 5 cannot easily drive
directly: the MAX30100 pulse oximeter and the MLX90640BAB thermal
sensor have I²C interfaces with timing requirements that compete with
the Pi's other workloads, and the VL53L0X height-measurement sensor
needs reliable interrupt handling.

Offloading these sensors to microcontrollers gives them deterministic
timing, isolates the Pi from sensor faults (a misbehaving sensor can
hang an MCU without affecting the kiosk's UI), and keeps the Pi's
software focused on application logic rather than low-level driver
work.

## Decision

Use **two ESP32 microcontrollers**, communicating with the Pi over MQTT:

- **ESP32-A** drives the MAX30100 (SpO2, heart rate)
- **ESP32-B** drives the MLX90640BAB (temperature) and VL53L0X (height)

Each ESP32 publishes sensor readings to topics on a local MQTT broker
running on the Pi; the kiosk subscribes and routes the data to the
appropriate session.

## Alternatives considered

- _Single ESP32 driving all four sensors:_ rejected. The MLX90640BAB
  has substantial frame-rate and bandwidth needs that compete with the
  MAX30100's sampling. Splitting them gives each MCU enough headroom.
- _Pi drives all sensors directly via I²C:_ rejected. Linux's I²C
  subsystem doesn't give the timing guarantees that the MAX30100's
  signal processing prefers, and a sensor fault would risk wedging
  the I²C bus that other peripherals share.
- _Three or more ESP32s, one per sensor:_ rejected as wasteful; the
  pairing of MLX90640BAB with VL53L0X on one MCU is comfortable
  bandwidth-wise.

## Consequences

- The kiosk depends on a working MQTT broker on the Pi; the deployment
  runbook documents the broker setup.
- Two firmware codebases must be maintained. Common code (MQTT plumbing,
  JSON encoding, configuration) is shared via a git submodule or
  vendored helper library.
- The MQTT topic taxonomy is part of the system's API contract; changes
  require coordinated updates to firmware and kiosk. Documented in
  Section 3.4 of the paper and in the firmware repos' READMEs.
- If an ESP32 becomes unresponsive (hang, crash), the affected sensor
  data simply stops arriving on its topic; the kiosk detects this via
  per-topic timeouts and surfaces a sensor-unavailable error in the UI.
