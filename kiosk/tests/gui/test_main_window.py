"""Main window state-to-screen routing.

The KioskMainWindow listens to the FSM's ``state_changed`` signal
and switches the visible page in its QStackedWidget. These tests
drive transitions on the FSM directly and verify the right screen
becomes the current widget.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import Citizen
from ginhawa_kiosk.fsm import EventBus, SessionFSM
from ginhawa_kiosk.gui.main_window import KioskMainWindow
from ginhawa_kiosk.services.printer import MockPrinterService


@pytest.fixture
def main_window(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> Iterator[KioskMainWindow]:
    printer = MockPrinterService()
    citizen_lookup = AsyncMock(return_value=None)
    w = KioskMainWindow(
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        printer=printer,
        citizen_lookup=citizen_lookup,
        deployment_barangay="Tibagan",
        device_id="test-device",
    )
    qtbot.addWidget(w)
    yield w


def _current_object_name(window: KioskMainWindow) -> str:
    return window.centralWidget().currentWidget().objectName()  # type: ignore[no-any-return,union-attr]


# Verifies that for each FSM state the QStackedWidget shows the
# correct screen widget. We drive the FSM through its triggers (not
# the bus) to keep the test focused on routing.
# Mortality: 'Would fail if state-to-screen mapping were broken.'
def test_main_window_routes_state_to_screen(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    # IDLE is the initial state.
    assert _current_object_name(main_window) == "idle_screen"

    # IDLE → IDENTIFYING
    fsm.rfid_scanned("CARD_ROUTE_TEST")
    assert _current_object_name(main_window) == "identifying_screen"

    # IDENTIFYING → LANGUAGE_SELECT (unknown citizen path)
    fsm.citizen_identified(None)
    assert _current_object_name(main_window) == "language_select_screen"

    # LANGUAGE_SELECT → REGISTER_FORM
    fsm.language_chosen("en")
    assert _current_object_name(main_window) == "register_form_screen"

    # REGISTER_FORM → CONSENT (set citizen + complete registration)
    citizen = _make_citizen(db_session)
    fsm.set_current_citizen(citizen)
    fsm.registration_complete()
    assert _current_object_name(main_window) == "consent_screen"

    # CONSENT → PATH_CHOICE
    fsm.consent_given()
    assert _current_object_name(main_window) == "path_choice_screen"

    # PATH_CHOICE → MEASURING_VITALS
    fsm.path_selected("vitals")
    assert _current_object_name(main_window) == "measuring_vitals_screen"

    # MEASURING_VITALS → REPORT
    fsm.measurement_path_complete()
    assert _current_object_name(main_window) == "report_screen"

    # REPORT → PRINTING
    fsm.print_requested()
    assert _current_object_name(main_window) == "printing_screen"

    # PRINTING → END
    fsm.print_complete(success=True, printed_status="printed_ok")
    assert _current_object_name(main_window) == "end_screen"

    # END → IDLE
    fsm.acknowledge()
    assert _current_object_name(main_window) == "idle_screen"


# Verifies the cancel signal from any cancellable screen is forwarded
# to fsm.cancel() and the window switches to the ABORTED screen.
# Mortality: would fail if BaseScreen's cancel signal weren't wired
# to fsm.cancel().
def test_main_window_routes_cancel_to_aborted(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    fsm.rfid_scanned("CARD_CANCEL")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    assert _current_object_name(main_window) == "path_choice_screen"

    # Emulate the citizen tapping Cancel on PathChoiceScreen — the
    # main window forwards the signal to fsm.cancel().
    screen = main_window.centralWidget().currentWidget()  # type: ignore[union-attr]
    screen.cancel_requested.emit()
    assert _current_object_name(main_window) == "aborted_screen"


# Verifies the change-language signal from REPORT is forwarded to
# fsm.change_language() and the window switches back to the
# language-select screen.
# Mortality: would fail if BaseScreen's change_language signal
# weren't wired to fsm.change_language().
def test_main_window_routes_change_language_to_language_select(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    fsm.rfid_scanned("CARD_CHANGE_LANG")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    assert _current_object_name(main_window) == "report_screen"

    screen = main_window.centralWidget().currentWidget()  # type: ignore[union-attr]
    screen.change_language_requested.emit()
    assert _current_object_name(main_window) == "language_select_screen"


# Verifies the report screen filters to is_valid=1 measurements when
# the main window populates it on REPORT entry.
def test_main_window_filters_invalid_measurements_on_report(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    from ginhawa_kiosk.db.models import Measurement
    from PyQt6.QtWidgets import QListWidget

    fsm.rfid_scanned("CARD_FILTER")
    citizen = _make_citizen(db_session)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert fsm.current_session is not None

    # Insert one valid + one invalid measurement directly.
    db_session.add(
        Measurement(
            id="meas-valid-1",
            session_id=fsm.current_session.id,
            type="systolic_bp",
            value=128.0,
            unit="mmHg",
            source_device="test",
            measured_at="2026-05-04T00:00:00+00:00",
            is_valid=1,
            validation_notes=None,
            raw_json=None,
            synced=0,
            updated_at="2026-05-04T00:00:00+00:00",
        )
    )
    db_session.add(
        Measurement(
            id="meas-invalid-1",
            session_id=fsm.current_session.id,
            type="systolic_bp",
            value=320.0,
            unit="mmHg",
            source_device="test",
            measured_at="2026-05-04T00:00:00+00:00",
            is_valid=0,
            validation_notes="out_of_range",
            raw_json=None,
            synced=0,
            updated_at="2026-05-04T00:00:00+00:00",
        )
    )
    db_session.flush()

    fsm.measurement_path_complete()
    assert _current_object_name(main_window) == "report_screen"

    list_widget = (
        main_window.centralWidget()
        .currentWidget()
        .findChild(  # type: ignore[union-attr]
            QListWidget, "report_list"
        )
    )
    assert list_widget is not None
    assert list_widget.count() == 1
    assert "128 mmHg" in list_widget.item(0).text()


# Verifies entry to MEASURING_VITALS does NOT auto-publish
# BpMeasurementRequested — the citizen must tap the connect button
# first. The 2026-05-05 InProgress regression was caused by firing
# on state entry, before the cuff was in pairing mode. Mortality:
# 'Would fail if the auto-fire regression came back.'
@pytest.mark.asyncio
async def test_measuring_vitals_does_not_autopublish_bp_request(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    from ginhawa_kiosk.fsm import BpMeasurementRequested

    received: list[BpMeasurementRequested] = []

    async def listener(event: BpMeasurementRequested) -> None:
        received.append(event)

    bus.subscribe(BpMeasurementRequested, listener)

    fsm.rfid_scanned("CARD_BP_AUTOPUBLISH")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert _current_object_name(main_window) == "measuring_vitals_screen"

    # Yield once so any erroneously-scheduled task gets to run.
    await asyncio.sleep(0)
    assert received == [], (
        "MEASURING_VITALS entry must not auto-publish BpMeasurementRequested; "
        f"got {len(received)} events"
    )


# Verifies tapping the connect-to-cuff button publishes exactly one
# BpMeasurementRequested. The whole point of the user-gated flow:
# the request only fires when the citizen has the cuff in pairing
# mode and chooses to connect. Mortality: 'Would fail if the button
# weren't wired to the bus.'
@pytest.mark.asyncio
async def test_connect_to_cuff_button_publishes_bp_request(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    from ginhawa_kiosk.fsm import BpMeasurementRequested

    received: list[BpMeasurementRequested] = []

    async def listener(event: BpMeasurementRequested) -> None:
        received.append(event)

    bus.subscribe(BpMeasurementRequested, listener)

    fsm.rfid_scanned("CARD_BP_BUTTON")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")

    main_window._measuring_vitals_screen.connect_to_cuff_requested.emit()
    # Drain pending tasks so the create_task(bus.publish(...)) coroutine
    # actually runs to the listener.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(received) == 1


def _make_citizen(db: Session) -> Citizen:
    citizen = Citizen(
        id="cit-test-1",
        rfid_uid="CARD_ROUTE_TEST",
        full_name="Test Citizen",
        dob="1990-01-01",
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version="v1",
        consent_given_at="2026-05-04T00:00:00+00:00",
        registered_at="2026-05-04T00:00:00+00:00",
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at="2026-05-04T00:00:00+00:00",
    )
    db.add(citizen)
    db.commit()
    return citizen


@pytest.fixture
def _silence_unused_imports() -> Any:
    # Keep collected-but-unused imports out of ruff's complaints in
    # this test file (Iterator is used in the main_window fixture
    # above; this no-op keeps the import chain explicit).
    return None
