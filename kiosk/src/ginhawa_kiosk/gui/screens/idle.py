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


class IdleScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("idle_screen")
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # The large GINHAWA wordmark anchors the splash; #screenH1
        # in the global stylesheet drives its size/weight.
        title = QLabel("GINHAWA")
        title.setObjectName("screenH1")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Bilingual prompts shown together — the citizen hasn't yet
        # picked a language so we render both. Strings module owns the
        # canonical English + Tagalog wording.
        from ..strings import BILINGUAL_STRINGS

        prompt_en = QLabel(BILINGUAL_STRINGS.idle_tap_prompt_en)
        # Keep the legacy idle_prompt_en objectName for test lookups;
        # the bodyLg style is applied via stylesheet selector below
        # (we extend styles.qss to target idle_prompt_*).
        prompt_en.setObjectName("idle_prompt_en")
        prompt_en.setAlignment(Qt.AlignmentFlag.AlignCenter)

        prompt_tl = QLabel(BILINGUAL_STRINGS.idle_tap_prompt_tl)
        prompt_tl.setObjectName("idle_prompt_tl")
        prompt_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(40)
        layout.addWidget(prompt_en)
        layout.addSpacing(8)
        layout.addWidget(prompt_tl)
        layout.addStretch(2)

        self.setLayout(layout)
