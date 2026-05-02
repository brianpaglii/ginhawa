"""Kiosk session state machine.

Models the kiosk's user-facing flow per Figure 3.8 of the paper:
RFID scan → identify or register → consent → menu → measurements →
report → end.

Implementation uses the ``transitions`` library. The FSM is
deliberately decoupled from sensors and the GUI — it consumes typed
events and emits database mutations + audit rows; nothing in this
module reaches into BLE / MQTT / Qt. Tests drive transitions by
calling triggers directly.

Side-effect contracts:

* On entering MENU for the first time in a session (whether from
  IDENTIFYING, CONSENT_VERIFICATION, or REGISTERING via consent_given),
  the FSM creates a ``sessions`` row with ``status='in_progress'``.
* On successful END, the row's ``status`` flips to ``'completed'``,
  ``ended_at`` is set, and ``printed_status`` records the print
  outcome.
* On ABORTED, the row's status flips to ``'aborted'``.
* On ERROR, the row's status flips to ``'error'`` and ``error_reason``
  is populated.
* Every transition writes one ``audit_log`` row via the kiosk-side
  ``record_audit`` helper. ``actor_type='citizen'`` for citizen-
  driven transitions; ``'system'`` for timeout / error / acknowledge.

The FSM does not own the database session — the caller passes one
in. This lets the GUI layer batch FSM mutations with surrounding
work in a single transaction, and lets tests use an in-memory DB.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session as SAOrmSession
from transitions import Machine

from ..db.models import Citizen
from ..db.models import Session as SessionModel
from ..services.audit import ActorType, record_audit


_PathChoice = Literal["vitals", "anthropometric", "full"]


# Public state names. Lowercased per ``transitions`` convention; the
# names match the schema's session.status values where applicable
# (``in_progress``, ``completed``, ``aborted``, ``error``).
class State:
    IDLE = "idle"
    IDENTIFYING = "identifying"
    REGISTERING = "registering"
    CONSENT_VERIFICATION = "consent_verification"
    MENU = "menu"
    MEASURING_VITALS = "measuring_vitals"
    MEASURING_ANTHROPOMETRIC = "measuring_anthropometric"
    REPORT = "report"
    PRINTING = "printing"
    END = "end"
    ERROR = "error"
    ABORTED = "aborted"


_ALL_STATES: list[str] = [
    State.IDLE,
    State.IDENTIFYING,
    State.REGISTERING,
    State.CONSENT_VERIFICATION,
    State.MENU,
    State.MEASURING_VITALS,
    State.MEASURING_ANTHROPOMETRIC,
    State.REPORT,
    State.PRINTING,
    State.END,
    State.ERROR,
    State.ABORTED,
]

_TIMEOUT_SOURCES: list[str] = [
    s
    for s in _ALL_STATES
    if s not in (State.IDLE, State.END, State.ERROR, State.ABORTED)
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionFSM:
    """The kiosk's session state machine.

    Construction parameters:

    * ``db`` — SQLAlchemy session. The caller owns the transaction;
      the FSM flushes but never commits.
    * ``device_id`` — the kiosk's UUID (matches
      ``device_credentials.device_id`` in the cloud).
    * ``current_consent_version`` — the consent text revision the
      kiosk is currently presenting. Used to detect stale consent on
      returning citizens.
    """

    # ``transitions.Machine`` injects this attribute at __init__ time;
    # the annotation lets mypy see it. Triggers (rfid_scanned, etc.) are
    # also injected dynamically — they aren't typed here on purpose; the
    # trigger-name reminder block below doubles as their interface.
    state: str

    def __init__(
        self,
        db: SAOrmSession,
        *,
        device_id: str,
        current_consent_version: str,
    ) -> None:
        self.db = db
        self.device_id = device_id
        self.current_consent_version = current_consent_version

        self.current_session: SessionModel | None = None
        self.current_citizen: Citizen | None = None

        # Per-trigger ephemeral state. Set by the corresponding trigger,
        # consumed by the after-callback, then cleared.
        self._pending_rfid_uid: str | None = None
        self._pending_path: _PathChoice | None = None
        self._error_reason: str | None = None
        self._print_status_override: str | None = None

        self._machine = Machine(
            model=self,
            states=_ALL_STATES,
            initial=State.IDLE,
            auto_transitions=False,  # don't generate to_<state>() helpers
            ignore_invalid_triggers=False,
        )

        self._wire_transitions()

    # ------------------------------------------------------------------
    # Transition wiring
    # ------------------------------------------------------------------

    def _wire_transitions(self) -> None:
        m = self._machine

        # IDLE → IDENTIFYING
        m.add_transition(
            "rfid_scanned",
            State.IDLE,
            State.IDENTIFYING,
            after="_after_rfid_scanned",
        )

        # IDENTIFYING → REGISTERING / CONSENT_VERIFICATION / MENU.
        # transitions evaluates conditions in declaration order; the
        # first match wins. We declare unknown-citizen first so a None
        # short-circuits before the consent checks that would NPE.
        m.add_transition(
            "citizen_identified",
            State.IDENTIFYING,
            State.REGISTERING,
            conditions="_is_citizen_unknown",
            after="_after_enter_registering",
        )
        m.add_transition(
            "citizen_identified",
            State.IDENTIFYING,
            State.CONSENT_VERIFICATION,
            conditions="_consent_is_stale",
            after="_after_set_citizen",
        )
        m.add_transition(
            "citizen_identified",
            State.IDENTIFYING,
            State.MENU,
            conditions="_consent_is_current",
            after="_after_enter_menu_with_existing_citizen",
        )

        # CONSENT_VERIFICATION → MENU / ABORTED.
        # REGISTERING → MENU (after registration completes, the GUI
        # fires consent_given to confirm the freshly-collected consent).
        m.add_transition(
            "consent_given",
            [State.CONSENT_VERIFICATION, State.REGISTERING],
            State.MENU,
            after="_after_consent_given",
        )
        m.add_transition(
            "consent_refused",
            State.CONSENT_VERIFICATION,
            State.ABORTED,
            after="_after_aborted",
        )

        # MENU → MEASURING_*.
        # 'full' starts with vitals and chains to anthropometric on
        # measurement_path_complete.
        m.add_transition(
            "path_selected",
            State.MENU,
            State.MEASURING_VITALS,
            conditions="_path_is_vitals_or_full",
            after="_after_path_selected",
        )
        m.add_transition(
            "path_selected",
            State.MENU,
            State.MEASURING_ANTHROPOMETRIC,
            conditions="_path_is_anthropometric",
            after="_after_path_selected",
        )

        # MEASURING_VITALS → MEASURING_ANTHROPOMETRIC (only if 'full')
        m.add_transition(
            "measurement_path_complete",
            State.MEASURING_VITALS,
            State.MEASURING_ANTHROPOMETRIC,
            conditions="_path_is_full",
            after="_after_path_step",
        )
        # MEASURING_VITALS → REPORT (vitals-only path)
        m.add_transition(
            "measurement_path_complete",
            State.MEASURING_VITALS,
            State.REPORT,
            conditions="_path_is_vitals_only",
            after="_after_enter_report",
        )
        # MEASURING_ANTHROPOMETRIC → REPORT (any path that ends here)
        m.add_transition(
            "measurement_path_complete",
            State.MEASURING_ANTHROPOMETRIC,
            State.REPORT,
            after="_after_enter_report",
        )

        # REPORT → PRINTING / END.
        m.add_transition(
            "print_requested",
            State.REPORT,
            State.PRINTING,
            after="_after_print_requested",
        )
        m.add_transition(
            "print_complete",
            State.PRINTING,
            State.END,
            after="_after_print_complete",
        )
        m.add_transition(
            "finish_without_printing",
            State.REPORT,
            State.END,
            after="_after_finish_without_printing",
        )
        # paper_out_detected can fire from either REPORT (pre-print
        # paper-out check) or PRINTING (mid-print failure).
        m.add_transition(
            "paper_out_detected",
            State.REPORT,
            State.END,
            after="_after_paper_out_pre",
        )
        m.add_transition(
            "paper_out_detected",
            State.PRINTING,
            State.END,
            after="_after_paper_out_mid",
        )

        # error from any state → ERROR. timeout from non-idle/terminal
        # states → ABORTED. The before callback marks the abort as
        # system-driven so _after_aborted attributes it correctly.
        m.add_transition("error", "*", State.ERROR, after="_after_error")
        m.add_transition(
            "timeout",
            _TIMEOUT_SOURCES,  # type: ignore[arg-type]
            State.ABORTED,
            before="_before_timeout",
            after="_after_aborted",
        )

        # END / ERROR / ABORTED → IDLE on acknowledge (citizen taps OK
        # or the auto-clear timer fires).
        m.add_transition(
            "acknowledge",
            [State.END, State.ERROR, State.ABORTED],
            State.IDLE,
            after="_after_acknowledge",
        )

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------

    def _is_citizen_unknown(self, citizen: Citizen | None) -> bool:
        return citizen is None

    def _consent_is_stale(self, citizen: Citizen | None) -> bool:
        return (
            citizen is not None
            and citizen.consent_version != self.current_consent_version
        )

    def _consent_is_current(self, citizen: Citizen | None) -> bool:
        return (
            citizen is not None
            and citizen.consent_version == self.current_consent_version
        )

    def _path_is_vitals_or_full(self, path: _PathChoice) -> bool:
        return path in ("vitals", "full")

    def _path_is_anthropometric(self, path: _PathChoice) -> bool:
        return path == "anthropometric"

    def _path_is_full(self) -> bool:
        return self._pending_path == "full"

    def _path_is_vitals_only(self) -> bool:
        return self._pending_path == "vitals"

    # ------------------------------------------------------------------
    # Trigger name reminder
    # ------------------------------------------------------------------
    # ``Machine.add_transition`` generates the named triggers as
    # instance attributes at construction time. This module deliberately
    # does NOT define class-level stubs — they would be silently shadowed
    # at runtime, which makes the failure mode confusing if a stub
    # gets out of sync. Triggers exposed:
    #
    #   rfid_scanned(uid: str)
    #   citizen_identified(citizen: Citizen | None)
    #   consent_given()
    #   consent_refused()
    #   path_selected(path: Literal['vitals','anthropometric','full'])
    #   measurement_path_complete()
    #   print_requested()
    #   print_complete(success: bool, printed_status: str)
    #   finish_without_printing()
    #   paper_out_detected()
    #   error(reason: str)
    #   timeout()
    #   acknowledge()

    # ------------------------------------------------------------------
    # After-callbacks: side effects and audit
    # ------------------------------------------------------------------

    def _after_rfid_scanned(self, uid: str) -> None:
        self._pending_rfid_uid = uid
        self._record_audit(
            action="fsm.rfid_scanned",
            actor_type="citizen",
            actor_id=None,
            details={"to_state": self.state, "rfid_uid": uid},
        )

    def _after_set_citizen(self, citizen: Citizen | None) -> None:
        # Stale-consent path: leaves IDENTIFYING for CONSENT_VERIFICATION,
        # carrying the citizen forward but NOT yet creating the Session
        # (we wait for consent before the session row exists).
        self.current_citizen = citizen
        self._record_audit(
            action="fsm.citizen_identified",
            actor_type="citizen",
            actor_id=citizen.id if citizen else None,
            details={"to_state": self.state},
        )

    def _after_enter_registering(self, citizen: Citizen | None) -> None:
        # Unknown citizen — registration UI takes over. We do NOT
        # create the Session here; it lands when consent_given fires
        # and we transition into MENU.
        self.current_citizen = None
        self._record_audit(
            action="fsm.registering",
            actor_type="citizen",
            actor_id=None,
            details={"to_state": self.state},
        )

    def _after_enter_menu_with_existing_citizen(self, citizen: Citizen | None) -> None:
        # Current-consent path: identifying → menu directly.
        assert citizen is not None  # _consent_is_current guarantees this
        self.current_citizen = citizen
        self._ensure_session_row()
        self._record_audit(
            action="fsm.menu",
            actor_type="citizen",
            actor_id=citizen.id,
            details={"to_state": self.state},
        )

    def _after_consent_given(self) -> None:
        # Two source states: CONSENT_VERIFICATION (returning citizen
        # re-consenting) and REGISTERING (new citizen post-registration).
        # In both cases, the GUI is responsible for having attached the
        # citizen to the FSM via ``set_current_citizen`` before firing
        # this trigger.
        self._ensure_session_row()
        actor_id = self.current_citizen.id if self.current_citizen else None
        self._record_audit(
            action="fsm.consent_given",
            actor_type="citizen",
            actor_id=actor_id,
            details={"to_state": self.state},
        )

    def _after_aborted(self) -> None:
        if self.current_session is not None:
            self.current_session.status = "aborted"
            self.current_session.updated_at = _utc_now_iso()
        actor_type: ActorType = "system" if self._was_timeout() else "citizen"
        self._record_audit(
            action="fsm.aborted",
            actor_type=actor_type,
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state, "timed_out": self._was_timeout()},
        )
        self._reset_timeout_marker()

    def _after_path_selected(self, path: _PathChoice) -> None:
        self._pending_path = path
        if self.current_session is not None:
            self.current_session.measurement_path = (
                "vitals"
                if path == "vitals"
                else "anthropometric"
                if path == "anthropometric"
                else "full"
            )
            self.current_session.updated_at = _utc_now_iso()
        self._record_audit(
            action="fsm.path_selected",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state, "path": path},
        )

    def _after_path_step(self) -> None:
        # full path: vitals just completed, moving to anthropometric.
        self._record_audit(
            action="fsm.measurement_path_step",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state},
        )

    def _after_enter_report(self) -> None:
        self._record_audit(
            action="fsm.report",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state},
        )

    def _after_print_requested(self) -> None:
        self._record_audit(
            action="fsm.print_requested",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state},
        )

    def _after_print_complete(self, success: bool, printed_status: str) -> None:
        self._finalise_session_completed(printed_status=printed_status)
        self._record_audit(
            action="fsm.print_complete",
            actor_type="system",
            actor_id=self.device_id,
            details={
                "to_state": self.state,
                "success": success,
                "printed_status": printed_status,
            },
        )

    def _after_finish_without_printing(self) -> None:
        self._finalise_session_completed(printed_status="not_requested")
        self._record_audit(
            action="fsm.finish_without_printing",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state},
        )

    def _after_paper_out_pre(self) -> None:
        self._finalise_session_completed(printed_status="paper_out_pre")
        self._record_audit(
            action="fsm.paper_out_pre",
            actor_type="system",
            actor_id=self.device_id,
            details={"to_state": self.state},
        )

    def _after_paper_out_mid(self) -> None:
        self._finalise_session_completed(printed_status="paper_out_mid")
        self._record_audit(
            action="fsm.paper_out_mid",
            actor_type="system",
            actor_id=self.device_id,
            details={"to_state": self.state},
        )

    def _after_error(self, reason: str) -> None:
        self._error_reason = reason
        if self.current_session is not None:
            self.current_session.status = "error"
            self.current_session.error_reason = reason
            self.current_session.updated_at = _utc_now_iso()
        self._record_audit(
            action="fsm.error",
            actor_type="system",
            actor_id=self.device_id,
            details={"to_state": self.state, "reason": reason},
        )

    def _after_acknowledge(self) -> None:
        # Reset all per-session ephemeral state so the next IDLE → ...
        # cycle starts clean.
        self.current_session = None
        self.current_citizen = None
        self._pending_rfid_uid = None
        self._pending_path = None
        self._error_reason = None
        self._record_audit(
            action="fsm.acknowledge",
            actor_type="system",
            actor_id=self.device_id,
            details={"to_state": self.state},
        )

    # ------------------------------------------------------------------
    # Public side-channel API (NOT triggers)
    # ------------------------------------------------------------------

    def set_current_citizen(self, citizen: Citizen) -> None:
        """Attach a citizen to the FSM mid-flow.

        Used by the registration handler: when REGISTERING completes,
        the GUI calls this with the freshly-created Citizen row, then
        fires ``consent_given`` to enter MENU. Without it, the
        ``_after_consent_given`` callback would have no citizen_id to
        attribute the new Session to.
        """
        self.current_citizen = citizen

    def measurement_captured(self, measurement_id: str) -> None:
        """Record that a measurement was added to the current session.

        This is NOT a state-changing trigger — measurements accumulate
        within MEASURING_VITALS / MEASURING_ANTHROPOMETRIC; the state
        only changes on ``measurement_path_complete``. We still write
        an audit row so the audit_log captures every captured reading.
        """
        if self.state not in (
            State.MEASURING_VITALS,
            State.MEASURING_ANTHROPOMETRIC,
        ):
            raise RuntimeError(
                f"measurement_captured fired in state {self.state!r}; "
                f"only measurement states accept captures"
            )
        self._record_audit(
            action="fsm.measurement_captured",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state, "measurement_id": measurement_id},
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_session_row(self) -> None:
        if self.current_session is not None:
            return
        if self.current_citizen is None:
            raise RuntimeError(
                "_ensure_session_row called with no current_citizen; "
                "wiring bug — caller must set the citizen before consent_given"
            )
        now = _utc_now_iso()
        self.current_session = SessionModel(
            id=str(uuid.uuid4()),
            citizen_id=self.current_citizen.id,
            device_id=self.device_id,
            started_at=now,
            ended_at=None,
            status="in_progress",
            error_reason=None,
            measurement_path=None,
            printed_status="not_requested",
            synced=0,
            updated_at=now,
        )
        self.db.add(self.current_session)
        self.db.flush()

    def _finalise_session_completed(self, *, printed_status: str) -> None:
        if self.current_session is None:
            return
        now = _utc_now_iso()
        self.current_session.status = "completed"
        self.current_session.ended_at = now
        self.current_session.printed_status = printed_status
        self.current_session.updated_at = now

    def _was_timeout(self) -> bool:
        # Set transiently by the `_before_timeout` callback; cleared in
        # `_after_aborted` via `_reset_timeout_marker`. The state
        # machine guarantees `_before_timeout` runs before
        # `_after_aborted` for every timeout-driven transition.
        return getattr(self, "_timeout_pending", False)

    def _reset_timeout_marker(self) -> None:
        self._timeout_pending = False

    def _before_timeout(self) -> None:
        self._timeout_pending = True

    def _record_audit(
        self,
        *,
        action: str,
        actor_type: ActorType,
        actor_id: str | None,
        details: dict[str, Any],
    ) -> None:
        record_audit(
            self.db,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            object_type="session",
            object_id=self.current_session.id if self.current_session else None,
            details=details,
        )
