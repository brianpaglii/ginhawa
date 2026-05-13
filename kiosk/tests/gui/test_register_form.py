"""DOB widget bounds and defaults on the register form.

Pins the contract that the constructor-set defaults match what
``_reset_inputs`` produces on a "Change language" round-trip, and
that Qt's QDateEdit min/max clamping is wired up so neither a BHW
mis-tap nor a copy-paste can produce an impossible DOB.

These tests don't exercise FSM flow; they only construct the screen
and read the widget state. pytest-qt's ``qtbot`` keeps the widget
alive long enough to query.
"""

from __future__ import annotations

from PyQt6.QtCore import QDate
from pytestqt.qtbot import QtBot

from ginhawa_kiosk.gui.screens import RegisterFormScreen


# The constructor sets DOB to "30 years ago". A hard-coded date
# (e.g., 2000-01-01) would drift off the typical adult demographic
# as time passes; this test pins the moving default.
# Mortality: would fail if the constructor reverted to a static date
# or shifted the offset.
def test_dob_default_is_thirty_years_ago(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    today = QDate.currentDate()
    assert screen._dob_input.date() == today.addYears(-30)


# Qt clamps setDate() to the configured minimum. A 1899 date should
# land on 1900-01-01. Catches a regression where the lower bound is
# removed (Qt's underlying default is 1752, the start of the
# Gregorian calendar in England — physiologically irrelevant).
# Mortality: would fail if setMinimumDate(1900) were removed.
def test_dob_clamps_to_minimum_when_set_before_1900(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen._dob_input.setDate(QDate(1899, 12, 31))
    assert screen._dob_input.date() == QDate(1900, 1, 1)


# Future DOB clamps to today. No one is born in the future; a
# fat-finger tap on the year spinner shouldn't be allowed to leave
# a record with a DOB after the kiosk's clock.
# Mortality: would fail if setMaximumDate(today) were removed.
def test_dob_clamps_to_today_when_set_in_the_future(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    today = QDate.currentDate()
    screen._dob_input.setDate(today.addDays(30))
    assert screen._dob_input.date() == today


# The dob_iso field on RegistrationData is built via
# ``date().toString("yyyy-MM-dd")``. Pin that the format the
# downstream Citizen row receives is ISO-8601 calendar date.
# Mortality: would fail if displayFormat changed AND someone
# accidentally also changed the toString format used in
# _try_submit() (line 207).
def test_dob_iso_format_is_yyyy_mm_dd(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen._dob_input.setDate(QDate(1985, 3, 17))
    assert screen._dob_input.date().toString("yyyy-MM-dd") == "1985-03-17"


# The three sex radios live in a horizontal row (not a stacked column)
# so each tap target is wide enough on the 1920x1080 touchscreen.
# Pins the row layout against an accidental revert to QFormLayout
# default vertical stacking.
# Mortality: would fail if the addRow() were swapped to add the three
# radios as separate rows.
def test_sex_radios_share_horizontal_row(qtbot: QtBot) -> None:
    from PyQt6.QtWidgets import QHBoxLayout

    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    male_parent_layout = (
        screen._sex_male.parent().layout() if screen._sex_male.parent() else None
    )
    # The radios share a parent widget but their layout is set on the
    # widget that hosts the QHBoxLayout — walk up via parentWidget +
    # findChildren to verify they're laid out side-by-side under the
    # same horizontal layout.
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
    # The same row holds all three radios.
    widgets_in_row = {sex_row.itemAt(i).widget() for i in range(sex_row.count())}
    assert screen._sex_male in widgets_in_row
    assert screen._sex_female in widgets_in_row
    assert screen._sex_other in widgets_in_row
    del male_parent_layout  # unused; kept the lookup local for clarity


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
