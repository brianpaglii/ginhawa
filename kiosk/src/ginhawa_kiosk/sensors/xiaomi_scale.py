"""Xiaomi Smart Scale S200 sensor (per ADR-0017).

Listens to BLE advertisements via ``bleak.BleakScanner``. Each
advertisement is wrapped into ``BluetoothServiceInfoBleak`` (per the
``home-assistant-bluetooth`` shape ``xiaomi_ble`` expects) and fed to
:class:`xiaomi_ble.XiaomiBluetoothDeviceData`'s ``update`` method,
which decrypts using the per-scale bindkey loaded from
``device_config.xiaomi_scale_bindkey`` at start time.

Decisions pinned by code:

* **profile_id is ignored.** In a kiosk context, dozens of unrelated
  citizens use the same physical scale, so the scale-side
  user-identification is meaningless. Citizen identity comes from
  the RFID tap; the scale only contributes a weight reading.
  CLAUDE.md "Xiaomi scale specifics" pins this rule too.
* **Body-composition outputs (body fat %, muscle mass, water content,
  bone mass, segmental analysis) are NOT published.** They are out of
  declared scope under the Data Privacy Act consent, and bioimpedance
  is too noisy for community screening anyway.
* **The scale's foot-electrode heart rate is NOT published.** Heart
  rate comes from the MAX30100 on ESP32-A.
* **Mass-only filter:** advertisements without a ``mass`` entity are
  ignored. Xiaomi advertises signal-strength-only frames between
  measurements; those are not a reading.
* **One reading per session:** the S200 broadcasts mass roughly
  every 5 s while a user stands on it, and the kiosk's mental model
  is per-session-one-weight. The :class:`_WeightStabilityGate`
  buffers the last K readings and publishes the median only after
  the buffer is "stable" (max - min ≤ tolerance). Once published,
  the gate locks itself; further advertisements are dropped on the
  floor until a :class:`SessionResetForSensors` event releases it.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping
from statistics import median
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DeviceConfig
from ..fsm.event_bus import EventBus, MeasurementProposed, SessionResetForSensors
from .base import Sensor, SensorUnavailable
from .ble_lock import BleAdapterLock


_BINDKEY_CONFIG_KEY = "xiaomi_scale_bindkey"
_SOURCE_DEVICE = "xiaomi_s200_ble"

# Stability gate tunables. The scale broadcasts every ~5 s while a
# person stands on it; with K=3 readings, that's ~15 s of dwell
# before the kiosk captures the weight, well within the user's
# expected "stand on the scale" window. Tolerance of 0.2 kg covers
# the small fluctuations the scale itself reports as the user
# settles, while still rejecting genuine shifts (e.g., a child
# stepping off and on, or two people sharing the scale).
_WEIGHT_STABILITY_BUFFER_K = 3
_WEIGHT_STABILITY_TOLERANCE_KG = 0.2
# Warmup window after gate unlock. Bench evidence (2026-05-09):
# weight publishes were observed within 50 ms – 1 s of the
# gate_unlocked event, far faster than 3 stable readings × 5 s
# broadcast cadence allows. Either the BleAdapterLock pause/resume
# cycle re-delivers cached advertisements when the scanner resumes,
# or xiaomi-ble caches state and emits on next event regardless of
# the gate's buffer history. Either way, dropping incoming readings
# for one full broadcast cycle plus margin guarantees the buffer
# fills with genuinely-fresh readings from the citizen who just
# stepped on. ~8 s is one cycle (5 s) plus margin and is well under
# the dwell window the user already needs to stand on the scale.
_GATE_WARMUP_SECONDS = 8.0


# ---------------------------------------------------------------------------
# Stability gate — shared by mock and real
# ---------------------------------------------------------------------------


class _WeightStabilityGate:
    """Captures one stable weight per session.

    The gate buffers the last ``K`` readings in a deque. It returns
    a publishable value (the median of the buffer) only when:

    * the buffer is full (``K`` readings have been seen), AND
    * the spread is within ``tolerance_kg`` (``max - min``), AND
    * the gate is not locked.

    On a successful publish, the gate locks itself; subsequent
    :meth:`accept` calls return ``None`` until :meth:`unlock` clears
    the lock and the buffer. ``unlock`` is idempotent and is the
    main_window's hook for "new session starting" / "session ended".
    """

    def __init__(
        self,
        *,
        buffer_k: int = _WEIGHT_STABILITY_BUFFER_K,
        tolerance_kg: float = _WEIGHT_STABILITY_TOLERANCE_KG,
        warmup_seconds: float = _GATE_WARMUP_SECONDS,
    ) -> None:
        self._k = buffer_k
        self._tolerance = tolerance_kg
        self._warmup_seconds = warmup_seconds
        self._buffer: deque[float] = deque(maxlen=buffer_k)
        self._locked = False
        # Set on every unlock(); ``None`` for a freshly-constructed
        # gate (no warmup applied to the first session, since the
        # cached-broadcast race only occurs across an unlock cycle).
        self._unlocked_at: float | None = None

    def accept(self, value: float) -> float | None:
        """Feed one reading; return the publish value if stable, else None."""
        if self._locked:
            return None
        # Warmup window: drop readings for ``_warmup_seconds`` after
        # the most recent unlock. The Xiaomi-BLE library and/or the
        # BleAdapterLock pause/resume cycle can replay cached
        # advertisements when the scanner resumes; without this gate
        # the buffer fills with stale readings from before the
        # citizen actually stepped on, and a single ~5 s broadcast
        # can publish a stale weight in <1 s. _unlocked_at is None
        # only on a fresh gate (no prior unlock) — that path skips
        # the warmup check entirely.
        if self._unlocked_at is not None:
            if time.monotonic() - self._unlocked_at < self._warmup_seconds:
                return None
        self._buffer.append(value)
        if len(self._buffer) < self._k:
            return None
        if max(self._buffer) - min(self._buffer) > self._tolerance:
            return None
        published = float(median(self._buffer))
        self._locked = True
        return published

    def unlock(self) -> None:
        """Release the lock and clear the buffer for a fresh session."""
        self._locked = False
        self._buffer.clear()
        self._unlocked_at = time.monotonic()

    def is_locked(self) -> bool:
        return self._locked


# ---------------------------------------------------------------------------
# Helpers — mass extraction
# ---------------------------------------------------------------------------


def extract_mass_kg(entity_values: Mapping[Any, Any]) -> float | None:
    """Look for a 'mass' entity in a parsed Xiaomi BLE update.

    Accepts the shape that ``XiaomiBluetoothDeviceData.update()``
    produces: ``entity_values`` is a mapping where keys identify
    entity types and values carry a ``native_value`` attribute (or
    are floats directly in the mock case). Returns ``None`` when no
    mass entry is found — typically a signal-strength-only
    advertisement.
    """
    for key, value in entity_values.items():
        key_str = (
            getattr(key, "key", None) if not isinstance(key, str) else key
        ) or str(key)
        if "mass" in key_str.lower():
            native = getattr(value, "native_value", value)
            try:
                return float(native)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                return None
    return None


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


class MockXiaomiScale(Sensor):
    """In-memory scale. Tests / dev call :meth:`simulate_weight`."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._gate = _WeightStabilityGate()
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def reset_for_new_session(self) -> None:
        """Release the stability gate so a new session can capture a weight."""
        self._gate.unlock()

    async def simulate_weight(self, kg: float) -> None:
        published = self._gate.accept(kg)
        if published is None:
            return
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="weight",
                value=published,
                unit="kg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )


