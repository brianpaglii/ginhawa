"""Omron BP cuff sensor: SIG payload parsing + mock event publishing."""

from __future__ import annotations

import pytest

from ginhawa_kiosk.fsm import EventBus, MeasurementProposed
from sqlalchemy.orm import Session

from ginhawa_kiosk.sensors.omron_bp import (
    BpReading,
    MockOmronBp,
    OmronBpSensor,
    parse_bp_measurement,
    parse_sfloat16,
)


# Verifies SFLOAT-16 decode: small positive integers (mantissa with
# zero exponent) and one negative exponent case (10.0 = 100 * 10^-1).
# Mortality: would fail if the exponent or mantissa sign-extension
# were broken.
def test_parse_sfloat16_basic_cases() -> None:
    # 120 = mantissa 0x078, exponent 0 → raw 0x0078 → bytes (0x78, 0x00)
    assert parse_sfloat16(0x78, 0x00) == pytest.approx(120.0)
    # 80 = 0x050 → (0x50, 0x00)
    assert parse_sfloat16(0x50, 0x00) == pytest.approx(80.0)
    # 10.0 = mantissa 100 (0x064), exponent -1 (high nibble 0xF) →
    # raw 0xF064 → bytes (0x64, 0xF0). 100 * 10^-1 = 10.0
    assert parse_sfloat16(0x64, 0xF0) == pytest.approx(10.0)


