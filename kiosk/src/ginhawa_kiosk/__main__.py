"""GINHAWA kiosk application entry point.

Run with::

    uv run python -m ginhawa_kiosk

Or via the installed console script (defined in ``pyproject.toml``)::

    ginhawa-kiosk

Wires together:

* :class:`QApplication` and a :class:`qasync.QEventLoop` so async
  sensor / printer coroutines run alongside the Qt event loop.
* :class:`Settings` from env / ``.env``.
* SQLCipher-encrypted DB engine + session.
* :class:`EventBus`.
* All sensors via :func:`create_all_sensors` (mock vs real selected
  by ``MOCK_HARDWARE``).
* The thermal printer via :func:`create_printer_service`.
* :class:`SessionFSM` bound to the DB session.
* :class:`KioskMainWindow` connected to the FSM, the bus, the
  printer, and a citizen-lookup hook.

Sensors are started on first IDLE entry; they keep running across
sessions (RFID is always listening). The kiosk shuts down cleanly
on SIGINT — sensors are stopped, DB session committed/closed,
QApplication exits.

This module is :func:`# pragma: no cover`-bound — it's the
hardware-bound entry point that needs a running qasync loop and
real PyQt6 application context. The unit-tested surface is the
individual layers (FSM, screens, printer, sensors).
"""

from __future__ import annotations

import asyncio
import sys

import qasync
import structlog
from PyQt6.QtWidgets import QApplication
from sqlalchemy import select
from sqlalchemy.orm import Session as SAOrmSession

from .core.config import get_settings
from .core.logging import configure_logging
from .db.models import Citizen, DeviceConfig
from .db.session import create_engine_for_kiosk, make_session_factory
from .fsm import EventBus, SessionFSM
from .gui.main_window import KioskMainWindow
from .sensors import create_all_sensors
from .services.printer import create_printer_service

_log = structlog.get_logger(__name__)


# Default consent_version key in device_config — the kiosk renders
# whatever version is currently provisioned. Changing the consent
# text means inserting a new device_config row + updating the
# strings catalogue in the same release.
_DEPLOYMENT_BARANGAY_KEY = "deployment_barangay"
_KIOSK_ID_KEY = "kiosk_id"
_CONSENT_VERSION_KEY = "consent_version"


def main() -> int:  # pragma: no cover - hardware-bound entry point
    settings = get_settings()
    configure_logging(settings)

    engine = create_engine_for_kiosk(settings.KIOSK_DB_PATH, settings.KIOSK_DB_KEY)
    session_factory = make_session_factory(engine)
    db = session_factory()

    # Pull the per-kiosk config (kiosk_id, deployment barangay, current
    # consent version). Provisioned by ``provision_db.py`` during
    # commissioning; the kiosk refuses to start if any are missing.
    device_id = _read_device_config(db, _KIOSK_ID_KEY)
    deployment_barangay = _read_device_config(db, _DEPLOYMENT_BARANGAY_KEY) or ""
    consent_version = _read_device_config(db, _CONSENT_VERSION_KEY) or "v1"

    if not device_id:
        _log.error(
            "kiosk.boot.missing_device_id",
            hint="run provision_db.py with --config seeding kiosk_id",
        )
        return 2

    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    bus = EventBus()
    sensors = create_all_sensors(bus, settings, db)
    printer = create_printer_service(settings)
    fsm = SessionFSM(db, device_id=device_id, current_consent_version=consent_version)

    async def lookup_citizen(uid: str) -> Citizen | None:
        return db.execute(
            select(Citizen).where(Citizen.rfid_uid == uid)
        ).scalar_one_or_none()

    main_window = KioskMainWindow(
        fsm=fsm,
        bus=bus,
        db_session=db,
        printer=printer,
        citizen_lookup=lookup_citizen,
        sensors=sensors,
        deployment_barangay=deployment_barangay,
        device_id=device_id,
    )
    main_window.showFullScreen()

    async def boot_sensors() -> None:
        for name, sensor in sensors.items():
            try:
                await sensor.start()
            except Exception as exc:
                _log.warning(
                    "kiosk.boot.sensor_start_failed",
                    sensor=name,
                    error=type(exc).__name__,
                )

    loop.create_task(boot_sensors())

    with loop:
        loop.run_forever()
    return 0


def _read_device_config(
    db: SAOrmSession, key: str
) -> str | None:  # pragma: no cover - boot path
    row = db.execute(
        select(DeviceConfig).where(DeviceConfig.key == key)
    ).scalar_one_or_none()
    return row.value if row is not None else None


if __name__ == "__main__":
    sys.exit(main())
