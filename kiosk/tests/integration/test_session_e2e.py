"""End-to-end kiosk session with mock sensors + mock printer.

Drives a single citizen flow from RFID tap → registration → consent
→ measurements → print, asserting that:

* a Citizen row was created with ``registered_by=NULL``;
* a Session row reached ``status='completed'`` with ``ended_at``
  populated;
* the right number of Measurement rows landed under the right
  session, all ``is_valid=1`` (we feed in-range readings);
* the audit_log captures the layered actions:
  Session creation, Citizen creation, consent given, each
  measurement, status change, receipt printed.

This is the big-cross-cutting test — it exercises the FSM, the GUI's
main window, the event bus, the validation service, the audit
writer, the printer mock, and the in-memory DB simultaneously.

Runs on a dev laptop without ``libsqlcipher`` because we use plain
in-memory SQLite for the schema; the SQLCipher contract is exercised
separately under ``tests/db/``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from PyQt6.QtWidgets import QApplication
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Force offscreen Qt before any PyQt6 widget import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from ginhawa_kiosk.db.base import Base  # noqa: E402
from ginhawa_kiosk.db.models import (  # noqa: E402
    AuditLog,
    Citizen,
    Measurement,
)
from ginhawa_kiosk.db.models import Session as SessionModel  # noqa: E402
from ginhawa_kiosk.fsm import (  # noqa: E402
    EventBus,
    MeasurementProposed,
    RfidScanned,
    SessionFSM,
)
from ginhawa_kiosk.gui.main_window import KioskMainWindow  # noqa: E402
from ginhawa_kiosk.gui.screens import RegistrationData  # noqa: E402
from ginhawa_kiosk.services.printer import MockPrinterService  # noqa: E402

DEVICE_ID = "00000000-0000-0000-0000-000000000e2e"
CONSENT_VERSION = "v1"
RFID_UID = "E2E_TEST_UID_001"


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def fsm(db: Session) -> SessionFSM:
    return SessionFSM(db, device_id=DEVICE_ID, current_consent_version=CONSENT_VERSION)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def printer() -> MockPrinterService:
    return MockPrinterService()


@pytest.fixture
def qapp() -> Iterator[QApplication]:
    # QMainWindow requires a QApplication, not just QCoreApplication;
    # pytest-qt would normally provide this but the test isn't using
    # the qtbot fixture (it drives the FSM/bus directly).
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        yield existing
    else:
        app = QApplication([])
        yield app


@pytest.fixture
def main_window(
    qapp: QApplication,
    fsm: SessionFSM,
    bus: EventBus,
    db: Session,
    printer: MockPrinterService,
) -> Iterator[KioskMainWindow]:
    async def lookup(uid: str) -> Citizen | None:
        return db.execute(
            select(Citizen).where(Citizen.rfid_uid == uid)
        ).scalar_one_or_none()

    w = KioskMainWindow(
        fsm=fsm,
        bus=bus,
        db_session=db,
        printer=printer,
        citizen_lookup=lookup,
        deployment_barangay="Tibagan",
        device_id=DEVICE_ID,
    )
    yield w


# Verifies the full session thread end-to-end with mock sensors,
# self-service registration, and a successful print.
# Mortality: 'Would fail if any layer (FSM, services, sensors,
# persistence, audit) were broken.'
@pytest.mark.asyncio
async def test_full_session_e2e_with_mocks(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db: Session,
    printer: MockPrinterService,
) -> None:
    # 1) Citizen taps. Bus → main window → fsm.rfid_scanned + lookup.
    await bus.publish(RfidScanned(uid=RFID_UID))
    # Lookup returned None → IDENTIFYING → LANGUAGE_SELECT.

    # 2) Citizen picks Tagalog.
    fsm.language_chosen("tl")

    # 3) Citizen submits the registration form. Drive the screen's
    # submitted signal directly with the data.
    main_window._register_form_screen.submitted.emit(
        RegistrationData(
            full_name="Juan Dela Cruz E2E",
            dob_iso="1995-04-12",
            sex="M",
            barangay="Tibagan",
            phone="09171234567",
        )
    )

    # 4) Citizen agrees to the consent prompt.
    main_window._consent_screen.consent_given.emit()

    # 5) Citizen picks the full path.
    main_window._path_choice_screen.path_selected.emit("full")

    # 6) Vitals stream in via the bus.
    for t, v, u, src in [
        ("systolic_bp", 128.0, "mmHg", "mock_omron"),
        ("diastolic_bp", 82.0, "mmHg", "mock_omron"),
        ("heart_rate", 74.0, "bpm", "mock_max30100"),
        ("spo2", 98.0, "%", "mock_max30100"),
        ("temperature", 36.6, "C", "mock_mlx90640"),
    ]:
        await bus.publish(
            MeasurementProposed(
                measurement_type=t,
                value=v,
                unit=u,
                source_device=src,
                claimed_is_valid=True,
            )
        )

    # 7) Anthropometrics stream in via the bus.
    for t, v, u, src in [
        ("height", 165.0, "cm", "mock_vl53l0x"),
        ("weight", 65.0, "kg", "mock_xiaomi"),
    ]:
        await bus.publish(
            MeasurementProposed(
                measurement_type=t,
                value=v,
                unit=u,
                source_device=src,
                claimed_is_valid=True,
            )
        )

    # 8) Citizen taps Print.
    main_window._report_screen.print_requested.emit()
    # The print runner is async — flush the event loop so the mock
    # printer's coroutine gets to run and fire print_complete.
    await _drain_pending_tasks()

    # 9) Citizen acknowledges.
    fsm.acknowledge()

    db.expire_all()

    # ---- Assertions on Citizen ------------------------------------
    citizens = list(db.execute(select(Citizen)).scalars())
    assert len(citizens) == 1
    new_citizen = citizens[0]
    assert new_citizen.full_name == "Juan Dela Cruz E2E"
    assert new_citizen.rfid_uid == RFID_UID
    assert new_citizen.sex == "M"
    assert new_citizen.barangay == "Tibagan"
    assert new_citizen.registered_by is None  # self-service
    assert new_citizen.consent_version == CONSENT_VERSION

    # ---- Assertions on Session ------------------------------------
    sessions = list(db.execute(select(SessionModel)).scalars())
    assert len(sessions) == 1
    s = sessions[0]
    assert s.citizen_id == new_citizen.id
    assert s.status == "completed"
    assert s.ended_at is not None
    assert s.printed_status == "printed_ok"
    assert s.measurement_path == "full"

    # ---- Assertions on Measurements -------------------------------
    measurements = list(db.execute(select(Measurement)).scalars())
    assert len(measurements) == 7  # 5 vitals + 2 anthro
    assert all(m.session_id == s.id for m in measurements)
    assert all(m.is_valid == 1 for m in measurements)

    # ---- Assertions on Audit log ----------------------------------
    audits = list(db.execute(select(AuditLog).order_by(AuditLog.id)).scalars())
    actions = [a.action for a in audits]
    # Every layer wrote at least the audit row we expect.
    assert "fsm.rfid_scanned" in actions
    assert "citizen.read" in actions
    assert "citizen.create" in actions
    assert "fsm.language_chosen" in actions
    assert "fsm.registration_complete" in actions
    assert "fsm.consent_given" in actions
    assert "fsm.session_started" in actions
    assert "fsm.measurement_captured" in actions
    assert "fsm.path_selected" in actions
    assert "fsm.measurement_path_step" in actions  # full → vitals→anthro
    assert "fsm.report" in actions
    assert "fsm.print_requested" in actions
    assert "fsm.print_complete" in actions
    assert "receipt_printed" in actions
    assert "fsm.acknowledge" in actions
    # 7 measurement_captured rows (one per measurement).
    assert actions.count("fsm.measurement_captured") == 7


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def _async_setup() -> AsyncIterator[Any]:
    yield None


async def _drain_pending_tasks() -> None:
    # Yield control to the event loop a few times so the print
    # coroutine has a chance to complete — the runner is scheduled
    # via loop.create_task() and needs a turn to actually finish.
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)
