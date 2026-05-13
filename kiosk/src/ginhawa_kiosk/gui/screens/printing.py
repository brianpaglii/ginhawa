"""PRINTING: loading indicator while the printer service runs.

Pure status display — no buttons. The main window kicks off the
print job as a qasync task on entry and waits for the
``PrintComplete`` event to advance the FSM.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout

from ..strings import Language, get_strings
from .base import BaseScreen


class PrintingScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("printing_screen")

        self._title = QLabel()
        self._title.setObjectName("printing_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Simple animated dots — keeping it cheap so a stuck printer
        # query doesn't burn the CPU. The identifying_label style is
        # reused to match the spinner sizing.
        self._spinner = QLabel("...")
        self._spinner.setObjectName("identifying_label")
        self._spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(self._title)
        layout.addWidget(self._spinner)
        layout.addStretch(2)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        self._title.setText(get_strings(language).printing_title)
