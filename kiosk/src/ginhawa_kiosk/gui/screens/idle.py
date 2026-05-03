"""IDLE splash: bilingual prompt + RFID-tap wait.

No language has been chosen yet — the citizen literally hasn't
interacted with the kiosk — so the prompt is shown in BOTH languages
on the same page. The RFID reader is active in the background; this
screen does NOT subscribe to the reader directly. The main window /
sensor coordinator publishes ``RfidScanned`` events on the bus, the
FSM transitions to IDENTIFYING, and the main window switches to the
IdentifyingScreen.

No cancel button: there is nothing to cancel from IDLE.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..strings import BILINGUAL_STRINGS


class IdleScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("idle_screen")
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("GINHAWA")
        title.setObjectName("idle_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 64px; font-weight: bold;")

        # Both languages on the same page — the citizen will pick one
        # on the next screen, but at IDLE we don't yet know which.
        prompt_en = QLabel(BILINGUAL_STRINGS.idle_tap_prompt_en)
        prompt_en.setObjectName("idle_prompt_en")
        prompt_en.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_en.setStyleSheet("font-size: 28px;")

        prompt_tl = QLabel(BILINGUAL_STRINGS.idle_tap_prompt_tl)
        prompt_tl.setObjectName("idle_prompt_tl")
        prompt_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_tl.setStyleSheet("font-size: 28px;")

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(40)
        layout.addWidget(prompt_en)
        layout.addSpacing(8)
        layout.addWidget(prompt_tl)
        layout.addStretch(2)

        self.setLayout(layout)
