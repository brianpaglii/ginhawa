"""Omron HEM-7155T blood-pressure cuff sensor.

The HEM-7155T implements the Bluetooth SIG Blood Pressure Service
(UUID 0x1810). Unlike the Xiaomi scale we don't need a vendor-
specific library: we connect with bleak, subscribe to notifications
on the Blood Pressure Measurement characteristic (0x2A35), receive a
single notification per measurement, and parse it per the SIG
specification.

Protocol summary (Bluetooth SIG Blood Pressure Measurement, 0x2A35):

* Byte 0: flags
    bit 0 — units (0 = mmHg, 1 = kPa)
    bit 1 — time-stamp present
    bit 2 — pulse-rate present
    bit 3 — user-id present
    bit 4 — measurement-status present
* Bytes 1-2: systolic   (IEEE 11073 SFLOAT-16, little-endian)
* Bytes 3-4: diastolic  (SFLOAT-16)
* Bytes 5-6: MAP        (SFLOAT-16)
* Optional fields follow in order: time-stamp (7 bytes), pulse-rate
  (SFLOAT-16, 2 bytes), user-id (1 byte), measurement-status
  (2 bytes).

CRITICAL: per CLAUDE.md "Hardware safety", we never write to the
HEM-7155T EEPROM. This implementation only subscribes to
notifications — it issues no write commands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DeviceConfig
from ..fsm.event_bus import (
    BpMeasurementRequested,
    EventBus,
    MeasurementProposed,
)
from .base import Sensor, SensorUnavailable


_BP_MEASUREMENT_CHAR_UUID = "00002a35-0000-1000-8000-00805f9b34fb"
_CUFF_MAC_CONFIG_KEY = "omron_cuff_mac"
_SOURCE_DEVICE = "omron_hem7155t"


# ---------------------------------------------------------------------------
# SFLOAT-16 + payload parsing — pure logic, fully testable
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BpReading:
    """Parsed result of one Blood Pressure Measurement notification."""

    systolic_mmhg: float
    diastolic_mmhg: float
    map_mmhg: float
    pulse_bpm: float | None  # None when the cuff didn't report pulse


def parse_sfloat16(byte0: int, byte1: int) -> float:
    """Decode an IEEE 11073 SFLOAT-16 (little-endian) into a float.

    16-bit value, 4-bit signed exponent in the high nibble of the
    high byte, 12-bit signed mantissa. We sign-extend each field
    by hand because two's complement on a sub-byte width is fiddly.
    Special values (NaN, NRes, ±Infinity, Reserved) are not expected
    on the BP path; if seen, the float they decode to is acceptable
    given downstream physiological-range validation will reject them.
    """
    raw = byte0 | (byte1 << 8)
    exponent = (raw >> 12) & 0x0F
    if exponent >= 0x08:
        exponent -= 0x10
    mantissa = raw & 0x0FFF
    if mantissa >= 0x800:
        mantissa -= 0x1000
    return float(mantissa * (10**exponent))


def parse_bp_measurement(payload: bytes) -> BpReading:
    """Parse a Blood Pressure Measurement characteristic payload.

    Raises ``ValueError`` if the payload is shorter than the 7-byte
    minimum (flags + 3 SFLOAT-16 fields).
    """
    if len(payload) < 7:
        raise ValueError(f"BP measurement payload too short: {len(payload)} < 7 bytes")
    flags = payload[0]
    systolic = parse_sfloat16(payload[1], payload[2])
    diastolic = parse_sfloat16(payload[3], payload[4])
    mean_arterial = parse_sfloat16(payload[5], payload[6])

    offset = 7
    if flags & 0x02:  # time-stamp present (year MSB-LSB then 5 bytes)
        offset += 7

    pulse: float | None = None
    if flags & 0x04:  # pulse-rate present
        if len(payload) < offset + 2:
            raise ValueError("BP payload claims pulse-rate but is truncated")
        pulse = parse_sfloat16(payload[offset], payload[offset + 1])

    return BpReading(
        systolic_mmhg=systolic,
        diastolic_mmhg=diastolic,
        map_mmhg=mean_arterial,
        pulse_bpm=pulse,
    )


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


class MockOmronBp(Sensor):
    """In-memory BP cuff. Tests / dev call :meth:`simulate_measurement`."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def simulate_measurement(
        self, systolic: float, diastolic: float, pulse: float | None = None
    ) -> None:
        await self._publish_reading(
            BpReading(
                systolic_mmhg=systolic,
                diastolic_mmhg=diastolic,
                map_mmhg=(systolic + 2 * diastolic) / 3,
                pulse_bpm=pulse,
            )
        )

    async def _publish_reading(self, reading: BpReading) -> None:
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="systolic_bp",
                value=reading.systolic_mmhg,
                unit="mmHg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="diastolic_bp",
                value=reading.diastolic_mmhg,
                unit="mmHg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
        if reading.pulse_bpm is not None:
            await self._bus.publish(
                MeasurementProposed(
                    measurement_type="heart_rate",
                    value=reading.pulse_bpm,
                    unit="bpm",
                    source_device=_SOURCE_DEVICE,
                    claimed_is_valid=True,
                )
            )


