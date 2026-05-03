"""LANGUAGE_SELECT: citizen picks English or Tagalog.

Two large equal-weight buttons. Emits :attr:`language_chosen` with
``'en'`` or ``'tl'``; the main window forwards this to
``fsm.language_chosen(language)``.

The screen has a Cancel button (lower-left) but no Change-language
button (you're already on the language screen). Both label strings
come from the bilingual catalogue because the citizen has not yet
chosen a language.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..strings import Language, get_strings
from .base import BaseScreen


class LanguageSelectScreen(BaseScreen):
    language_chosen = pyqtSignal(str)  # 'en' or 'tl'

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("language_select_screen")

        layout = QVBoxLayout()

        # Bilingual heading — both forms always visible.
        heading_en = QLabel(get_strings("en").language_select_title)
        heading_en.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_en.setStyleSheet("font-size: 32px; font-weight: bold;")

        heading_tl = QLabel(get_strings("tl").language_select_title)
        heading_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_tl.setStyleSheet("font-size: 32px; font-weight: bold;")

        # Buttons: English / Tagalog, equal weight.
        buttons_row = QHBoxLayout()

        en_button = QPushButton("English")
        en_button.setObjectName("language_button_en")
        en_button.setStyleSheet("font-size: 28px; padding: 40px;")
        en_button.clicked.connect(lambda: self.language_chosen.emit("en"))

        tl_button = QPushButton("Tagalog")
        tl_button.setObjectName("language_button_tl")
        tl_button.setStyleSheet("font-size: 28px; padding: 40px;")
        tl_button.clicked.connect(lambda: self.language_chosen.emit("tl"))

        buttons_row.addStretch(1)
        buttons_row.addWidget(en_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(tl_button)
        buttons_row.addStretch(1)

        layout.addStretch(1)
        layout.addWidget(heading_en)
        layout.addSpacing(8)
        layout.addWidget(heading_tl)
        layout.addSpacing(40)
        layout.addLayout(buttons_row)
        layout.addStretch(2)
        layout.addLayout(self._build_chrome_row())

        self.setLayout(layout)

        self._en_button = en_button
        self._tl_button = tl_button

    def on_enter(self, language: Language) -> None:
        # The Change-language button is meaningless on this screen
        # (we ARE the language screen) — BaseScreen builds it for
        # uniformity; suppress it here.
        super().on_enter(language)
        if self._change_language_button is not None:
            self._change_language_button.setVisible(False)
        # Bilingual cancel label so it remains intelligible regardless
        # of which language the citizen will pick on the next tap.
        if self._cancel_button is not None:
            self._cancel_button.setText(
                f"{get_strings('en').cancel_button} / {get_strings('tl').cancel_button}"
            )