# Verifies the SIG Blood Pressure Measurement parser extracts
# systolic / diastolic / MAP / pulse from a hardcoded payload that
# represents a normal reading. The flags byte sets bit 2 (pulse-rate
# present) but no time-stamp; the parser must skip the time-stamp
# field correctly.
# Mortality: would fail if the SFLOAT-16 parsing were broken or the
# optional-field offsets miscalculated.
def test_omron_bp_parses_sig_blood_pressure_measurement() -> None:
    payload = bytes(
        [
            0x04,  # flags: pulse-rate present, no time-stamp
            0x78,
            0x00,  # systolic = 120
            0x50,
            0x00,  # diastolic = 80
            0x5D,
            0x00,  # MAP = 93
            0x48,
            0x00,  # pulse = 72
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.systolic_mmhg == pytest.approx(120.0)
    assert reading.diastolic_mmhg == pytest.approx(80.0)
    assert reading.map_mmhg == pytest.approx(93.0)
    assert reading.pulse_bpm == pytest.approx(72.0)


# Verifies the parser correctly skips a 7-byte time-stamp field when
# flags bit 1 is set, then reads pulse from the right offset.
def test_omron_bp_parser_skips_optional_timestamp() -> None:
    payload = bytes(
        [
            0x06,  # flags: time-stamp + pulse-rate present
            0x78,
            0x00,  # systolic
            0x50,
            0x00,  # diastolic
            0x5D,
            0x00,  # MAP
            0xE8,
            0x07,  # year LSB-MSB = 2024 (0x07E8)
            0x05,  # month
            0x02,  # day
            0x0E,  # hour
            0x1E,  # minute
            0x00,  # second
            0x4B,
            0x00,  # pulse = 75
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.pulse_bpm == pytest.approx(75.0)


# Verifies the parser raises a clear error on a payload that's
# shorter than the 7-byte minimum (flags + 3 SFLOAT-16 fields).
def test_omron_bp_parser_rejects_truncated_payload() -> None:
    with pytest.raises(ValueError, match="too short"):
        parse_bp_measurement(bytes([0x04, 0x78, 0x00]))


# Verifies pulse_bpm is None when flags bit 2 is not set (no pulse
# field present). The cuff doesn't always report pulse.
def test_omron_bp_parser_returns_none_pulse_when_absent() -> None:
    payload = bytes(
        [
            0x00,  # flags: nothing optional
            0x78,
            0x00,
            0x50,
            0x00,
            0x5D,
            0x00,
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.pulse_bpm is None


# Verifies the parser raises when flags claim a pulse field but the
# payload is too short to contain one. Exercises the pulse-rate
# truncation guard (a separate branch from the minimum-length check).
def test_omron_bp_parser_rejects_truncated_pulse_field() -> None:
    payload = bytes(
        [
            0x04,  # pulse-rate present
            0x78,
            0x00,
            0x50,
            0x00,
            0x5D,
            0x00,
            # No pulse bytes follow.
        ]
    )
    with pytest.raises(ValueError, match="pulse-rate but is truncated"):
        parse_bp_measurement(payload)


# Verifies the mock's lifecycle. is_running flips correctly across
# start/stop and survives a no-pulse simulate_measurement call.
@pytest.mark.asyncio
async def test_mock_omron_bp_lifecycle(bus: EventBus) -> None:
    sensor = MockOmronBp(bus)
    assert sensor.is_running is False
    await sensor.start()
    assert sensor.is_running is True
    await sensor.stop()
    assert sensor.is_running is False


# Verifies the mock publishes one MeasurementProposed event per
# component (systolic / diastolic / pulse).
@pytest.mark.asyncio
async def test_mock_omron_bp_publishes_events(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    sensor = MockOmronBp(bus)
    await sensor.simulate_measurement(systolic=120, diastolic=80, pulse=72)

    types = [m.measurement_type for m in captured_measurements]
    assert types == ["systolic_bp", "diastolic_bp", "heart_rate"]
    by_type = {m.measurement_type: m for m in captured_measurements}
    assert by_type["systolic_bp"].value == pytest.approx(120)
    assert by_type["diastolic_bp"].value == pytest.approx(80)
    assert by_type["heart_rate"].value == pytest.approx(72)


# Verifies pulse is omitted from the published events when None.
@pytest.mark.asyncio
async def test_mock_omron_bp_omits_pulse_when_absent(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    sensor = MockOmronBp(bus)
    await sensor.simulate_measurement(systolic=120, diastolic=80)
    types = {m.measurement_type for m in captured_measurements}
    assert types == {"systolic_bp", "diastolic_bp"}


# Verifies BpReading is the right shape — small sanity test for the
# dataclass that downstream parsing depends on.
def test_bp_reading_dataclass_carries_all_fields() -> None:
    r = BpReading(systolic_mmhg=120, diastolic_mmhg=80, map_mmhg=93, pulse_bpm=72)
    assert r.systolic_mmhg == 120
    assert r.diastolic_mmhg == 80
    assert r.map_mmhg == 93
    assert r.pulse_bpm == 72


# Verifies SFLOAT-16 sign-extends a negative mantissa correctly.
# A mantissa of 0xFFE (= -2 after sign extension) with exponent 0
# should decode to -2.0. None of the BP path readings hit this in
# practice, but the parser must be correct end-to-end.
def test_parse_sfloat16_negative_mantissa() -> None:
    assert parse_sfloat16(0xFE, 0x0F) == pytest.approx(-2.0)


# Verifies OmronBpSensor's __init__ + is_running surface, and that
# _publish_reading routes a BpReading into three MeasurementProposed
# events (or two when pulse is None). Bypasses BLE entirely.
@pytest.mark.asyncio
async def test_omron_bp_sensor_publish_reading(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    sensor = OmronBpSensor(bus, db_session)
    assert sensor.is_running is False

    await sensor._publish_reading(
        BpReading(systolic_mmhg=120, diastolic_mmhg=80, map_mmhg=93, pulse_bpm=72)
    )
    types = [m.measurement_type for m in captured_measurements]
    assert types == ["systolic_bp", "diastolic_bp", "heart_rate"]

    # Without pulse: only two events, no heart_rate.
    captured_measurements.clear()
    await sensor._publish_reading(
        BpReading(systolic_mmhg=118, diastolic_mmhg=78, map_mmhg=91, pulse_bpm=None)
    )
    types = {m.measurement_type for m in captured_measurements}
    assert types == {"systolic_bp", "diastolic_bp"}
