"""Primary call-to-action button.

Visual rules (background, hover, pressed, disabled, border-radius)
all live in the global stylesheet under the ``#primaryButton``
selector. This subclass only sets ``objectName`` and the minimum
size required for a touchscreen tap target.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QPushButton, QWidget

from ..theme import SIZING


class PrimaryButton(QPushButton):
    """A large teal call-to-action button suitable for a 1920x1080 kiosk."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("primaryButton")
        self.setMinimumSize(
            SIZING.primary_button_min_width,
            SIZING.primary_button_min_height,
        )
