"""Physiological-range and unit validation for measurements.

Mirrors the cloud's ``ginhawa_cloud.api.schemas._MEASUREMENT_RANGES``
and ``ginhawa_cloud.api.measurements._EXPECTED_UNITS``. Kept
duplicated deliberately, NOT imported across the kiosk/cloud package
boundary — the two trees ship independently.

CONTRACT WITH CLOUD
-------------------
If the cloud changes any of the constants below, the kiosk MUST be
updated to match in the same release. A divergence would mean the
kiosk's local "valid" decision (``is_valid=1``) differs from the
cloud's, and the cloud's resync logic flips the kiosk's row to
``is_valid=0`` on upload — silently invalidating data the kiosk
showed to the citizen as in-range.

Refactoring this into a shared ``ginhawa-common`` package is Phase 4
work; for now, treat the constants below as the canonical kiosk-side
copy and update them whenever the cloud's equivalents move.

Out-of-range values are NOT a hard reject. The kiosk records them
with ``is_valid=0`` and a descriptive ``validation_notes`` so the
reading is preserved for diagnostic review (sensor calibration
drift, operator error). Same contract the cloud uses on
``/api/v1/sync/measurements``.
"""

from __future__ import annotations

from typing import NamedTuple


class ValidationResult(NamedTuple):
    is_valid: bool
    validation_notes: str | None


# Source of truth: cloud/src/ginhawa_cloud/api/schemas.py::_MEASUREMENT_RANGES
# (mirrored verbatim — see CONTRACT WITH CLOUD above).
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

# Source of truth: cloud/src/ginhawa_cloud/api/measurements.py::_EXPECTED_UNITS.
# Notes on specific entries:
#   * temperature accepts "C" and "°C" — both forms appear in BLE
#     payloads from different sensor firmware revisions.
#   * bmi accepts "kg/m^2", "kg/m²", and "" — derived values rarely
#     carry an explicit unit; the empty string is allowed for that
#     case.
_EXPECTED_UNITS: dict[str, frozenset[str]] = {
    "systolic_bp": frozenset({"mmHg"}),
    "diastolic_bp": frozenset({"mmHg"}),
    "spo2": frozenset({"%"}),
    "heart_rate": frozenset({"bpm"}),
    "temperature": frozenset({"C", "°C"}),
    "height": frozenset({"cm"}),
    "weight": frozenset({"kg"}),
    "bmi": frozenset({"kg/m^2", "kg/m²", ""}),
}


def validate_measurement(
    type: str,  # noqa: A002 — mirrors the schema column name
    value: float,
    unit: str,
) -> ValidationResult:
    """Validate a measurement against physiological range AND unit.

    Returns a :class:`ValidationResult` whose ``is_valid`` is True
    only if BOTH the value lies in range AND the unit is expected
    for the given type. When ``is_valid`` is False, ``validation_notes``
    holds a short, log-safe explanation of which check failed.

    Unknown measurement types fail validation (rather than silently
    pass) — this guards against typos at call sites that bypass type
    checking.
    """
    if type not in _RANGES:
        return ValidationResult(False, f"unknown measurement type {type!r}")

    expected_units = _EXPECTED_UNITS[type]
    if unit not in expected_units:
        # Render expected as a stable sorted list so the message is
        # deterministic across runs (useful for snapshot tests).
        expected_repr = ", ".join(sorted(expected_units))
        return ValidationResult(
            False,
            f"{type} unit must be {expected_repr}, got {unit!r}",
        )

    lo, hi = _RANGES[type]
    if not (lo <= value <= hi):
        return ValidationResult(
            False,
            f"{type} value {value} outside physiological range [{lo}, {hi}]",
        )

    return ValidationResult(True, None)
