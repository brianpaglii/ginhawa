"""REGISTER_FORM: self-service citizen registration.

Collects name, DOB, sex, barangay, optional phone. Validation is
inline — empty required fields produce a banner; the screen does
NOT emit ``submitted`` until validation passes.

The barangay field is pre-filled from
``device_config.deployment_barangay`` (passed in via
:class:`RegisterFormScreen` constructor) so the kiosk's location is
the default and the citizen only edits if they're visiting from a
different barangay. Phone is optional.

On a successful submit the screen emits :attr:`submitted` with a
:class:`RegistrationData` dataclass; the main window inserts the
Citizen row, calls ``fsm.set_current_citizen`` + ``fsm.registration_complete()``.

The screen resets its inputs on every ``on_enter`` so a "Change
language" round-trip lands on a clean form (the test
``test_change_language_resets_partial_register_form_data`` pins
this).

The DOB field uses :class:`_InlineCalendarPicker` — a custom
always-visible QCalendarWidget with year and decade jump buttons.
This replaces the previous ``QDateEdit(setCalendarPopup=True)``
which was unreliable on the kiosk touchscreen: tapping inside the
popup registered as a click-outside event on X11 and dismissed
the popup before the day-cell tap landed.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..strings import Language, get_strings
from .base import BaseScreen


@dataclass(frozen=True)
class RegistrationData:
    full_name: str
    dob_iso: str
    sex: str  # 'M' | 'F' | 'O'
    barangay: str
    phone: str | None


class _InlineCalendarPicker(QWidget):
    """Always-visible date picker for touchscreens.

    Replaces ``QDateEdit + setCalendarPopup(True)`` which is
    unreliable on touch: the popup's child widgets register as
    "outside" the popup hit-test region when receiving touch events
    on X11 / xcb, so a day-cell tap dismisses the popup before the
    selection is committed.

    Layout: a decade-jump / year-jump row above the
    :class:`QCalendarWidget`, with a "Selected: yyyy-MM-dd" label
    below. The calendar's built-in ``◀`` / ``▶`` arrows still drive
    month navigation; the custom buttons bypass the year QSpinBox
    that's likewise hard to touch.

    Public API mirrors the subset of :class:`QDateEdit` the form
    consumed before: ``selectedDate()`` and ``setSelectedDate(date)``.
    ``setSelectedDate`` clamps to the configured min/max so a
    parent caller can't accidentally seed an out-of-range date.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inlineCalendarPicker")

        self._calendar = QCalendarWidget()
        self._calendar.setObjectName("registerDobCalendar")
        # Bounds: 1900 covers everyone alive; nobody is born in the
        # future. setSelectedDate (ours) clamps incoming dates to
        # this range so a wrong programmatic seed can't sneak past.
        self._calendar.setMinimumDate(QDate(1900, 1, 1))
        self._calendar.setMaximumDate(QDate.currentDate())
        # Default to ~30 years ago — covers the adult demographic
        # that makes up the typical kiosk user.
        self._calendar.setSelectedDate(QDate.currentDate().addYears(-30))
        self._calendar.setGridVisible(True)
        self._calendar.setNavigationBarVisible(True)
        self._calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self._calendar.setHorizontalHeaderFormat(
            QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames
        )

        # Disable the navigation-bar's month-name QToolButton popup
        # menu. On touch screens the popup's menu items don't align
        # with their hit-test regions reliably and tapping "January"
        # often lands on "March". The « Month / Month » buttons below
        # are the supported month-nav affordance. The button stays
        # VISIBLE (so the current month name is still displayed) but
        # is non-interactive; styles.qss keeps it looking normal
        # rather than disabled-greyed.
        month_button = self._calendar.findChild(QToolButton, "qt_calendar_monthbutton")
        if month_button is not None:
            month_button.setMenu(None)
            month_button.setEnabled(False)

        # Month / year / decade jump buttons — unique objectNames so
        # tests can target each direction via findChild.
        self._month_back = QPushButton("« Month")
        self._month_back.setObjectName("calendarMonthBackButton")
        self._month_back.clicked.connect(self._jump_month_back)

        self._month_forward = QPushButton("Month »")
        self._month_forward.setObjectName("calendarMonthForwardButton")
        self._month_forward.clicked.connect(self._jump_month_forward)

        self._year_back = QPushButton("« Year")
        self._year_back.setObjectName("calendarYearBackButton")
        self._year_back.clicked.connect(self._jump_year_back)

        self._year_forward = QPushButton("Year »")
        self._year_forward.setObjectName("calendarYearForwardButton")
        self._year_forward.clicked.connect(self._jump_year_forward)

        # Decade jump buttons — for elderly users who would otherwise
        # tap year-back 30+ times to reach a typical adult DOB.
        self._decade_back = QPushButton("« 10 yrs")
        self._decade_back.setObjectName("calendarDecadeBackButton")
        self._decade_back.clicked.connect(self._jump_decade_back)

        self._decade_forward = QPushButton("10 yrs »")
        self._decade_forward.setObjectName("calendarDecadeForwardButton")
        self._decade_forward.clicked.connect(self._jump_decade_forward)

        # Order: decade > year > month on the left, mirrored on the
        # right. Bigger jumps sit on the outside so the common case
        # (single-month nudge) is closest to the centre stretch.
        jumps = QHBoxLayout()
        jumps.addWidget(self._decade_back)
        jumps.addWidget(self._year_back)
        jumps.addWidget(self._month_back)
        jumps.addStretch(1)
        jumps.addWidget(self._month_forward)
        jumps.addWidget(self._year_forward)
        jumps.addWidget(self._decade_forward)

        self._selected_label = QLabel()
        self._selected_label.setObjectName("calendarSelectedLabel")
        self._selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_selected_label()
        # Keep the label in sync whenever the citizen taps a day cell.
        self._calendar.selectionChanged.connect(self._update_selected_label)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.addLayout(jumps)
        layout.addWidget(self._calendar)
        layout.addWidget(self._selected_label)

    # ------------------------------------------------------------------
    # Public API — mimics the slice of QDateEdit the form consumed.
    # ------------------------------------------------------------------

    def selectedDate(self) -> QDate:
        return self._calendar.selectedDate()

    def setSelectedDate(self, date: QDate) -> None:
        # Clamp before forwarding — QCalendarWidget silently rejects
        # out-of-range dates rather than clamping them.
        min_date = self._calendar.minimumDate()
        max_date = self._calendar.maximumDate()
        if date < min_date:
            date = min_date
        elif date > max_date:
            date = max_date
        self._calendar.setSelectedDate(date)
        self._update_selected_label()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _jump_month_back(self) -> None:
        self._jump_months(-1)

    def _jump_month_forward(self) -> None:
        self._jump_months(1)

    def _jump_months(self, delta: int) -> None:
        # Delegate clamping to setSelectedDate so the month buttons
        # can't push the selection outside [min, max] either.
        self.setSelectedDate(self._calendar.selectedDate().addMonths(delta))

    def _jump_year_back(self) -> None:
        self._jump_years(-1)

    def _jump_year_forward(self) -> None:
        self._jump_years(1)

    def _jump_decade_back(self) -> None:
        self._jump_years(-10)

    def _jump_decade_forward(self) -> None:
        self._jump_years(10)

    def _jump_years(self, delta: int) -> None:
        # Delegate clamping to setSelectedDate so the year buttons
        # can't push the selection outside [min, max] either.
        self.setSelectedDate(self._calendar.selectedDate().addYears(delta))

    def _update_selected_label(self) -> None:
        date = self._calendar.selectedDate()
        self._selected_label.setText(f"Selected: {date.toString('yyyy-MM-dd')}")


