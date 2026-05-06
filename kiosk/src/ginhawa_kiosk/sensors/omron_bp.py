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

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
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
from .ble_lock import BleAdapterLock


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

# Freshness window for stored measurements. The HEM-7155T's
# store-and-forward BLE model means a citizen who taps "Connect to
# cuff" without first taking a fresh BP will retrieve whatever the
# cuff stored last — possibly hours old, possibly belonging to a
# different citizen if the kiosk is shared. The 2026-05-06 bench
# proved this: connecting in pairing mode without re-pressing START
# returned a measurement timestamped 5 hours earlier verbatim. Any
# reading whose embedded timestamp is older than this window is
# treated as stale and dropped on the floor; the citizen is
# prompted (via the GUI's re-enabled Connect button) to take a
# fresh measurement and tap Connect again.
_BP_FRESHNESS_WINDOW_S = 180.0  # 3 minutes

# Outer wall-clock budget for the drain-and-wait phase, measured
# from the moment notifications start flowing. The cuff dumps any
# stored readings within a few seconds of the indicate subscription
# (store-and-forward), so this only needs to span "drain stale
# readings + give the user a beat to press START on the cuff again
# if the kiosk's prompt is what reminded them". 90 s comfortably
# covers both phases without leaving the citizen staring at a
# spinner forever when the cuff has nothing fresh to deliver.
_BP_FRESH_READ_TIMEOUT_S = 90.0


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
    # Timestamp embedded in the SIG payload (bytes 7-13). The SIG
    # Date Time field has no timezone marker; the cuff transmits
    # whatever wall-clock its internal RTC is set to (in practice,
    # the deployment's local time). The parser tags the value with
    # the host's local timezone so freshness comparisons against
    # ``datetime.now(...)`` reduce to a common UTC instant
    # regardless of whether the Pi is set to UTC or local. None when
    # the cuff didn't include a timestamp — older firmware revisions
    # or non-Omron BP devices that share the SIG profile may omit it.
    taken_at: datetime | None


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
    taken_at: datetime | None = None
    if flags & 0x02:  # time-stamp present (year LSB-MSB then 5 bytes)
        if len(payload) < offset + 7:
            raise ValueError("BP payload claims timestamp but is truncated")
        taken_at = _parse_timestamp(payload[offset : offset + 7])
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
        taken_at=taken_at,
    )


def _is_fresh(
    taken_at: datetime | None,
    *,
    now: Callable[[], datetime] | None = None,
    window_s: float = _BP_FRESHNESS_WINDOW_S,
) -> bool:
    """Decide whether a SIG-payload timestamp is recent enough to publish.

    Returns ``False`` when the cuff didn't include a timestamp at
    all — without it we can't distinguish a fresh measurement from
    a months-old stored one, and the kiosk would rather drop the
    reading than misattribute someone else's BP to the active
    citizen.

    Otherwise returns ``True`` iff
    ``abs(now - taken_at) <= window_s``. The check is symmetric to
    tolerate small clock drift between the cuff's RTC and the Pi
    (the cuff's RTC is not network-synchronised; a few seconds of
    skew either direction is normal and shouldn't drop a fresh
    reading). The wall-clock-vs-timezone mismatch the cuff used to
    introduce — encoding local time in a SIG field that carries no
    tz metadata — is corrected upstream in :func:`_parse_timestamp`
    by tagging the value with the host's local timezone, so by the
    time we get here both ``now`` and ``taken_at`` reduce to the
    same UTC instant.

    ``now`` is injectable for tests; defaults to UTC wall-clock.
    """
    if taken_at is None:
        return False
    current = (now or (lambda: datetime.now(timezone.utc)))()
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    delta_s = abs((current - taken_at).total_seconds())
    return delta_s <= window_s


