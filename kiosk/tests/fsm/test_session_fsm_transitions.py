"""FSM transition tests added for the GUI-integration prompt.

The renamed/repurposed transitions covered by ``test_session_fsm.py``
and ``test_session_fsm_branches.py`` continue to live there. This
module focuses on the LANGUAGE_SELECT-driven transitions and the
session-language / change-language context handling that the prompt-8
work introduced.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import AuditLog
from ginhawa_kiosk.fsm import IdentificationResult, SessionFSM, State

from .conftest import (
    CURRENT_CONSENT_VERSION,
    STALE_CONSENT_VERSION,
    make_citizen,
)


# Verifies IDLE → IDENTIFYING fires on rfid_scanned and the DB lookup
# stage transitions through to LANGUAGE_SELECT only on
# citizen_identified — not as a side-effect of rfid_scanned alone.
# Mortality: 'Would fail if FSM did not subscribe to RfidScanned events.'
def test_idle_to_identifying_on_rfid_scan(fsm: SessionFSM, db_session: Session) -> None:
    assert fsm.state == State.IDLE
    fsm.rfid_scanned("CARD_TRANSITION_1")
    assert fsm.state == State.IDENTIFYING
    # The lookup hasn't completed yet — must NOT have advanced past
    # IDENTIFYING just because an RFID arrived.
    assert fsm.identification_result is None


# Verifies IDENTIFYING → LANGUAGE_SELECT on citizen_identified, with
# the identification_result populated for the LANGUAGE_SELECT routing.
# Mortality: 'Would fail if FSM did not transition after DB lookup
# completes.'
def test_identifying_to_language_select_on_lookup_complete(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)

    assert fsm.state == State.LANGUAGE_SELECT
    assert fsm.identification_result is not None
    assert fsm.identification_result.citizen is not None
    assert fsm.identification_result.citizen.id == citizen.id
    assert fsm.identification_result.is_unknown is False
    assert fsm.identification_result.consent_is_stale is False


# Verifies IDENTIFYING → ERROR on identification_failed, with the
# reason persisted on the audit row. The kiosk's clock will auto-
# return to IDLE; this test only checks the immediate transition.
# Mortality: 'Would fail if FSM did not handle DB exceptions in the
# IDENTIFYING state.'
def test_identifying_to_error_on_db_unreachable(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_FAILED_LOOKUP")
    assert fsm.state == State.IDENTIFYING

    fsm.identification_failed("sqlcipher_unreadable")
    assert fsm.state == State.ERROR

    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "fsm.error")
    ).scalar_one()
    assert audit.actor_type == "system"


# Verifies the consent-current branch out of LANGUAGE_SELECT lands on
# PATH_CHOICE without prompting for re-consent.
# Mortality: 'Would fail if FSM did not check consent_version on the
# looked-up citizen.'
def test_language_select_to_path_choice_when_consent_current(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE


# Verifies the consent-stale branch out of LANGUAGE_SELECT routes to
# CONSENT for re-prompting.
# Mortality: 'Would fail if FSM skipped consent re-prompting after
# privacy notice version change.'
def test_language_select_to_consent_when_consent_stale(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.CONSENT


# Verifies the unknown-uid branch out of LANGUAGE_SELECT routes to
# REGISTER_FORM for self-service registration.
# Mortality: 'Would fail if FSM did not detect the new-citizen path.'
def test_language_select_to_register_form_when_uid_unknown(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_UNREGISTERED")
    fsm.citizen_identified(None)
    fsm.language_chosen("tl")
    assert fsm.state == State.REGISTER_FORM


# Verifies the chosen language is captured on the FSM's
# session_language attribute as soon as language_chosen fires.
# Mortality: 'Would fail if FSM did not store the chosen language in
# context.'
def test_session_language_set_at_language_select_transition(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    assert fsm.session_language is None  # not yet chosen
    fsm.language_chosen("tl")
    assert fsm.session_language == "tl"


# Verifies session_language is reset to None on END → IDLE
# (acknowledge), so the next session re-prompts the citizen for
# language. Same pattern asserted for ABORTED and ERROR below.
# Mortality: 'Would fail if session-scoped state leaked across sessions.'
def test_session_language_cleared_on_return_to_idle_via_end(
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
    assert fsm.session_language == "en"

    fsm.acknowledge()
    assert fsm.state == State.IDLE
    assert fsm.session_language is None
    assert fsm.identification_result is None
    assert fsm.current_session is None


# Mortality: 'Would fail if session-scoped state leaked across sessions.'
def test_session_language_cleared_on_return_to_idle_via_aborted(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.cancel()
    assert fsm.state == State.ABORTED
    assert fsm.session_language == "en"

    fsm.acknowledge()
    assert fsm.state == State.IDLE
    assert fsm.session_language is None
    assert fsm.identification_result is None


# Mortality: 'Would fail if session-scoped state leaked across sessions.'
def test_session_language_cleared_on_return_to_idle_via_error(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.error("test_error")
    assert fsm.state == State.ERROR
    assert fsm.session_language == "en"

    fsm.acknowledge()
    assert fsm.state == State.IDLE
    assert fsm.session_language is None
    assert fsm.identification_result is None


# Verifies REGISTER_FORM → LANGUAGE_SELECT on change_language; the
# screen retains the citizen's pending registration intent (no
# session row yet) but lets them pick a different language.
# Mortality: 'Would fail if "Change language" button were missed in
# implementation.'
def test_register_form_change_language_returns_to_language_select(
    fsm: SessionFSM, db_session: Session
) -> None:
    fsm.rfid_scanned("CARD_NEW_LANG_CHANGE")
    fsm.citizen_identified(None)
    fsm.language_chosen("en")
    assert fsm.state == State.REGISTER_FORM

    fsm.change_language()
    assert fsm.state == State.LANGUAGE_SELECT
    # Identification result preserved across language change
    assert fsm.identification_result is not None
    assert fsm.identification_result.is_unknown is True
    # Language reset so the next language_chosen branches afresh
    assert fsm.session_language is None


# Mortality: 'Would fail if "Change language" button were missed in
# implementation.'
def test_consent_change_language_returns_to_language_select(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.CONSENT

    fsm.change_language()
    assert fsm.state == State.LANGUAGE_SELECT
    assert fsm.identification_result is not None
    assert fsm.identification_result.consent_is_stale is True
    assert fsm.session_language is None


# Mortality: 'Would fail if "Change language" button were missed in
# implementation.'
def test_path_choice_change_language_returns_to_language_select(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE
    # Session row was created on entry to PATH_CHOICE
    assert fsm.current_session is not None
    session_id_before = fsm.current_session.id

    fsm.change_language()
    assert fsm.state == State.LANGUAGE_SELECT
    # Session row preserved across language change so the next
    # language_chosen continues the same session.
    assert fsm.current_session is not None
    assert fsm.current_session.id == session_id_before


# Mortality: 'Would fail if "Change language" button were missed in
# implementation.'
def test_report_change_language_returns_to_language_select(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    fsm.measurement_path_complete()
    assert fsm.state == State.REPORT

    fsm.change_language()
    assert fsm.state == State.LANGUAGE_SELECT
    assert fsm.session_language is None


# Verifies that change_language → language_chosen with a different
# language continues the same logical flow rendered in the new
# language: the FSM lands on the same logical state with the same
# identification context.
# Mortality: 'Would fail if change-language reset the identification
# context, forcing citizen to re-tap their card.'
def test_change_language_preserves_identification_context(
    fsm: SessionFSM, db_session: Session
) -> None:
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.rfid_scanned(citizen.rfid_uid)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")
    assert fsm.state == State.PATH_CHOICE

    # Citizen taps "Change language" then picks Tagalog.
    fsm.change_language()
    assert fsm.state == State.LANGUAGE_SELECT
    fsm.language_chosen("tl")
    # Same logical state, new language.
    assert fsm.state == State.PATH_CHOICE
    assert fsm.session_language == "tl"
    assert fsm.current_citizen is not None
    assert fsm.current_citizen.id == citizen.id


# Verifies the cancel trigger fires from every cancellable state and
# attributes the audit row to actor_type='citizen' (distinct from
# the timeout path which attributes 'system').
@pytest.mark.parametrize(
    "drive_to_state",
    [
        State.LANGUAGE_SELECT,
        State.REGISTER_FORM,
        State.CONSENT,
        State.PATH_CHOICE,
        State.MEASURING_VITALS,
        State.REPORT,
    ],
)
def test_cancel_button_from_each_cancellable_state(
    fsm: SessionFSM, db_session: Session, drive_to_state: str
) -> None:
    """Mortality: would fail if the cancel button were dropped from
    any of the cancellable states (citizen would have no exit other
    than waiting for a timeout)."""
    if drive_to_state == State.LANGUAGE_SELECT:
        fsm.rfid_scanned("CARD_CANCEL")
        fsm.citizen_identified(make_citizen(db_session))
    elif drive_to_state == State.REGISTER_FORM:
        fsm.rfid_scanned("CARD_CANCEL_NEW")
        fsm.citizen_identified(None)
        fsm.language_chosen("en")
    elif drive_to_state == State.CONSENT:
        fsm.rfid_scanned("CARD_CANCEL_STALE")
        fsm.citizen_identified(
            make_citizen(db_session, consent_version=STALE_CONSENT_VERSION)
        )
        fsm.language_chosen("en")
    elif drive_to_state == State.PATH_CHOICE:
        fsm.rfid_scanned("CARD_CANCEL_PATH")
        fsm.citizen_identified(make_citizen(db_session))
        fsm.language_chosen("en")
    elif drive_to_state == State.MEASURING_VITALS:
        fsm.rfid_scanned("CARD_CANCEL_VITALS")
        fsm.citizen_identified(make_citizen(db_session))
        fsm.language_chosen("en")
        fsm.path_selected("vitals")
    elif drive_to_state == State.REPORT:
        fsm.rfid_scanned("CARD_CANCEL_REPORT")
        fsm.citizen_identified(make_citizen(db_session))
        fsm.language_chosen("en")
        fsm.path_selected("vitals")
        fsm.measurement_path_complete()

    assert fsm.state == drive_to_state
    fsm.cancel()
    assert fsm.state == State.ABORTED

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
    assert audit.actor_type == "citizen"


# Verifies the FSM emits state_changed via the Qt signal carrier on
# every successful transition, with the post-transition state name
# and an FsmSnapshot carrying the live context.
# Mortality: would fail if the signal weren't wired (the GUI's main
# window would never switch pages).
def test_state_changed_signal_fires_on_transition(
    fsm: SessionFSM, db_session: Session
) -> None:
    received: list[tuple[str, object]] = []

    def listener(state: str, snapshot: object) -> None:
        received.append((state, snapshot))

    fsm.signals.state_changed.connect(listener)

    fsm.rfid_scanned("CARD_SIGNAL")
    citizen = make_citizen(db_session, consent_version=CURRENT_CONSENT_VERSION)
    fsm.citizen_identified(citizen)
    fsm.language_chosen("en")

    states = [s for s, _ in received]
    assert states == [State.IDENTIFYING, State.LANGUAGE_SELECT, State.PATH_CHOICE]
    # Last snapshot reflects the final state's context.
    last_state, last_snapshot = received[-1]
    assert last_state == State.PATH_CHOICE
    # Snapshot type-check via attribute access (avoids cyclic import
    # on FsmSnapshot for the runtime test).
    assert getattr(last_snapshot, "session_language") == "en"
    assert isinstance(
        getattr(last_snapshot, "identification_result"), IdentificationResult
    )
