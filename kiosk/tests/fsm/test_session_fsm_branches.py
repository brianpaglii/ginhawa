"""Supplemental FSM tests covering branches not exercised by the
per-transition / happy-path tests. State names mechanically renamed
for the GUI-integration prompt and language_chosen steps inserted
where a known citizen now passes through LANGUAGE_SELECT."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import AuditLog
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.fsm import SessionFSM, State

from .conftest import CURRENT_CONSENT_VERSION, make_citizen


# Verifies the anthropometric-only path: PATH_CHOICE → MEASURING_ANTHRO.
def test_path_choice_to_measuring_anthro_on_path_selected_anthro(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("anthropometric")

    assert fsm.state == State.MEASURING_ANTHRO
    assert fsm.current_session is not None
    assert fsm.current_session.measurement_path == "anthropometric"


# Verifies the vitals-only path: MEASURING_VITALS → REPORT.
def test_vitals_only_path_skips_anthropometric(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    assert fsm.state == State.REPORT


# Verifies REPORT → END on finish_without_printing.
def test_finish_without_printing_marks_not_requested(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    fsm.finish_without_printing()

    assert fsm.state == State.END
    assert fsm.current_session is not None
    assert fsm.current_session.printed_status == "not_requested"
    assert fsm.current_session.status == "completed"


# Verifies the pre-print paper-out branch.
def test_paper_out_detected_from_report_marks_paper_out_pre(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    fsm.paper_out_detected()

    assert fsm.state == State.END
    assert fsm.current_session is not None
    assert fsm.current_session.printed_status == "paper_out_pre"
    assert fsm.current_session.status == "completed"


# Verifies the mid-print paper-out branch.
def test_paper_out_detected_from_printing_marks_paper_out_mid(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    fsm.print_requested()
    assert fsm.state == State.PRINTING

    fsm.paper_out_detected()
    assert fsm.state == State.END
    assert fsm.current_session is not None
    assert fsm.current_session.printed_status == "paper_out_mid"


# Verifies REGISTER_FORM → CONSENT (after registration_complete with
# attached citizen) → PATH_CHOICE (after consent_given).
def test_register_form_to_path_choice_via_set_current_citizen(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_NEW_001")
    fsm.citizen_identified(None)
    fsm.language_chosen("en")
    assert fsm.state == State.REGISTER_FORM

    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.set_current_citizen(citizen)
    fsm.registration_complete()
    assert fsm.state == State.CONSENT

    fsm.consent_given()
    assert fsm.state == State.PATH_CHOICE
    assert fsm.current_session is not None
    assert fsm.current_session.citizen_id == citizen.id


# Verifies _ensure_session_row's defensive guard.
def test_consent_given_without_citizen_raises(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_NO_CITIZEN")
    fsm.citizen_identified(None)
    fsm.language_chosen("en")
    assert fsm.state == State.REGISTER_FORM
    fsm.registration_complete()
    assert fsm.state == State.CONSENT

    with pytest.raises(RuntimeError, match="no current_citizen"):
        fsm.consent_given()


# Verifies measurement_captured raises if fired in a non-measurement
# state.
def test_measurement_captured_outside_measurement_states_raises(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE

    with pytest.raises(RuntimeError, match="only measurement states"):
        fsm.measurement_captured("meas-from-path-choice")


# Verifies the timeout audit attribution flips to actor_type='system'.
def test_timeout_audit_attribution_is_system(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
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
    db_session.flush()
    assert fsm.current_session is not None
    stored = db_session.execute(
        select(SessionModel).where(SessionModel.id == fsm.current_session.id)
    ).scalar_one()
    assert stored.status == "aborted"
