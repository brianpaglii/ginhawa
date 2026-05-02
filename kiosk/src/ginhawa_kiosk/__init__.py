"""GINHAWA kiosk application package.

The kiosk runs on a Raspberry Pi 5 with PyQt6, captures vital-sign
measurements over BLE/MQTT/RFID, persists them to a SQLCipher-encrypted
SQLite database, and syncs to the GINHAWA cloud backend whenever the
internet is available.

Phase 2 scaffolding only: the package wiring, settings, logging, and
abstract sensor base classes are present; concrete sensor / GUI /
sync-daemon implementations land in subsequent prompts.
"""

__version__ = "0.1.0"
