"""Kiosk session state machine.

Models the kiosk's user-facing flow per Figure 3.8 of the paper, with
the Phase 2 prompt-8 GUI integration: IDLE → RFID scan → IDENTIFYING
(DB lookup) → LANGUAGE_SELECT (citizen picks EN/TL) → CONSENT /
REGISTER_FORM / PATH_CHOICE (per identification result) → measurements
→ REPORT → END.

Implementation uses the ``transitions`` library for the state graph
plus a small Qt signal (:class:`_FsmSignals`) so the main window can
update the visible :class:`QStackedWidget` page on every transition.
The FSM is deliberately decoupled from sensors and the GUI — it
consumes typed events / triggers and emits database mutations + audit
rows; nothing in this module reaches into BLE / MQTT / Qt widgets.
Tests drive transitions by calling triggers directly.

Side-effect contracts:

* On entering PATH_CHOICE for the first time in a session — whether
  via LANGUAGE_SELECT (current consent), CONSENT (re-consent flow),
  or REGISTER_FORM → CONSENT → PATH_CHOICE — the FSM creates a
  ``sessions`` row with ``status='in_progress'`` (see
  ``_ensure_session_row``).
* On successful END, the row's ``status`` flips to ``'completed'``,
  ``ended_at`` is set, and ``printed_status`` records the print
  outcome.
* On ABORTED, the row's status flips to ``'aborted'``.
* On ERROR, the row's status flips to ``'error'`` and ``error_reason``
  is populated.
* Every transition writes one ``audit_log`` row via the kiosk-side
  ``record_audit`` helper. ``actor_type='citizen'`` for citizen-driven
  transitions; ``'system'`` for timeout / cancel-by-system / error /
  acknowledge attribution.
* Language choice is in-memory only — there is no ``Session.language``
  column. Returning to IDLE clears ``session_language``,
  ``identification_result``, and ``current_session`` so the next visit
  starts clean.

The FSM does not own the database session — the caller passes one
in. This lets the GUI layer batch FSM mutations with surrounding
work in a single transaction, and lets tests use an in-memory DB.

Qt signal surface
-----------------
``self.signals.state_changed`` is a ``pyqtSignal(str, object)`` that
fires AFTER every transition with the new state name and an
:class:`FsmSnapshot` carrying the session's current context. The
GUI's main window connects to this to switch the visible page; tests
that don't care about Qt simply ignore it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from PyQt6.QtCore import QObject, pyqtSignal
from sqlalchemy.orm import Session as SAOrmSession
from transitions import Machine

from ..db.models import Citizen
from ..db.models import Session as SessionModel
from ..services.audit import ActorType, record_audit


Language = Literal["en", "tl"]
_PathChoice = Literal["vitals", "anthropometric", "full"]


# Public state names. Lowercased per ``transitions`` convention; the
# names match the schema's session.status values where applicable
# (``in_progress``, ``completed``, ``aborted``, ``error``).
class State:
    IDLE = "idle"
    IDENTIFYING = "identifying"
    LANGUAGE_SELECT = "language_select"
    REGISTER_FORM = "register_form"
    CONSENT = "consent"
    PATH_CHOICE = "path_choice"
    MEASURING_VITALS = "measuring_vitals"
    MEASURING_ANTHRO = "measuring_anthro"
    REPORT = "report"
    PRINTING = "printing"
    END = "end"
    ERROR = "error"
    ABORTED = "aborted"


_ALL_STATES: list[str] = [
    State.IDLE,
    State.IDENTIFYING,
    State.LANGUAGE_SELECT,
    State.REGISTER_FORM,
    State.CONSENT,
    State.PATH_CHOICE,
    State.MEASURING_VITALS,
    State.MEASURING_ANTHRO,
    State.REPORT,
    State.PRINTING,
    State.END,
    State.ERROR,
    State.ABORTED,
]

# States from which the citizen-facing cancel button (or a system
# timeout) should land on ABORTED. IDLE / IDENTIFYING / END / ERROR /
# ABORTED are excluded — they are entry, transient, or terminal
# states with no meaningful "cancel" semantics.
_CANCELLABLE_STATES: list[str] = [
    State.LANGUAGE_SELECT,
    State.REGISTER_FORM,
    State.CONSENT,
    State.PATH_CHOICE,
    State.MEASURING_VITALS,
    State.MEASURING_ANTHRO,
    State.REPORT,
    State.PRINTING,
]

# Source states for ``change_language`` — explicit list so a future
# screen can opt in or out at the FSM level without touching wiring.
_CHANGE_LANGUAGE_SOURCES: list[str] = [
    State.REGISTER_FORM,
    State.CONSENT,
    State.PATH_CHOICE,
    State.REPORT,
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IdentificationResult:
    """Outcome of the IDENTIFYING-state DB lookup.

    ``citizen`` is None when the RFID UID resolved to no row; in that
    case the FSM routes LANGUAGE_SELECT → REGISTER_FORM. Otherwise
    ``consent_is_stale`` decides between CONSENT (re-prompt) and
    PATH_CHOICE (consent already current).
    """

    citizen: Citizen | None
    consent_is_stale: bool

    @property
    def is_unknown(self) -> bool:
        return self.citizen is None


@dataclass(frozen=True)
class FsmSnapshot:
    """Read-only view of FSM context, broadcast on every transition.

    Decouples GUI subscribers from internal mutable state — the
    snapshot is captured at emission time, so a screen receiving a
    state_changed signal sees the values that were live when the
    transition fired.
    """

    state: str
    session_language: Language | None
    identification_result: IdentificationResult | None
    current_session_id: str | None
    current_citizen_id: str | None


class _FsmSignals(QObject):
    """Qt signal carrier for the FSM.

    Held as a member of :class:`SessionFSM` rather than baked into
    SessionFSM's class hierarchy: composition keeps the FSM free of
    QObject-metaclass interactions with ``transitions.Machine`` and
    keeps non-GUI tests Qt-free at the type level.
    """

    state_changed = pyqtSignal(str, object)  # (state, FsmSnapshot)


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

    # ``transitions.Machine`` injects ``state`` and one method per
    # named trigger at __init__ time; the annotations below let mypy
    # see them. ``transitions`` overrides these with bound callables
    # at runtime — the assignments here are pure type information.
    state: str

    if TYPE_CHECKING:

        def rfid_scanned(self, uid: str) -> None: ...
        def citizen_identified(self, citizen: Citizen | None) -> None: ...
        def identification_failed(self, reason: str) -> None: ...
        def language_chosen(self, language: Language) -> None: ...
        def registration_complete(self) -> None: ...
        def consent_given(self) -> None: ...
        def consent_refused(self) -> None: ...
        def change_language(self) -> None: ...
        def path_selected(self, path: _PathChoice) -> None: ...
        def measurement_path_complete(self) -> None: ...
        def print_requested(self) -> None: ...
        def print_complete(self, success: bool, printed_status: str) -> None: ...
        def finish_without_printing(self) -> None: ...
        def paper_out_detected(self) -> None: ...
        def cancel(self) -> None: ...
        def error(self, reason: str) -> None: ...
        def timeout(self) -> None: ...
        def acknowledge(self) -> None: ...

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
        self.session_language: Language | None = None
        self.identification_result: IdentificationResult | None = None

        # Per-trigger ephemeral state. Set by the corresponding trigger,
        # consumed by the after-callback, then cleared.
        self._pending_rfid_uid: str | None = None
        self._pending_path: _PathChoice | None = None
        self._error_reason: str | None = None
        self._abort_attribution: ActorType = "citizen"

        # Journal-side logger for state-change traces. The audit_log
        # table (via record_audit) is the forensic record; structlog
        # is the real-time ops trace. Both are written on every
        # transition.
        import structlog

        self._fsm_logger = structlog.get_logger("fsm.session")

        self.signals = _FsmSignals()

        self._machine = Machine(
            model=self,
            states=_ALL_STATES,
            initial=State.IDLE,
            auto_transitions=False,  # don't generate to_<state>() helpers
            ignore_invalid_triggers=False,
            after_state_change="_emit_state_changed",
        )

        self._wire_transitions()

    # ------------------------------------------------------------------
    # Public read-only context
    # ------------------------------------------------------------------

    @property
    def current_session_id(self) -> str | None:
        return self.current_session.id if self.current_session is not None else None

    # ------------------------------------------------------------------
    # Transition wiring
    # ------------------------------------------------------------------

    def _wire_transitions(self) -> None:
        m = self._machine

        # IDLE → IDENTIFYING (citizen taps card).
        m.add_transition(
            "rfid_scanned",
            State.IDLE,
            State.IDENTIFYING,
            after="_after_rfid_scanned",
        )

        # IDENTIFYING → LANGUAGE_SELECT (DB lookup completed).
        m.add_transition(
            "citizen_identified",
            State.IDENTIFYING,
            State.LANGUAGE_SELECT,
            after="_after_citizen_identified",
        )

        # IDENTIFYING → ERROR (DB unreachable / timeout).
        m.add_transition(
            "identification_failed",
            State.IDENTIFYING,
            State.ERROR,
            after="_after_error",
        )

        # LANGUAGE_SELECT → REGISTER_FORM / CONSENT / PATH_CHOICE.
        # transitions evaluates conditions in declaration order; first
        # match wins. We declare unknown-citizen first so a None
        # short-circuits before the consent checks that would NPE.
        m.add_transition(
            "language_chosen",
            State.LANGUAGE_SELECT,
            State.REGISTER_FORM,
            conditions="_identification_is_unknown",
            after="_after_language_chosen_to_register",
        )
        m.add_transition(
            "language_chosen",
            State.LANGUAGE_SELECT,
            State.CONSENT,
            conditions="_identification_consent_is_stale",
            after="_after_language_chosen_to_consent",
        )
        m.add_transition(
            "language_chosen",
            State.LANGUAGE_SELECT,
            State.PATH_CHOICE,
            conditions="_identification_consent_is_current",
            after="_after_language_chosen_to_path_choice",
        )

        # REGISTER_FORM → CONSENT (after the GUI inserts the new Citizen
        # row and attaches it via set_current_citizen).
        m.add_transition(
            "registration_complete",
            State.REGISTER_FORM,
            State.CONSENT,
            after="_after_registration_complete",
        )

        # CONSENT → PATH_CHOICE / ABORTED.
        m.add_transition(
            "consent_given",
            State.CONSENT,
            State.PATH_CHOICE,
            after="_after_consent_given",
        )
        m.add_transition(
            "consent_refused",
            State.CONSENT,
            State.ABORTED,
            after="_after_aborted",
        )

        # change_language: REGISTER_FORM / CONSENT / PATH_CHOICE / REPORT
        # → LANGUAGE_SELECT. Preserves identification_result and
        # current_session so the next language_chosen continues the
        # same logical flow rendered in the new language.
        m.add_transition(
            "change_language",
            _CHANGE_LANGUAGE_SOURCES,  # type: ignore[arg-type]
            State.LANGUAGE_SELECT,
            after="_after_change_language",
        )

        # PATH_CHOICE → MEASURING_*.
        m.add_transition(
            "path_selected",
            State.PATH_CHOICE,
            State.MEASURING_VITALS,
            conditions="_path_is_vitals_or_full",
            after="_after_path_selected",
        )
        m.add_transition(
            "path_selected",
            State.PATH_CHOICE,
            State.MEASURING_ANTHRO,
            conditions="_path_is_anthropometric",
            after="_after_path_selected",
        )

        # MEASURING_VITALS → MEASURING_ANTHRO (only if 'full')
        m.add_transition(
            "measurement_path_complete",
            State.MEASURING_VITALS,
            State.MEASURING_ANTHRO,
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
        # MEASURING_ANTHRO → REPORT (any path that ends here)
        m.add_transition(
            "measurement_path_complete",
            State.MEASURING_ANTHRO,
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

        # cancel: any cancellable state → ABORTED, attributed to the
        # citizen (they tapped the on-screen Cancel button).
        m.add_transition(
            "cancel",
            _CANCELLABLE_STATES,  # type: ignore[arg-type]
            State.ABORTED,
            before="_before_cancel",
            after="_after_aborted",
        )

        # error from any state → ERROR. timeout from any cancellable
        # state → ABORTED, attributed to the system.
        m.add_transition("error", "*", State.ERROR, after="_after_error")
        m.add_transition(
            "timeout",
            _CANCELLABLE_STATES,  # type: ignore[arg-type]
            State.ABORTED,
            before="_before_timeout",
            after="_after_aborted",
        )

        # END / ERROR / ABORTED → IDLE on acknowledge.
        m.add_transition(
            "acknowledge",
            [State.END, State.ERROR, State.ABORTED],
            State.IDLE,
            after="_after_acknowledge",
        )

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------

    def _identification_is_unknown(self, language: Language) -> bool:
        return (
            self.identification_result is not None
            and self.identification_result.is_unknown
        )

    def _identification_consent_is_stale(self, language: Language) -> bool:
        return (
            self.identification_result is not None
            and not self.identification_result.is_unknown
            and self.identification_result.consent_is_stale
        )

    def _identification_consent_is_current(self, language: Language) -> bool:
        return (
            self.identification_result is not None
            and not self.identification_result.is_unknown
            and not self.identification_result.consent_is_stale
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

    def _after_citizen_identified(self, citizen: Citizen | None) -> None:
        self.current_citizen = citizen
        consent_is_stale = (
            citizen is not None
            and citizen.consent_version != self.current_consent_version
        )
        self.identification_result = IdentificationResult(
            citizen=citizen,
            consent_is_stale=consent_is_stale,
        )
        self._record_audit(
            action="fsm.identified",
            actor_type="citizen",
            actor_id=citizen.id if citizen else None,
            details={
                "to_state": self.state,
                "is_unknown": citizen is None,
                "consent_is_stale": consent_is_stale,
            },
        )

    def _after_language_chosen_to_register(self, language: Language) -> None:
        self.session_language = language
        self._record_audit(
            action="fsm.language_chosen",
            actor_type="citizen",
            actor_id=None,
            details={
                "to_state": self.state,
                "language": language,
                "branch": "register_form",
            },
        )

    def _after_language_chosen_to_consent(self, language: Language) -> None:
        self.session_language = language
        self._record_audit(
            action="fsm.language_chosen",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={
                "to_state": self.state,
                "language": language,
                "branch": "consent",
            },
        )

    def _after_language_chosen_to_path_choice(self, language: Language) -> None:
        self.session_language = language
        self._ensure_session_row()
        self._record_audit(
            action="fsm.language_chosen",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={
                "to_state": self.state,
                "language": language,
                "branch": "path_choice",
            },
        )

    def _after_registration_complete(self) -> None:
        self._record_audit(
            action="fsm.registration_complete",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state},
        )

    def _after_consent_given(self) -> None:
        self._ensure_session_row()
        actor_id = self.current_citizen.id if self.current_citizen else None
        self._record_audit(
            action="fsm.consent_given",
            actor_type="citizen",
            actor_id=actor_id,
            details={
                "to_state": self.state,
                "consent_version": self.current_consent_version,
                "language": self.session_language,
            },
        )

    def _after_change_language(self) -> None:
        # Returning to LANGUAGE_SELECT: clear the chosen language so
        # the next language_chosen re-routes correctly. Preserve
        # identification_result + current_session so the same logical
        # flow continues in the new language.
        self.session_language = None
        self._record_audit(
            action="fsm.change_language",
            actor_type="citizen",
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={"to_state": self.state},
        )

    def _after_aborted(self) -> None:
        if self.current_session is not None:
            self.current_session.status = "aborted"
            self.current_session.updated_at = _utc_now_iso()
        self._record_audit(
            action="fsm.aborted",
            actor_type=self._abort_attribution,
            actor_id=self.current_citizen.id if self.current_citizen else None,
            details={
                "to_state": self.state,
                "abort_attribution": self._abort_attribution,
            },
        )
        self._abort_attribution = "citizen"

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
        if printed_status == "printed_ok":
            self._record_audit(
                action="receipt_printed",
                actor_type="system",
                actor_id=self.device_id,
                details={
                    "session_id": self.current_session_id,
                    "language": self.session_language,
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
        self.current_session = None
        self.current_citizen = None
        self.session_language = None
        self.identification_result = None
        self._pending_rfid_uid = None
        self._pending_path = None
        self._error_reason = None
        self._abort_attribution = "citizen"
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

        Used by the registration handler: when REGISTER_FORM completes,
        the GUI calls this with the freshly-created Citizen row, then
        fires ``registration_complete`` to enter CONSENT.
        """
        self.current_citizen = citizen
        self.identification_result = IdentificationResult(
            citizen=citizen,
            consent_is_stale=False,
        )

    def measurement_captured(self, measurement_id: str) -> None:
        """Record that a measurement was added to the current session."""
        if self.state not in (State.MEASURING_VITALS, State.MEASURING_ANTHRO):
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

    def snapshot(self) -> FsmSnapshot:
        """Capture the FSM's current public state for the GUI."""
        return FsmSnapshot(
            state=self.state,
            session_language=self.session_language,
            identification_result=self.identification_result,
            current_session_id=self.current_session_id,
            current_citizen_id=self.current_citizen.id
            if self.current_citizen is not None
            else None,
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
                "wiring bug — caller must set the citizen before this transition"
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
        self._record_audit(
            action="fsm.session_started",
            actor_type="citizen",
            actor_id=self.current_citizen.id,
            details={"to_state": self.state, "language": self.session_language},
        )

    def _finalise_session_completed(self, *, printed_status: str) -> None:
        if self.current_session is None:
            return
        now = _utc_now_iso()
        self.current_session.status = "completed"
        self.current_session.ended_at = now
        self.current_session.printed_status = printed_status
        self.current_session.updated_at = now

    def _before_cancel(self) -> None:
        self._abort_attribution = "citizen"

    def _before_timeout(self) -> None:
        self._abort_attribution = "system"

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
            object_id=self.current_session_id,
            details=details,
        )

    def _emit_state_changed(self, *args: Any, **kwargs: Any) -> None:
        # ``transitions`` invokes after_state_change with the trigger
        # arguments; we ignore them and snapshot the resulting state.
        snapshot = self.snapshot()
        # Journal trace of the FSM walk — pairs with record_audit's
        # encrypted-DB trail. Per CLAUDE.md, structured fields only,
        # no PII at INFO level: language is fine, citizen-id is a
        # UUID (fine), but the citizen's RFID UID / name never appear.
        self._fsm_logger.info(
            "fsm.state_changed",
            state=snapshot.state,
            session_language=snapshot.session_language,
            session_id=snapshot.current_session_id,
            has_citizen=snapshot.current_citizen_id is not None,
        )
        self.signals.state_changed.emit(snapshot.state, snapshot)
