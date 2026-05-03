"""MEASURING_VITALS: BP (Omron) + SpO2/HR (MAX30100 via MQTT).

Shows step-by-step instructions for the citizen. The actual sensor
reads are async and run via the sensor coordinator wired in
``__main__``; this screen is purely informational + a "next-step"
indicator. As measurements arrive on the bus, the main window
forwards them to :meth:`apply_measurement` for live display.

The screen does NOT subscribe to the bus directly to keep the
sensor-coordinator boundary clean and testable.
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
        layout.addSpacing(20)
        layout.addWidget(self._captured_list, stretch=1)
        layout.addWidget(self._capturing_label)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.measuring_vitals_title)
        self._bp_instruction.setText(s.measuring_vitals_bp_instruction)
        self._pulse_instruction.setText(s.measuring_vitals_pulse_instruction)
        self._capturing_label.setText(s.measuring_vitals_capturing)
        self._captured_list.clear()

    def apply_measurement(self, label: str, value: str) -> None:
        """Add a row to the captured-measurements list — called by the
        main window when a MeasurementProposed event arrives during
        this state."""
        item = QListWidgetItem(f"{label}: {value}")
        self._captured_list.addItem(item)
