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


def main() -> int:
    """Console-script entry point.

    Defined here as a thin re-export so ``pyproject.toml``'s
    ``[project.scripts]`` can bind ``ginhawa-kiosk = "ginhawa_kiosk:main"``
    without hardcoding the module path. The actual implementation
    lives in :mod:`ginhawa_kiosk.__main__`.
    """
    from .__main__ import main as _main

    return _main()
