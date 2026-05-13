"""GINHAWA branded footer.

Layout: barangay name left, app version centred, network indicator
on the right. Fixed-height (see :data:`SIZING.footer_height`).

main_window calls :meth:`set_barangay`, :meth:`set_version`, and
:meth:`set_network_online` as appropriate. The footer itself has no
business logic.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme import SIZING


class BrandedFooter(QWidget):
    """Bottom-of-screen footer with barangay, version, and network state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("brandedFooter")
        self.setFixedHeight(SIZING.footer_height)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(40, 8, 40, 8)
        layout.setSpacing(24)

        self._barangay = QLabel("")
        self._barangay.setObjectName("footerBarangay")
        self._barangay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._barangay)

        layout.addStretch(1)

        self._version = QLabel("")
        self._version.setObjectName("footerVersion")
        self._version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._version)

        layout.addStretch(1)

        # Initial state matches set_network_online(False): open circle
        # for offline (Unicode text glyph, not the emoji-presentation
        # ⚫ U+26AB which renders as tofu without an emoji font on
        # Pi OS).
        self._network = QLabel("○ Offline")
        self._network.setObjectName("footerNetwork")
        self._network.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._network)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_barangay(self, name: str) -> None:
        """Display the deployment barangay (left)."""
        self._barangay.setText(name)

    def set_version(self, version: str) -> None:
        """Display the kiosk app version (centre)."""
        self._version.setText(version)

    def set_network_online(self, online: bool) -> None:
        """Toggle the network indicator (right) between Online / Offline."""
        if online:
            self._network.setText("● Online")
        else:
            self._network.setText("○ Offline")
