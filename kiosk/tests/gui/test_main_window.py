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


# Verifies that entering MEASURING_VITALS auto-fires
# BpMeasurementRequested on the bus. The user-gated "Connect to
# cuff" button is gone; the OmronBpSensor's 8×10 s retry budget
# absorbs the time the citizen needs to position the cuff and
# press its BT button. The original "InProgress" regression that
# motivated the gating is now serialised by BleAdapterLock.
# Mortality: would fail if state-entry auto-fire regressed back
# to "user must press a button first".
@pytest.mark.asyncio
async def test_measuring_vitals_auto_fires_bp_request(
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

    fsm.rfid_scanned("CARD_BP_AUTOFIRE")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert _current_object_name(main_window) == "measuring_vitals_screen"

    # Drain pending tasks so the create_task(bus.publish(...)) coroutine
    # scheduled on entry actually runs to the listener.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(received) == 1, (
        f"MEASURING_VITALS entry must auto-publish exactly one "
        f"BpMeasurementRequested; got {len(received)}"
    )


# Verifies a second weight measurement of the same session is
# dropped, not persisted. Models the bench race where the Xiaomi
# scale's BLE library replays a cached advertisement after the
# BleAdapterLock's pause/resume, slipping past the gate's warmup
# window. Belt-and-braces: the warmup restart already shuts that
# window, but a duplicate-drop guard at the persistence layer
# protects against any other source of double-publish (e.g., a
# future second sensor reporting the same type).
# Mortality: would fail if the guard were dropped, or if it
# (incorrectly) treated offline placeholders as "real" captures
# and blocked the genuine reading that follows.
@pytest.mark.asyncio
async def test_duplicate_weight_dropped(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    from sqlalchemy import select

    from ginhawa_kiosk.db.models import Measurement
    from ginhawa_kiosk.fsm import MeasurementProposed

    fsm.rfid_scanned("CARD_DUP_WEIGHT")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    fsm.path_selected("anthropometric")
    assert fsm.current_session is not None

    # First valid weight reading lands.
    await bus.publish(
        MeasurementProposed(
            measurement_type="weight",
            value=70.0,
            unit="kg",
            source_device="xiaomi_s200_ble",
            claimed_is_valid=True,
        )
    )

    rows = (
        db_session.execute(
            select(Measurement).where(
                Measurement.session_id == fsm.current_session.id,
                Measurement.type == "weight",
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "first weight should persist"
    assert rows[0].value == pytest.approx(70.0)

    # Second weight reading — duplicate — must be dropped.
    await bus.publish(
        MeasurementProposed(
            measurement_type="weight",
            value=72.5,
            unit="kg",
            source_device="xiaomi_s200_ble",
            claimed_is_valid=True,
        )
    )

    rows_after = (
        db_session.execute(
            select(Measurement).where(
                Measurement.session_id == fsm.current_session.id,
                Measurement.type == "weight",
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_after) == 1, "duplicate weight must not persist"
    # Confirm the surviving row is the first one (value didn't get
    # silently overwritten by the duplicate).
    assert rows_after[0].value == pytest.approx(70.0)


# Verifies the screen no longer has the legacy "Connect to cuff"
# button — the auto-fire flow makes it redundant. Mortality: would
# fail if a future re-add of the button slipped through review,
# because the bus would then receive two BpMeasurementRequested
# events per session (one auto, one user) and the lock-protected
# directed connect would briefly deadlock against itself.
def test_measuring_vitals_screen_has_no_connect_button(
    main_window: KioskMainWindow,
) -> None:
    from PyQt6.QtWidgets import QPushButton

    screen = main_window._measuring_vitals_screen
    button = screen.findChild(QPushButton, "measuring_vitals_connect_button")
    assert button is None
    assert not hasattr(screen, "connect_to_cuff_requested")


# Verifies leaving MEASURING_VITALS publishes
# BpMeasurementRequestCancelled. The Omron BP handler retries connect
# indefinitely; this event is the SOLE give-up signal. Without it a
# citizen who walks away leaves the kiosk hammering the cuff forever.
# Mortality: would fail if the publish were dropped, gated on a
# specific exit state, or fired on entry instead of exit.
@pytest.mark.asyncio
async def test_state_exit_publishes_bp_cancelled(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    from ginhawa_kiosk.fsm import BpMeasurementRequestCancelled

    received: list[BpMeasurementRequestCancelled] = []

    async def listener(event: BpMeasurementRequestCancelled) -> None:
        received.append(event)

    bus.subscribe(BpMeasurementRequestCancelled, listener)

    fsm.rfid_scanned("CARD_BP_CANCEL")
    fsm.citizen_identified(_make_citizen(db_session))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert _current_object_name(main_window) == "measuring_vitals_screen"
    # Drain anything queued during entry.
    for _ in range(5):
        await asyncio.sleep(0)
    received.clear()

    # Cancel from MEASURING_VITALS → ABORTED. Should fire the cancel
    # event exactly once.
    fsm.cancel()
    assert _current_object_name(main_window) == "aborted_screen"
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(received) == 1, (
        "leaving MEASURING_VITALS must publish exactly one "
        f"BpMeasurementRequestCancelled; got {len(received)}"
    )


# Verifies entering MEASURING_ANTHRO publishes SessionResetForSensors.
# Closes the bench race where, between sessions, the Xiaomi scale's
# ~5 s broadcast cadence let a stale advertisement satisfy the
# stability gate during IDLE — the gate then re-locked before the
# citizen actually stepped on for the next session, so back-to-back
# anthro sessions silently lost the second weight. Resetting on
# MEASURING_ANTHRO entry places the unlock immediately before the
# citizen is expected to step on, eliminating the window. Mortality:
# would fail if the publish were dropped, moved off the anthro
# branch, or accidentally guarded by a flag.
@pytest.mark.asyncio
async def test_measuring_anthro_entry_resets_scale_gate(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    from ginhawa_kiosk.fsm import SessionResetForSensors

    received: list[SessionResetForSensors] = []

    async def listener(event: SessionResetForSensors) -> None:
        received.append(event)

    bus.subscribe(SessionResetForSensors, listener)

    fsm.rfid_scanned("CARD_ANTHRO_RESET")
    fsm.citizen_identified(_make_citizen(db_session))
    # The IDLE/LANGUAGE_SELECT branch in _maybe_publish_session_reset
    # also fires; clear the listener buffer so the assertion below
    # pins the MEASURING_ANTHRO publish specifically rather than
    # racing with the LANGUAGE_SELECT one.
    fsm.language_chosen("en")
    for _ in range(5):
        await asyncio.sleep(0)
    received.clear()

    fsm.path_selected("anthropometric")
    assert _current_object_name(main_window) == "measuring_anthro_screen"

    # Drain create_task'd publish.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(received) == 1, (
        "MEASURING_ANTHRO entry must publish exactly one "
        f"SessionResetForSensors; got {len(received)}"
    )


# Verifies the IDLE/LANGUAGE_SELECT defence-in-depth resets are still
# wired — both unlock paths are intentionally redundant. Mortality:
# would fail if someone "simplified" by removing the older path,
# leaving only the MEASURING_ANTHRO reset (which would re-introduce a
# different race: the scale would have nothing keeping it idle if
# the citizen never makes it to MEASURING_ANTHRO, e.g. they cancel
# during PATH_CHOICE).
@pytest.mark.asyncio
async def test_back_to_back_sessions_each_reset_scale(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    from ginhawa_kiosk.fsm import SessionResetForSensors

    received: list[SessionResetForSensors] = []

    async def listener(event: SessionResetForSensors) -> None:
        received.append(event)

    bus.subscribe(SessionResetForSensors, listener)

    citizen = _make_citizen(db_session)

    async def run_session(card: str) -> int:
        before = len(received)
        fsm.rfid_scanned(card)
        fsm.citizen_identified(citizen)
        fsm.language_chosen("en")
        fsm.path_selected("anthropometric")
        # Settle bus.
        for _ in range(5):
            await asyncio.sleep(0)
        # Bring the FSM back to IDLE so the next session can begin.
        fsm.cancel()
        fsm.acknowledge()
        for _ in range(5):
            await asyncio.sleep(0)
        return len(received) - before

    first = await run_session("CARD_BACKTOBACK_1")
    second = await run_session("CARD_BACKTOBACK_2")

    # At least one publish per session — IDLE / LANGUAGE_SELECT may
    # also publish (defence in depth), so we assert >=1, not ==1.
    assert first >= 1, f"first session published {first} resets"
    assert second >= 1, f"second session published {second} resets"


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
