"""Supplemental FSM tests covering branches not exercised by the
per-transition / happy-path tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import AuditLog
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.fsm import SessionFSM, State

from .conftest import CURRENT_CONSENT_VERSION, make_citizen


# Verifies the anthropometric-only path: MENU → MEASURING_ANTHROPOMETRIC
# → REPORT, skipping vitals entirely. This exercises the
# _path_is_anthropometric condition that the vitals/full tests don't
# touch.
def test_menu_to_measuring_anthropometric_on_path_selected_anthro(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("anthropometric")

    assert fsm.state == State.MEASURING_ANTHROPOMETRIC
    assert fsm.current_session is not None
    assert fsm.current_session.measurement_path == "anthropometric"


# Verifies the vitals-only path: MENU → MEASURING_VITALS →
# measurement_path_complete → REPORT (no anthropometric step).
# This exercises the _path_is_vitals_only condition path.
def test_vitals_only_path_skips_anthropometric(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    assert fsm.state == State.REPORT


# Verifies REPORT → END on finish_without_printing, with the
# Session row's printed_status set to 'not_requested'.
def test_finish_without_printing_marks_not_requested(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    fsm.finish_without_printing()

    assert fsm.state == State.END
    assert fsm.current_session is not None
    assert fsm.current_session.printed_status == "not_requested"
    assert fsm.current_session.status == "completed"


# Verifies the pre-print paper-out branch: paper_out_detected from
# REPORT lands on END with printed_status='paper_out_pre'. The
# session is still 'completed' — printer outage is not a session
# failure (CLAUDE.md, "printer is best-effort").
def test_paper_out_detected_from_report_marks_paper_out_pre(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    fsm.paper_out_detected()

    assert fsm.state == State.END
    assert fsm.current_session is not None
    assert fsm.current_session.printed_status == "paper_out_pre"
    assert fsm.current_session.status == "completed"


# Verifies the mid-print paper-out branch: paper_out_detected from
# PRINTING lands on END with printed_status='paper_out_mid'.
def test_paper_out_detected_from_printing_marks_paper_out_mid(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    fsm.print_requested()
    assert fsm.state == State.PRINTING

    fsm.paper_out_detected()
    assert fsm.state == State.END
    assert fsm.current_session is not None
    assert fsm.current_session.printed_status == "paper_out_mid"


# Verifies the registration → menu thread: REGISTERING → consent_given
# → MENU only works after the GUI has called set_current_citizen with
# the freshly-registered Citizen row. Without that, _ensure_session_row
# raises RuntimeError. This pins the contract that registration must
# attach the citizen before firing consent_given.
def test_registering_to_menu_via_set_current_citizen(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_NEW_001")
    fsm.citizen_identified(None)
    assert fsm.state == State.REGISTERING

    # GUI completes registration and inserts the citizen row, then
    # attaches it before firing consent_given.
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.set_current_citizen(citizen)
    fsm.consent_given()

    assert fsm.state == State.MENU
    assert fsm.current_session is not None
    assert fsm.current_session.citizen_id == citizen.id


# Verifies _ensure_session_row's defensive guard: if consent_given
# fires from REGISTERING with no citizen attached, the FSM raises
# rather than silently creating an orphaned Session.
def test_consent_given_without_citizen_raises(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_NO_CITIZEN")
    fsm.citizen_identified(None)
    assert fsm.state == State.REGISTERING
    # No set_current_citizen call.

    with pytest.raises(RuntimeError, match="no current_citizen"):
        fsm.consent_given()


# Verifies measurement_captured raises if fired in a non-measurement
# state. The FSM is the SOLE serialiser of measurement events; calls
# from IDLE / REPORT / etc. would record audit rows for measurements
# that were never actually taken.
def test_measurement_captured_outside_measurement_states_raises(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    assert fsm.state == State.MENU

    with pytest.raises(RuntimeError, match="only measurement states"):
        fsm.measurement_captured("meas-from-menu")


# Verifies the timeout audit attribution flips to actor_type='system'
# in the abort row (we covered the menu-source case in the main suite,
# but this also captures that the audit row's actor_id is None — there
# is no specific "user" who timed out; it's the kiosk's clock).
def test_timeout_audit_attribution_is_system_with_no_actor(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")
    fsm.timeout()

    audit = (
        db_session.execute(
            select(AuditLog)
            .where(AuditLog.action == "fsm.aborted")
            .order_by(AuditLog.id.desc())
        )
        .scalars()
        .first()
    )
    assert audit is not None
    assert audit.actor_type == "system"
    # Session was created when entering MENU; abort updates its status.
    db_session.flush()
    stored = db_session.execute(
        select(SessionModel).where(SessionModel.id == fsm.current_session.id)
    ).scalar_one()
    assert stored.status == "aborted"
