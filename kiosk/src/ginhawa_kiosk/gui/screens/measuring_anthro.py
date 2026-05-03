"""MEASURING_ANTHRO: height (VL53L0X) + weight (Xiaomi) + temperature (MLX90640).

Same structure as MeasuringVitalsScreen — informational instructions
plus a live capture list, fed by the main window.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from ..strings import Language, get_strings
from .base import BaseScreen


class MeasuringAnthroScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("measuring_anthro_screen")

        self._title = QLabel()
        self._title.setObjectName("measuring_anthro_title")
        self._title.setStyleSheet("font-size: 28px; font-weight: bold;")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._height = QLabel()
        self._height.setObjectName("measuring_anthro_height_instruction")
        self._height.setWordWrap(True)
        self._weight = QLabel()
        self._weight.setObjectName("measuring_anthro_weight_instruction")
        self._weight.setWordWrap(True)
        self._temperature = QLabel()
        self._temperature.setObjectName("measuring_anthro_temperature_instruction")
        self._temperature.setWordWrap(True)

        for lbl in (self._height, self._weight, self._temperature):
            lbl.setStyleSheet("font-size: 18px;")

        self._captured_list = QListWidget()
        self._captured_list.setObjectName("measuring_anthro_captured_list")

        self._capturing_label = QLabel()
        self._capturing_label.setObjectName("measuring_anthro_capturing")
        self._capturing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(20)
        layout.addWidget(self._height)
        layout.addWidget(self._weight)
        layout.addWidget(self._temperature)
        layout.addSpacing(20)
        layout.addWidget(self._captured_list, stretch=1)
        layout.addWidget(self._capturing_label)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.measuring_anthro_title)
        self._height.setText(s.measuring_anthro_height_instruction)
        self._weight.setText(s.measuring_anthro_weight_instruction)
        self._temperature.setText(s.measuring_anthro_temperature_instruction)
        self._capturing_label.setText(s.measuring_anthro_capturing)
        self._captured_list.clear()

    def apply_measurement(self, label: str, value: str) -> None:
        item = QListWidgetItem(f"{label}: {value}")
        self._captured_list.addItem(item)
