"""Deterministic mock sensors for development and CI.

These return fixed, in-range readings so the FSM and audit pipeline
can be exercised on a laptop without any hardware. The values are
intentionally boring (mid-range "well" adult) so a developer running
the kiosk in mock mode immediately recognises the data as fake.
"""

from __future__ import annotations

from .base import (
    BaseAnthropometricSensor,
    BaseBloodPressureSensor,
    BasePulseOximeter,
    BaseRfidReader,
    BaseScale,
    BaseThermalCamera,
    BloodPressureReading,
    HeightReading,
    PulseOxReading,
    RfidScan,
    TemperatureReading,
    WeightReading,
)


class MockRfidReader(BaseRfidReader):
    def __init__(self, uid: str = "MOCK_CARD_0001") -> None:
        self._uid = uid

    def scan(self, timeout_seconds: float) -> RfidScan | None:
        return RfidScan(uid=self._uid)


class MockBloodPressureSensor(BaseBloodPressureSensor):
    def acquire(self) -> BloodPressureReading:
        return BloodPressureReading(systolic_mmhg=120.0, diastolic_mmhg=80.0)


class MockPulseOximeter(BasePulseOximeter):
    def read_once(self) -> PulseOxReading:
        return PulseOxReading(spo2_percent=98.0, heart_rate_bpm=72.0)


class MockThermalCamera(BaseThermalCamera):
    def read_once(self) -> TemperatureReading:
        return TemperatureReading(temperature_celsius=36.6)


class MockAnthropometricSensor(BaseAnthropometricSensor):
    def read_once(self) -> HeightReading:
        return HeightReading(height_cm=165.0)


class MockScale(BaseScale):
    def read_once(self) -> WeightReading:
        return WeightReading(weight_kg=68.0)
