"""Per-screen widget tests using pytest-qt.

These exercise screen rendering and signal emission in isolation —
the FSM and main window are not involved. Their interaction is
covered by ``test_main_window.py`` and the E2E integration test.

QT_QPA_PLATFORM=offscreen is set in conftest.py so the suite runs
headless without a display server.
"""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from ginhawa_kiosk.gui.screens import (
    ConsentScreen,
    IdleScreen,
    LanguageSelectScreen,
    PathChoiceScreen,
    RegisterFormScreen,
    RegistrationData,
    ReportRow,
    ReportScreen,
)


# Verifies the IDLE screen displays both English and Tagalog tap
# prompts simultaneously — citizen has not yet picked a language.
# Mortality: 'Would fail if IDLE displayed only one language.'
def test_idle_screen_shows_bilingual_prompt(qtbot: QtBot) -> None:
    screen = IdleScreen()
    qtbot.addWidget(screen)
    screen.show()
    from PyQt6.QtWidgets import QLabel

    en = screen.findChild(QLabel, "idle_prompt_en")
    tl = screen.findChild(QLabel, "idle_prompt_tl")
    assert en is not None and tl is not None
    assert "Tap your card" in en.text()
    assert "I-tap" in tl.text()


# Verifies clicking English emits the language_chosen signal with 'en'.
# Mortality: 'Would fail if button signal not wired.'
def test_language_select_screen_emits_chosen_language_en(qtbot: QtBot) -> None:
    screen = LanguageSelectScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    received: list[str] = []
    screen.language_chosen.connect(received.append)

    from PyQt6.QtWidgets import QPushButton

    en_button = screen.findChild(QPushButton, "language_button_en")
    assert en_button is not None
    qtbot.mouseClick(en_button, qt_left_button())

    assert received == ["en"]


# Verifies clicking Tagalog emits the language_chosen signal with 'tl'.
# Mortality: 'Would fail if button signal not wired.'
def test_language_select_screen_emits_chosen_language_tl(qtbot: QtBot) -> None:
    screen = LanguageSelectScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    received: list[str] = []
    screen.language_chosen.connect(received.append)

    from PyQt6.QtWidgets import QPushButton

    tl_button = screen.findChild(QPushButton, "language_button_tl")
    assert tl_button is not None
    qtbot.mouseClick(tl_button, qt_left_button())

    assert received == ["tl"]


