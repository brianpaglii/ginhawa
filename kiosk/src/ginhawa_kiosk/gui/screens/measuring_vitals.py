"""MEASURING_VITALS: BP (Omron) + SpO2/HR (MAX30100 via MQTT).

Shows step-by-step instructions for the citizen. The BP cuff
sub-flow is **auto-fired**: ``BpMeasurementRequested`` is published
by the main window the moment the FSM enters MEASURING_VITALS.
The OmronBpSensor's retry-with-backoff loop (8 attempts × 10 s ≈
80 s window) absorbs the time the citizen needs to position the
cuff and press its BT button. Earlier code gated this behind a
"Connect to cuff" button to avoid a BlueZ ``InProgress`` error
caused by the Xiaomi scanner colliding with the Omron's directed
connect on the same hci0 adapter; the
:class:`~ginhawa_kiosk.sensors.ble_lock.BleAdapterLock` now
serialises that contention, so the button is no longer needed.

The pulse-oximeter sub-flow is passive — the MAX30100 streams its
readings via MQTT as soon as a finger is detected; no kiosk-side
trigger needed.

The temperature sub-flow is GATED. The MLX90640 streams continuously
regardless of whether the citizen has positioned the sensor on their
forehead — so we display each MQTT publish as a live "Current: X"
preview and only persist when the citizen taps the Capture button.
That tap emits :attr:`capture_temperature_requested` carrying the
last-seen live value; main_window forwards that to the FSM via a
``MeasurementProposed`` event. Recapture is allowed until the
screen exits.

The screen does NOT subscribe to the bus directly to keep the
sensor-coordinator boundary clean and testable. The main window
calls :meth:`update_status` and :meth:`set_live_temperature` to
push sensor state in.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..strings import Language, get_strings
from .base import BaseScreen


class MeasuringVitalsScreen(BaseScreen):
    # Citizen tapped "Capture Temperature" — payload is the live
    # value (°C) at the moment of the tap. main_window connects
    # this to a ``MeasurementProposed`` publish.
    capture_temperature_requested = pyqtSignal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("measuring_vitals_screen")

        self._title = QLabel()
        self._title.setObjectName("measuring_vitals_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._bp_instruction = QLabel()
        self._bp_instruction.setObjectName("measuring_vitals_bp_instruction")
        self._bp_instruction.setWordWrap(True)

        self._pulse_instruction = QLabel()
        self._pulse_instruction.setObjectName("measuring_vitals_pulse_instruction")
        self._pulse_instruction.setWordWrap(True)

        # Status line driven by main_window.update_status. We
        # initialise to the "waiting" copy — the FSM auto-fires
        # BpMeasurementRequested on entry, so by the time this
        # screen first paints, the sensor is already retrying.
        self._status = QLabel()
        self._status.setObjectName("measuring_vitals_status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Temperature live-preview + capture button. State machine:
        # - _live_temperature_value: last seen live value (always
        #   tracked, even while display is frozen post-capture, so
        #   a recapture tap immediately picks up the latest reading).
        # - _captured_temperature: set when a capture has fired;
        #   suppresses display updates from set_live_temperature.
        self._live_temperature_value: float | None = None
        self._captured_temperature: float | None = None

        self._temp_instruction = QLabel()
        self._temp_instruction.setObjectName("measuring_vitals_temperature_instruction")
        self._temp_instruction.setWordWrap(True)

        self._temp_live_label = QLabel()
        self._temp_live_label.setObjectName("measurementValue")
        self._temp_live_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._temp_capture_button = QPushButton()
        self._temp_capture_button.setObjectName("primaryButton")
        self._temp_capture_button.clicked.connect(self._on_capture_clicked)
        # Disabled until the first live update arrives — nothing to
        # capture yet, and a stale "—" capture would surface as a
        # 0.0 °C MeasurementProposed at the FSM.
        self._temp_capture_button.setEnabled(False)

        self._captured_list = QListWidget()
        self._captured_list.setObjectName("measuring_vitals_captured_list")

        self._capturing_label = QLabel()
        self._capturing_label.setObjectName("measuring_vitals_capturing")
        self._capturing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        instructions_row = QHBoxLayout()
        instructions_row.addWidget(self._bp_instruction)
        instructions_row.addSpacing(20)
        instructions_row.addWidget(self._pulse_instruction)

        # Temperature section — instruction + live value + capture
        # button right-aligned so it reads as a deliberate action.
        temperature_section = QVBoxLayout()
        temperature_section.setSpacing(12)
        temperature_section.addWidget(self._temp_instruction)
        temperature_section.addWidget(self._temp_live_label)
        capture_row = QHBoxLayout()
        capture_row.addStretch(1)
        capture_row.addWidget(self._temp_capture_button)
        temperature_section.addLayout(capture_row)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(20)
        layout.addLayout(instructions_row)
        layout.addSpacing(12)
        layout.addWidget(self._status)
        layout.addSpacing(12)
        layout.addLayout(temperature_section)
        layout.addSpacing(12)
        layout.addWidget(self._captured_list, stretch=1)
        layout.addWidget(self._capturing_label)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.measuring_vitals_title)
        self._bp_instruction.setText(s.measuring_vitals_bp_instruction)
        self._pulse_instruction.setText(s.measuring_vitals_pulse_instruction)
        self._capturing_label.setText(s.measuring_vitals_capturing)
        self._temp_instruction.setText(s.measuring_vitals_temperature_instruction)
        self._temp_capture_button.setText(s.measuring_vitals_temperature_capture_button)
        # Reset the captured list and seed the status line every time
        # the screen mounts (Change-language re-entry, new session).
        self._captured_list.clear()
        self._status.setText(s.measuring_vitals_status_waiting)
        # Reset the temperature sub-flow — new session, no prior live
        # readings, no capture. Clear inline success styling that a
        # prior capture would have left behind.
        self._live_temperature_value = None
        self._captured_temperature = None
        self._temp_live_label.setText(
            f"{s.measuring_vitals_temperature_current_prefix}: —"
        )
        self._temp_live_label.setStyleSheet("")
        self._temp_capture_button.setEnabled(False)

    # ------------------------------------------------------------------
    # API used by the main window
    # ------------------------------------------------------------------

    def apply_measurement(self, label: str, value: str) -> None:
        item = QListWidgetItem(f"{label}: {value}")
        self._captured_list.addItem(item)

    def update_status(self, text: str) -> None:
        """Set the status line under the BP instructions.

        Main window passes one of the localised strings —
        ``measuring_vitals_status_waiting`` /
        ``..._connected`` / ``..._failed`` — as the sensor
        progresses. Plain text on purpose: the screen doesn't
        need to know the sensor's state machine, only which copy
        to show.
        """
        self._status.setText(text)

    def set_live_temperature(self, value: float, unit: str) -> None:
        """Push one MLX90640 publish into the live preview.

        Called by main_window's :class:`LiveTemperatureUpdate`
        subscriber on every ESP32-A publish. We always update the
        internal ``_live_temperature_value`` so a recapture tap can
        pick up the freshest reading, but suppress the on-screen
        update when a captured value is already displayed — the
        citizen has committed to that value until they tap
        Recapture.

        ``unit`` is the wire unit string ("C" from the firmware).
        We render with the degree symbol regardless ("°C") because
        that's what's readable on a kiosk display.
        """
        del unit  # the firmware sends "C"; we render °C for display
        self._live_temperature_value = value
        if self._captured_temperature is not None:
            return
        s = get_strings(self._language)
        self._temp_live_label.setText(
            f"{s.measuring_vitals_temperature_current_prefix}: {value:.1f} °C"
        )
        self._temp_capture_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_capture_clicked(self) -> None:
        if self._live_temperature_value is None:
            return
        captured = self._live_temperature_value
        self._captured_temperature = captured
        s = get_strings(self._language)
        self._temp_live_label.setText(
            f"{s.measuring_vitals_temperature_captured_prefix}: {captured:.1f} °C ✓"
        )
        # Inline success styling — the captured state is transient
        # per-session and doesn't merit a global QSS class. The
        # on_enter reset clears this back to default.
        self._temp_live_label.setStyleSheet("color: #52B788; font-weight: 800;")
        self._temp_capture_button.setText(
            s.measuring_vitals_temperature_recapture_button
        )
        self.capture_temperature_requested.emit(captured)
