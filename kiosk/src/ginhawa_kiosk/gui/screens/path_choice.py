"""PATH_CHOICE: vitals / anthropometric / full.

Three :class:`SectionCard` tiles in a horizontal row. Each card hosts
a large Unicode icon glyph, a short localized title, a description
of the measurements that path captures, and a primary "Start" button.
Emits :attr:`path_selected` with the chosen path string; the main
window forwards to ``fsm.path_selected(path)``.

Icon glyphs are picked from the BMP Symbol blocks (♥ U+2665, ⚖
U+2696, ✚ U+271A) rather than the colour-emoji blocks (💉 / 📏 /
🩺). Pi OS doesn't ship a colour-emoji font by default; without one
Qt renders the picture emoji as tofu boxes. The BMP symbols are
plain text glyphs present in DejaVu Sans / Noto Sans and they
recolour cleanly via QSS ``color`` on the ``cardIcon`` selector.

The buttons keep their legacy ``path_button_*`` objectNames so the
existing GUI tests that look them up by name continue to find them.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..strings import Language, get_strings
from ..widgets.primary_button import PrimaryButton
from ..widgets.section_card import SectionCard
from .base import BaseScreen


# Unicode symbol glyphs in the Misc Symbols / Dingbats blocks — these
# render reliably without a colour-emoji font.
_ICON_VITALS = "♥"  # ♥ HEART (pulse / vitals)
_ICON_ANTHRO = "⚖"  # ⚖ SCALES (body measurements)
_ICON_FULL = "✚"  # ✚ HEAVY GREEK CROSS (medical / full check)


class PathChoiceScreen(BaseScreen):
    path_selected = pyqtSignal(str)  # 'vitals' | 'anthropometric' | 'full'

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("path_choice_screen")

        self._title = QLabel()
        self._title.setObjectName("path_choice_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._help = QLabel()
        self._help.setObjectName("path_choice_help")
        self._help.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Three service cards. _build_card returns the card frame, its
        # title label, description label, and Start button so
        # on_enter() can swap per-language text without rebuilding.
        (
            self._vitals_card,
            self._vitals_title,
            self._vitals_desc,
            self._vitals_button,
        ) = self._build_card(_ICON_VITALS, "path_button_vitals", "vitals")
        (
            self._anthro_card,
            self._anthro_title,
            self._anthro_desc,
            self._anthro_button,
        ) = self._build_card(
            _ICON_ANTHRO, "path_button_anthropometric", "anthropometric"
        )
        (
            self._full_card,
            self._full_title,
            self._full_desc,
            self._full_button,
        ) = self._build_card(_ICON_FULL, "path_button_full", "full")

        cards_row = QHBoxLayout()
        cards_row.setSpacing(32)
        cards_row.addWidget(self._vitals_card, 1)
        cards_row.addWidget(self._anthro_card, 1)
        cards_row.addWidget(self._full_card, 1)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(8)
        layout.addWidget(self._help)
        layout.addSpacing(40)
        layout.addLayout(cards_row, 1)
        layout.addSpacing(24)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    def _build_card(
        self, icon_glyph: str, button_object_name: str, path_value: str
    ) -> tuple[SectionCard, QLabel, QLabel, PrimaryButton]:
        # SectionCard's title slot is left empty — the cardTitle label
        # is hand-placed inside content_layout below the icon so the
        # vertical order is icon → title → description → button.
        card = SectionCard()

        icon = QLabel(icon_glyph)
        icon.setObjectName("cardIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel()
        title.setObjectName("cardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc = QLabel()
        desc.setObjectName("body")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)

        button = PrimaryButton()
        # Override the inherited "primaryButton" objectName so existing
        # tests can find these via findChild(QPushButton, "path_button_*")
        # — the per-objectName QSS block keeps them looking primary.
        button.setObjectName(button_object_name)
        button.clicked.connect(lambda: self.path_selected.emit(path_value))

        card.add_widget(icon)
        card.add_widget(title)
        card.add_widget(desc)
        card.content_layout.addStretch(1)
        card.add_widget(button)
        return card, title, desc, button

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)

        self._title.setText(s.path_choice_title)
        self._help.setText(s.path_choice_help)

        # Card titles: short localized name (icon already rendered).
        self._vitals_title.setText(s.path_choice_vitals_title)
        self._anthro_title.setText(s.path_choice_anthropometric_title)
        self._full_title.setText(s.path_choice_full_title)

        # Descriptions: the existing long-form path_choice_* strings.
        self._vitals_desc.setText(s.path_choice_vitals)
        self._anthro_desc.setText(s.path_choice_anthropometric)
        self._full_desc.setText(s.path_choice_full)

        # Buttons share the same "Start" label across the three cards
        # — the card title disambiguates the action.
        self._vitals_button.setText(s.path_choice_start_button)
        self._anthro_button.setText(s.path_choice_start_button)
        self._full_button.setText(s.path_choice_start_button)
