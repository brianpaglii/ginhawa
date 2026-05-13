"""Temperature live-preview + capture button on MEASURING_VITALS.

The MLX90640 streams continuously regardless of whether the citizen
has positioned the sensor on their forehead, so the kiosk shows a
live "Current: X" preview and only persists when the citizen taps
Capture. These tests pin the screen's public contract — set live
value, button enables, capture freezes display + emits signal,
recapture re-emits with the latest live reading, on_enter resets.

pytest-qt's ``qtbot`` keeps the widget alive long enough to drive
the button click and read back internal state.
"""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ginhawa_kiosk.gui.screens import MeasuringVitalsScreen


# No live updates yet → capture button disabled. Pins the
# initial-state contract that protects the FSM from receiving a
# 0.0 °C MeasurementProposed if the citizen taps the button before
# the ESP32 has published anything.
# Mortality: would fail if the constructor enabled the button
# eagerly or if a stale captured value carried over from a previous
# instance.
def test_capture_button_disabled_initially(qtbot: QtBot) -> None:
    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    assert not screen._temp_capture_button.isEnabled()


# First live update arrives → capture button enables. ESP32-A
# publishes every ~3-5 s, so the citizen has plenty of room to
# wait for one before tapping.
# Mortality: would fail if set_live_temperature didn't toggle the
# button or if the toggle was gated on an extra condition.
def test_set_live_temperature_enables_button(qtbot: QtBot) -> None:
    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_live_temperature(36.7, "C")
    assert screen._temp_capture_button.isEnabled()


# Live value renders in the display with one decimal and the °C
# suffix. The display unit is hard-coded (the wire unit is "C") so
# a future firmware unit-string drift doesn't silently render
# "36.7 K" on the kiosk.
def test_set_live_temperature_updates_display(qtbot: QtBot) -> None:
    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_live_temperature(36.7, "C")
    assert "36.7" in screen._temp_live_label.text()
    assert "°C" in screen._temp_live_label.text()


# Capture tap freezes the display: a subsequent live update updates
# the internal _live_temperature_value (so the next recapture has a
# fresh value to grab) but does NOT touch the on-screen label, so
# the citizen sees their committed reading until they tap Recapture.
# Mortality: would fail if the freeze branch were removed from
# set_live_temperature.
def test_capture_freezes_display(qtbot: QtBot) -> None:
    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_live_temperature(36.7, "C")
    received: list[float] = []
    screen.capture_temperature_requested.connect(received.append)
    screen._temp_capture_button.click()
    assert received == [36.7]
    # Display now reads "Captured: 36.7 °C ✓"
    assert "36.7" in screen._temp_live_label.text()
    assert "✓" in screen._temp_live_label.text()
    # New live update arrives — display must NOT change.
    screen.set_live_temperature(38.0, "C")
    assert "36.7" in screen._temp_live_label.text()
    assert "38.0" not in screen._temp_live_label.text()
    # But the internal tracker did advance, so a Recapture would
    # pick up the new value.
    assert screen._live_temperature_value == 38.0


# After a capture, a second tap (the button now reads "Recapture
# Temperature") re-emits the signal with whatever the latest live
# value is — citizens who need a second take after moving the
# sensor get a fresh capture without re-entering the screen.
# Mortality: would fail if recapture were a no-op or required a
# separate state reset method.
def test_recapture_captures_latest_live_value(qtbot: QtBot) -> None:
    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_live_temperature(36.7, "C")
    received: list[float] = []
    screen.capture_temperature_requested.connect(received.append)
    screen._temp_capture_button.click()
    # Sensor moves; new live values arrive while display is frozen.
    screen.set_live_temperature(37.2, "C")
    # Recapture
    screen._temp_capture_button.click()
    assert received == [36.7, 37.2]
    assert "37.2" in screen._temp_live_label.text()


# on_enter is the screen's reset hook: every entry (initial mount,
# language switch, re-entry from elsewhere) clears any prior capture
# and disables the button so the next citizen's session starts from
# a clean slate.
# Mortality: would fail if on_enter forgot to clear any of the four
# pieces of state (_live_temperature_value, _captured_temperature,
# the label text, the button enabled flag).
def test_on_enter_resets_temperature_state(qtbot: QtBot) -> None:
    screen = MeasuringVitalsScreen()
    qtbot.addWidget(screen)
    screen.on_enter("en")
    screen.set_live_temperature(36.7, "C")
    screen._temp_capture_button.click()
    # New session starts.
    screen.on_enter("en")
    assert screen._live_temperature_value is None
    assert screen._captured_temperature is None
    assert "—" in screen._temp_live_label.text()
    assert not screen._temp_capture_button.isEnabled()
