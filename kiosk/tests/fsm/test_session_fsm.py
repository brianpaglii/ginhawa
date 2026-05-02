"""SessionFSM behaviour, one transition per test."""

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
# writes one audit row attributing the scan to actor_type='citizen'
# (citizen physically tapped the card). No Session row exists yet —
# the citizen hasn't been resolved.
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


# Verifies a known citizen with current consent flows IDENTIFYING → MENU
# directly, AND that entering MENU creates the Session row with
# status='in_progress'.
# Would fail if _consent_is_current returned False on equal versions,
# or if _ensure_session_row stopped firing on the menu transition.
def test_identifying_to_menu_on_known_citizen_with_current_consent(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)

    assert fsm.state == State.MENU
    assert fsm.current_session is not None
    assert fsm.current_session.status == "in_progress"
    assert fsm.current_session.citizen_id == citizen.id
    assert fsm.current_session.device_id == TEST_DEVICE_ID


# Verifies a known citizen whose stored consent version is older than
# the kiosk's current version transitions IDENTIFYING → CONSENT_VERIFICATION,
# and that NO Session row is created yet (we wait for consent_given).
# Would fail if _consent_is_stale were inverted or if the FSM created
# a Session prematurely (which would persist a row attributed to a
# citizen who never consented to the new version).
def test_identifying_to_consent_verification_on_stale_consent(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)

    assert fsm.state == State.CONSENT_VERIFICATION
    assert fsm.current_session is None
    assert fsm.current_citizen is not None
    assert fsm.current_citizen.id == citizen.id


# Verifies an unknown RFID (citizen=None) routes IDENTIFYING → REGISTERING.
# No Session row is created — the citizen doesn't exist yet; the
# registration UI takes over and the Session lands when consent_given
# fires after registration completes.
# Would fail if _is_citizen_unknown were inverted, or if the unknown-
# citizen path tried to dereference None to create a Session.
def test_identifying_to_registering_on_unknown_rfid(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_UNKNOWN")
    fsm.citizen_identified(None)

    assert fsm.state == State.REGISTERING
    assert fsm.current_session is None
    assert fsm.current_citizen is None


# Verifies CONSENT_VERIFICATION → MENU on consent_given, AND that the
# Session row is created at this point (deferred from IDENTIFYING).
# Would fail if _ensure_session_row were dropped from
# _after_consent_given (the Session row would never appear and the
# subsequent measurement_captured calls would have no parent row).
def test_consent_verification_to_menu_on_consent_given(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    assert fsm.state == State.CONSENT_VERIFICATION
    assert fsm.current_session is None  # not yet

    fsm.consent_given()
    assert fsm.state == State.MENU
    assert fsm.current_session is not None
    assert fsm.current_session.status == "in_progress"


# Verifies CONSENT_VERIFICATION → ABORTED on consent_refused. The
# audit row is attributed to actor_type='citizen' (the citizen made
# the choice). No Session was ever created so there is nothing to
# update — but the ABORTED state itself must hold.
# Would fail if consent_refused routed to ERROR (which would mis-
# attribute a citizen's choice as a system failure) or if the trigger
# weren't wired at all.
def test_consent_verification_to_aborted_on_consent_refused(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.consent_refused()

    assert fsm.state == State.ABORTED
    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "fsm.aborted")
    ).scalar_one()
    assert audit.actor_type == "citizen"


# Verifies MENU → MEASURING_VITALS on path_selected('vitals'), and
# that the Session row's measurement_path is updated to record the
# choice. The 'full' path also lands in MEASURING_VITALS first.
# Would fail if _path_is_vitals_or_full were tightened to vitals-only,
# or if _after_path_selected stopped writing measurement_path.
def test_menu_to_measuring_vitals_on_path_selected_vitals(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")

    assert fsm.state == State.MEASURING_VITALS
    assert fsm.current_session is not None
    assert fsm.current_session.measurement_path == "vitals"


# Verifies the full happy-path threading IDLE → END through every state
# in order, including the 'full' path that goes vitals first then
# anthropometric. The final Session row records status='completed',
# ended_at populated, and printed_status='printed_ok'.
# Would fail if any single transition along this thread were broken
# — this is the integration check across the FSM.
def test_full_session_happy_path(fsm: SessionFSM, db_session: Session) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    assert fsm.state == State.IDENTIFYING
    fsm.citizen_identified(citizen)
    assert fsm.state == State.MENU

    fsm.path_selected("full")
    assert fsm.state == State.MEASURING_VITALS

    # Vitals captures (state-preserving)
    fsm.measurement_captured("meas-vitals-1")
    fsm.measurement_captured("meas-vitals-2")

    fsm.measurement_path_complete()
    assert fsm.state == State.MEASURING_ANTHROPOMETRIC

    fsm.measurement_captured("meas-anthro-1")
    fsm.measurement_path_complete()
    assert fsm.state == State.REPORT

    fsm.print_requested()
    assert fsm.state == State.PRINTING

    fsm.print_complete(success=True, printed_status="printed_ok")
    assert fsm.state == State.END

    db_session.flush()
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


# Verifies timeout from any non-idle / non-terminal state lands on
# ABORTED, AND that the resulting audit row is attributed to
# actor_type='system' (citizen walked away — kiosk decided to abort).
# We exercise the timeout from MENU; the timeout transition is
# parameterised to fire from every non-terminal source state.
# Would fail if _before_timeout were dropped (audit would mis-
# attribute the abort to the citizen) or if _TIMEOUT_SOURCES were
# truncated to a subset.
def test_timeout_from_any_non_idle_state_transitions_to_aborted(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    assert fsm.state == State.MENU

    fsm.timeout()

    assert fsm.state == State.ABORTED
    db_session.flush()
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
# the reason persisted to the Session row's error_reason and the
# audit row attributing the error to actor_type='system' with
# actor_id=device_id. The error trigger fires from any state ('*'),
# so MEASURING_VITALS is just one representative source.
# Would fail if _after_error stopped persisting the reason, or if
# the trigger were renamed (which would clash with auto-generated
# names).
def test_error_in_measurement_transitions_to_error_state_with_reason(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.path_selected("vitals")
    assert fsm.state == State.MEASURING_VITALS

    fsm.error("ble_disconnect_during_bp")

    assert fsm.state == State.ERROR
    db_session.flush()
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
