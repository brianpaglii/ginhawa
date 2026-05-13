"""Base widget for kiosk screens.

Centralises the chrome shared by every "active session" screen:
* a Cancel button (lower-left) that emits :attr:`cancel_requested`
* a Change-language button (lower-right) that emits
  :attr:`change_language_requested`

Screens that need this chrome inherit from :class:`BaseScreen` and
call :meth:`add_chrome` after their content layout is in place.
Screens that don't need chrome (IDLE / IDENTIFYING / END / ABORTED /
ERROR) simply don't call it.

Each screen also exposes an :meth:`on_enter` hook so the main
window can refresh language-dependent labels on every transition
into the screen — important because language can change mid-
session via the "Change language" button.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ..strings import Language, get_strings
from ..widgets import SecondaryButton


class BaseScreen(QWidget):
    """Common base for screens that need cancel + change-language chrome.

    Subclasses build their content in their ``__init__``; they call
    ``self.add_chrome(parent_layout)`` once their content is in place
    so the chrome row sits at the bottom.
    """

    # Emitted when the citizen taps Cancel. The main window forwards
    # this to ``fsm.cancel()``.
    cancel_requested = pyqtSignal()

    # Emitted when the citizen taps Change language. The main window
    # forwards this to ``fsm.change_language()``.
    change_language_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._cancel_button: SecondaryButton | None = None
        self._change_language_button: SecondaryButton | None = None
        # The currently rendered language, set in on_enter. Kept as
        # an attribute so subclasses don't have to re-thread it
        # through their internal callbacks (e.g., a Submit click in
        # REGISTER_FORM needs to render validation errors in the
        # active language).
        self._language: Language = "en"
        # Optional content slot that screens may populate via
        # :pyattr:`content_layout`. The main window now owns the
        # outer chrome (header + footer), so screens build only
        # their per-state content into this vertical layout.
        # Screens that don't use it construct their own layouts as
        # before.
        self.content_layout: QVBoxLayout = QVBoxLayout()

    # ------------------------------------------------------------------
    # Hooks the main window calls
    # ------------------------------------------------------------------

    def on_enter(self, language: Language) -> None:
        """Refresh language-dependent labels when this screen becomes
        visible. Override in subclasses; the base implementation
        updates the chrome buttons if they exist."""
        self._language = language
        self._refresh_chrome_labels(language)

    # ------------------------------------------------------------------
    # Chrome construction
    # ------------------------------------------------------------------

    def _build_chrome_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        # Both chrome buttons are SecondaryButtons so the global QSS
        # styles them. We override the objectName afterwards so the
        # existing find-by-name tests keep working — Qt allows one
        # objectName per widget; we trade the QSS selector for the
        # legacy name and accept the secondary visual treatment via
        # widget class only on the matching cancel_button /
        # change_language_button selectors below in styles.qss.
        cancel = SecondaryButton()
        cancel.setObjectName("cancel_button")
        cancel.clicked.connect(self.cancel_requested.emit)

        change_lang = SecondaryButton()
        change_lang.setObjectName("change_language_button")
        change_lang.clicked.connect(self.change_language_requested.emit)

        spacer = QSpacerItem(
            40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        row.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignLeft)
        row.addItem(spacer)
        row.addWidget(change_lang, alignment=Qt.AlignmentFlag.AlignRight)

        self._cancel_button = cancel
        self._change_language_button = change_lang
        return row

    def _refresh_chrome_labels(self, language: Language) -> None:
        if self._cancel_button is None or self._change_language_button is None:
            return
        s = get_strings(language)
        self._cancel_button.setText(s.cancel_button)
        self._change_language_button.setText(s.change_language_button)
