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
* **Deduplication:** the scale rebroadcasts the same measurement
  multiple times for reception reliability. We publish a new
  :class:`MeasurementProposed` event only when the value differs by
  ≥0.1 kg from the last published value, OR ≥5 seconds have elapsed
  since the last publish.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DeviceConfig
from ..fsm.event_bus import EventBus, MeasurementProposed
from .base import Sensor, SensorUnavailable


_BINDKEY_CONFIG_KEY = "xiaomi_scale_bindkey"
_VALUE_DEDUP_THRESHOLD_KG = 0.1
_TIME_DEDUP_THRESHOLD_S = 5.0
_SOURCE_DEVICE = "xiaomi_s200_ble"


# ---------------------------------------------------------------------------
# Dedup helper — shared by mock and real
# ---------------------------------------------------------------------------


class _Dedup:
    """Decides whether to publish a new reading.

    First reading always passes. Subsequent readings pass when the
    value differs by ≥``value_threshold`` OR ≥``time_threshold``
    seconds have elapsed since the last publish.
    """

    def __init__(
        self,
        *,
        value_threshold: float = _VALUE_DEDUP_THRESHOLD_KG,
        time_threshold: float = _TIME_DEDUP_THRESHOLD_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._value_threshold = value_threshold
        self._time_threshold = time_threshold
        self._clock = clock or time.monotonic
        self._last_value: float | None = None
        self._last_ts: float | None = None

    def should_publish(self, value: float) -> bool:
        if self._last_value is None or self._last_ts is None:
            return True
        if abs(value - self._last_value) >= self._value_threshold:
            return True
        if (self._clock() - self._last_ts) >= self._time_threshold:
            return True
        return False

    def mark_published(self, value: float) -> None:
        self._last_value = value
        self._last_ts = self._clock()


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

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._bus = bus
        self._dedup = _Dedup(clock=clock)
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def simulate_weight(self, kg: float) -> None:
        if not self._dedup.should_publish(kg):
            return
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="weight",
                value=kg,
                unit="kg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
        self._dedup.mark_published(kg)


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
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._logger = structlog.get_logger("sensor.xiaomi_scale")
        self._dedup = _Dedup(clock=clock)
        self._device_data: Any | None = None
        self._scanner: Any | None = None
        self._running = False

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

    async def stop(self) -> None:  # pragma: no cover - hardware path
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None
        self._device_data = None
        self._running = False

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
        if not self._dedup.should_publish(kg):
            return
        await self._bus.publish(
            MeasurementProposed(
                measurement_type="weight",
                value=kg,
                unit="kg",
                source_device=_SOURCE_DEVICE,
                claimed_is_valid=True,
            )
        )
        self._dedup.mark_published(kg)
