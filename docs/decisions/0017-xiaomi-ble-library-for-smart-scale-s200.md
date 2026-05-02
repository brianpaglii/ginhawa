# ADR 0017: xiaomi-ble for the Xiaomi Smart Scale S200, with per-scale bindkey at commissioning

- **Status:** Accepted
- **Date:** 05-02-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The kiosk's anthropometric path (per ADR-0004 and Section 3.3 of the
paper) reads weight from a Xiaomi Smart Scale S200, paired directly to
the Raspberry Pi 5 over BLE. The S200 (model `xiaomi.scales.ms111`,
product ID `0x4C04`) advertises encrypted service-data frames using
Xiaomi's MiBeacon V5 / V6 protocol. Decoding those frames requires a
per-device bindkey that Xiaomi mints when the scale is first paired
to a phone running the Mi Home app — there is no flat documented
protocol that yields raw weight without the key.

The kiosk has no Mi Home app. We need a Python library that:

- Decodes MiBeacon V5/V6 service data,
- Honours per-device bindkeys at parse time,
- Exposes weight (and impedance, which we discard per the CLAUDE.md
  "Xiaomi scale specifics" rule), and
- Runs under `bleak`-compatible passive scanning so we can keep
  serialising BLE access through the session FSM (CLAUDE.md, "no
  concurrent BLE").

The community-maintained `Bluetooth-Devices/xiaomi-ble` package
(referenced as the GINHAWA-maintained fork in CLAUDE.md) covers all
four requirements.

## Decision

Use **`xiaomi-ble>=1.3.0`** as the BLE parser for the Xiaomi Smart
Scale S200 (`xiaomi.scales.ms111`, product ID `0x4C04`). The library
is consumed as a regular dependency in `kiosk/pyproject.toml`; the
scale adapter under `sensors/` calls it from inside the FSM-held BLE
lock, never directly from event-bus handlers.

Per-scale **bindkey extraction at commissioning is a documented
runbook step**. The deployment runbook walks the technician through
pairing the scale to a designated commissioning phone, exporting the
key from the Mi Home `device_info` blob, and entering it into the
kiosk's provisioning script. The bindkey is then **stored in
`device_config`** (e.g., `device_config.key='xiaomi_scale_bindkey'`,
`value=<hex>`) and inherits the SQLCipher encryption that already
protects every other row in the kiosk's local database (ADR-0001).

The bindkey is treated as a sensitive secret on par with
`KIOSK_DB_KEY`: it never appears in logs, never leaves the kiosk over
the wire, and is not synced to the cloud (the cloud has no BLE
counterparty and no use for it).

## Alternatives considered

- _Switch to the Mi Body Composition Scale 2:_ rejected. The S200 was
  selected against the original survey criteria (weight accuracy,
  in-country availability, BLE-only operation without a Wi-Fi
  dependency). The Mi 2 would re-open the hardware-evaluation work
  for no functional gain, and we would still need a per-device
  bindkey via the same Mi Home pairing flow.
- _Reverse-engineer the MiBeacon V5/V6 protocol ourselves:_ rejected.
  `xiaomi-ble` already implements decoding for ~40 Xiaomi BLE devices
  and is actively maintained against MiBeacon spec drift. Re-doing
  that work in-tree would absorb engineering time that is better spent
  on the kiosk's domain logic and would saddle GINHAWA with a
  reverse-engineered binary parser to maintain across firmware
  refreshes.
- _Wait for native Bluetooth SIG support (Body Composition Service /
  Weight Scale Service profiles):_ rejected. Xiaomi has not announced a
  roadmap to migrate the S200 to standard SIG profiles, and the
  expected lifetime of this kiosk deployment exceeds any plausible
  Xiaomi firmware migration window. We cannot block the project on a
  vendor change that is not in flight.

## Consequences

- The per-scale bindkey is a real-world asset that must survive
  re-imaging of the Pi: the runbook captures the bindkey alongside the
  SQLCipher passphrase, both in the same root-only credentials file
  consumed by the systemd unit at boot. Losing the bindkey requires
  re-pairing the scale to the commissioning phone — not the kiosk
  itself.
- Adding a new Xiaomi-family device in the future (e.g., a successor
  thermometer or scale) is a one-line dependency-version bump rather
  than a parser rewrite, provided the device is supported upstream.
- `xiaomi-ble`'s body-composition outputs (body fat %, muscle mass,
  water content, bone mass, segmental analysis) are read from the
  advertisement but **immediately discarded** by the kiosk adapter —
  these are out of declared scope under the DPA consent and the
  underlying bioimpedance signal is too noisy for community screening
  (see CLAUDE.md, "Xiaomi scale specifics"). The kiosk records weight
  only.
- The library's heart-rate output (foot-electrode HR) is also
  discarded; the kiosk's heart-rate signal comes from the MAX30100
  pulse oximeter on ESP32-A.
- Upstream upgrades (`>=1.3.0`) require regression testing against the
  same physical S200 unit before deployment. The kiosk's CI cannot
  exercise the BLE path; verification is a manual runbook step
  whenever the pin moves.
