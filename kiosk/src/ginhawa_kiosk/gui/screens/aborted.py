"""ABORTED: bilingual cancellation message.

Both languages on the same page — language context is being torn
down by the time the auto-return timer fires.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..strings import BILINGUAL_STRINGS


class AbortedScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("aborted_screen")

        title_en = QLabel(BILINGUAL_STRINGS.aborted_title_en)
        title_en.setObjectName("aborted_title_en")
        title_en.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_tl = QLabel(BILINGUAL_STRINGS.aborted_title_tl)
        title_tl.setObjectName("aborted_title_tl")
        title_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        message_en = QLabel(BILINGUAL_STRINGS.aborted_message_en)
        message_en.setObjectName("aborted_message_en")
        message_en.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message_en.setWordWrap(True)

        message_tl = QLabel(BILINGUAL_STRINGS.aborted_message_tl)
        message_tl.setObjectName("aborted_message_tl")
        message_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message_tl.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(title_en)
        layout.addSpacing(8)
        layout.addWidget(title_tl)
        layout.addSpacing(20)
        layout.addWidget(message_en)
        layout.addSpacing(8)
        layout.addWidget(message_tl)
        layout.addStretch(2)
        self.setLayout(layout)
