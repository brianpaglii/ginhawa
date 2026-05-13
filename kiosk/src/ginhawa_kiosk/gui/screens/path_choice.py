"""PATH_CHOICE: vitals / anthropometric / full.

Three :class:`SectionCard` tiles in a horizontal row. Each card hosts
an icon-paired title (emoji + short label), a description of the
measurements that path captures, and a primary "Start" button.
Emits :attr:`path_selected` with the chosen path string; the main
window forwards to ``fsm.path_selected(path)``.

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
        # description label, and the Start button so on_enter() can
        # update text per language without rebuilding the layout.
        (
            self._vitals_card,
            self._vitals_desc,
            self._vitals_button,
        ) = self._build_card("path_button_vitals", "vitals")
        (
            self._anthro_card,
            self._anthro_desc,
            self._anthro_button,
        ) = self._build_card("path_button_anthropometric", "anthropometric")
        (
            self._full_card,
            self._full_desc,
            self._full_button,
        ) = self._build_card("path_button_full", "full")

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
        self, button_object_name: str, path_value: str
    ) -> tuple[SectionCard, QLabel, PrimaryButton]:
        # Passing title="" creates the cardTitle label up-front so
        # on_enter() can swap text per language via set_title.
        card = SectionCard(title="")

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

        card.add_widget(desc)
        card.content_layout.addStretch(1)
        card.add_widget(button)
        return card, desc, button

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)

        self._title.setText(s.path_choice_title)
        self._help.setText(s.path_choice_help)

        # Card titles: emoji + short name.
        self._vitals_card.set_title(f"\U0001f489  {s.path_choice_vitals_title}")
        self._anthro_card.set_title(f"\U0001f4cf  {s.path_choice_anthropometric_title}")
        self._full_card.set_title(f"\U0001fa7a  {s.path_choice_full_title}")

        # Descriptions: the existing long-form path_choice_* strings.
        self._vitals_desc.setText(s.path_choice_vitals)
        self._anthro_desc.setText(s.path_choice_anthropometric)
        self._full_desc.setText(s.path_choice_full)

        # Buttons share the same "Start" label across the three cards
        # — the card title disambiguates the action.
        self._vitals_button.setText(s.path_choice_start_button)
        self._anthro_button.setText(s.path_choice_start_button)
        self._full_button.setText(s.path_choice_start_button)
