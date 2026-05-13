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

        # Personalized greeting — populated on entry by main_window
        # via set_citizen_first_name() with the FSM's current
        # citizen. Hidden when no citizen is attached (defensive;
        # LANGUAGE_SELECT only follows successful identification, so
        # this should always be set in practice).
        self._greeting = QLabel("")
        self._greeting.setObjectName("languageSelectGreeting")
        self._greeting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._greeting.setVisible(False)

        # Bilingual heading — both forms always visible. The screenH2
        # objectName lets the global stylesheet drive the font size.
        heading_en = QLabel(get_strings("en").language_select_title)
        heading_en.setObjectName("screenH2")
        heading_en.setAlignment(Qt.AlignmentFlag.AlignCenter)

        heading_tl = QLabel(get_strings("tl").language_select_title)
        heading_tl.setObjectName("screenH2")
        heading_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Buttons: English / Tagalog, equal weight. The QSS targets
        # these objectNames with the giant-button treatment per spec.
        buttons_row = QHBoxLayout()

        en_button = QPushButton("English")
        en_button.setObjectName("language_button_en")
        en_button.clicked.connect(lambda: self.language_chosen.emit("en"))

        tl_button = QPushButton("Tagalog")
        tl_button.setObjectName("language_button_tl")
        tl_button.clicked.connect(lambda: self.language_chosen.emit("tl"))

        buttons_row.addStretch(1)
        buttons_row.addWidget(en_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(tl_button)
        buttons_row.addStretch(1)

        layout.addStretch(1)
        layout.addWidget(self._greeting)
        layout.addSpacing(16)
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

    def set_citizen_first_name(self, first_name: str | None) -> None:
        """Update the personalized greeting above the headings.

        ``first_name`` is the citizen's first word from full_name —
        the caller is expected to do the split. Pass ``None`` or an
        empty string to hide the greeting; the screen falls back to
        the bilingual "Choose Language / Pumili ng Wika" heading
        alone, which is the original pre-greeting behaviour.

        The greeting is intentionally bilingual ("Hi, X! Kumusta?")
        rather than language-switched — the citizen hasn't picked a
        language yet, and a mixed greeting feels warmer than a
        formal one-or-the-other.
        """
        if first_name:
            self._greeting.setText(f"Hi, {first_name}! Kumusta?")
            self._greeting.setVisible(True)
        else:
            self._greeting.setText("")
            self._greeting.setVisible(False)

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
