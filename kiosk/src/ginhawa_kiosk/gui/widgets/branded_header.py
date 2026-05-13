"""GINHAWA branded header.

Layout: wordmark + tagline on the left, screen title in the middle,
clock + language indicator on the right. The header is fixed-height
(see :data:`SIZING.header_height`) so screens beneath always paint
into a stable area.

The header has no business logic of its own — main_window calls
:meth:`set_screen_title`, :meth:`set_language`, etc. as the FSM
transitions; the QTimer below updates the clock once per second.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme import SIZING


class BrandedHeader(QWidget):
    """Top-of-screen header carrying the GINHAWA wordmark + state title."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("brandedHeader")
        self.setFixedHeight(SIZING.header_height)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(40, 12, 40, 12)
        outer.setSpacing(24)

        # Left: wordmark stacked above a tagline.
        brand_column = QVBoxLayout()
        brand_column.setSpacing(0)
        wordmark = QLabel("GINHAWA")
        wordmark.setObjectName("brandWordmark")
        tagline = QLabel("Health for every barangay")
        tagline.setObjectName("brandTagline")
        brand_column.addWidget(wordmark)
        brand_column.addWidget(tagline)
        outer.addLayout(brand_column)

        outer.addStretch(1)

        # Middle: per-screen title set by the main window.
        self._screen_title = QLabel("")
        self._screen_title.setObjectName("headerScreenTitle")
        self._screen_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._screen_title)

        outer.addStretch(1)

        # Right: clock above a language indicator.
        right_column = QVBoxLayout()
        right_column.setSpacing(0)
        right_column.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._clock = QLabel("")
        self._clock.setObjectName("headerClock")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._language = QLabel("")
        self._language.setObjectName("headerLanguage")
        self._language.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_column.addWidget(self._clock)
        right_column.addWidget(self._language)
        outer.addLayout(right_column)

        # Tick the clock once per second. We use a Qt timer because it
        # lives on the GUI thread and does not depend on the asyncio
        # event loop being up (the kiosk's qasync loop is, but tests
        # construct the header standalone).
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    # ------------------------------------------------------------------
    # Public API used by main_window
    # ------------------------------------------------------------------

    def set_screen_title(self, title: str) -> None:
        """Update the centred per-state title (empty string clears it)."""
        self._screen_title.setText(title)

    def set_language(self, label: str) -> None:
        """Update the small language indicator at the right edge."""
        self._language.setText(label)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        # Local-time HH:MM for the operator; seconds are noise on a
        # health kiosk and would just churn paint.
        self._clock.setText(datetime.now().strftime("%H:%M"))
