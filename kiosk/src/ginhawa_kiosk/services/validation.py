"""Physiological-range validation for measurements.

Mirrors the cloud's ``schemas._MEASUREMENT_RANGES`` — kept duplicated
deliberately, NOT imported across the kiosk/cloud boundary, because
the kiosk and cloud package trees are independent and one must not
take a transitive dep on the other.

Out-of-range values are NOT rejected. They are recorded with
``is_valid=0`` and a ``validation_notes`` string, matching the
contract enforced by ``/api/v1/sync/measurements`` on the cloud
side. This preserves the kiosk's clinical decision to capture even
implausible readings (calibration drift, operator error) for later
diagnostic review.
"""

from __future__ import annotations

_RANGES: dict[str, tuple[float, float]] = {
    "systolic_bp": (70.0, 250.0),
    "diastolic_bp": (40.0, 150.0),
    "spo2": (70.0, 100.0),
    "heart_rate": (30.0, 220.0),
    "temperature": (30.0, 45.0),
    "height": (80.0, 220.0),
    "weight": (20.0, 250.0),
    "bmi": (10.0, 60.0),
}


def validate_measurement(measurement_type: str, value: float) -> tuple[int, str | None]:
    """Return ``(is_valid, validation_notes)`` for a (type, value) pair.

    Unknown types are treated as invalid with an explanatory note;
    this guards against typos at call sites that bypass type-checking.
    """
    if measurement_type not in _RANGES:
        return 0, f"unknown measurement type {measurement_type!r}"
    lo, hi = _RANGES[measurement_type]
    if not (lo <= value <= hi):
        return 0, (
            f"{measurement_type} value {value} outside physiological range [{lo}, {hi}]"
        )
    return 1, None
