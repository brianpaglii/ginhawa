"""Secondary outlined button.

Outlined teal-on-white treatment for actions that are not the primary
call-to-action (Cancel, Change language, Finish without printing).
All visual rules live in the global stylesheet under
``#secondaryButton``.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QPushButton, QWidget

from ..theme import SIZING


class SecondaryButton(QPushButton):
    """An outlined teal-on-white button for secondary actions."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("secondaryButton")
        self.setMinimumSize(
            SIZING.secondary_button_min_width,
            SIZING.secondary_button_min_height,
        )
