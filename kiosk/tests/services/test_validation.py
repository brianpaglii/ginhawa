"""Physiological-range and unit validation."""

from __future__ import annotations

import pytest

from ginhawa_kiosk.services.validation import ValidationResult, validate_measurement


# Verifies an in-range systolic with the expected unit passes.
# Mortality: would fail if the range constant for systolic_bp moved
# above 120.0 (lower bound) or below 120.0 (upper bound).
def test_systolic_in_range_passes() -> None:
    result = validate_measurement("systolic_bp", 120.0, "mmHg")
    assert result == ValidationResult(True, None)


# Verifies a value above the systolic upper bound fails with a
# descriptive note that names the offending value AND the range.
# Mortality: would fail if the upper-bound range constant for
# systolic_bp were raised above 300.0, or if the validation_notes
# string format dropped the bracketed range.
def test_systolic_above_range_fails() -> None:
    result = validate_measurement("systolic_bp", 300.0, "mmHg")
    assert result.is_valid is False
    assert result.validation_notes is not None
    assert "300.0" in result.validation_notes
    assert "[70.0, 250.0]" in result.validation_notes


# Verifies the lower-boundary check for systolic. 70.0 is the lower
# bound and MUST pass; 69.9 MUST fail.
# Mortality: would fail if the lower-bound comparison used `>`
# instead of `>=` (silently invalidating exactly-on-boundary
# readings) or if the constant moved.
def test_systolic_lower_boundary() -> None:
    assert validate_measurement("systolic_bp", 70.0, "mmHg").is_valid is True
    assert validate_measurement("systolic_bp", 69.9, "mmHg").is_valid is False


# Per-type range coverage. Each row asserts that the lower bound is
# accepted, the upper bound is accepted, and one above-upper value
# is rejected. Centralised here so range changes across types
# trigger one obvious test failure rather than nine.
@pytest.mark.parametrize(
    "type_name, unit, lo, hi, above",
    [
        ("diastolic_bp", "mmHg", 40.0, 150.0, 200.0),
        ("spo2", "%", 70.0, 100.0, 110.0),
        ("heart_rate", "bpm", 30.0, 220.0, 250.0),
        ("temperature", "C", 30.0, 45.0, 50.0),
        ("height", "cm", 80.0, 220.0, 250.0),
        ("weight", "kg", 20.0, 250.0, 300.0),
        ("bmi", "", 10.0, 60.0, 99.0),
    ],
)
def test_per_type_range_boundaries(
    type_name: str, unit: str, lo: float, hi: float, above: float
) -> None:
    assert validate_measurement(type_name, lo, unit).is_valid is True
    assert validate_measurement(type_name, hi, unit).is_valid is True
    out_of_range = validate_measurement(type_name, above, unit)
    assert out_of_range.is_valid is False
    assert (
        out_of_range.validation_notes is not None
        and f"[{lo}, {hi}]" in out_of_range.validation_notes
    )


# Verifies a unit mismatch fails validation with a note that names
# both the offending unit and the expected one(s). Tested here with
# Fahrenheit for body temperature — a real foot-gun on consumer
# thermometers.
# Mortality: would fail if the unit check were dropped, or if the
# expected-units mapping for temperature lost "C".
def test_unit_mismatch_fails() -> None:
    result = validate_measurement("temperature", 36.5, "F")
    assert result.is_valid is False
    assert result.validation_notes is not None
    assert "must be" in result.validation_notes
    assert "C" in result.validation_notes
    assert "'F'" in result.validation_notes


# Verifies temperature accepts both "C" and "°C" — different sensor
# firmwares emit different forms in their BLE payloads, so both
# must validate.
# Mortality: would fail if the expected-units set for temperature
# dropped either form (kiosk would reject readings the sensor
# legitimately produced).
def test_temperature_accepts_both_celsius_forms() -> None:
    assert validate_measurement("temperature", 36.5, "C").is_valid is True
    assert validate_measurement("temperature", 36.5, "°C").is_valid is True


# Verifies bmi accepts the empty-unit form (derived values rarely
# carry an explicit unit; the BMI calculation produces a unitless
# number and downstream code passes "" through unchanged).
# Mortality: would fail if "" were dropped from the BMI expected-
# units set.
def test_bmi_accepts_empty_unit() -> None:
    assert validate_measurement("bmi", 22.0, "").is_valid is True
    assert validate_measurement("bmi", 22.0, "kg/m^2").is_valid is True
    assert validate_measurement("bmi", 22.0, "kg/m²").is_valid is True


# Verifies an unknown measurement type fails fast with a note that
# names the offending value. This guards against typos at call sites
# that bypass type checking (e.g., a string built from a stale
# config). Mortality: would fail if the unknown-type branch were
# replaced with a silent pass.
def test_unknown_type_fails() -> None:
    result = validate_measurement("blood_alcohol", 0.05, "mg/dL")
    assert result.is_valid is False
    assert result.validation_notes is not None
    assert "blood_alcohol" in result.validation_notes
