"""Sensor factory — the only place ``MOCK_HARDWARE`` is consulted.

When ``MOCK_HARDWARE=True`` we return the mock implementations. When
False, we will eventually instantiate the real BLE / MQTT / SPI
adapters. Until those land we deliberately raise ``NotImplementedError``
so a misconfigured production deployment fails loud rather than
silently using mocks.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import Settings
from .base import (
    BaseAnthropometricSensor,
    BaseBloodPressureSensor,
    BasePulseOximeter,
    BaseRfidReader,
    BaseScale,
    BaseThermalCamera,
)
from .mock import (
    MockAnthropometricSensor,
    MockBloodPressureSensor,
    MockPulseOximeter,
    MockRfidReader,
    MockScale,
    MockThermalCamera,
)


@dataclass(frozen=True)
class SensorSet:
    rfid: BaseRfidReader
    blood_pressure: BaseBloodPressureSensor
    pulse_oximeter: BasePulseOximeter
    thermal_camera: BaseThermalCamera
    anthropometric: BaseAnthropometricSensor
    scale: BaseScale


def build_sensor_set(settings: Settings) -> SensorSet:
    """Return a SensorSet appropriate for the runtime mode.

    Production sensor adapters are not yet implemented; we raise
    NotImplementedError on ``MOCK_HARDWARE=False`` rather than
    silently falling back to mocks. This is deliberate — failing
    loud at startup beats a clinic discovering at deploy time that
    the BP cuff was never being read.
    """
    if settings.MOCK_HARDWARE:
        return SensorSet(
            rfid=MockRfidReader(),
            blood_pressure=MockBloodPressureSensor(),
            pulse_oximeter=MockPulseOximeter(),
            thermal_camera=MockThermalCamera(),
            anthropometric=MockAnthropometricSensor(),
            scale=MockScale(),
        )
    raise NotImplementedError(
        "Production sensor adapters land in a later prompt. Set "
        "MOCK_HARDWARE=true for development; do NOT remove this guard "
        "to ship — a kiosk with mock sensors in production would record "
        "fake data."
    )
