"""DB-commit semantics for FSM-trigger handlers in main_window.

The 2026-05-07 bench surfaced a class of bug: handlers that fired
FSM triggers (cancel, change_language, path_selected, print_requested,
finish_without_printing) updated the SQLAlchemy session but never
committed, so audit rows + session-status mutations the FSM's
after-callbacks emit stayed in memory and never reached the
encrypted DB. The visible symptom: 20 sessions stuck "in_progress"
even though the END screen had rendered.

Each test in this module drives one such handler, then asserts the
DB sees the persisted state after a fresh re-read (we evict the
ORM cache via ``db_session.expire_all()`` so the assertions read
real on-disk state, not stale in-memory objects).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import AuditLog, Citizen
from ginhawa_kiosk.db.models import Session as SessionModel
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
    w = KioskMainWindow(
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        printer=MockPrinterService(),
        citizen_lookup=AsyncMock(return_value=None),
        deployment_barangay="Tibagan",
        device_id="test-device",
    )
    qtbot.addWidget(w)
    yield w


def _make_citizen(db_session: Session, rfid: str) -> Citizen:
    citizen = Citizen(
        id=f"citizen-{rfid}",
        rfid_uid=rfid,
        full_name="Test Citizen",
        dob="1990-01-01",
        sex="M",
        barangay="Tibagan",
        consent_version="v1",
        consent_given_at="2026-01-01T00:00:00+00:00",
        registered_at="2026-01-01T00:00:00+00:00",
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    db_session.add(citizen)
    db_session.flush()
    return citizen


def _drive_to_report(fsm: SessionFSM, db_session: Session, rfid: str) -> SessionModel:
    fsm.rfid_scanned(rfid)
    fsm.citizen_identified(_make_citizen(db_session, rfid))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert fsm.current_session is not None
    session_obj = fsm.current_session
    fsm.measurement_path_complete()
    assert fsm.state == "report"
    return session_obj


def _audit_count(
    db_session: Session, *, action: str, object_id: str | None = None
) -> int:
    stmt = select(AuditLog).where(AuditLog.action == action)
    if object_id is not None:
        stmt = stmt.where(AuditLog.object_id == object_id)
    return len(list(db_session.execute(stmt).scalars()))


# Verifies that the "Finish without printing" path commits the
# FSM's after-callback mutations (session.status='completed' +
# ended_at + the fsm.finish_without_printing audit row). Drives
# the report screen's signal directly, then re-reads the session
# row through a fresh query to prove the change reached disk and
# isn't just sitting in the ORM identity map.
# Mortality: would fail if the wrapper handler dropped the commit
# (the 2026-05-07 regression) or if the report-screen signal
# reverted to direct fsm.finish_without_printing wiring.
def test_finish_without_printing_commits_session_completion(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    session_obj = _drive_to_report(fsm, db_session, "CARD_FINISH_COMMIT")
    session_id = session_obj.id

    main_window._report_screen.finish_without_printing_requested.emit()

    assert fsm.state == "end"
    db_session.expire_all()
    fresh = db_session.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    ).scalar_one()
    assert fresh.status == "completed"
    assert fresh.ended_at is not None
    assert (
        _audit_count(
            db_session,
            action="fsm.finish_without_printing",
            object_id=session_id,
        )
        >= 1
    )


# Verifies cancel from a cancellable state commits the resulting
# transition + the fsm.cancel audit row. Drives the cancel signal
# from PATH_CHOICE so the FSM ends up in ABORTED.
def test_cancel_commits_aborted_state(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    fsm.rfid_scanned("CARD_CANCEL_COMMIT")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_CANCEL_COMMIT"))
    fsm.language_chosen("en")
    assert fsm.state == "path_choice"
    session_id = fsm.current_session.id if fsm.current_session else None

    screen = main_window.centralWidget().currentWidget()  # type: ignore[union-attr]
    screen.cancel_requested.emit()

    assert fsm.state == "aborted"
    db_session.expire_all()
    if session_id is not None:
        fresh = db_session.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        ).scalar_one()
        assert fresh.status == "aborted"
    # Cancel routes through the FSM's _after_cancel callback which
    # records the row with action="fsm.aborted" (the FSM's terminal
    # name for the user-driven abort). The session-status mutation
    # is what the commit guard primarily protects; the audit is
    # confirmation the after-callback ran.
    assert _audit_count(db_session, action="fsm.aborted") >= 1


# Verifies that "change language" from REPORT commits the
# transition + the fsm.change_language audit row. Drives the
# signal off the report screen.
def test_change_language_commits_state(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    _drive_to_report(fsm, db_session, "CARD_CHGLANG_COMMIT")

    screen = main_window.centralWidget().currentWidget()  # type: ignore[union-attr]
    screen.change_language_requested.emit()

    assert fsm.state == "language_select"
    db_session.expire_all()
    assert _audit_count(db_session, action="fsm.change_language") >= 1


# Verifies that the path-choice handler commits the transition
# + the session-create audit row. Path selection is the moment
# the FSM creates the session row; without a commit the row would
# be flush-only and would never reach disk.
def test_path_selected_commits(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    fsm.rfid_scanned("CARD_PATH_COMMIT")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_PATH_COMMIT"))
    fsm.language_chosen("en")

    screen = main_window.centralWidget().currentWidget()  # type: ignore[union-attr]
    screen.path_selected.emit("vitals")

    assert fsm.state == "measuring_vitals"
    assert fsm.current_session is not None
    session_id = fsm.current_session.id

    db_session.expire_all()
    fresh = db_session.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    ).scalar_one()
    assert fresh.status == "in_progress"
    assert fresh.measurement_path == "vitals"


# Verifies that print_requested commits the FSM transition + the
# fsm.print_requested audit row before _kick_off_print_job runs
# (the actual print work happens after PRINTING entry, but the
# trigger itself must persist).
def test_print_requested_commits(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    _drive_to_report(fsm, db_session, "CARD_PRINT_COMMIT")

    main_window._report_screen.print_requested.emit()

    # The FSM transitioned to PRINTING; the actual print is async
    # and won't have completed by now, but the trigger should be
    # audited and committed.
    assert fsm.state == "printing"
    db_session.expire_all()
    assert _audit_count(db_session, action="fsm.print_requested") >= 1