def _parse_timestamp(raw: bytes) -> datetime | None:
    """Decode the SIG Date Time field (7 bytes, year LSB-MSB then m/d/h/m/s).

    The SIG Date Time field carries wall-clock time with NO timezone
    metadata. The HEM-7155T cuff's internal RTC is set to deployment
    local time, so the bytes encode local wall-clock — naively
    attaching UTC would put a UTC+8 cuff's reading 8 h in the future
    of the kiosk's ``datetime.now(timezone.utc)`` and trip the
    freshness gate every time (2026-05-06 bench).

    We construct a naive datetime from the bytes and call
    ``.astimezone()`` (no argument) which resolves the host's local
    timezone via ``/etc/localtime`` and returns an aware datetime in
    that zone. Downstream comparisons against ``datetime.now(...)``
    work on a common UTC instant regardless of which zone either
    side carries.

    Returns ``None`` if the cuff sent an obviously bogus timestamp
    (year 0, out-of-range month/day/etc) — keeps a malformed clock
    from crashing the BP path. Out-of-range readings are caught by
    ``datetime``'s own constructor; we map ValueError to None.
    """
    year = raw[0] | (raw[1] << 8)
    month = raw[2]
    day = raw[3]
    hour = raw[4]
    minute = raw[5]
    second = raw[6]
    if year == 0:
        return None
    try:
        naive = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    return naive.astimezone()


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
        self,
        systolic: float,
        diastolic: float,
        pulse: float | None = None,
        taken_at: datetime | None = None,
    ) -> None:
        # Mock readings default to "now" so the freshness gate never
        # rejects them in dev/laptop mode. Tests that need to exercise
        # the stale-reading branch pass an explicit taken_at.
        if taken_at is None:
            taken_at = datetime.now(timezone.utc)
        await self._publish_reading(
            BpReading(
                systolic_mmhg=systolic,
                diastolic_mmhg=diastolic,
                map_mmhg=(systolic + 2 * diastolic) / 3,
                pulse_bpm=pulse,
                taken_at=taken_at,
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
        ble_lock: BleAdapterLock | None = None,
    ) -> None:
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
        # Adapter-wide coordinator: pauses the Xiaomi scanner during
        # the BP connect. The 2026-05-06 bench surfaced exactly this
        # collision (Xiaomi's continuous BleakScanner + Omron's
        # directed connect on the same hci0 adapter -> InProgress on
        # every retry). With the lock acquired, the Xiaomi side
        # stops, the BP path runs, then the Xiaomi side resumes.
        self._ble_lock = ble_lock

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
            # Acquire the BLE adapter exclusively for the duration of
            # the directed connect. The Xiaomi scale's continuous
            # scanner pauses on entry and resumes on exit (success or
            # failure). When ble_lock is None — typical of unit tests
            # that inject a client_factory — fall through without
            # serialisation.
            try:
                if self._ble_lock is not None:
                    async with self._ble_lock.exclusive():
                        reading = await self._read_notifications_until_fresh(self._mac)
                else:
                    reading = await self._read_notifications_until_fresh(self._mac)
            except Exception as exc:
                self._logger.warning(
                    "omron_bp.connect_failed", mac=self._mac, error=str(exc)
                )
                return
            if reading is None:
                # Outer drain timeout already logged with stale_count.
                return
            await self._publish_reading(reading)

    async def _read_notifications_until_fresh(self, mac: str) -> BpReading | None:
        """Connect, subscribe, drain stored readings until a fresh one arrives.

        The HEM-7155T uses a store-and-forward BLE model: on connect
        it dumps every measurement it has buffered (typically just
        the most recent, but possibly several) via SIG indicate.
        Older firmware revisions, and post-battery-pull state,
        dump multiple. The 2026-05-06 bench surfaced this: the
        first indicate after ``start_notify`` was a 5-hour-old
        stored reading; the freshly-pressed measurement would have
        followed it but the kiosk had already disconnected.

        Behaviour:

        * Each notification is parsed; parse failures are logged and
          skipped (we keep draining — a malformed indicate doesn't
          mean the next one is also bad).
        * Stale readings (timestamp older than
          ``_BP_FRESHNESS_WINDOW_S``, or no timestamp at all) are
          logged as ``omron_bp.stored_reading_drained`` and skipped.
        * The first reading inside the freshness window is logged
          as ``omron_bp.measurement_received`` and returned.
        * The whole drain phase is bounded by
          ``_BP_FRESH_READ_TIMEOUT_S``; on timeout we log
          ``omron_bp.fresh_reading_timeout`` (with how many stale
          readings were drained) and return ``None``.

        Two construction paths for the underlying client:

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
          always release the BLE handle even on drain timeout.
        """
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def callback(_char: Any, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        if self._client_factory is not None:
            client_cm = self._client_factory(mac)
            async with client_cm as client:
                await client.start_notify(_BP_MEASUREMENT_CHAR_UUID, callback)
                try:
                    return await self._drain_until_fresh(queue, mac)
                finally:
                    await client.stop_notify(_BP_MEASUREMENT_CHAR_UUID)

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
            return await self._drain_until_fresh(queue, mac)
        finally:
            try:
                await connected_client.stop_notify(_BP_MEASUREMENT_CHAR_UUID)
            except Exception:
                pass
            await connected_client.disconnect()

    async def _drain_until_fresh(
        self, queue: asyncio.Queue[bytes], mac: str
    ) -> BpReading | None:
        """Consume notifications until a fresh reading or the outer timeout.

        Held inside the BLE-adapter lock for the full drain
        duration (the caller establishes the lock); the queue is
        fed by the notify callback. Returns the first fresh
        :class:`BpReading` or ``None`` on timeout.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _BP_FRESH_READ_TIMEOUT_S
        stale_count = 0

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                self._logger.warning(
                    "omron_bp.fresh_reading_timeout",
                    mac=mac,
                    stale_count=stale_count,
                    timeout_s=_BP_FRESH_READ_TIMEOUT_S,
                )
                return None
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "omron_bp.fresh_reading_timeout",
                    mac=mac,
                    stale_count=stale_count,
                    timeout_s=_BP_FRESH_READ_TIMEOUT_S,
                )
                return None
            try:
                reading = parse_bp_measurement(payload)
            except ValueError as exc:
                self._logger.warning(
                    "omron_bp.parse_failed",
                    error=str(exc),
                    bytes=payload.hex(),
                )
                continue
            if not _is_fresh(reading.taken_at):
                stale_count += 1
                self._logger.info(
                    "omron_bp.stored_reading_drained",
                    mac=mac,
                    taken_at=reading.taken_at.isoformat() if reading.taken_at else None,
                    freshness_window_s=_BP_FRESHNESS_WINDOW_S,
                )
                continue
            self._logger.info(
                "omron_bp.measurement_received",
                mac=mac,
                has_pulse=reading.pulse_bpm is not None,
                taken_at=reading.taken_at.isoformat() if reading.taken_at else None,
            )
            return reading

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
