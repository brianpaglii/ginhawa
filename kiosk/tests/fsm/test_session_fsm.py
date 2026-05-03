"""SessionFSM behaviour, one transition per test.

State names mechanically renamed for the GUI-integration prompt:
REGISTERING → REGISTER_FORM, CONSENT_VERIFICATION → CONSENT,
MENU → PATH_CHOICE, MEASURING_ANTHROPOMETRIC → MEASURING_ANTHRO.
The IDENTIFYING → MENU direct path is replaced by IDENTIFYING →
LANGUAGE_SELECT → PATH_CHOICE; tests now thread a ``language_chosen``
step where applicable so the existing semantics (citizen identified
→ session created → measurement) still hold.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import AuditLog
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.fsm import SessionFSM, State

from .conftest import (
    CURRENT_CONSENT_VERSION,
    STALE_CONSENT_VERSION,
    TEST_DEVICE_ID,
    make_citizen,
)


def _audit_actions(db: Session) -> list[str]:
    return list(
        db.execute(select(AuditLog.action).order_by(AuditLog.id)).scalars().all()
    )


# Verifies the IDLE → IDENTIFYING transition fires on rfid_scanned and
# writes one audit row attributing the scan to actor_type='citizen'.
# Would fail if the rfid_scanned trigger were renamed, the IDLE → IDENTIFYING
# transition were removed, or _after_rfid_scanned stopped writing audit.
def test_idle_to_identifying_on_rfid_scan(fsm: SessionFSM, db_session: Session) -> None:
    assert fsm.state == State.IDLE
    fsm.rfid_scanned("CARD_PROBE_001")
    assert fsm.state == State.IDENTIFYING
    assert fsm.current_session is None

    actions = _audit_actions(db_session)
    assert "fsm.rfid_scanned" in actions

    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "fsm.rfid_scanned")
    ).scalar_one()
    assert audit.actor_type == "citizen"


# Verifies a known citizen with current consent flows IDENTIFYING →
# LANGUAGE_SELECT → PATH_CHOICE on language_chosen, AND that entering
# PATH_CHOICE creates the Session row with status='in_progress'.
def test_identifying_to_path_choice_on_known_citizen_with_current_consent(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    assert fsm.state == State.LANGUAGE_SELECT

    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE
    assert fsm.current_session is not None
    assert fsm.current_session.status == "in_progress"
    assert fsm.current_session.citizen_id == citizen.id
    assert fsm.current_session.device_id == TEST_DEVICE_ID
    assert fsm.session_language == "en"


# Verifies a known citizen with stale consent transitions
# LANGUAGE_SELECT → CONSENT, and that NO Session row is created yet.
def test_language_select_to_consent_on_stale_consent(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")

    assert fsm.state == State.CONSENT
    assert fsm.current_session is None
    assert fsm.current_citizen is not None
    assert fsm.current_citizen.id == citizen.id


# Verifies an unknown RFID routes IDENTIFYING → LANGUAGE_SELECT →
# REGISTER_FORM with no Session row yet.
def test_language_select_to_register_form_on_unknown_rfid(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_UNKNOWN")
    fsm.citizen_identified(None)
    fsm.language_chosen("tl")

    assert fsm.state == State.REGISTER_FORM
    assert fsm.current_session is None
    assert fsm.current_citizen is None
    assert fsm.session_language == "tl"


# Verifies CONSENT → PATH_CHOICE on consent_given for a stale-consent
# re-prompt, AND that the Session row is created at this point.
def test_consent_to_path_choice_on_consent_given(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.CONSENT
    assert fsm.current_session is None  # not yet

    fsm.consent_given()
    assert fsm.state == State.PATH_CHOICE
    assert fsm.current_session is not None
    assert fsm.current_session.status == "in_progress"


# Verifies CONSENT → ABORTED on consent_refused, attributed to the
# citizen.
def test_consent_to_aborted_on_consent_refused(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.consent_refused()

    assert fsm.state == State.ABORTED
    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "fsm.aborted")
    ).scalar_one()
    assert audit.actor_type == "citizen"


# Verifies PATH_CHOICE → MEASURING_VITALS on path_selected('vitals'),
# and that the Session row's measurement_path is updated.
def test_path_choice_to_measuring_vitals_on_path_selected_vitals(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")

    assert fsm.state == State.MEASURING_VITALS
    assert fsm.current_session is not None
    assert fsm.current_session.measurement_path == "vitals"


# Verifies the full happy-path threading IDLE → END through every state.
def test_full_session_happy_path(fsm: SessionFSM, db_session: Session) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    assert fsm.state == State.IDENTIFYING
    fsm.citizen_identified(citizen)
    assert fsm.state == State.LANGUAGE_SELECT
    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE

    fsm.path_selected("full")
    assert fsm.state == State.MEASURING_VITALS

    fsm.measurement_captured("meas-vitals-1")
    fsm.measurement_captured("meas-vitals-2")

    fsm.measurement_path_complete()
    assert fsm.state == State.MEASURING_ANTHRO

    fsm.measurement_captured("meas-anthro-1")
    fsm.measurement_path_complete()
    assert fsm.state == State.REPORT

    fsm.print_requested()
    assert fsm.state == State.PRINTING

    fsm.print_complete(success=True, printed_status="printed_ok")
    assert fsm.state == State.END

    db_session.flush()
    assert fsm.current_session is not None
    stored = db_session.execute(
        select(SessionModel).where(SessionModel.id == fsm.current_session.id)
    ).scalar_one()
    assert stored.status == "completed"
    assert stored.ended_at is not None
    assert stored.printed_status == "printed_ok"
    assert stored.measurement_path == "full"

    fsm.acknowledge()
    assert fsm.state == State.IDLE
    assert fsm.current_session is None
    assert fsm.current_citizen is None
    assert fsm.session_language is None
    assert fsm.identification_result is None


# Verifies timeout from a non-idle / non-terminal state lands on
# ABORTED with actor_type='system'.
def test_timeout_from_path_choice_transitions_to_aborted_with_system_attribution(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE

    fsm.timeout()

    assert fsm.state == State.ABORTED
    db_session.flush()
    assert fsm.current_session is not None
    stored = db_session.execute(
        select(SessionModel).where(SessionModel.id == fsm.current_session.id)
    ).scalar_one()
    assert stored.status == "aborted"

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


# Verifies error('reason') from MEASURING_VITALS lands on ERROR with
# the reason persisted to the Session row.
def test_error_in_measurement_transitions_to_error_state_with_reason(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert fsm.state == State.MEASURING_VITALS

    fsm.error("ble_disconnect_during_bp")

    assert fsm.state == State.ERROR
    db_session.flush()
    assert fsm.current_session is not None
    stored = db_session.execute(
        select(SessionModel).where(SessionModel.id == fsm.current_session.id)
    ).scalar_one()
    assert stored.status == "error"
    assert stored.error_reason == "ble_disconnect_during_bp"

    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "fsm.error")
    ).scalar_one()
    assert audit.actor_type == "system"
    assert audit.actor_id == TEST_DEVICE_ID