# ---------------------------------------------------------------------------
# Real — bleak + bleak-retry-connector
# ---------------------------------------------------------------------------


class OmronBpSensor(Sensor):
    """BLE-connected Omron HEM-7155T cuff.

    Subscribes to :class:`BpMeasurementRequested` events on the bus.
    On each request: connects to the cuff (MAC from
    ``device_config.omron_cuff_mac``), subscribes to the BP
    Measurement characteristic, awaits one notification, parses it,
    publishes ``MeasurementProposed`` events for systolic / diastolic
    / pulse, then disconnects. The kiosk pre-pairs with the cuff at
    commissioning so the connection is fast.
    """

    def __init__(
        self,
        bus: EventBus,
        db: Session,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._logger = structlog.get_logger("sensor.omron_bp")
        self._client_factory = client_factory  # tests can inject
        self._mac: str | None = None
        self._running = False

    async def start(self) -> None:  # pragma: no cover - hardware path
        if self._running:
            return
        self._mac = self._load_mac()
        if not self._mac:
            raise SensorUnavailable(
                f"{_CUFF_MAC_CONFIG_KEY} missing from device_config; "
                "the kiosk cannot operate the BP cuff without it"
            )
        self._bus.subscribe(BpMeasurementRequested, self._handle_request)
        self._running = True

    async def stop(self) -> None:  # pragma: no cover - hardware path
        # The bus has no unsubscribe today; simply mark as not-running so
        # _handle_request short-circuits if a stale event arrives.
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_mac(self) -> str | None:  # pragma: no cover - via start()
        row = self._db.execute(
            select(DeviceConfig).where(DeviceConfig.key == _CUFF_MAC_CONFIG_KEY)
        ).scalar_one_or_none()
        return row.value if row is not None else None

    async def _handle_request(  # pragma: no cover - hardware path
        self, _event: BpMeasurementRequested
    ) -> None:
        if not self._running or self._mac is None:
            return
        try:
            payload = await self._read_one_notification(self._mac)
        except Exception as exc:
            self._logger.warning(
                "omron_bp.connect_failed", mac=self._mac, error=str(exc)
            )
            return
        try:
            reading = parse_bp_measurement(payload)
        except ValueError as exc:
            self._logger.warning(
                "omron_bp.parse_failed", error=str(exc), bytes=payload.hex()
            )
            return
        await self._publish_reading(reading)

    async def _read_one_notification(  # pragma: no cover - hardware path
        self, mac: str
    ) -> bytes:
        """Connect, subscribe, await one notification, return payload."""
        import asyncio

        if self._client_factory is not None:
            client_cm = self._client_factory(mac)
        else:
            from bleak_retry_connector import establish_connection
            from bleak import BleakClient

            # bleak-retry-connector accepts a BLEDevice or its address;
            # we pass the address (str) and let the connector resolve.
            # The signature stub disagrees, hence the type ignore on
            # the offending arg.
            client_cm = await establish_connection(
                BleakClient,
                mac,  # type: ignore[arg-type]
                mac,
            )

        received: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()

        def callback(_char: Any, data: bytearray) -> None:
            if not received.done():
                received.set_result(bytes(data))

        async with client_cm as client:
            await client.start_notify(_BP_MEASUREMENT_CHAR_UUID, callback)
            payload = await asyncio.wait_for(received, timeout=120.0)
            await client.stop_notify(_BP_MEASUREMENT_CHAR_UUID)
        return payload

    async def _publish_reading(self, reading: BpReading) -> None:
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="systolic_bp",
                value=reading.systolic_mmhg,
                unit="mmHg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="diastolic_bp",
                value=reading.diastolic_mmhg,
                unit="mmHg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
        if reading.pulse_bpm is not None:
            await self._bus.publish(
                MeasurementProposed(
                    measurement_type="heart_rate",
                    value=reading.pulse_bpm,
                    unit="bpm",
                    source_device=_SOURCE_DEVICE,
                    claimed_is_valid=True,
                )
            )
