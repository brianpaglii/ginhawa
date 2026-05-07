"""FSM-side resilience when sensor transports (BLE / MQTT) are down.

Bench evidence (2026-05-07): 20 sessions in the DB, zero completed
because MQTT was offline and the FSM hung in MEASURING_VITALS /
MEASURING_ANTHRO waiting for SpO2 / temperature / height that
would never arrive. The fix seeds is_valid=0 placeholder rows
when a sensor reports ``is_running=False`` at state entry, so
``_maybe_advance_measurement_path`` can fire path-complete on the
real measurements that DO arrive.

Tests in this module construct ``KioskMainWindow`` with a
hand-rolled ``sensors`` mapping of stubbed Sensor instances,
toggle their ``is_running`` to simulate transport state, and
drive the FSM through the entry into the measuring states.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import Citizen, Measurement
from ginhawa_kiosk.fsm import (
    EventBus,
    MeasurementProposed,
    SessionFSM,
)
from ginhawa_kiosk.gui.main_window import KioskMainWindow
from ginhawa_kiosk.sensors.base import Sensor
from ginhawa_kiosk.services.printer import MockPrinterService


class _StubSensor(Sensor):
    """Minimal Sensor stub whose is_running can be toggled by tests."""

    def __init__(self, *, running: bool) -> None:
        self._running = running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


def _make_citizen(db_session: Session, rfid: str) -> Citizen:
    citizen = Citizen(
        id=f"citizen-{rfid}",
        rfid_uid=rfid,
        full_name="Test Citizen",
        dob="1990-01-01",
        sex="M",
        barangay="Tibagan",
        consent_version="v1",
        consent_given_at="2026-01-01T00:00:00+00:00",
        registered_at="2026-01-01T00:00:00+00:00",
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    db_session.add(citizen)
    db_session.flush()
    return citizen


def _build_window(
    *,
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    sensors: dict[str, Sensor],
    captured_measurements: list[MeasurementProposed],
) -> KioskMainWindow:
    bus.subscribe(MeasurementProposed, _capture_into(captured_measurements))
    w = KioskMainWindow(
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        printer=MockPrinterService(),
        citizen_lookup=AsyncMock(return_value=None),
        sensors=sensors,
        deployment_barangay="Tibagan",
        device_id="test-device",
    )
    qtbot.addWidget(w)
    return w


def _capture_into(sink: list[MeasurementProposed]) -> Any:
    async def handler(event: MeasurementProposed) -> None:
        sink.append(event)

    return handler


@pytest.fixture
def captured_proposals() -> Iterator[list[MeasurementProposed]]:
    """Collected MeasurementProposed events fired through the bus."""
    proposals: list[MeasurementProposed] = []
    yield proposals


# Verifies that when MQTT is down at the start of MEASURING_VITALS,
# the FSM seeds placeholder rows for spo2 + temperature (the MQTT-
# served types in that path), and the BP triple is left to the
# Omron sensor to fill in for real. Mortality: would fail if the
# seeder dropped the offline detection or if it accidentally seeded
# BP placeholders when the BP sensor was running.
@pytest.mark.asyncio
async def test_measuring_vitals_with_mqtt_offline(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    captured_proposals: list[MeasurementProposed],
) -> None:
    sensors: dict[str, Sensor] = {
        "omron_bp": _StubSensor(running=True),
        "xiaomi_scale": _StubSensor(running=True),
        "mqtt_sensors": _StubSensor(running=False),
    }
    _build_window(
        qtbot=qtbot,
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        sensors=sensors,
        captured_measurements=captured_proposals,
    )

    fsm.rfid_scanned("CARD_OFFLINE_VITALS")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_OFFLINE_VITALS"))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    # MEASURING_VITALS is now active; the seeder fired during the
    # state-entry hook. Drain the bus so the proposals reach the
    # capture sink (and the persist path).
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    seeded_types = {
        e.measurement_type for e in captured_proposals if e.source_device == "(offline)"
    }
    assert seeded_types == {"spo2", "temperature"}
    for e in captured_proposals:
        if e.source_device == "(offline)":
            assert e.claimed_is_valid is False
            assert e.validation_notes == "sensor_offline"


# Verifies the MEASURING_ANTHRO path: weight is BLE (xiaomi_scale,
# online here) and height is MQTT (offline). The seeder should
# emit one placeholder for height and leave weight to the scale.
@pytest.mark.asyncio
async def test_measuring_anthro_with_mqtt_offline(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    captured_proposals: list[MeasurementProposed],
) -> None:
    sensors: dict[str, Sensor] = {
        "omron_bp": _StubSensor(running=True),
        "xiaomi_scale": _StubSensor(running=True),
        "mqtt_sensors": _StubSensor(running=False),
    }
    _build_window(
        qtbot=qtbot,
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        sensors=sensors,
        captured_measurements=captured_proposals,
    )

    fsm.rfid_scanned("CARD_OFFLINE_ANTHRO")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_OFFLINE_ANTHRO"))
    fsm.language_chosen("en")
    fsm.path_selected("anthropometric")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    seeded_types = {
        e.measurement_type for e in captured_proposals if e.source_device == "(offline)"
    }
    assert seeded_types == {"height"}


# Verifies the no-op path: when every responsible sensor is online,
# the seeder produces no placeholders. Mortality: would fail if the
# seeder fell into the offline branch unconditionally.
@pytest.mark.asyncio
async def test_no_placeholders_when_all_sensors_online(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    captured_proposals: list[MeasurementProposed],
) -> None:
    sensors: dict[str, Sensor] = {
        "omron_bp": _StubSensor(running=True),
        "xiaomi_scale": _StubSensor(running=True),
        "mqtt_sensors": _StubSensor(running=True),
    }
    _build_window(
        qtbot=qtbot,
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        sensors=sensors,
        captured_measurements=captured_proposals,
    )

    fsm.rfid_scanned("CARD_ALL_ONLINE")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_ALL_ONLINE"))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    offline = [e for e in captured_proposals if e.source_device == "(offline)"]
    assert offline == []


# Verifies the end-to-end advance: with MQTT offline placeholders
# seeded, real BP measurements coming in via the Omron path drive
# the FSM all the way to REPORT. Mortality: would fail if the
# captured_types set wasn't being populated for is_valid=0 rows
# (the original hang root cause), or if the placeholder persist
# path was broken.
@pytest.mark.asyncio
async def test_fsm_advances_after_real_bp_with_offline_spo2(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    captured_proposals: list[MeasurementProposed],
) -> None:
    sensors: dict[str, Sensor] = {
        "omron_bp": _StubSensor(running=True),
        "xiaomi_scale": _StubSensor(running=True),
        "mqtt_sensors": _StubSensor(running=False),
    }
    _build_window(
        qtbot=qtbot,
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        sensors=sensors,
        captured_measurements=captured_proposals,
    )

    fsm.rfid_scanned("CARD_E2E")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_E2E"))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    # Let the seeder + persist path drain.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Publish "real" BP triple as the Omron sensor would.
    for measurement_type, value, unit in (
        ("systolic_bp", 128.0, "mmHg"),
        ("diastolic_bp", 82.0, "mmHg"),
        ("heart_rate", 74.0, "bpm"),
    ):
        await bus.publish(
            MeasurementProposed(
                measurement_type=measurement_type,
                value=value,
                unit=unit,
                source_device="omron_hem7155t",
                claimed_is_valid=True,
            )
        )
    await asyncio.sleep(0)

    # FSM should have advanced past MEASURING_VITALS — vitals path
    # in fsm goes to REPORT for vitals-only.
    assert fsm.state == "report"

    # DB shape: at least 5 measurement rows for this session
    # (3 real BP + 2 offline placeholders for spo2 + temperature).
    assert fsm.current_session is not None
    rows = (
        db_session.query(Measurement)
        .filter(Measurement.session_id == fsm.current_session.id)
        .all()
    )
    by_type = {r.type: r for r in rows}
    assert by_type["systolic_bp"].is_valid == 1
    assert by_type["spo2"].is_valid == 0
    assert by_type["spo2"].validation_notes == "sensor_offline"
    assert by_type["temperature"].is_valid == 0
    assert by_type["temperature"].validation_notes == "sensor_offline"


# Verifies that a re-entry into the same measuring state during
# one session does NOT double-seed placeholders. Mortality: would
# fail if the _offline_placeholders_seeded gate were dropped.
@pytest.mark.asyncio
async def test_seeder_is_idempotent_within_a_session(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    captured_proposals: list[MeasurementProposed],
) -> None:
    sensors: dict[str, Sensor] = {
        "omron_bp": _StubSensor(running=True),
        "xiaomi_scale": _StubSensor(running=True),
        "mqtt_sensors": _StubSensor(running=False),
    }
    window = _build_window(
        qtbot=qtbot,
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        sensors=sensors,
        captured_measurements=captured_proposals,
    )

    fsm.rfid_scanned("CARD_IDEMPOTENT")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_IDEMPOTENT"))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    first_seed = sum(1 for e in captured_proposals if e.source_device == "(offline)")
    assert first_seed == 2  # spo2 + temperature

    # Re-call the seeder explicitly — same state, same session.
    window._seed_offline_sensor_placeholders("measuring_vitals")
    await asyncio.sleep(0)
    second_seed = sum(1 for e in captured_proposals if e.source_device == "(offline)")
    assert second_seed == first_seed  # no duplicates


# Verifies that ending a session (state → IDLE) clears the seeded
# gate so the NEXT session's MEASURING_* entry re-evaluates the
# offline set. Mortality: would fail if the seeder kept its gate
# across sessions, leading to subsequent sessions hanging the
# moment a sensor that came online during session 1 went offline
# again before session 2.
def test_seeder_resets_between_sessions(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
    captured_proposals: list[MeasurementProposed],
) -> None:
    sensors: dict[str, Sensor] = {
        "omron_bp": _StubSensor(running=True),
        "xiaomi_scale": _StubSensor(running=True),
        "mqtt_sensors": _StubSensor(running=False),
    }
    window = _build_window(
        qtbot=qtbot,
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        sensors=sensors,
        captured_measurements=captured_proposals,
    )
    # Drive once into MEASURING_VITALS so the gate is set.
    fsm.rfid_scanned("CARD_RESET_1")
    fsm.citizen_identified(_make_citizen(db_session, "CARD_RESET_1"))
    fsm.language_chosen("en")
    fsm.path_selected("vitals")
    assert "measuring_vitals" in window._offline_placeholders_seeded

    # Tear down to IDLE via cancel → aborted → IDLE.
    fsm.cancel()
    fsm.acknowledge()
    assert fsm.state == "idle"
    # On entry to IDLE, the gate should have cleared.
    assert window._offline_placeholders_seeded == set()
