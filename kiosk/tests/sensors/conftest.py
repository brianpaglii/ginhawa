"""Shared fixtures for sensor tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.models import DeviceConfig
from ginhawa_kiosk.db.session import (
    create_engine_for_kiosk,
    init_database,
    make_session_factory,
)
from ginhawa_kiosk.fsm import (
    EventBus,
    LiveTemperatureUpdate,
    MeasurementProposed,
    RfidScanned,
)


_TEST_KEY = "0" * 64  # pragma: allowlist secret


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine_for_kiosk(tmp_path / "sensors.db", _TEST_KEY)
    init_database(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def captured_rfid(bus: EventBus) -> list[RfidScanned]:
    """A list that fills with every RfidScanned event published."""
    captured: list[RfidScanned] = []

    async def _handler(event: RfidScanned) -> None:
        captured.append(event)

    bus.subscribe(RfidScanned, _handler)
    return captured


@pytest.fixture
def captured_measurements(bus: EventBus) -> list[MeasurementProposed]:
    captured: list[MeasurementProposed] = []

    async def _handler(event: MeasurementProposed) -> None:
        captured.append(event)

    bus.subscribe(MeasurementProposed, _handler)
    return captured


@pytest.fixture
def captured_live_temperatures(bus: EventBus) -> list[LiveTemperatureUpdate]:
    """Sibling of captured_measurements for the temperature-only path.

    The MLX90640 stream no longer maps to ``MeasurementProposed``
    (citizens tap Capture to persist). Tests verifying the topic-
    routing now assert that temperature flows here while spo2 /
    heart_rate / height flow through ``captured_measurements``.
    """
    captured: list[LiveTemperatureUpdate] = []

    async def _handler(event: LiveTemperatureUpdate) -> None:
        captured.append(event)

    bus.subscribe(LiveTemperatureUpdate, _handler)
    return captured


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_device_config(db: Session, key: str, value: str) -> None:
    db.add(DeviceConfig(key=key, value=value, updated_at=utc_now_iso()))
    db.commit()
