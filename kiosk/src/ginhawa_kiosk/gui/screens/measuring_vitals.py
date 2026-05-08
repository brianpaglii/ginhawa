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

The screen does NOT subscribe to the bus directly to keep the
sensor-coordinator boundary clean and testable. The main window
calls :meth:`update_status` to surface "Waiting / Connected /
Failed" copy as the sensor progresses.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..strings import Language, get_strings
from .base import BaseScreen


class MeasuringVitalsScreen(BaseScreen):
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

        # Status line driven by main_window.update_status. We
        # initialise to the "waiting" copy — the FSM auto-fires
        # BpMeasurementRequested on entry, so by the time this
        # screen first paints, the sensor is already retrying.
        self._status = QLabel()
        self._status.setObjectName("measuring_vitals_status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("font-size: 16px; color: #1a5fb4;")

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
        layout.addWidget(self._status)
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
        # Reset the captured list and seed the status line every time
        # the screen mounts (Change-language re-entry, new session).
        self._captured_list.clear()
        self._status.setText(s.measuring_vitals_status_waiting)

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
