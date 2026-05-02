# RFID bench test — RC522 on Pi 5

**Date:** 2026-05-02
**Hardware:** Raspberry Pi 5, MFRC522 module on SPI0, MIFARE Classic 1K cards
**Verdict:** PASS

## Setup
- pyproject.toml: rpi-lgpio replaces RPi.GPIO for Pi 5 compatibility
- libsqlcipher-dev, python3-lgpio installed via apt
- SPI enabled via raspi-config

## Tests
1. Single tap → one RfidScanned event ✓
2. Three rapid taps same card → one event (debounce) ✓
3. Distinct card → new event ✓

## Findings
[None | List any unexpected behavior]