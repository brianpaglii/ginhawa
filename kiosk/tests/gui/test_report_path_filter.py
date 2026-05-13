"""REPORT screen path filtering: vitals_only / anthropometric_only /
full_check hide rows that don't belong to the citizen's chosen path.

The motivating case (Problem 2 in the wider bug list): a Xiaomi scale
that prefires during a vitals_only session writes a stray weight row
to the DB. The DB keeps the row for audit, but the citizen-facing
report must not show it — the citizen never asked for an anthro
measurement and seeing one is confusing on the receipt.

These tests pin the filter at the screen boundary so the contract
holds regardless of whether ``main_window`` ever passes a stray row.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QListWidget
from pytestqt.qtbot import QtBot

from ginhawa_kiosk.gui.screens import ReportRow, ReportScreen


def _list_texts(screen: ReportScreen) -> list[str]:
    listw = screen.findChild(QListWidget, "report_list")
    assert listw is not None
    return [listw.item(i).text() for i in range(listw.count())]


def _make_rows() -> list[ReportRow]:
    return [
        ReportRow(
            label="Systolic BP", value="128 mmHg", measurement_type="systolic_bp"
        ),
        ReportRow(
            label="Diastolic BP", value="82 mmHg", measurement_type="diastolic_bp"
        ),
        ReportRow(label="Heart rate", value="72 bpm", measurement_type="heart_rate"),
        ReportRow(label="SpO2", value="97 %", measurement_type="spo2"),
        ReportRow(label="Temperature", value="36.7 C", measurement_type="temperature"),
        ReportRow(label="Weight", value="65.4 kg", measurement_type="weight"),
        ReportRow(label="Height", value="170.0 cm", measurement_type="height"),
    ]


# Vitals-only path hides every anthropometric row. The citizen who
# picked "Vitals Only" must not see weight or height on their report,
# even if those rows exist in the DB.
# Mortality: would fail if the filter were removed or if vitals_only
# fell through to "show everything".
def test_vitals_only_hides_anthropometric_rows(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_measurements(_make_rows(), measurement_path="vitals")
    texts = _list_texts(screen)
    assert any("Systolic BP" in t for t in texts)
    assert any("SpO2" in t for t in texts)
    assert any("Temperature" in t for t in texts)
    assert all("Weight" not in t for t in texts)
    assert all("Height" not in t for t in texts)


# Anthro-only path hides every vitals row. Mirror image of the
# previous test.
# Mortality: would fail if the filter were removed or if
# anthropometric fell through to "show everything".
def test_anthropometric_only_hides_vitals_rows(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_measurements(_make_rows(), measurement_path="anthropometric")
    texts = _list_texts(screen)
    assert any("Weight" in t for t in texts)
    assert any("Height" in t for t in texts)
    for vitals_label in (
        "Systolic BP",
        "Diastolic BP",
        "Heart rate",
        "SpO2",
        "Temperature",
    ):
        assert all(vitals_label not in t for t in texts), (
            f"{vitals_label!r} should be hidden on anthro-only path"
        )


# Full path renders every row — the existing (pre-filter) behavior
# stays intact for citizens who pick the full check.
# Mortality: would fail if "full" were misclassified as a single-path
# variant and dropped rows.
def test_full_check_shows_all_rows(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    rows = _make_rows()
    screen.set_measurements(rows, measurement_path="full")
    texts = _list_texts(screen)
    assert len(texts) == len(rows)


# None path (e.g., the FSM hasn't set Session.measurement_path yet,
# or a legacy caller passes nothing) shows every row, preserving the
# pre-fix behavior and keeping the existing test_screens.py tests
# (which call ``set_measurements(rows)`` without a path) green.
# Mortality: would fail if the default ever flipped to "filter
# everything".
def test_no_path_shows_all_rows(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    rows = _make_rows()
    screen.set_measurements(rows)
    texts = _list_texts(screen)
    assert len(texts) == len(rows)


# Problem 2 case: a stray weight reading lands in the DB during a
# vitals_only session because the scale prefired. The report must
# show only the vitals; the stray weight is invisible to the citizen
# even though it persists for audit.
# Mortality: would fail if the screen rendered every passed-in row
# regardless of path.
def test_stray_anthropometric_row_hidden_on_vitals_path(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    rows = [
        ReportRow(label="SpO2", value="97 %", measurement_type="spo2"),
        # The stray reading that Problem 2 tracks.
        ReportRow(label="Weight", value="65.4 kg", measurement_type="weight"),
    ]
    screen.set_measurements(rows, measurement_path="vitals")
    texts = _list_texts(screen)
    assert any("SpO2" in t for t in texts)
    assert all("Weight" not in t for t in texts)


# When the filter empties the visible list (e.g., an anthropometric_
# only session whose anthro measurements all failed validation, so
# only is_valid=1 vitals placeholders survived), the screen's
# "no measurements" message must show. This pins the empty-state
# branch on post-filter row count, not pre-filter.
# Mortality: would fail if the visibility toggle ran against the
# unfiltered input.
def test_empty_after_filter_shows_no_measurements_label(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    rows = [
        ReportRow(label="SpO2", value="97 %", measurement_type="spo2"),
    ]
    screen.set_measurements(rows, measurement_path="anthropometric")
    listw = screen.findChild(QListWidget, "report_list")
    assert listw is not None
    assert not listw.isVisible()
    from PyQt6.QtWidgets import QLabel

    no_meas = screen.findChild(QLabel, "report_no_measurements")
    assert no_meas is not None and no_meas.isVisible()
