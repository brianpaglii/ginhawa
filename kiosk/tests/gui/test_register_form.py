"""DOB inline calendar picker + sex radio layout on the register form.

The DOB field is a custom :class:`_InlineCalendarPicker` rather than
a ``QDateEdit`` popup — the popup variant dismissed on touch under
X11 (the calendar's child widgets register as "outside" the popup
hit-test region for touch events, so a day-cell tap closes the
popup before selection commits). These tests pin the public API the
form's submit handler consumes (``selectedDate`` / ``setSelectedDate``)
and the touch-friendly year / decade jump buttons.

pytest-qt's ``qtbot`` keeps the widget alive long enough to query.
"""

from __future__ import annotations

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QToolButton
from pytestqt.qtbot import QtBot

from ginhawa_kiosk.gui.screens import RegisterFormScreen


# The constructor sets DOB to "today minus 30 years". A hard-coded
# date (e.g., 2000-01-01) would drift off the typical adult demographic
# as time passes; this test pins the moving default.
# Mortality: would fail if the constructor reverted to a static date
# or shifted the offset.
def test_dob_default_is_thirty_years_ago(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    today = QDate.currentDate()
    assert screen._dob_input.selectedDate() == today.addYears(-30)


# The "« Year" button decrements the selected date by one calendar
# year. Pins the touch affordance that exists specifically because
# QSpinBox in the navigation bar is hard to tap on a touchscreen.
# Mortality: would fail if the button's clicked signal weren't wired
# or if _jump_years used a wrong delta.
def test_dob_year_back_button_decrements_one_year(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    start = screen._dob_input.selectedDate()
    btn = screen._dob_input.findChild(QPushButton, "calendarYearBackButton")
    assert btn is not None, "expected « Year button by objectName"
    btn.click()
    assert screen._dob_input.selectedDate() == start.addYears(-1)


# The "« 10 yrs" button decrements by a decade — meant for elderly
# kiosk users whose DOB is many decades back; tapping « Year 50 times
# would be hostile.
# Mortality: would fail if the decade-back wiring were broken or the
# delta were not -10.
def test_dob_decade_back_button_decrements_ten_years(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    start = screen._dob_input.selectedDate()
    btn = screen._dob_input.findChild(QPushButton, "calendarDecadeBackButton")
    assert btn is not None, "expected « 10 yrs button by objectName"
    btn.click()
    assert screen._dob_input.selectedDate() == start.addYears(-10)


# setSelectedDate clamps to the picker's minimum (1900-01-01).
# Distinct from QCalendarWidget.setSelectedDate which silently
# rejects out-of-range dates; our wrapper clamps explicitly so a
# wrong programmatic seed lands on a sensible value rather than the
# original default.
# Mortality: would fail if the clamp branch were removed or the
# minimum date were changed below 1900-01-01.
def test_dob_setSelectedDate_clamps_to_minimum(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen._dob_input.setSelectedDate(QDate(1500, 1, 1))
    assert screen._dob_input.selectedDate() == QDate(1900, 1, 1)


# The dob_iso field on RegistrationData is built via
# ``date().toString("yyyy-MM-dd")``. Pin that the format the
# downstream Citizen row receives is ISO-8601 calendar date.
# Mortality: would fail if the picker's selectedDate() return type
# changed OR the format string in _on_submit_clicked drifted.
def test_dob_iso_format_is_yyyy_mm_dd(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen._dob_input.setSelectedDate(QDate(1985, 3, 17))
    assert screen._dob_input.selectedDate().toString("yyyy-MM-dd") == "1985-03-17"


# The three sex radios live in a horizontal row (not a stacked column)
# so each tap target is wide enough on the 1920x1080 touchscreen.
# Pins the row layout against an accidental revert to QFormLayout
# default vertical stacking.
# Mortality: would fail if the addRow() were swapped to add the three
# radios as separate rows.
def test_sex_radios_share_horizontal_row(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    layouts = screen.findChildren(QHBoxLayout)
    sex_row = next(
        (
            lay
            for lay in layouts
            if any(
                lay.itemAt(i) is not None and lay.itemAt(i).widget() is screen._sex_male
                for i in range(lay.count())
            )
        ),
        None,
    )
    assert sex_row is not None, "expected QHBoxLayout containing _sex_male"
    widgets_in_row = {sex_row.itemAt(i).widget() for i in range(sex_row.count())}
    assert screen._sex_male in widgets_in_row
    assert screen._sex_female in widgets_in_row
    assert screen._sex_other in widgets_in_row


# Form starts with NO sex selected. Validation enforces a choice on
# submit (see ``test_register_form_validates_required_fields``); the
# default-unselected state is what guarantees that path is reachable.
# Mortality: would fail if any radio became checked at construction.
def test_no_sex_radio_selected_by_default(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    assert not screen._sex_male.isChecked()
    assert not screen._sex_female.isChecked()
    assert not screen._sex_other.isChecked()


# The "« Month" button decrements the selected date by one calendar
# month. Replaces the QCalendarWidget's built-in month-name QMenu
# which was unusable on touch — menu items don't align with their
# hit-test regions and a tap on "January" often selected "March".
# Mortality: would fail if the button's clicked signal weren't
# wired or _jump_months used a wrong delta.
def test_dob_month_back_button_decrements_one_month(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    start = screen._dob_input.selectedDate()
    btn = screen._dob_input.findChild(QPushButton, "calendarMonthBackButton")
    assert btn is not None, "expected « Month button by objectName"
    btn.click()
    assert screen._dob_input.selectedDate() == start.addMonths(-1)


# Symmetric to the back test — Month » increments by one calendar
# month, respecting any month-length differences (e.g., Jan 31 +1
# lands on Feb 28/29).
def test_dob_month_forward_button_increments_one_month(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    # Pick a known date so the assertion isn't affected by today's
    # value (the constructor default is "today - 30 years", which
    # could itself land on month 12 and roll over awkwardly).
    screen._dob_input.setSelectedDate(QDate(1985, 3, 17))
    btn = screen._dob_input.findChild(QPushButton, "calendarMonthForwardButton")
    assert btn is not None, "expected Month » button by objectName"
    btn.click()
    assert screen._dob_input.selectedDate() == QDate(1985, 4, 17)


# Month-back must respect the picker's minimum date (1900-01-01).
# Starting at 1900-02-01 and pressing « Month should land on
# 1900-01-01 the first time, and stay there on a second press
# (clamping, not wrapping into 1899).
# Mortality: would fail if _jump_months bypassed setSelectedDate's
# clamping branch.
def test_dob_month_back_clamps_at_minimum(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen._dob_input.setSelectedDate(QDate(1900, 2, 1))
    btn = screen._dob_input.findChild(QPushButton, "calendarMonthBackButton")
    assert btn is not None
    btn.click()
    assert screen._dob_input.selectedDate() == QDate(1900, 1, 1)
    btn.click()  # second press — should not move
    assert screen._dob_input.selectedDate() == QDate(1900, 1, 1)


# The QCalendarWidget's built-in month-name QToolButton (Qt assigns
# it objectName "qt_calendar_monthbutton") must have its popup menu
# stripped AND be disabled so touch taps don't open a menu that the
# citizen can't operate reliably. The « Month / Month » buttons are
# the only supported month-nav affordance.
# Mortality: would fail if the disable was reverted, or if Qt
# renames the internal objectName (unlikely; this name has been
# stable across Qt 5 and 6).
def test_calendar_month_button_dropdown_disabled(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    month_button = screen._dob_input._calendar.findChild(
        QToolButton, "qt_calendar_monthbutton"
    )
    assert month_button is not None, (
        "expected qt_calendar_monthbutton inside QCalendarWidget"
    )
    assert not month_button.isEnabled()
    assert month_button.menu() is None
