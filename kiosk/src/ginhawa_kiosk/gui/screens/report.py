"""REPORT: list of valid measurements + Print / Finish buttons.

Filters to ``is_valid=1`` measurements only — out-of-range readings
stay in the DB for diagnostic review but don't appear here. The
Print button is hidden if either ``printer.is_available()`` or
``printer.is_paper_present()`` returns False; in that case the
"Finish without printing" path is the only forward action.

Cancel and Change-language are both available (Change-language is
preserved per spec so a citizen can re-render the report in the
other language before deciding).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..strings import Language, get_strings
from .base import BaseScreen


@dataclass(frozen=True)
class ReportRow:
    """One row to render in the report. The screen has already
    received only ``is_valid=1`` rows from the main window — it does
    not re-filter."""

    label: str  # already localised by the caller
    value: str  # rendered value with units, e.g., "128 mmHg"


class ReportScreen(BaseScreen):
    print_requested = pyqtSignal()
    finish_without_printing_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("report_screen")

        self._title = QLabel()
        self._title.setObjectName("report_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._list = QListWidget()
        self._list.setObjectName("report_list")

        self._no_measurements = QLabel()
        self._no_measurements.setObjectName("report_no_measurements")
        self._no_measurements.setWordWrap(True)
        self._no_measurements.setVisible(False)

        self._printer_unavailable = QLabel()
        self._printer_unavailable.setObjectName("report_printer_unavailable")
        self._printer_unavailable.setWordWrap(True)
        self._printer_unavailable.setVisible(False)

        self._print_button = QPushButton()
        self._print_button.setObjectName("report_print_button")
        self._print_button.clicked.connect(self.print_requested.emit)

        self._finish_button = QPushButton()
        self._finish_button.setObjectName("report_finish_without_printing_button")
        self._finish_button.clicked.connect(self.finish_without_printing_requested.emit)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self._finish_button)
        button_row.addSpacing(20)
        button_row.addWidget(self._print_button)
        button_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addSpacing(12)
        layout.addWidget(self._list, stretch=1)
        layout.addWidget(self._no_measurements)
        layout.addWidget(self._printer_unavailable)
        layout.addLayout(button_row)
        layout.addLayout(self._build_chrome_row())
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # API used by the main window
    # ------------------------------------------------------------------

    def set_measurements(self, rows: list[ReportRow]) -> None:
        """Replace the rendered list with the given valid rows.

        The caller is responsible for filtering to ``is_valid=1``;
        passing an out-of-range row would silently advertise it to
        the citizen and is treated as a wiring bug at the call site.
        """
        self._list.clear()
        for r in rows:
            item = QListWidgetItem(f"{r.label}: {r.value}")
            self._list.addItem(item)
        self._no_measurements.setVisible(not rows)
        self._list.setVisible(bool(rows))

    def set_printer_state(self, *, available: bool, paper_present: bool) -> None:
        """Show or hide the Print button based on printer state.

        Both flags must be true for the Print button to be visible —
        a printer that's online but has no paper would otherwise
        show Print, get tapped, and immediately fail to PAPER_OUT_PRE.
        Hiding the button is the simpler UX.
        """
        can_print = available and paper_present
        self._print_button.setVisible(can_print)
        self._printer_unavailable.setVisible(not can_print)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.report_title)
        self._no_measurements.setText(s.report_no_measurements)
        self._printer_unavailable.setText(s.report_printer_unavailable)
        self._print_button.setText(s.report_print_button)
        self._finish_button.setText(s.report_finish_without_printing_button)
