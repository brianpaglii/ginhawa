"""Section card with optional title.

A soft-surface card with a 1 px border that hosts grouped content
(e.g., a measurement panel on REPORT, a service tile on PATH_CHOICE).
Visual rules live under the ``#sectionCard`` / ``#cardTitle``
selectors in the global stylesheet.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..theme import SIZING


class SectionCard(QFrame):
    """A bordered surface that groups related content with an optional title."""

    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            SIZING.card_padding,
            SIZING.card_padding,
            SIZING.card_padding,
            SIZING.card_padding,
        )
        outer.setSpacing(16)

        self._title_label: QLabel | None = None
        if title is not None:
            label = QLabel(title)
            label.setObjectName("cardTitle")
            outer.addWidget(label)
            self._title_label = label

        # Inner content layout — screens populate this directly.
        self.content_layout: QVBoxLayout = QVBoxLayout()
        self.content_layout.setSpacing(12)
        outer.addLayout(self.content_layout)

    def set_title(self, title: str) -> None:
        """Update the card's title text (no-op when constructed without one)."""
        if self._title_label is not None:
            self._title_label.setText(title)

    def add_widget(self, widget: QWidget) -> None:
        """Append a widget to the card's content area."""
        self.content_layout.addWidget(widget)
