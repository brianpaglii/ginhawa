"""ERROR: bilingual error message + diagnostic code.

The diagnostic code is set via :meth:`set_diagnostic` by the main
window after an :func:`fsm.error(reason)` call. Citizens are asked
to consult the BHW; the code helps the BHW (and the kiosk's logs)
correlate to the underlying failure.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..strings import BILINGUAL_STRINGS, get_strings


class ErrorScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("error_screen")

        title_en = QLabel(BILINGUAL_STRINGS.error_title_en)
        title_en.setObjectName("error_title_en")
        title_en.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_tl = QLabel(BILINGUAL_STRINGS.error_title_tl)
        title_tl.setObjectName("error_title_tl")
        title_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        message_en = QLabel(BILINGUAL_STRINGS.error_message_en)
        message_en.setObjectName("error_message_en")
        message_en.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message_en.setWordWrap(True)

        message_tl = QLabel(BILINGUAL_STRINGS.error_message_tl)
        message_tl.setObjectName("error_message_tl")
        message_tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message_tl.setWordWrap(True)

        self._diagnostic = QLabel()
        self._diagnostic.setObjectName("error_diagnostic")
        self._diagnostic.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(title_en)
        layout.addSpacing(8)
        layout.addWidget(title_tl)
        layout.addSpacing(20)
        layout.addWidget(message_en)
        layout.addSpacing(8)
        layout.addWidget(message_tl)
        layout.addSpacing(20)
        layout.addWidget(self._diagnostic)
        layout.addStretch(2)
        self.setLayout(layout)

    def set_diagnostic(self, code: str | None) -> None:
        if not code:
            self._diagnostic.clear()
            return
        # Bilingual label for the code prefix; the code itself is a
        # language-neutral identifier the BHW or sysadmin can search.
        en_label = get_strings("en").error_diagnostic_label
        tl_label = get_strings("tl").error_diagnostic_label
        self._diagnostic.setText(f"{en_label} / {tl_label}: {code}")
