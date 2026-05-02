"""Abstract base for kiosk sensors.

All sensors implement this surface. Sensors are event-driven: they
push :class:`MeasurementProposed` (or :class:`RfidScanned`) events to
the event bus when relevant inputs arrive — they do NOT return values
to their caller, and they do NOT write directly to the database. The
event bus is the integration point with the rest of the kiosk; the
persistence layer subscribes and writes rows.

Each sensor has a mock implementation (for development on a laptop
without hardware) and a real implementation (for the Pi with hardware
connected). The factory in :mod:`ginhawa_kiosk.sensors.__init__`
selects between them based on ``Settings.MOCK_HARDWARE``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SensorUnavailable(Exception):
    """Raised when a sensor cannot complete its setup.

    Carries a short, log-safe message; does NOT carry sensor payloads
    (which may include patient identifiers).
    """


class Sensor(ABC):
    """Common base for all kiosk sensors."""

    @abstractmethod
    async def start(self) -> None:
        """Begin listening for events from the underlying device.

        Idempotent: calling start() on an already-running sensor is a
        no-op (or raises if the implementation can't safely no-op).
        """

    @abstractmethod
    async def stop(self) -> None:
        """Clean shutdown. Releases hardware handles and joins
        background threads. Idempotent."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True between successful ``start()`` and ``stop()``."""
