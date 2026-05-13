"""CONSENT: privacy notice + I agree / I do not agree.

The body text is the kiosk's current consent_version notice. We
deliberately do NOT version the rendered text on the screen
(that's the kiosk-config / device_config concern); the screen
displays whatever text the strings catalogue offers for the
configured consent_version. If the catalogue is updated the
strings module is updated.

Cancel and Change-language are both available.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..strings import Language, get_strings
from .base import BaseScreen


class ConsentScreen(BaseScreen):
    consent_given = pyqtSignal()
    consent_refused = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("consent_screen")

        self._title = QLabel()
        self._title.setObjectName("consent_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._body = QLabel()
        self._body.setObjectName("consent_body")
        self._body.setWordWrap(True)

        self._agree_button = QPushButton()
        self._agree_button.setObjectName("consent_agree_button")
        self._agree_button.clicked.connect(self.consent_given.emit)

        self._disagree_button = QPushButton()
        self._disagree_button.setObjectName("consent_disagree_button")
        self._disagree_button.clicked.connect(self.consent_refused.emit)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self._disagree_button)
        button_row.addSpacing(20)
        button_row.addWidget(self._agree_button)
        button_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(20)
        layout.addWidget(self._body)
        layout.addStretch(1)
        layout.addLayout(button_row)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.consent_title)
        self._body.setText(s.consent_body)
        self._agree_button.setText(s.consent_agree_button)
        self._disagree_button.setText(s.consent_disagree_button)
