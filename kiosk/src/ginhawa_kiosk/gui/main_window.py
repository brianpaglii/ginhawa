"""Kiosk main window.

Owns a :class:`QStackedWidget` whose pages map 1:1 to the FSM's
states. Listens to ``fsm.signals.state_changed`` and switches the
visible page on every transition. Listens to user-action signals
emitted by the screens and forwards them as FSM triggers (e.g.,
``LanguageSelectScreen.language_chosen`` → ``fsm.language_chosen``).

The main window also owns:

* The FSM-driven QTimer for state-bound timeouts (CONSENT 60 s,
  REPORT 60 s, END 5 s, ABORTED 3 s, ERROR 10 s, IDENTIFYING 5 s).
* The bus subscriptions for sensor events that drive the FSM
  (``RfidScanned``, ``MeasurementProposed``, ``CitizenIdentified``).
* The printer / citizen lookup hooks injected at construction —
  injected so tests can pass mocks without booting real hardware.

Async work (printer.print_session_report, sensor reads, DB lookups)
runs as :func:`qasync.asyncio.create_task` tasks. ``__main__.py``
sets up the qasync event loop; this module assumes the loop is
running.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import structlog
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMainWindow, QStackedWidget
from sqlalchemy.orm import Session as SAOrmSession

from ..db.models import Citizen, Measurement
from ..fsm import (
    BpMeasurementRequested,
    EventBus,
    FsmSnapshot,
    Language,
    MeasurementProposed,
    RfidScanned,
    SessionFSM,
    State,
)
from ..services.audit import record_audit
from ..services.printer import PrinterService, PrintResult
from ..services.validation import validate_measurement
from .screens import (
    AbortedScreen,
    BaseScreen,
    ConsentScreen,
    EndScreen,
    ErrorScreen,
    IdentifyingScreen,
    IdleScreen,
    LanguageSelectScreen,
    MeasuringAnthroScreen,
    MeasuringVitalsScreen,
    PathChoiceScreen,
    PrintingScreen,
    RegisterFormScreen,
    RegistrationData,
    ReportRow,
    ReportScreen,
)

_log = structlog.get_logger(__name__)


# Per-state auto-return timeouts in milliseconds. Pulled out as
# constants so tests can monkey-patch them to zero for fast E2E.
TIMEOUT_IDENTIFYING_MS = 5_000
TIMEOUT_CONSENT_MS = 60_000
TIMEOUT_REPORT_MS = 60_000
TIMEOUT_END_MS = 5_000
TIMEOUT_ABORTED_MS = 3_000
TIMEOUT_ERROR_MS = 10_000

# How long to leave the "Connect to cuff" button disabled after a
# tap. Sized to the OmronBpSensor's worst-case window: 5 connect
# retries at 2 s each (10 s) plus the 120 s notify-wait timeout,
# rounded up to 135 s. After this, the citizen can re-tap to retry
# without restarting the session — handy when they pressed the BT
# button at the wrong time and the cuff dropped out of pairing
# mode.
BP_CONNECT_REENABLE_MS = 135_000


# Localised measurement labels used on REPORT and the live capture
# lists during MEASURING_*. Maps schema type → per-language label.
_MEASUREMENT_LABELS: dict[Language, dict[str, str]] = {
    "en": {
        "systolic_bp": "Systolic BP",
        "diastolic_bp": "Diastolic BP",
        "heart_rate": "Heart rate",
        "spo2": "SpO2",
        "temperature": "Temperature",
        "height": "Height",
        "weight": "Weight",
        "bmi": "BMI",
    },
    "tl": {
        "systolic_bp": "Sistoliko",
        "diastolic_bp": "Diastoliko",
        "heart_rate": "Tibok ng puso",
        "spo2": "SpO2",
        "temperature": "Temperatura",
        "height": "Taas",
        "weight": "Timbang",
        "bmi": "BMI",
    },
}

# Path → measurement types expected before measurement_path_complete
# can fire. Used to decide when the "vitals done" / "anthro done"
# transitions should be triggered automatically as readings arrive.
_VITALS_TYPES = {"systolic_bp", "diastolic_bp", "heart_rate", "spo2", "temperature"}
_ANTHRO_TYPES = {"height", "weight"}


CitizenLookup = Callable[[str], Awaitable[Citizen | None]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_uid(uid: str) -> str:
    """8-char SHA-256 prefix of an RFID UID, for journal correlation only.

    CLAUDE.md "Never log sensitive personal information at INFO level
    or higher": the raw UID stays out of stdout / journalctl. The
    audit_log table holds the unhashed UID under SQLCipher for the
    forensic record; this short hash lets a developer correlate two
    taps of the same card within a debug window without reversing
    the citizen identity.
    """
    import hashlib

    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8]


class KioskMainWindow(QMainWindow):
    """The kiosk's single top-level window.

    Construction parameters:

    * ``fsm`` — the SessionFSM driving state. Already constructed by
      ``__main__`` with a DB session attached.
    * ``bus`` — the event bus the sensor coordinator publishes to.
    * ``db_session`` — same DB session the FSM holds; used by the
      main window for citizen / measurement persistence.
    * ``printer`` — :class:`PrinterService` (mock or real per
      ``Settings.MOCK_HARDWARE``).
    * ``citizen_lookup`` — async callable; given an RFID UID, returns
      the Citizen row or None. Injected so tests can stub it.
    * ``deployment_barangay`` — pre-fill for the registration form's
      barangay field, sourced from device_config.
    * ``device_id`` — the kiosk's UUID; used for audit attribution
      on system-driven actions.
    """

    def __init__(
        self,
        *,
        fsm: SessionFSM,
        bus: EventBus,
        db_session: SAOrmSession,
        printer: PrinterService,
        citizen_lookup: CitizenLookup,
        deployment_barangay: str = "",
        device_id: str = "",
    ) -> None:
        super().__init__()
        self.setObjectName("kiosk_main_window")
        self.setWindowTitle("GINHAWA Kiosk")

        self._fsm = fsm
        self._bus = bus
        self._db = db_session
        self._printer = printer
        self._citizen_lookup = citizen_lookup
        self._deployment_barangay = deployment_barangay
        self._device_id = device_id

        # Build screens
        self._idle_screen = IdleScreen()
        self._identifying_screen = IdentifyingScreen()
        self._language_select_screen = LanguageSelectScreen()
        self._register_form_screen = RegisterFormScreen(
            default_barangay=deployment_barangay
        )
        self._consent_screen = ConsentScreen()
        self._path_choice_screen = PathChoiceScreen()
        self._measuring_vitals_screen = MeasuringVitalsScreen()
        self._measuring_anthro_screen = MeasuringAnthroScreen()
        self._report_screen = ReportScreen()
        self._printing_screen = PrintingScreen()
        self._end_screen = EndScreen()
        self._aborted_screen = AbortedScreen()
        self._error_screen = ErrorScreen()

        # State → screen
        self._screens: dict[str, Any] = {
            State.IDLE: self._idle_screen,
            State.IDENTIFYING: self._identifying_screen,
            State.LANGUAGE_SELECT: self._language_select_screen,
            State.REGISTER_FORM: self._register_form_screen,
            State.CONSENT: self._consent_screen,
            State.PATH_CHOICE: self._path_choice_screen,
            State.MEASURING_VITALS: self._measuring_vitals_screen,
            State.MEASURING_ANTHRO: self._measuring_anthro_screen,
            State.REPORT: self._report_screen,
            State.PRINTING: self._printing_screen,
            State.END: self._end_screen,
            State.ABORTED: self._aborted_screen,
            State.ERROR: self._error_screen,
        }

        self._stack = QStackedWidget()
        for screen in self._screens.values():
            self._stack.addWidget(screen)
        self.setCentralWidget(self._stack)

        # Per-state timer used for all auto-return / hard-timeout
        # transitions. Single instance reused — Qt reschedules
        # cleanly on .start() with a new interval.
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)

        # Track in-progress measurement counts by type so the main
        # window can fire measurement_path_complete once each path is
        # fully captured.
        self._captured_types: set[str] = set()

        self._wire_signals()

        # Initial state is IDLE; render its content.
        self._stack.setCurrentWidget(self._screens[State.IDLE])

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        # FSM → main window: state_changed is the master signal.
        self._fsm.signals.state_changed.connect(self._on_fsm_state_changed)

        # User-action signals: every screen's emit → FSM trigger.
        self._language_select_screen.language_chosen.connect(self._on_language_chosen)
        self._register_form_screen.submitted.connect(self._on_registration_submitted)
        self._consent_screen.consent_given.connect(self._on_consent_given)
        self._consent_screen.consent_refused.connect(self._fsm.consent_refused)
        self._path_choice_screen.path_selected.connect(self._on_path_selected)
        self._report_screen.print_requested.connect(self._on_print_requested)
        self._report_screen.finish_without_printing_requested.connect(
            self._fsm.finish_without_printing
        )
        # User-gated BP cuff connect — fires BpMeasurementRequested ONLY
        # when the citizen has the cuff in pairing mode and taps the
        # button. See sensors/omron_bp.py for why auto-firing on state
        # entry produces InProgress errors on real hardware.
        self._measuring_vitals_screen.connect_to_cuff_requested.connect(
            self._on_bp_connect_requested
        )

        # Cancel + Change-language are uniform across BaseScreen children.
        for screen in self._screens.values():
            if isinstance(screen, BaseScreen):
                screen.cancel_requested.connect(self._on_cancel)
                screen.change_language_requested.connect(self._on_change_language)

        # Auto-return / hard-timeout timer.
        self._auto_timer.timeout.connect(self._on_auto_timer_fired)

        # Bus → main window. Sensor events arrive here and are
        # translated into FSM triggers / DB writes.
        self._bus.subscribe(RfidScanned, self._on_rfid_scanned_event)
        self._bus.subscribe(MeasurementProposed, self._on_measurement_proposed_event)

    # ------------------------------------------------------------------
    # FSM-driven view switching
    # ------------------------------------------------------------------

    def _on_fsm_state_changed(self, state: str, snapshot: FsmSnapshot) -> None:
        screen = self._screens.get(state)
        if screen is None:
            _log.error("main_window.unknown_state", state=state)
            return

        # Re-enter hook — every BaseScreen refreshes language-bound
        # labels here. Choose a sensible language even when the
        # session hasn't picked one yet (IDLE / IDENTIFYING /
        # terminals): default to English.
        active_language: Language = snapshot.session_language or "en"
        if hasattr(screen, "on_enter"):
            screen.on_enter(active_language)

        self._stack.setCurrentWidget(screen)
        self._captured_types.clear()
        self._configure_state_specific(state, snapshot, active_language)
        self._configure_state_timeout(state)

    def _configure_state_specific(
        self, state: str, snapshot: FsmSnapshot, language: Language
    ) -> None:
        if state == State.REPORT:
            self._render_report(snapshot, language)
        elif state == State.PRINTING:
            self._kick_off_print_job(snapshot, language)
        elif state == State.END:
            self._end_screen.set_countdown(
                seconds=TIMEOUT_END_MS // 1000,
                language=snapshot.session_language,
            )
        elif state == State.ERROR:
            self._error_screen.set_diagnostic(getattr(self._fsm, "_error_reason", None))

    def _configure_state_timeout(self, state: str) -> None:
        ms_by_state: dict[str, int] = {
            State.IDENTIFYING: TIMEOUT_IDENTIFYING_MS,
            State.CONSENT: TIMEOUT_CONSENT_MS,
            State.REPORT: TIMEOUT_REPORT_MS,
            State.END: TIMEOUT_END_MS,
            State.ABORTED: TIMEOUT_ABORTED_MS,
            State.ERROR: TIMEOUT_ERROR_MS,
        }
        self._auto_timer.stop()
        ms = ms_by_state.get(state)
        if ms is not None:
            self._auto_timer.start(ms)

    def _on_auto_timer_fired(self) -> None:
        # The fired-state's auto-action depends on which state we're
        # currently in. IDENTIFYING → identification_failed; CONSENT →
        # timeout (citizen walked away); END/ABORTED/ERROR →
        # acknowledge → IDLE; REPORT → finish_without_printing.
        state = self._fsm.state
        if state == State.IDENTIFYING:
            self._fsm.identification_failed("identifying_timeout")
        elif state == State.CONSENT:
            self._fsm.timeout()
        elif state == State.REPORT:
            self._fsm.finish_without_printing()
        elif state in (State.END, State.ABORTED, State.ERROR):
            self._fsm.acknowledge()

    # ------------------------------------------------------------------
    # User-action handlers
    # ------------------------------------------------------------------

    def _on_language_chosen(self, language: str) -> None:
        # Qt's signal carries a generic str; the FSM's typing accepts
        # the Literal narrowed-back via runtime check.
        if language not in ("en", "tl"):
            _log.error("main_window.invalid_language", language=language)
            return
        _log.info("main_window.language_chosen", language=language)
        self._fsm.language_chosen(language)  # type: ignore[arg-type]

    def _on_registration_submitted(self, data: object) -> None:
        if not isinstance(data, RegistrationData):
            _log.error("main_window.bad_registration_payload", data=type(data).__name__)
            return
        _log.info(
            "main_window.registration_submitted",
            sex=data.sex,
            has_phone=bool(data.phone),
        )
        # Insert the Citizen row attributed to a self-service
        # registration (registered_by=None). The FSM owns the audit
        # row for the registration_complete transition; the main
        # window owns the citizen.create audit.
        now = _utc_now_iso()
        new_id = str(uuid.uuid4())
        citizen = Citizen(
            id=new_id,
            rfid_uid=getattr(self._fsm, "_pending_rfid_uid", "") or "",
            full_name=data.full_name,
            dob=data.dob_iso,
            sex=data.sex,
            barangay=data.barangay,
            phone=data.phone,
            consent_version=self._fsm.current_consent_version,
            consent_given_at=now,
            registered_at=now,
            registered_by=None,
            is_active=1,
            synced=0,
            updated_at=now,
        )
        self._db.add(citizen)
        record_audit(
            self._db,
            actor_type="citizen",
            actor_id=new_id,
            action="citizen.create",
            object_type="citizen",
            object_id=new_id,
            details={"self_service": True, "language": self._fsm.session_language},
        )
        self._db.flush()
        self._fsm.set_current_citizen(citizen)
        self._fsm.registration_complete()
        self._db.commit()

    def _on_path_selected(self, path: str) -> None:
        _log.info("main_window.path_selected", path=path)
        self._fsm.path_selected(path)  # type: ignore[arg-type]

    def _on_consent_given(self) -> None:
        _log.info("main_window.consent_given")
        self._fsm.consent_given()
        self._db.commit()

    def _on_print_requested(self) -> None:
        # Only fire the FSM transition; the actual print kicks off
        # in _kick_off_print_job once the FSM lands on PRINTING.
        _log.info("main_window.print_requested")
        self._fsm.print_requested()

    def _on_bp_connect_requested(self) -> None:
        """Citizen pressed "Connect to cuff" on MeasuringVitalsScreen.

        Publishes ``BpMeasurementRequested`` for the OmronBpSensor to
        pick up. The screen has already disabled its own button (see
        :meth:`MeasuringVitalsScreen._on_connect_clicked`); we schedule
        a re-enable timer scoped to the sensor's worst-case timeout
        (5 connect retries × 2 s + 120 s notify-wait) so the citizen
        can retry if the connection silently fails.
        """
        _log.info("main_window.bp_connect_requested")
        asyncio.create_task(self._bus.publish(BpMeasurementRequested()))
        # Re-enable the button after the sensor's worst-case window so
        # a failed connect doesn't leave the citizen stuck.
        QTimer.singleShot(
            BP_CONNECT_REENABLE_MS,
            lambda: self._measuring_vitals_screen.set_connecting(False),
        )

    def _on_cancel(self) -> None:
        # Cancel from any cancellable state → ABORTED.
        _log.info("main_window.cancel_requested", from_state=self._fsm.state)
        try:
            self._fsm.cancel()
        except Exception as exc:  # pragma: no cover - defensive guard
            _log.warning("main_window.cancel_invalid", error=type(exc).__name__)

    def _on_change_language(self) -> None:
        _log.info("main_window.change_language_requested", from_state=self._fsm.state)
        try:
            self._fsm.change_language()
        except Exception as exc:  # pragma: no cover - defensive guard
            _log.warning(
                "main_window.change_language_invalid", error=type(exc).__name__
            )

    # ------------------------------------------------------------------
    # Bus event handlers
    # ------------------------------------------------------------------

    async def _on_rfid_scanned_event(self, event: RfidScanned) -> None:
        # 1) Tell the FSM the citizen tapped (IDLE → IDENTIFYING)
        # Per CLAUDE.md "Never log sensitive personal information at
        # INFO level": the raw RFID UID stays out of the journal. The
        # audit_log table records the UID under SQLCipher; here we
        # only note that a tap occurred and a hashed handle for cross-
        # correlating taps within a debug session.
        _log.info("main_window.rfid_scanned", uid_short=_hash_uid(event.uid))
        self._fsm.rfid_scanned(event.uid)
        # 2) Run the citizen lookup (async, may hit the DB)
        try:
            citizen = await self._citizen_lookup(event.uid)
        except Exception as exc:
            _log.warning("main_window.citizen_lookup_failed", error=type(exc).__name__)
            self._fsm.identification_failed(f"lookup_error:{type(exc).__name__}")
            return
        # 3) Audit the read attempt explicitly. Mirrors the production
        # citizen-lookup service's behaviour from the p1-5 prompt.
        record_audit(
            self._db,
            actor_type="kiosk",
            actor_id=self._device_id,
            action="citizen.read",
            object_type="citizen",
            object_id=citizen.id if citizen else None,
            details={"rfid_uid": event.uid, "found": citizen is not None},
        )
        self._db.flush()
        _log.info("main_window.citizen_identified", citizen_found=citizen is not None)
        self._fsm.citizen_identified(citizen)

    async def _on_measurement_proposed_event(self, event: MeasurementProposed) -> None:
        # Validate, persist, and update the live screen.
        # Type + unit are not PII; the value is, so it stays out of
        # the journal at INFO level (it lands in the encrypted
        # measurements table via the persist below).
        _log.info(
            "main_window.measurement_proposed",
            type=event.measurement_type,
            unit=event.unit,
            source_device=event.source_device,
        )
        result = validate_measurement(event.measurement_type, event.value, event.unit)

        # The session row is created on entry to PATH_CHOICE (or after
        # consent_given for new citizens); if it isn't there yet, we
        # got a measurement out-of-flow — log and drop.
        if self._fsm.current_session is None:
            _log.warning(
                "main_window.measurement_without_session",
                type=event.measurement_type,
            )
            return

        now = _utc_now_iso()
        meas = Measurement(
            id=str(uuid.uuid4()),
            session_id=self._fsm.current_session.id,
            type=event.measurement_type,
            value=event.value,
            unit=event.unit,
            source_device=event.source_device,
            measured_at=now,
            is_valid=1 if result.is_valid else 0,
            validation_notes=result.validation_notes,
            raw_json=None,
            synced=0,
            updated_at=now,
        )
        self._db.add(meas)
        self._db.flush()
        self._fsm.measurement_captured(meas.id)
        self._db.commit()

        # Live screen update + measurement_path_complete trigger.
        self._on_measurement_persisted(meas)

    def _on_measurement_persisted(self, meas: Measurement) -> None:
        if not bool(meas.is_valid):
            return
        language: Language = self._fsm.session_language or "en"
        labels = _MEASUREMENT_LABELS[language]
        label = labels.get(meas.type, meas.type)
        rendered = self._format_value(meas.type, float(meas.value), meas.unit)

        if self._fsm.state == State.MEASURING_VITALS:
            self._measuring_vitals_screen.apply_measurement(label, rendered)
        elif self._fsm.state == State.MEASURING_ANTHRO:
            self._measuring_anthro_screen.apply_measurement(label, rendered)

        self._captured_types.add(meas.type)
        self._maybe_advance_measurement_path()

    def _maybe_advance_measurement_path(self) -> None:
        # If every measurement type expected for the current state has
        # been captured at least once, fire measurement_path_complete.
        # Real flows allow re-takes; this triggers the first time the
        # set is fully covered.
        state = self._fsm.state
        if state == State.MEASURING_VITALS:
            if _VITALS_TYPES.issubset(self._captured_types):
                self._fsm.measurement_path_complete()
        elif state == State.MEASURING_ANTHRO:
            if _ANTHRO_TYPES.issubset(self._captured_types):
                self._fsm.measurement_path_complete()

    # ------------------------------------------------------------------
    # Report rendering
    # ------------------------------------------------------------------

    def _render_report(self, snapshot: FsmSnapshot, language: Language) -> None:
        if snapshot.current_session_id is None:
            self._report_screen.set_measurements([])
            self._report_screen.set_printer_state(
                available=self._printer.is_available(),
                paper_present=self._printer.is_paper_present(),
            )
            return

        from sqlalchemy import select

        rows_db = (
            self._db.execute(
                select(Measurement).where(
                    Measurement.session_id == snapshot.current_session_id,
                    Measurement.is_valid == 1,
                )
            )
            .scalars()
            .all()
        )
        labels = _MEASUREMENT_LABELS[language]
        rows = [
            ReportRow(
                label=labels.get(m.type, m.type),
                value=self._format_value(m.type, float(m.value), m.unit),
            )
            for m in rows_db
        ]
        self._report_screen.set_measurements(rows)
        self._report_screen.set_printer_state(
            available=self._printer.is_available(),
            paper_present=self._printer.is_paper_present(),
        )

    @staticmethod
    def _format_value(measurement_type: str, value: float, unit: str) -> str:
        if measurement_type in ("systolic_bp", "diastolic_bp", "heart_rate", "spo2"):
            return f"{int(round(value))} {unit}".rstrip()
        return f"{value:.1f} {unit}".rstrip()

    # ------------------------------------------------------------------
    # Print job
    # ------------------------------------------------------------------

    def _kick_off_print_job(self, snapshot: FsmSnapshot, language: Language) -> None:
        if snapshot.current_session_id is None or self._fsm.current_citizen is None:
            self._fsm.print_complete(success=False, printed_status="print_failed")
            return

        from sqlalchemy import select

        session_row = self._fsm.current_session
        citizen = self._fsm.current_citizen
        rows_db = list(
            self._db.execute(
                select(Measurement).where(
                    Measurement.session_id == snapshot.current_session_id,
                )
            ).scalars()
        )

        async def runner() -> None:
            assert session_row is not None
            assert citizen is not None
            try:
                result: PrintResult = await self._printer.print_session_report(
                    session_row, citizen, rows_db, language=language
                )
            except Exception as exc:
                _log.warning("main_window.print_exception", error=type(exc).__name__)
                self._fsm.print_complete(success=False, printed_status="print_failed")
                return
            self._fsm.print_complete(
                success=result.success, printed_status=str(result.printed_status)
            )

        # Schedule the print on the integrated qasync loop.
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:  # pragma: no cover - never hit when qasync is up
            asyncio.run(runner())
            return
        loop.create_task(runner())
