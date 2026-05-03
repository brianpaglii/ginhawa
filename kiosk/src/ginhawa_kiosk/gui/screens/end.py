"""END: bilingual thank-you + countdown to IDLE.

Renders both languages because the language context is being torn
down — by the time the timer fires, ``session_language`` has been
reset to None.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..strings import BILINGUAL_STRINGS, Language, get_strings


class EndScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("end_screen")

        self._thank_en = QLabel(BILINGUAL_STRINGS.end_thank_you_en)
        self._thank_en.setObjectName("end_thank_en")
        self._thank_en.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thank_en.setStyleSheet("font-size: 32px; font-weight: bold;")

        self._thank_tl = QLabel(BILINGUAL_STRINGS.end_thank_you_tl)
        self._thank_tl.setObjectName("end_thank_tl")
        self._thank_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thank_tl.setStyleSheet("font-size: 32px; font-weight: bold;")

        self._countdown = QLabel()
        self._countdown.setObjectName("end_countdown")
        self._countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._countdown.setStyleSheet("font-size: 18px;")

        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(self._thank_en)
        layout.addSpacing(8)
        layout.addWidget(self._thank_tl)
        layout.addSpacing(40)
        layout.addWidget(self._countdown)
        layout.addStretch(2)
        self.setLayout(layout)

    def set_countdown(self, seconds: int, language: Language | None) -> None:
        """Render the auto-return countdown in the active language,
        defaulting to English when no language is set."""
        s = get_strings(language or "en")
        self._countdown.setText(s.end_auto_return_in.format(n=seconds))

    def on_enter(self, language: Language | None = None) -> None:
        # No language-dependent labels except the countdown — set it
        # via set_countdown from the main window.
        self.set_countdown(5, language)
