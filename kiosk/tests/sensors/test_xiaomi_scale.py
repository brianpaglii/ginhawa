"""Xiaomi Smart Scale S200 sensor: mock + real (BLE-bypassed)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from ginhawa_kiosk.fsm import EventBus, MeasurementProposed
from ginhawa_kiosk.sensors.base import SensorUnavailable
from ginhawa_kiosk.sensors.xiaomi_scale import (
    MockXiaomiScale,
    XiaomiScaleSensor,
    extract_mass_kg,
)

from .conftest import set_device_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MassEntity:
    """Mimic the xiaomi_ble entity object exposed under .native_value."""

    def __init__(self, native_value: float) -> None:
        self.native_value = native_value


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _mass_advertisement(kg: float) -> dict[str, Any]:
    """A synthetic entity_values payload carrying mass."""
    return {"mass": _MassEntity(kg)}


def _signal_strength_only_advertisement() -> dict[str, Any]:
    """A synthetic entity_values payload with only signal_strength —
    Xiaomi advertises these between measurements."""
    return {"signal_strength": _MassEntity(-72.0)}


# ---------------------------------------------------------------------------
# Tests — mock + dedup
# ---------------------------------------------------------------------------


# Verifies the mock's lifecycle and dedup logic. start/stop flip
# is_running; repeated simulate_weight calls within the dedup
# window publish only once.
@pytest.mark.asyncio
async def test_mock_xiaomi_scale_lifecycle_and_dedup(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    clock = _FakeClock()
    scale = MockXiaomiScale(bus, clock=clock.now)
    assert scale.is_running is False
    await scale.start()
    assert scale.is_running is True
    await scale.simulate_weight(70.0)
    await scale.simulate_weight(70.0)  # within dedup window
    assert len(captured_measurements) == 1
    await scale.stop()
    assert scale.is_running is False


# Verifies the mock publishes one MeasurementProposed event per
# simulate_weight call (subject to dedup, which we test separately).
@pytest.mark.asyncio
async def test_mock_xiaomi_scale_publishes_event_on_simulate_weight(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    scale = MockXiaomiScale(bus)
    await scale.simulate_weight(68.5)
    assert len(captured_measurements) == 1
    event = captured_measurements[0]
    assert event.measurement_type == "weight"
    assert event.value == pytest.approx(68.5)
    assert event.unit == "kg"
    assert event.source_device == "xiaomi_s200_ble"
    assert event.claimed_is_valid is True


# Verifies dedup: same advertisement three times within 1 second
# produces ONE event; the same value after >5 seconds produces a
# second event. The kiosk's scale rebroadcasts each measurement
# multiple times for reception reliability — we want one event per
# actual measurement.
# Mortality: would fail if dedup logic were removed or threshold
# changed.
@pytest.mark.asyncio
async def test_xiaomi_scale_deduplicates_repeated_readings(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "xiaomi_scale_bindkey", "00" * 16)
    clock = _FakeClock()
    sensor = XiaomiScaleSensor(bus, db_session, clock=clock.now)

    payload = _mass_advertisement(70.0)
    await sensor._on_sensor_update(payload)
    clock.advance(0.3)
    await sensor._on_sensor_update(payload)
    clock.advance(0.3)
    await sensor._on_sensor_update(payload)
    assert len(captured_measurements) == 1

    # Past the 5-second window — a re-broadcast at the same value
    # publishes again.
    clock.advance(6.0)
    await sensor._on_sensor_update(payload)
    assert len(captured_measurements) == 2


# Verifies a value-difference also breaks dedup: a fresh value
# differing by ≥0.1 kg from the last published value publishes
# even within the 5-second window. Necessary for the case where the
# citizen shifts on the scale and the reading actually changes.
# Mortality: would fail if the value-threshold branch were dropped.
@pytest.mark.asyncio
async def test_xiaomi_scale_publishes_when_value_differs_within_window(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "xiaomi_scale_bindkey", "00" * 16)
    clock = _FakeClock()
    sensor = XiaomiScaleSensor(bus, db_session, clock=clock.now)

    await sensor._on_sensor_update(_mass_advertisement(70.0))
    clock.advance(0.5)
    await sensor._on_sensor_update(_mass_advertisement(70.5))  # +0.5 kg
    assert len(captured_measurements) == 2


# Verifies signal-strength-only advertisements (no mass entity) are
# silently ignored. Xiaomi emits these between actual measurements;
# the sensor must not treat them as readings.
# Mortality: would fail if the mass-presence check were removed.
@pytest.mark.asyncio
async def test_xiaomi_scale_ignores_signal_strength_only_advertisements(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "xiaomi_scale_bindkey", "00" * 16)
    sensor = XiaomiScaleSensor(bus, db_session)

    await sensor._on_sensor_update(_signal_strength_only_advertisement())
    assert captured_measurements == []


# Verifies extract_mass_kg returns None when no mass key is present
# (covers the helper directly, since the sensor pipeline above only
# exercises one path through it).
def test_extract_mass_kg_returns_none_when_no_mass_key() -> None:
    assert extract_mass_kg({"signal_strength": _MassEntity(-72.0)}) is None
    assert extract_mass_kg({}) is None


# Verifies start() refuses to run if no bindkey is in device_config.
# A kiosk cannot be commissioned without one (per ADR-0017); failing
# loud beats silently scanning with no decryption.
# Mortality: would fail if the kiosk silently accepted a missing
# bindkey.
@pytest.mark.asyncio
async def test_xiaomi_scale_raises_on_missing_bindkey(
    bus: EventBus, db_session: Session
) -> None:
    sensor = XiaomiScaleSensor(bus, db_session)
    with pytest.raises(SensorUnavailable, match="bindkey missing"):
        await sensor.start()


# Verifies start() also refuses on a malformed (non-hex) bindkey,
# rather than letting bytes.fromhex blow up later mid-scan.
@pytest.mark.asyncio
async def test_xiaomi_scale_raises_on_malformed_bindkey(
    bus: EventBus, db_session: Session
) -> None:
    set_device_config(db_session, "xiaomi_scale_bindkey", "not-hex-at-all")
    sensor = XiaomiScaleSensor(bus, db_session)
    with pytest.raises(SensorUnavailable, match="not valid hex"):
        await sensor.start()
