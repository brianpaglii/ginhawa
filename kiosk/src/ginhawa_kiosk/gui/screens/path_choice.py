"""PATH_CHOICE: vitals / anthropometric / full.

Three large equal-weight buttons. Emits :attr:`path_selected` with
the chosen path string; the main window forwards to
``fsm.path_selected(path)``.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout

from ..strings import Language, get_strings
from .base import BaseScreen


class PathChoiceScreen(BaseScreen):
    path_selected = pyqtSignal(str)  # 'vitals' | 'anthropometric' | 'full'

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("path_choice_screen")

        self._title = QLabel()
        self._title.setObjectName("path_choice_title")
        self._title.setStyleSheet("font-size: 28px; font-weight: bold;")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._help = QLabel()
        self._help.setObjectName("path_choice_help")
        self._help.setStyleSheet("font-size: 16px;")
        self._help.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._vitals_button = QPushButton()
        self._vitals_button.setObjectName("path_button_vitals")
        self._vitals_button.setStyleSheet("font-size: 22px; padding: 32px;")
        self._vitals_button.clicked.connect(lambda: self.path_selected.emit("vitals"))

        self._anthro_button = QPushButton()
        self._anthro_button.setObjectName("path_button_anthropometric")
        self._anthro_button.setStyleSheet("font-size: 22px; padding: 32px;")
        self._anthro_button.clicked.connect(
            lambda: self.path_selected.emit("anthropometric")
        )

        self._full_button = QPushButton()
        self._full_button.setObjectName("path_button_full")
        self._full_button.setStyleSheet("font-size: 22px; padding: 32px;")
        self._full_button.clicked.connect(lambda: self.path_selected.emit("full"))

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(8)
        layout.addWidget(self._help)
        layout.addSpacing(40)
        layout.addWidget(self._vitals_button)
        layout.addWidget(self._anthro_button)
        layout.addWidget(self._full_button)
        layout.addStretch(1)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.path_choice_title)
        self._help.setText(s.path_choice_help)
        self._vitals_button.setText(s.path_choice_vitals)
        self._anthro_button.setText(s.path_choice_anthropometric)
        self._full_button.setText(s.path_choice_full)
