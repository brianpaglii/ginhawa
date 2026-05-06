"""MEASURING_VITALS: BP (Omron) + SpO2/HR (MAX30100 via MQTT).

Shows step-by-step instructions for the citizen. The BP cuff
sub-flow is **user-gated**: a "Connect to cuff" button publishes
``BpMeasurementRequested`` only after the citizen has taken the
measurement on the cuff alone AND pressed the Bluetooth button to
put it in pairing mode. This matches the cuff's store-and-forward
BLE model documented in :mod:`ginhawa_kiosk.sensors.omron_bp` —
firing the request before pairing mode is active produces a
``[org.bluez.Error.InProgress]`` failure on every retry.

The pulse-oximeter sub-flow is passive — the MAX30100 streams its
readings via MQTT as soon as a finger is detected; no kiosk-side
trigger needed.

The screen does NOT subscribe to the bus directly to keep the
sensor-coordinator boundary clean and testable.
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
    # Emitted when the citizen taps "Connect to cuff" — the main window
    # publishes ``BpMeasurementRequested`` only on this signal, never on
    # state entry. See :mod:`ginhawa_kiosk.sensors.omron_bp` for why
    # auto-firing on entry produces ``InProgress`` errors.
    connect_to_cuff_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("measuring_vitals_screen")

        self._title = QLabel()
        self._title.setObjectName("measuring_vitals_title")
        self._title.setStyleSheet("font-size: 28px; font-weight: bold;")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._bp_instruction = QLabel()
        self._bp_instruction.setObjectName("measuring_vitals_bp_instruction")
        self._bp_instruction.setWordWrap(True)
        self._bp_instruction.setStyleSheet("font-size: 18px;")

        self._pulse_instruction = QLabel()
        self._pulse_instruction.setObjectName("measuring_vitals_pulse_instruction")
        self._pulse_instruction.setWordWrap(True)
        self._pulse_instruction.setStyleSheet("font-size: 18px;")

        # User-gated BP connect button. Disabled once tapped to prevent
        # double-publish; re-enabled by ``set_connecting(False)`` from
        # the main window after a timeout, or on the next ``on_enter``.
        self._connect_button = QPushButton()
        self._connect_button.setObjectName("measuring_vitals_connect_button")
        self._connect_button.setStyleSheet("font-size: 22px; padding: 16px 32px;")
        self._connect_button.clicked.connect(self._on_connect_clicked)

        self._connect_help = QLabel()
        self._connect_help.setObjectName("measuring_vitals_connect_help")
        self._connect_help.setWordWrap(True)
        self._connect_help.setStyleSheet("font-size: 14px; color: #555;")
        self._connect_help.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._connect_status = QLabel()
        self._connect_status.setObjectName("measuring_vitals_connect_status")
        self._connect_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connect_status.setStyleSheet("font-size: 14px; color: #1a5fb4;")
        self._connect_status.setVisible(False)

        connect_row = QHBoxLayout()
        connect_row.addStretch(1)
        connect_row.addWidget(self._connect_button)
        connect_row.addStretch(1)

        self._captured_list = QListWidget()
        self._captured_list.setObjectName("measuring_vitals_captured_list")

        self._capturing_label = QLabel()
        self._capturing_label.setObjectName("measuring_vitals_capturing")
        self._capturing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        instructions_row = QHBoxLayout()
        instructions_row.addWidget(self._bp_instruction)
        instructions_row.addSpacing(20)
        instructions_row.addWidget(self._pulse_instruction)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(20)
        layout.addLayout(instructions_row)
        layout.addSpacing(12)
        layout.addWidget(self._connect_help)
        layout.addLayout(connect_row)
        layout.addWidget(self._connect_status)
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
        self._connect_button.setText(s.measuring_vitals_connect_button)
        self._connect_help.setText(s.measuring_vitals_connect_help)
        self._capturing_label.setText(s.measuring_vitals_capturing)
        self._captured_list.clear()
        # Reset to the "ready to connect" UI on every entry so a citizen
        # who came back via Change-language or who started a new session
        # sees an enabled button regardless of prior state.
        self.set_connecting(False)

    # ------------------------------------------------------------------
    # API used by the main window
    # ------------------------------------------------------------------

    def apply_measurement(self, label: str, value: str) -> None:
        item = QListWidgetItem(f"{label}: {value}")
        self._captured_list.addItem(item)

    def set_connecting(self, connecting: bool) -> None:
        """Toggle the connect button between "ready" and "connecting".

        While ``connecting=True``, the button is disabled and a
        "Connecting..." status line is visible. The main window calls
        this with True immediately after publishing
        ``BpMeasurementRequested`` and with False after a timer
        elapses (or when the FSM transitions out of MEASURING_VITALS).
        """
        self._connect_button.setEnabled(not connecting)
        # Use the active language (set in on_enter) so the status text
        # matches everything else on the screen.
        s = get_strings(self._language)
        self._connect_status.setText(s.measuring_vitals_connecting)
        self._connect_status.setVisible(connecting)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_connect_clicked(self) -> None:
        # Disable synchronously to prevent double-fire from rapid taps;
        # the main window will re-enable via set_connecting(False) on
        # timeout or on FSM exit.
        self.set_connecting(True)
        self.connect_to_cuff_requested.emit()