class RegisterFormScreen(BaseScreen):
    submitted = pyqtSignal(object)  # RegistrationData

    def __init__(self, *, default_barangay: str = "") -> None:
        super().__init__()
        self.setObjectName("register_form_screen")
        self._default_barangay = default_barangay

        # Heading + intro
        self._title = QLabel()
        self._title.setObjectName("register_title")
        self._intro = QLabel()
        self._intro.setObjectName("register_intro")
        self._intro.setWordWrap(True)

        # Form fields
        self._name_input = QLineEdit()
        self._name_input.setObjectName("register_name_input")
        # DOB uses an inline always-visible calendar — the popup
        # version (QDateEdit + setCalendarPopup) dismissed on tap
        # under X11 touch events. See _InlineCalendarPicker docstring.
        self._dob_input = _InlineCalendarPicker()
        self._dob_input.setObjectName("register_dob_input")
        self._barangay_input = QLineEdit()
        self._barangay_input.setObjectName("register_barangay_input")
        self._phone_input = QLineEdit()
        self._phone_input.setObjectName("register_phone_input")

        # Sex radios
        self._sex_male = QRadioButton()
        self._sex_male.setObjectName("register_sex_male")
        self._sex_female = QRadioButton()
        self._sex_female.setObjectName("register_sex_female")
        self._sex_other = QRadioButton()
        self._sex_other.setObjectName("register_sex_other")
        self._sex_group = QButtonGroup(self)
        self._sex_group.addButton(self._sex_male, 0)
        self._sex_group.addButton(self._sex_female, 1)
        self._sex_group.addButton(self._sex_other, 2)

        # Validation banner
        self._error_label = QLabel()
        self._error_label.setObjectName("register_error_label")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)

        # Submit button
        self._submit_button = QPushButton()
        self._submit_button.setObjectName("register_submit_button")
        self._submit_button.clicked.connect(self._on_submit_clicked)

        # Field labels — populated in on_enter() so they re-render on
        # language change.
        self._name_label = QLabel()
        self._dob_label = QLabel()
        self._sex_label = QLabel()
        self._barangay_label = QLabel()
        self._phone_label = QLabel()

        # Each radio gets an equal third of the form width so the
        # styled rounded tap target stretches across; the trailing
        # stretch is intentionally NOT used (it would collapse the
        # radios to their content width and break the "wide button"
        # affordance the QSS counts on).
        sex_row = QHBoxLayout()
        sex_row.setSpacing(16)
        sex_row.addWidget(self._sex_male, 1)
        sex_row.addWidget(self._sex_female, 1)
        sex_row.addWidget(self._sex_other, 1)

        # Flat vertical layout (label-above-input) inside a scroll
        # container. The inline calendar is too wide to share a row
        # with its label in a QFormLayout, and even at the reduced
        # max-height of ~440 px the full form (title + intro + name
        # + DOB + sex + barangay + phone + submit) can exceed 1080.
        # QScrollArea handles overflow cleanly; the chrome row stays
        # OUTSIDE the scroll area so Cancel / Change Language remain
        # visible regardless of scroll position.
        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.setSpacing(16)
        form_layout.addWidget(self._title)
        form_layout.addWidget(self._intro)
        form_layout.addSpacing(8)
        form_layout.addWidget(self._name_label)
        form_layout.addWidget(self._name_input)
        form_layout.addWidget(self._dob_label)
        form_layout.addWidget(self._dob_input)
        form_layout.addWidget(self._sex_label)
        form_layout.addLayout(sex_row)
        form_layout.addWidget(self._barangay_label)
        form_layout.addWidget(self._barangay_input)
        form_layout.addWidget(self._phone_label)
        form_layout.addWidget(self._phone_input)
        form_layout.addWidget(self._error_label)
        form_layout.addWidget(
            self._submit_button, alignment=Qt.AlignmentFlag.AlignRight
        )

        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("registerScrollArea")
        self._scroll_area.setWidget(form_container)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(40, 24, 40, 24)
        outer_layout.addWidget(self._scroll_area, 1)
        outer_layout.addLayout(self._build_chrome_row())
        self.setLayout(outer_layout)

        # Note: an earlier revision auto-scrolled the form whenever a
        # widget gained focus, to lift text fields above the virtual
        # keyboard. That hook (QApplication.focusChanged → 300 ms
        # QTimer → setValue) fired on calendar-cell focus during
        # touch interactions and shifted the calendar mid-tap, so
        # the citizen's "tap January" ended up registering on a
        # different month. The auto-scroll has been removed; the
        # citizen can drag the scroll bar manually if a field is
        # hidden behind the keyboard.

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self, language: Language) -> None:
        super().on_enter(language)
        s = get_strings(language)
        self._title.setText(s.register_title)
        self._intro.setText(s.register_intro)
        self._name_label.setText(s.register_label_name)
        self._dob_label.setText(s.register_label_dob)
        self._sex_label.setText(s.register_label_sex)
        self._sex_male.setText(s.register_label_sex_male)
        self._sex_female.setText(s.register_label_sex_female)
        self._sex_other.setText(s.register_label_sex_other)
        self._barangay_label.setText(s.register_label_barangay)
        self._phone_label.setText(
            f"{s.register_label_phone} {s.register_phone_optional}"
        )
        self._submit_button.setText(s.submit_button)
        self._reset_inputs()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reset_inputs(self) -> None:
        self._name_input.clear()
        # Mirror the constructor default — 30 years ago — so a
        # "Change language" round-trip lands on the same starting
        # state, not a hard-coded year that drifts off the typical
        # adult kiosk user as time passes.
        self._dob_input.setSelectedDate(QDate.currentDate().addYears(-30))
        self._barangay_input.setText(self._default_barangay)
        self._phone_input.clear()
        self._sex_group.setExclusive(False)
        for btn in (self._sex_male, self._sex_female, self._sex_other):
            btn.setChecked(False)
        self._sex_group.setExclusive(True)
        self._error_label.setVisible(False)
        self._error_label.clear()

    def _selected_sex(self) -> str | None:
        if self._sex_male.isChecked():
            return "M"
        if self._sex_female.isChecked():
            return "F"
        if self._sex_other.isChecked():
            return "O"
        return None

    def _on_submit_clicked(self) -> None:
        # `on_enter` set self._language; validation errors render in
        # the active language without re-threading it through.
        s = get_strings(self._language)
        errors: list[str] = []
        name = self._name_input.text().strip()
        if not name:
            errors.append(s.register_validation_name_required)
        sex = self._selected_sex()
        if sex is None:
            errors.append(s.register_validation_sex_required)
        barangay = self._barangay_input.text().strip()
        if not barangay:
            errors.append(s.register_validation_barangay_required)

        if errors:
            self._error_label.setText("\n".join(errors))
            self._error_label.setVisible(True)
            return

        assert sex is not None  # narrowed above
        dob_iso = self._dob_input.selectedDate().toString("yyyy-MM-dd")
        phone_raw = self._phone_input.text().strip()
        data = RegistrationData(
            full_name=name,
            dob_iso=dob_iso,
            sex=sex,
            barangay=barangay,
            phone=phone_raw or None,
        )
        self._error_label.setVisible(False)
        self.submitted.emit(data)
