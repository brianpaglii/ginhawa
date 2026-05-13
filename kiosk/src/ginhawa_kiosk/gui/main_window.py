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
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import datetime, timezone
from typing import Any

import structlog
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QVBoxLayout, QWidget
from sqlalchemy.orm import Session as SAOrmSession

from ..db.models import Citizen, Measurement
from ..fsm import (
    BpMeasurementRequestCancelled,
    BpMeasurementRequested,
    EventBus,
    FsmSnapshot,
    Language,
    LiveTemperatureUpdate,
    MeasurementProposed,
    RfidScanned,
    SessionFSM,
    SessionResetForSensors,
    State,
)
from ..sensors.base import Sensor
from ..services.audit import record_audit
from ..services.printer import PrinterService, PrintResult
from ..services.validation import validate_measurement
from .widgets import BrandedFooter, BrandedHeader
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


def _is_measurement_allowed_for_path(
    measurement_type: str, measurement_path: str | None
) -> bool:
    """Return True if a measurement type belongs to the session's path.

    The Xiaomi scale BLE scanner and the ESP32 MQTT publishes are
    always-on; they have no knowledge of the active session's
    measurement_path. A stray advert or publish that arrives during
    the wrong path would otherwise be persisted against the session
    even though the citizen never asked for that measurement (audit:
    ``docs/audits/2026-05-13-scale-prefiring-audit.md``). This helper
    is the receipt-boundary filter that drops those prefires.

    ``"full"`` admits every type; ``"vitals"`` and ``"anthropometric"``
    admit only their own set. ``None`` and any other value are
    conservative drops — measurement_path is non-None for the entire
    MEASURING_* window because the FSM stamps it on PATH_CHOICE exit
    (see ``session_fsm._after_path_selected``), so a None here means
    a measurement arrived out of flow.
    """
    if measurement_path == "full":
        return True
    if measurement_path == "vitals":
        return measurement_type in _VITALS_TYPES
    if measurement_path == "anthropometric":
        return measurement_type in _ANTHRO_TYPES
    return False


# Map a measurement type to the sensor whose `is_running` status
# decides whether real readings will arrive in this session. Used
# by the offline-placeholder seeder so the FSM doesn't hang in a
# measuring state waiting on a sensor whose transport (BLE / MQTT)
# is down.
#
# - The Omron BP cuff covers the BP triple including heart_rate
#   (the cuff's own pulse). The MAX30100 over MQTT also reports
#   heart_rate; if either sensor is online, heart_rate will arrive
#   for real. We attribute heart_rate to omron_bp here because
#   it's the BP-path primary and the spec mapping pins it to that
#   sensor — when both are offline the placeholder is correctly
#   seeded; when MQTT is offline but Omron is online, no
#   placeholder is seeded and Omron delivers the real reading.
# - SpO2 + temperature + height all flow over MQTT (MAX30100 +
#   MLX90614 + HC-SR04 on the ESP32 nodes).
# - Weight comes from the Xiaomi scale over BLE.
# BP types (systolic_bp / diastolic_bp / heart_rate) are intentionally
# OMITTED from this map. Unlike SpO2 / temperature / height — whose
# transport (MQTT) is binary up-or-down — the BP path is gated on the
# citizen's physical interaction with the cuff (place, take BP, press
# BT button) and can take an unpredictable amount of time. Seeding
# is_valid=0 placeholders for BP at MEASURING_VITALS entry would
# advance the path before the cuff has a chance to deliver, masking
# real readings behind sensor_offline rows. The Omron handler retries
# indefinitely until the citizen cancels (BpMeasurementRequestCancelled
# from the GUI), so the path completion path WILL block on BP — that's
# intentional, the user is the bound.
_TYPE_TO_SENSOR_NAME: dict[str, str] = {
    "spo2": "mqtt_sensors",
    "temperature": "mqtt_sensors",
    "height": "mqtt_sensors",
    "weight": "xiaomi_scale",
}