# ---------------------------------------------------------------------------
# Real — bleak + xiaomi_ble
# ---------------------------------------------------------------------------


class XiaomiScaleSensor(Sensor):
    """Real Xiaomi S200 scale sensor.

    On :meth:`start`, loads the per-scale bindkey from
    ``device_config`` and instantiates a ``BleakScanner`` with a
    detection callback. Each advertisement is wrapped into
    ``BluetoothServiceInfoBleak`` and passed to
    ``XiaomiBluetoothDeviceData.update()``; mass entries are
    extracted, deduplicated, and published as
    :class:`MeasurementProposed` events.

    Tests bypass the BLE+scanner path by calling
    :meth:`_on_sensor_update` directly with a synthetic
    ``entity_values`` mapping.
    """

    def __init__(
        self,
        bus: EventBus,
        db: Session,
        *,
        ble_lock: BleAdapterLock | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._logger = structlog.get_logger("sensor.xiaomi_scale")
        self._gate = _WeightStabilityGate()
        self._device_data: Any | None = None
        self._scanner: Any | None = None
        self._running = False
        # Optional adapter-coordinator. When set, the scanner registers
        # pause/resume hooks so the Omron BP cuff's directed connect
        # can briefly take exclusive use of hci0 — see
        # :mod:`ginhawa_kiosk.sensors.ble_lock` for the why.
        self._ble_lock = ble_lock
        self._paused = False

    async def start(self) -> None:
        if self._running:  # pragma: no cover - idempotency guard
            return
        bindkey_hex = self._load_bindkey_hex()
        if bindkey_hex is None:
            raise SensorUnavailable(
                f"{_BINDKEY_CONFIG_KEY} missing from device_config; "
                "the kiosk cannot be commissioned without a per-scale "
                "bindkey (see ADR-0017)"
            )
        try:
            bindkey = bytes.fromhex(bindkey_hex)
        except ValueError as exc:
            raise SensorUnavailable(
                f"{_BINDKEY_CONFIG_KEY} in device_config is not valid hex"
            ) from exc

        # Subscribe to the session-reset event so the gate releases at
        # the end of a session (or the start of the next one).
        # main_window publishes this on entering IDLE / LANGUAGE_SELECT.
        self._bus.subscribe(SessionResetForSensors, self._on_session_reset)

        # Below: bleak + xiaomi-ble engagement. Hardware-bound; tests
        # exercise the post-decode pipeline (_on_sensor_update) directly.
        from xiaomi_ble import (  # pragma: no cover
            XiaomiBluetoothDeviceData,
        )

        self._device_data = XiaomiBluetoothDeviceData(  # pragma: no cover
            bindkey=bindkey
        )
        self._scanner = self._build_scanner()  # pragma: no cover
        await self._scanner.start()  # pragma: no cover
        self._running = True  # pragma: no cover
        if self._ble_lock is not None:  # pragma: no cover - hardware path
            self._ble_lock.register_scanner(
                pause=self._pause_scanner, resume=self._resume_scanner
            )

    def reset_for_new_session(self) -> None:
        """Release the stability gate so a new session can capture a weight.

        Direct call surface for callers that hold a sensor reference;
        the bus-driven path (:meth:`_on_session_reset`) is what the
        main_window uses in production.
        """
        self._gate.unlock()
        self._logger.info("xiaomi_scale.gate_unlocked")

    async def _on_session_reset(self, _event: SessionResetForSensors) -> None:
        self.reset_for_new_session()

    async def stop(self) -> None:  # pragma: no cover - hardware path
        if self._ble_lock is not None:
            self._ble_lock.unregister_scanner(
                pause=self._pause_scanner, resume=self._resume_scanner
            )
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None
        self._device_data = None
        self._running = False
        self._paused = False

    async def _pause_scanner(self) -> None:  # pragma: no cover - hardware path
        """Stop the BleakScanner so the BLE adapter is free for a directed connect.

        Idempotent: if the scanner is already paused (or never
        started) this is a no-op. The :class:`BleAdapterLock` guards
        against concurrent ``exclusive()`` blocks, so we don't need
        to count nested pauses.
        """
        if self._paused or self._scanner is None:
            return
        try:
            await self._scanner.stop()
        except Exception as exc:
            self._logger.warning("xiaomi_scale.pause_failed", error=type(exc).__name__)
            return
        self._paused = True
        self._logger.info("xiaomi_scale.paused")

    async def _resume_scanner(self) -> None:  # pragma: no cover - hardware path
        """Restart the BleakScanner after a connect-style sensor releases the adapter."""
        if not self._paused or self._scanner is None:
            return
        try:
            await self._scanner.start()
        except Exception as exc:
            self._logger.warning("xiaomi_scale.resume_failed", error=type(exc).__name__)
            return
        self._paused = False
        self._logger.info("xiaomi_scale.resumed")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internals — testable in isolation
    # ------------------------------------------------------------------

    def _load_bindkey_hex(self) -> str | None:
        row = self._db.execute(
            select(DeviceConfig).where(DeviceConfig.key == _BINDKEY_CONFIG_KEY)
        ).scalar_one_or_none()
        return row.value if row is not None else None

    def _build_scanner(self) -> Any:  # pragma: no cover - hardware path
        from bleak import BleakScanner

        return BleakScanner(detection_callback=self._on_advertisement)

    async def _on_advertisement(  # pragma: no cover - hardware path
        self, ble_device: Any, advertisement_data: Any
    ) -> None:
        # Wrap into BluetoothServiceInfoBleak per the spike findings —
        # xiaomi_ble's update() expects this shape.
        from home_assistant_bluetooth import BluetoothServiceInfoBleak

        service_info = BluetoothServiceInfoBleak(
            name=advertisement_data.local_name or "",
            address=ble_device.address,
            rssi=advertisement_data.rssi,
            manufacturer_data=advertisement_data.manufacturer_data,
            service_data=advertisement_data.service_data,
            service_uuids=advertisement_data.service_uuids,
            source="local",
            device=ble_device,
            advertisement=advertisement_data,
            connectable=True,
            time=time.monotonic(),
            tx_power=advertisement_data.tx_power,
        )
        if self._device_data is None:
            return
        update = self._device_data.update(service_info)
        await self._on_sensor_update(update.entity_values)

    async def _on_sensor_update(self, entity_values: Mapping[Any, Any]) -> None:
        """Process a parsed sensor update.

        Tests call this directly with synthetic ``entity_values``
        rather than going through the full BLE+xiaomi_ble pipeline.
        """
        kg = extract_mass_kg(entity_values)
        if kg is None:
            return  # signal-strength-only or other non-mass advertisement
        published = self._gate.accept(kg)
        if published is None:
            return
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="weight",
                value=published,
                unit="kg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
