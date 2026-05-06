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

ARCHITECTURAL NOTE (2026-05-02 bench finding): The HEM-7155T uses a
store-and-forward BLE model. Measurements happen on the cuff alone
(user presses START on cuff, no Pi connection needed). The cuff
stores the most recent measurement internally. When the user later
puts the cuff in pairing mode and the kiosk connects, the cuff
delivers the stored measurement via the SIG indicate mechanism.
Pairing mode and measurement mode are mutually exclusive on the
device — pressing START while in pairing mode exits pairing.

The kiosk's GUI flow (Phase 2 Prompt 8) must reflect this:
1. Prompt user to take BP on the cuff alone
2. Wait for user to indicate "done"
3. Prompt user to put cuff in pairing mode
4. Connect and retrieve stored measurement
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

# Retry budget for the connect() handshake. Total worst-case window:
# (RETRIES - 1) * DELAY ≈ 8 s of patience for the cuff to start
# advertising after the user presses the BT button. Tuned for the
# bench-tested HEM-7155T behaviour where the BT button takes a
# beat to flip the radio into pairing mode.
_BP_CONNECT_RETRIES = 5
_BP_CONNECT_RETRY_DELAY_S = 2.0


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
# Real — plain bleak (omblepy-style direct connect)
# ---------------------------------------------------------------------------
#
# We pass the MAC straight to ``bleak.BleakClient(mac).connect()``, the same
# pattern userx14/omblepy uses on the same cuff family. Two earlier attempts
# went wrong:
#
# 1. ``bleak_retry_connector.establish_connection(BleakClient, mac_str, mac_str)``
#    — fails at runtime with "'str' object has no attribute 'details'"
#    because the connector dereferences ``device.details`` on its second
#    positional arg, which the type stub already says must be a BLEDevice.
# 2. ``establish_connection(BleakClient, await find_device_by_address(mac), mac)``
#    — works, but adds a 20 s scan window the user has to wait through
#    every BP measurement, just to obtain a BLEDevice handle that bleak's
#    own connect() machinery would have resolved internally anyway.
#
# Plain BleakClient skips the explicit scan: BlueZ already knows the
# pre-paired device (see Phase 0 plan, "Pair and capture the Omron BP
# cuff"), and ``connect()`` resolves and connects in 1–3 s in practice.
# We replace bleak-retry-connector's transparent retry with our own small
# retry loop scoped to the kinds of transient failure the cuff actually
# produces during a pairing-mode handshake.


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
        import asyncio

        self._bus = bus
        self._db = db
        self._logger = structlog.get_logger("sensor.omron_bp")
        self._client_factory = client_factory  # tests can inject
        self._mac: str | None = None
        self._running = False
        # Serialise requests against this sensor. CLAUDE.md "no
        # concurrent BLE" plus a real-world failure mode: when the
        # GUI fires ``BpMeasurementRequested`` twice in quick
        # succession (e.g., a citizen rapid-tapping the connect
        # button), two ``_handle_request`` invocations would race
        # ``BleakClient(mac).connect()``. The first wins; the second
        # gets ``[org.bluez.Error.InProgress] Operation already in
        # progress``. The lock makes overlapping requests no-op
        # rather than corrupt the BLE handle.
        self._request_lock = asyncio.Lock()

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
        # Drop concurrent requests with a clear log line — the GUI
        # disables the connect button after a tap, but a stray
        # ``BpMeasurementRequested`` on the bus shouldn't be able to
        # race the in-flight handler.
        if self._request_lock.locked():
            self._logger.info(
                "omron_bp.request_ignored_already_in_flight", mac=self._mac
            )
            return
        async with self._request_lock:
            self._logger.info("omron_bp.request_started", mac=self._mac)
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
            self._logger.info(
                "omron_bp.measurement_received",
                mac=self._mac,
                has_pulse=reading.pulse_bpm is not None,
            )
            await self._publish_reading(reading)

    async def _read_one_notification(self, mac: str) -> bytes:
        """Connect, subscribe, await one notification, return payload.

        Two construction paths:

        * Test path (``self._client_factory`` is set): the factory
          returns an async context manager; we use ``async with`` to
          enter and leave it. Mocked factories produce real context
          managers in unit tests.

        * Real path: ``bleak.BleakClient(mac).connect()`` directly,
          mirroring userx14/omblepy. BlueZ has the cuff cached from
          commissioning, so direct connect is fast (1–3 s typical).
          We retry ``_BP_CONNECT_RETRIES`` times with
          ``_BP_CONNECT_RETRY_DELAY_S`` seconds between attempts to
          absorb the transient "not advertising yet" window between
          the user pressing the BT button and the cuff actually
          starting to broadcast. After the final retry exhausts, we
          surface a clear error pointing at pairing mode.
          ``client.disconnect()`` is in a ``finally`` block so we
          always release the BLE handle even on notify timeout.
        """
        import asyncio

        received: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()

        def callback(_char: Any, data: bytearray) -> None:
            if not received.done():
                received.set_result(bytes(data))

        if self._client_factory is not None:
            client_cm = self._client_factory(mac)
            async with client_cm as client:
                await client.start_notify(_BP_MEASUREMENT_CHAR_UUID, callback)
                payload = await asyncio.wait_for(received, timeout=120.0)
                await client.stop_notify(_BP_MEASUREMENT_CHAR_UUID)
            return payload

        from bleak import BleakClient
        from bleak.exc import BleakError

        # Fresh ``BleakClient`` per attempt. Reusing one instance across
        # retries (the previous design) carried over BlueZ state — once
        # the first ``connect()`` saw ``[org.bluez.Error.InProgress]``,
        # every subsequent attempt on the same client object hit the
        # same error because the underlying D-Bus method call was still
        # logically pending. A fresh client + best-effort disconnect on
        # failure lets BlueZ cleanly release the operation between
        # tries.
        connected_client: Any | None = None
        last_error: Exception | None = None
        for attempt in range(_BP_CONNECT_RETRIES):
            candidate = BleakClient(mac)
            try:
                await candidate.connect()
                connected_client = candidate
                break
            except (BleakError, asyncio.TimeoutError) as exc:
                last_error = exc
                self._logger.warning(
                    "omron_bp.connect_attempt_failed",
                    mac=mac,
                    attempt=attempt + 1,
                    of=_BP_CONNECT_RETRIES,
                    error=str(exc),
                )
                # Best-effort: tell BlueZ to release whatever it had
                # pending on this candidate. Errors on disconnect are
                # expected (the client never actually connected) so
                # they are silently absorbed.
                try:
                    await candidate.disconnect()
                except Exception:
                    pass
                if attempt < _BP_CONNECT_RETRIES - 1:
                    await asyncio.sleep(_BP_CONNECT_RETRY_DELAY_S)

        if connected_client is None:
            raise RuntimeError(
                f"Omron HEM-7155T at {mac} did not connect after "
                f"{_BP_CONNECT_RETRIES} attempts — put the cuff into "
                f"pairing mode and try again (last error: {last_error})"
            )

        try:
            await connected_client.start_notify(_BP_MEASUREMENT_CHAR_UUID, callback)
            payload = await asyncio.wait_for(received, timeout=120.0)
            await connected_client.stop_notify(_BP_MEASUREMENT_CHAR_UUID)
        finally:
            await connected_client.disconnect()
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