# Canonical unit per type, used when seeding offline placeholders.
# The validator's _EXPECTED_UNITS table is authoritative; we mirror
# the first acceptable form here to keep round-trip semantics
# sensible (a placeholder row with a recognised unit reads cleaner
# in the database than one with an "(offline)" sentinel unit, and
# stays compatible with any future "downgrade is_valid=1 → 0"
# tooling that checks units).
_TYPE_TO_UNIT: dict[str, str] = {
    "systolic_bp": "mmHg",
    "diastolic_bp": "mmHg",
    "heart_rate": "bpm",
    "spo2": "%",
    "temperature": "C",
    "height": "cm",
    "weight": "kg",
}

# Stamped on placeholders' source_device so a downstream reviewer
# can tell the row came from the FSM, not a real device.
_OFFLINE_SOURCE_DEVICE = "(offline)"
_OFFLINE_VALIDATION_NOTES = "sensor_offline"


# Per-state bilingual title rendered in the BrandedHeader. Empty
# string for IDLE because the splash already shows the wordmark.
_STATE_HEADER_TITLES: dict[str, str] = {
    State.IDLE: "",
    State.IDENTIFYING: "Identifying / Tinutukoy ka...",
    State.LANGUAGE_SELECT: "Choose Language / Pumili ng Wika",
    State.CONSENT: "Consent / Pahintulot",
    State.REGISTER_FORM: "Register / Magparehistro",
    State.PATH_CHOICE: "Choose Service / Pumili ng Serbisyo",
    State.MEASURING_VITALS: "Measuring Vitals / Sinusukat ang Vitals",
    State.MEASURING_ANTHRO: "Measuring Body / Sinusukat ang Katawan",
    State.REPORT: "Your Results / Ang Iyong Resulta",
    State.PRINTING: "Printing / Nagpi-print",
    State.END: "Thank You / Salamat",
    State.ABORTED: "Cancelled / Kanselado",
    State.ERROR: "Error / May Problema",
}


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
    * ``sensors`` — the kiosk's sensor instances keyed by name (the
      output of :func:`create_all_sensors`). Used to detect which
      sensors are offline at the start of a measuring state so the
      FSM can seed placeholder rows for the missing types instead
      of hanging forever on a dead transport. Tests can pass an
      empty mapping; offline detection then no-ops (no sensor =
      no placeholder, the existing wait-for-events path applies).
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
        sensors: Mapping[str, Sensor] | None = None,
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
        self._sensors: Mapping[str, Sensor] = sensors or {}
        self._deployment_barangay = deployment_barangay
        self._device_id = device_id

        # Track which states have already seeded their offline
        # placeholders so we don't double-seed if the user re-enters
        # a measuring state mid-session (e.g., after Cancel-back).
        self._offline_placeholders_seeded: set[str] = set()

        # Previous FSM state, tracked so we can publish
        # BpMeasurementRequestCancelled when leaving MEASURING_VITALS.
        # The Omron handler retries connect indefinitely until this
        # event arrives — without it, a citizen who walks away leaves
        # the kiosk hammering the cuff forever.
        self._previous_state: str | None = None

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

        # Wrap the stack in a vertical layout flanked by the GINHAWA
        # branded header (top) and footer (bottom). The central widget
        # is a plain QWidget container; tests reach into the
        # QStackedWidget via :pyattr:`stack`.
        self._header = BrandedHeader()
        self._footer = BrandedFooter()
        if deployment_barangay:
            self._footer.set_barangay(deployment_barangay)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header)
        outer.addWidget(self._stack, stretch=1)
        outer.addWidget(self._footer)
        self.setCentralWidget(container)

        # Per-state timer used for all auto-return / hard-timeout
        # transitions. Single instance reused — Qt reschedules
        # cleanly on .start() with a new interval.
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)

        # Track in-progress measurement counts by type so the main
        # window can fire measurement_path_complete once each path is
        # fully captured.
        self._captured_types: set[str] = set()
        # Types whose REAL (is_valid=1) reading has been persisted
        # this session. Distinct from `_captured_types`, which also
        # carries offline placeholders so the FSM can advance the
        # measurement path even when sensors are down. Used as the
        # duplicate-drop guard inside _on_measurement_proposed_event:
        # a placeholder followed by a real reading is the expected
        # upgrade, but a SECOND real reading (e.g., a stale broadcast
        # the Xiaomi scale emits 1 s after BLE adapter resume) is a
        # duplicate and gets dropped.
        self._captured_real_types: set[str] = set()

        self._wire_signals()

        # Initial state is IDLE; render its content.
        self._stack.setCurrentWidget(self._screens[State.IDLE])
        self._header.set_screen_title(_STATE_HEADER_TITLES.get(State.IDLE, ""))

    @property
    def stack(self) -> QStackedWidget:
        """The QStackedWidget hosting per-state screens.

        Exposed so tests can call ``main_window.stack.currentWidget()``;
        production code uses the FSM-driven transitions in
        :meth:`_on_fsm_state_changed`.
        """
        return self._stack

    def set_network_online(self, online: bool) -> None:
        """Forward cloud-reachability state to the branded footer.

        Wired in ``__main__.py`` as the ``SyncDaemon.on_cycle_complete``
        callback: every sync cycle that made at least one HTTP attempt
        flips the footer's network indicator. Empty cycles (nothing to
        sync) don't fire — the footer keeps its last known state.
        """
        self._footer.set_network_online(online)

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
            self._on_finish_without_printing
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
        # Temperature stream → live preview; persistence only on
        # citizen-tap (see _on_capture_temperature_requested).
        self._bus.subscribe(
            LiveTemperatureUpdate, self._on_live_temperature_update_event
        )
        self._measuring_vitals_screen.capture_temperature_requested.connect(
            self._on_capture_temperature_requested
        )

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
        self._header.set_screen_title(_STATE_HEADER_TITLES.get(state, ""))
        self._captured_types.clear()
        self._captured_real_types.clear()
        self._maybe_publish_bp_cancellation(self._previous_state, state)
        self._configure_state_specific(state, snapshot, active_language)
        self._configure_state_timeout(state)
        self._maybe_publish_session_reset(state)
        self._previous_state = state

    def _maybe_publish_bp_cancellation(
        self, prev_state: str | None, new_state: str
    ) -> None:
        """Publish BpMeasurementRequestCancelled when leaving MEASURING_VITALS.

        The Omron handler retries connect indefinitely; only this
        event tells it to stop. Fires for every exit path —
        ABORTED, LANGUAGE_SELECT, ERROR, REPORT (after the BP
        triple has already been published, in which case the cancel
        is a harmless no-op since the handler has already returned).
        """
        if prev_state != State.MEASURING_VITALS:
            return
        if new_state == State.MEASURING_VITALS:
            return
        self._publish_async(self._bus.publish(BpMeasurementRequestCancelled()))

    def _maybe_publish_session_reset(self, state: str) -> None:
        # Tell session-scoped sensors (e.g., the Xiaomi scale's
        # stability+lock gate) to release their per-session state.
        # IDLE covers normal end / aborted / error returns; the
        # following LANGUAGE_SELECT for a brand-new session also
        # publishes (idempotent — the gate's unlock is a no-op when
        # already unlocked) so the kiosk doesn't depend on which
        # state the previous session left from.
        if state not in (State.IDLE, State.LANGUAGE_SELECT):
            return
        # Per-session placeholder gate goes with the session reset —
        # the next MEASURING_* entry should re-evaluate which sensors
        # are offline, not skip seeding because the previous session
        # already did.
        self._offline_placeholders_seeded.clear()
        self._publish_async(self._bus.publish(SessionResetForSensors()))

    def _publish_async(self, coro: Coroutine[Any, Any, None]) -> None:
        """Schedule a bus.publish coroutine on the running loop.

        On the Pi, qasync runs the loop and ``asyncio.create_task``
        works. In unit tests there is no running loop at the moment
        of FSM-driven state changes — we close the coroutine
        cleanly so it doesn't dangle as an "un-awaited" warning,
        and tests that need to assert on the published event drive
        the bus directly.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — drop the coroutine cleanly. This is
            # the unit-test path; production always has qasync up.
            coro.close()
            return
        asyncio.create_task(coro)

    def _configure_state_specific(
        self, state: str, snapshot: FsmSnapshot, language: Language
    ) -> None:
        if state == State.LANGUAGE_SELECT:
            # Push the identified citizen's first name into the
            # screen's personalized greeting. Defensive: if for any
            # reason the citizen reference is missing (shouldn't
            # happen — LANGUAGE_SELECT only follows successful
            # identification or registration), the screen hides the
            # greeting label and falls back to the bilingual heading
            # alone.
            citizen = self._fsm.current_citizen
            first_name: str | None = None
            if citizen is not None and citizen.full_name:
                # "Brian Paglinawan" → "Brian". Single-word names
                # ("Madonna") survive intact because split(maxsplit=1)
                # returns the whole string when no separator exists.
                first_name = citizen.full_name.split(" ", 1)[0]
            self._language_select_screen.set_citizen_first_name(first_name)
        elif state == State.MEASURING_VITALS:
            self._seed_offline_sensor_placeholders(state)
            # Auto-fire the BP connect attempt. The OmronBpSensor's
            # 8 × 10 s retry window is sized to span the time the
            # citizen needs to position the cuff and press its BT
            # button. The Xiaomi-vs-Omron adapter contention that
            # previously demanded a user-gated trigger is now
            # serialised by BleAdapterLock — see sensors/ble_lock.py.
            _log.info("main_window.bp_request_auto_fired")
            # Stamp the session floor at request emission. The BP
            # handler uses this as a lower bound when deciding whether
            # the cuff's payload timestamp is fresh — anything older
            # than now is a stored reading from a prior session
            # (ADR-0020).
            self._publish_async(
                self._bus.publish(
                    BpMeasurementRequested(
                        session_floor=datetime.now(timezone.utc).isoformat()
                    )
                )
            )
        elif state == State.MEASURING_ANTHRO:
            self._seed_offline_sensor_placeholders(state)
            # Reset the Xiaomi scale's stability gate immediately
            # before the citizen is expected to step on. The
            # IDLE/LANGUAGE_SELECT reset above is kept for
            # defence-in-depth, but it has a known race: the scale
            # advertises every ~5 s, so unlocking the gate during
            # IDLE can let a stale broadcast slip through and re-lock
            # the gate before the next MEASURING_ANTHRO entry. Same
            # citizen back-to-back sessions then lose the second
            # weight. Resetting again here closes that window — the
            # gate publish is idempotent when already unlocked.
            self._publish_async(self._bus.publish(SessionResetForSensors()))
        elif state == State.REPORT:
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

    def _seed_offline_sensor_placeholders(self, state: str) -> None:
        """Synthesize is_valid=0 placeholder rows for offline sensors.

        Without this, the FSM would hang in MEASURING_VITALS /
        MEASURING_ANTHRO whenever a sensor's transport is down (most
        commonly: MQTT broker absent → no SpO2 / temperature / height
        ever arrives). The placeholders are real Measurement rows
        with ``is_valid=0`` and ``validation_notes="sensor_offline"``;
        they count toward _captured_types so
        ``_maybe_advance_measurement_path`` can fire path-complete
        once the available sensors finish, but the REPORT screen
        filters them out (it shows is_valid=1 only).

        Idempotent per-session: tracked via
        ``_offline_placeholders_seeded`` so a re-entry into the same
        measuring state (rare, but possible after a Cancel-back)
        doesn't double-seed.

        No-op when the sensors mapping is empty (typical of unit
        tests that don't care about the offline path) or when every
        expected sensor is reporting ``is_running``.
        """
        if state in self._offline_placeholders_seeded:
            return
        if not self._sensors:
            return
        expected = _VITALS_TYPES if state == State.MEASURING_VITALS else _ANTHRO_TYPES
        offline_types: list[str] = []
        for measurement_type in sorted(expected):
            sensor_name = _TYPE_TO_SENSOR_NAME.get(measurement_type)
            if sensor_name is None:
                continue
            sensor = self._sensors.get(sensor_name)
            if sensor is None:
                continue
            if sensor.is_running:
                continue
            offline_types.append(measurement_type)

        if not offline_types:
            self._offline_placeholders_seeded.add(state)
            return

        _log.info(
            "main_window.offline_sensors_detected",
            state=state,
            offline_types=offline_types,
        )
        for measurement_type in offline_types:
            unit = _TYPE_TO_UNIT.get(measurement_type, "")
            _log.info(
                "main_window.offline_placeholder_created",
                type=measurement_type,
            )
            self._publish_async(
                self._bus.publish(
                    MeasurementProposed(
                        measurement_type=measurement_type,
                        value=0.0,
                        unit=unit,
                        source_device=_OFFLINE_SOURCE_DEVICE,
                        claimed_is_valid=False,
                        validation_notes=_OFFLINE_VALIDATION_NOTES,
                    )
                )
            )
        self._offline_placeholders_seeded.add(state)

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
            # Route through the user-action handler so the same
            # commit + rollback semantics apply whether the citizen
            # walked away or tapped the button explicitly.
            self._on_finish_without_printing()
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
        try:
            self._fsm.path_selected(path)  # type: ignore[arg-type]
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            _log.warning(
                "main_window.path_selected_failed",
                error=type(exc).__name__,
            )
            raise

    def _on_consent_given(self) -> None:
        _log.info("main_window.consent_given")
        self._fsm.consent_given()
        self._db.commit()

    def _on_print_requested(self) -> None:
        # Only fire the FSM transition; the actual print kicks off
        # in _kick_off_print_job once the FSM lands on PRINTING. The
        # FSM's after-callbacks (and the audit row they emit via
        # services.audit) are flush-only — caller MUST commit for
        # the row to reach disk.
        _log.info("main_window.print_requested")
        try:
            self._fsm.print_requested()
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            _log.warning(
                "main_window.print_requested_failed",
                error=type(exc).__name__,
            )
            raise

    def _on_finish_without_printing(self) -> None:
        # The FSM's _after_finish_without_printing callback marks the
        # session row 'completed' and stamps ended_at; without an
        # explicit commit here those mutations stay in the SQLAlchemy
        # session and never reach the encrypted DB. The 2026-05-07
        # bench surfaced this as 20 in_progress rows that never
        # progressed despite reaching the END state on screen.
        _log.info("main_window.finish_without_printing")
        try:
            self._fsm.finish_without_printing()
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            _log.warning(
                "main_window.finish_without_printing_failed",
                error=type(exc).__name__,
            )
            raise

    def _on_cancel(self) -> None:
        # Cancel from any cancellable state → ABORTED.
        _log.info("main_window.cancel_requested", from_state=self._fsm.state)
        try:
            self._fsm.cancel()
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            _log.warning("main_window.cancel_invalid", error=type(exc).__name__)

    def _on_change_language(self) -> None:
        _log.info("main_window.change_language_requested", from_state=self._fsm.state)
        try:
            self._fsm.change_language()
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
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

    async def _on_live_temperature_update_event(
        self, event: LiveTemperatureUpdate
    ) -> None:
        """Forward MLX90640 live updates to the MEASURING_VITALS screen.

        Gated on FSM state so a publish that arrives while the citizen
        is still on IDLE / LANGUAGE_SELECT / PATH_CHOICE doesn't
        flicker into a hidden screen's labels. Outside MEASURING_VITALS
        the update is dropped silently — the ESP32 publishes
        continuously and we don't want to budget the kiosk's UI to
        process bursts of stale data.
        """
        if self._fsm.state != State.MEASURING_VITALS:
            return
        self._measuring_vitals_screen.set_live_temperature(event.value, event.unit)

    def _on_capture_temperature_requested(self, value: float) -> None:
        """Citizen tapped the Capture Temperature button.

        Publish a :class:`MeasurementProposed` for the FSM persistence
        path with the kiosk's standard temperature fields (unit "C",
        source_device "esp32_a_mlx90640"). The existing
        ``_on_measurement_proposed_event`` handler validates, persists,
        and updates the captured list — same path every other
        measurement type takes.
        """
        self._publish_async(
            self._bus.publish(
                MeasurementProposed(
                    measurement_type="temperature",
                    value=value,
                    unit="C",
                    source_device="esp32_a_mlx90640",
                    claimed_is_valid=True,
                )
            )
        )

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

        # The producer can short-circuit the unit/range validator by
        # claiming is_valid=False AND attaching its own
        # validation_notes — this is the channel the FSM uses to
        # seed offline placeholders ("sensor_offline") without the
        # validator clobbering the reason with a "unit must be …"
        # message. For everything else, we run the validator as
        # before; the kiosk doesn't trust a sensor adapter's
        # claimed_is_valid=True over the validator's verdict.
        if event.claimed_is_valid is False and event.validation_notes is not None:
            is_valid_int = 0
            validation_notes: str | None = event.validation_notes
        else:
            result = validate_measurement(
                event.measurement_type, event.value, event.unit
            )
            is_valid_int = 1 if result.is_valid else 0
            validation_notes = result.validation_notes

        # The session row is created on entry to PATH_CHOICE (or after
        # consent_given for new citizens); if it isn't there yet, we
        # got a measurement out-of-flow — log and drop.
        if self._fsm.current_session is None:
            _log.warning(
                "main_window.measurement_without_session",
                type=event.measurement_type,
            )
            return

        # Path filter: a REAL measurement (is_valid=1) whose type
        # doesn't belong to the active session's measurement_path is
        # a prefire from an always-listening sensor — the Xiaomi
        # scale's BLE scanner or one of the ESP32 MQTT publishers.
        # Drop it before persistence so the session's measurement set
        # never accumulates rows the citizen didn't ask for. Offline
        # placeholders and out-of-range invalid readings (both
        # ``is_valid=0``) are exempt: placeholders are seeded by the
        # FSM itself with full state knowledge, and invalid reals are
        # kept for diagnostic review. Audit:
        # ``docs/audits/2026-05-13-scale-prefiring-audit.md``.
        if is_valid_int == 1 and not _is_measurement_allowed_for_path(
            event.measurement_type,
            self._fsm.current_session.measurement_path,
        ):
            _log.warning(
                "main_window.measurement_path_mismatch_dropped",
                measurement_type=event.measurement_type,
                measurement_path=self._fsm.current_session.measurement_path,
                source_device=event.source_device,
                session_id=self._fsm.current_session.id,
            )
            return

        # Drop a duplicate REAL reading. Each measurement type is
        # recorded at most once per session — second weights, second
        # BP triples, etc. should not silently produce extra rows.
        # Placeholders (is_valid=0 with validation_notes="sensor_offline")
        # don't activate this guard: a real reading after a placeholder
        # is the expected upgrade path. The Xiaomi scale's gate +
        # warmup window already tries to suppress duplicates at the
        # sensor level; this is the belt-and-braces backstop for
        # sensors / cached advertisements that slip past.
        if is_valid_int == 1 and event.measurement_type in self._captured_real_types:
            _log.warning(
                "main_window.duplicate_measurement_dropped",
                type=event.measurement_type,
                unit=event.unit,
                source_device=event.source_device,
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
            is_valid=is_valid_int,
            validation_notes=validation_notes,
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
        # Track captured types REGARDLESS of validity. This lets
        # offline placeholders (is_valid=0, validation_notes=
        # "sensor_offline") count toward path completion so the FSM
        # can advance when only the available sensors finish — the
        # alternative is a hang in MEASURING_VITALS / MEASURING_ANTHRO
        # whenever a transport is down (2026-05-07 bench: 20 sessions,
        # 0 completed). The REPORT screen filters to is_valid=1, so
        # placeholders never reach the citizen.
        self._captured_types.add(meas.type)
        if bool(meas.is_valid):
            self._captured_real_types.add(meas.type)
        self._maybe_advance_measurement_path()

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
                measurement_type=m.type,
            )
            for m in rows_db
        ]
        # ``measurement_path`` is set by the FSM when the citizen picks
        # a path. The report screen filters rows so anthro-only and
        # vitals-only sessions only show their chosen measurement set —
        # offline placeholders and stray pre-fires for the other path
        # stay in the DB (for audit) but don't surface here.
        measurement_path = (
            self._fsm.current_session.measurement_path
            if self._fsm.current_session is not None
            else None
        )
        self._report_screen.set_measurements(rows, measurement_path)
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
