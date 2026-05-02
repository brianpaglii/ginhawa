"""Sensor adapter abstract base classes.

Every sensor type the kiosk reads from is wrapped by an adapter that
inherits from one of the bases here. The adapter exposes a small,
typed surface (``acquire`` / ``read_once`` / ``scan``) so the FSM
treats real hardware and mocks identically.

All read methods raise ``SensorUnavailable`` when the hardware is
absent or returns an unrecoverable error. The session FSM treats
``SensorUnavailable`` as "record this measurement as unavailable and
continue" — it does NOT crash the session (CLAUDE.md, "Failure modes").
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class SensorUnavailable(Exception):
    """Raised when a sensor read or scan cannot complete.

    Carries a short, log-safe message; does NOT carry the underlying
    sensor payload (which may include patient identifiers).
    """


@dataclass(frozen=True)
class BloodPressureReading:
    systolic_mmhg: float
    diastolic_mmhg: float


@dataclass(frozen=True)
class PulseOxReading:
    spo2_percent: float
    heart_rate_bpm: float


@dataclass(frozen=True)
class TemperatureReading:
    temperature_celsius: float


@dataclass(frozen=True)
class HeightReading:
    height_cm: float


@dataclass(frozen=True)
class WeightReading:
    weight_kg: float


@dataclass(frozen=True)
class RfidScan:
    """One RFID card scan. ``uid`` is the raw UID string from the card."""

    uid: str


class BaseRfidReader(ABC):
    @abstractmethod
    def scan(self, timeout_seconds: float) -> RfidScan | None:
        """Block up to ``timeout_seconds``; return ``None`` if no card was tapped."""


class BaseBloodPressureSensor(ABC):
    @abstractmethod
    def acquire(self) -> BloodPressureReading:
        """Initiate the BP measurement and return the result."""


class BasePulseOximeter(ABC):
    @abstractmethod
    def read_once(self) -> PulseOxReading:
        """Take one stable SpO2 + heart-rate reading."""


class BaseThermalCamera(ABC):
    @abstractmethod
    def read_once(self) -> TemperatureReading:
        """Capture one centre-ROI peak temperature reading."""


class BaseAnthropometricSensor(ABC):
    """Height, via the VL53L0X time-of-flight sensor on ESP32-B."""

    @abstractmethod
    def read_once(self) -> HeightReading: ...


class BaseScale(ABC):
    """Weight, via the Xiaomi Smart Scale S200 BLE advertisement."""

    @abstractmethod
    def read_once(self) -> WeightReading: ...
