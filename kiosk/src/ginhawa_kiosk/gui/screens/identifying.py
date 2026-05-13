"""IDENTIFYING screen: language-neutral spinner during DB lookup.

Visible only briefly (~200 ms typical, <5 s hard timeout) while the
citizen-lookup service resolves the RFID UID. No cancel: the lookup
is fast and the FSM transitions out of this state automatically.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class IdentifyingScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("identifying_screen")

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Language-neutral marker — not in the strings catalogue
        # because no language has been chosen yet at this point.
        label = QLabel("...")
        label.setObjectName("identifying_label")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)
        self.setLayout(layout)
