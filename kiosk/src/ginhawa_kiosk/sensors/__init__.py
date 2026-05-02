"""Sensor adapter registry.

Each sensor type defines an abstract base class plus a mock
implementation here; the production implementation lives in a sibling
module and is imported only when ``Settings.MOCK_HARDWARE is False``.
A factory chooses between them at construction time.
"""

from .base import (
    BaseAnthropometricSensor,
    BaseBloodPressureSensor,
    BasePulseOximeter,
    BaseRfidReader,
    BaseScale,
    BaseThermalCamera,
)
from .factory import build_sensor_set

__all__ = [
    "BaseAnthropometricSensor",
    "BaseBloodPressureSensor",
    "BasePulseOximeter",
    "BaseRfidReader",
    "BaseScale",
    "BaseThermalCamera",
    "build_sensor_set",
]