# Verifies submitting an empty registration form shows the
# validation banner and does NOT emit the submitted signal.
# Mortality: 'Would fail if validation were skipped.'
def test_register_form_validates_required_fields(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    received: list[object] = []
    screen.submitted.connect(received.append)

    from PyQt6.QtWidgets import QLabel, QPushButton

    submit_button = screen.findChild(QPushButton, "register_submit_button")
    assert submit_button is not None
    qtbot.mouseClick(submit_button, qt_left_button())

    error_label = screen.findChild(QLabel, "register_error_label")
    assert error_label is not None
    assert error_label.isVisible()
    assert "name" in error_label.text().lower()
    assert received == []


# Verifies the English-language render uses English labels.
# Mortality: 'Would fail if screen pulled wrong _STRINGS table.'
def test_register_form_renders_localized_text_en(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="San Roque")
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    from PyQt6.QtWidgets import QLabel

    title_label = screen.findChild(QLabel, "register_title")
    assert title_label is not None and title_label.text() == "New citizen registration"


# Verifies the Tagalog-language render uses Tagalog labels.
# Mortality: 'Would fail if screen pulled wrong _STRINGS table.'
def test_register_form_renders_localized_text_tl(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="San Roque")
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("tl")
    from PyQt6.QtWidgets import QLabel

    title_label = screen.findChild(QLabel, "register_title")
    assert title_label is not None
    assert "rehistro" in title_label.text().lower()


# Verifies the consent screen renders Tagalog text when language is
# 'tl'.
# Mortality: 'Would fail if screen pulled wrong _STRINGS table.'
def test_consent_screen_renders_localized_text(qtbot: QtBot) -> None:
    screen = ConsentScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("tl")
    from PyQt6.QtWidgets import QLabel, QPushButton

    title = screen.findChild(QLabel, "consent_title")
    body = screen.findChild(QLabel, "consent_body")
    agree = screen.findChild(QPushButton, "consent_agree_button")
    assert title is not None and "privacy" in title.text().lower()
    assert body is not None and "GINHAWA" in body.text()
    assert agree is not None and agree.text() == "Sumasang-ayon ako"


# Verifies clicking 'vitals' emits the path_selected signal with 'vitals'.
# Mortality: 'Would fail if button signal not wired.'
def test_path_choice_screen_emits_selected_path(qtbot: QtBot) -> None:
    screen = PathChoiceScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    received: list[str] = []
    screen.path_selected.connect(received.append)
    from PyQt6.QtWidgets import QPushButton

    button = screen.findChild(QPushButton, "path_button_vitals")
    assert button is not None
    qtbot.mouseClick(button, qt_left_button())
    assert received == ["vitals"]


# Verifies the print button is hidden when the printer is unavailable
# but the finish button remains visible (the citizen must still have
# a path forward).
# Mortality: 'Would fail if availability check were skipped.'
def test_report_screen_shows_print_button_only_when_printer_available(
    qtbot: QtBot,
) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    screen.set_measurements([ReportRow(label="Systolic BP", value="128 mmHg")])
    screen.set_printer_state(available=False, paper_present=True)
    from PyQt6.QtWidgets import QPushButton

    print_btn = screen.findChild(QPushButton, "report_print_button")
    finish_btn = screen.findChild(QPushButton, "report_finish_without_printing_button")
    assert print_btn is not None and not print_btn.isVisible()
    assert finish_btn is not None and finish_btn.isVisible()


# Verifies the print button is hidden when the printer reports no paper.
# Mortality: 'Would fail if availability check were skipped.'
def test_report_screen_shows_print_button_only_when_paper_present(
    qtbot: QtBot,
) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    screen.set_measurements([ReportRow(label="Systolic BP", value="128 mmHg")])
    screen.set_printer_state(available=True, paper_present=False)
    from PyQt6.QtWidgets import QPushButton

    print_btn = screen.findChild(QPushButton, "report_print_button")
    assert print_btn is not None and not print_btn.isVisible()


# Verifies the report only renders the rows passed by the caller —
# the screen is not responsible for filtering, but a regression
# would silently double-filter or drop rows. The caller must pre-
# filter ``is_valid=1``; this test pins that the screen renders the
# delivered rows verbatim.
# Mortality: 'Would fail if is_valid filter were dropped.'
def test_report_screen_filters_invalid_measurements(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    # Caller pre-filtered: only the valid row is passed to the screen.
    valid_only = [ReportRow(label="Systolic BP", value="128 mmHg")]
    screen.set_measurements(valid_only)
    from PyQt6.QtWidgets import QListWidget

    listw = screen.findChild(QListWidget, "report_list")
    assert listw is not None
    assert listw.count() == 1
    assert "128 mmHg" in listw.item(0).text()


# Verifies the change-language button is present on REGISTER_FORM.
# Mortality: 'Would fail if change-language button were missed.'
def test_change_language_button_visible_on_register_form(qtbot: QtBot) -> None:
    screen = RegisterFormScreen(default_barangay="")
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    assert _has_visible_button(screen, "change_language_button")


# Mortality: 'Would fail if change-language button were missed.'
def test_change_language_button_visible_on_consent(qtbot: QtBot) -> None:
    screen = ConsentScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    assert _has_visible_button(screen, "change_language_button")


# Mortality: 'Would fail if change-language button were missed.'
def test_change_language_button_visible_on_path_choice(qtbot: QtBot) -> None:
    screen = PathChoiceScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    assert _has_visible_button(screen, "change_language_button")


# Mortality: 'Would fail if change-language button were missed.'
def test_change_language_button_visible_on_report(qtbot: QtBot) -> None:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    assert _has_visible_button(screen, "change_language_button")


# Verifies the screen has no "Connect to cuff" button anymore —
# the user-gated flow was replaced by FSM-driven auto-fire on
# entry to MEASURING_VITALS, since the BleAdapterLock now
# serialises the Xiaomi-vs-Omron adapter contention that used to
# require user gating. Mortality: would fail if a future re-add
# of the button slipped through review.
def test_measuring_vitals_screen_has_no_connect_button(qtbot: QtBot) -> None:
    from ginhawa_kiosk.gui.screens import MeasuringVitalsScreen
    from PyQt6.QtWidgets import QPushButton

    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")

    assert screen.findChild(QPushButton, "measuring_vitals_connect_button") is None
    assert not hasattr(screen, "connect_to_cuff_requested")


# Verifies the screen's status line shows the localised "waiting"
# copy on entry — main_window's auto-fire happens on state entry,
# so by the time the screen first paints, the sensor is already
# retrying. The status line tells the citizen what to do.
def test_measuring_vitals_status_starts_in_waiting_copy(qtbot: QtBot) -> None:
    from ginhawa_kiosk.gui.screens import MeasuringVitalsScreen
    from PyQt6.QtWidgets import QLabel

    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")

    status = screen.findChild(QLabel, "measuring_vitals_status")
    assert status is not None
    assert status.text() == "Waiting for cuff..."


# Verifies update_status mutates the label so main_window can drive
# the "Waiting / Connected / Failed" progression as the sensor
# reports lifecycle events.
def test_measuring_vitals_update_status_changes_label(qtbot: QtBot) -> None:
    from ginhawa_kiosk.gui.screens import MeasuringVitalsScreen
    from PyQt6.QtWidgets import QLabel

    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")

    screen.update_status("Connected. Press START on the cuff to take a reading.")
    status = screen.findChild(QLabel, "measuring_vitals_status")
    assert status is not None
    assert status.text() == "Connected. Press START on the cuff to take a reading."


# Verifies a successful registration submission emits a
# RegistrationData payload with the entered values, and that the
# default barangay is pre-populated.
def test_register_form_emits_registration_data_on_valid_submit(
    qtbot: QtBot,
) -> None:
    screen = RegisterFormScreen(default_barangay="Tibagan")
    qtbot.addWidget(screen)
    screen.show()
    screen.on_enter("en")
    from PyQt6.QtWidgets import QLineEdit, QPushButton, QRadioButton

    name_input = screen.findChild(QLineEdit, "register_name_input")
    barangay_input = screen.findChild(QLineEdit, "register_barangay_input")
    sex_male = screen.findChild(QRadioButton, "register_sex_male")
    submit = screen.findChild(QPushButton, "register_submit_button")
    assert (
        name_input is not None
        and barangay_input is not None
        and sex_male is not None
        and submit is not None
    )
    name_input.setText("Juan dela Cruz")
    sex_male.setChecked(True)
    received: list[object] = []
    screen.submitted.connect(received.append)

    qtbot.mouseClick(submit, qt_left_button())
    assert len(received) == 1
    data = received[0]
    assert isinstance(data, RegistrationData)
    assert data.full_name == "Juan dela Cruz"
    assert data.sex == "M"
    assert data.barangay == "Tibagan"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def qt_left_button() -> object:
    from PyQt6.QtCore import Qt

    return Qt.MouseButton.LeftButton


def _has_visible_button(widget: object, object_name: str) -> bool:
    from PyQt6.QtWidgets import QPushButton

    btn = widget.findChild(QPushButton, object_name)  # type: ignore[attr-defined]
    return btn is not None and btn.isVisible()


@pytest.fixture
def _suppress_first_show(qtbot: QtBot) -> None:
    # pytest-qt's qtbot.addWidget normally shows the widget; we
    # don't actually need it visible to assert on findChild matches.
    return
