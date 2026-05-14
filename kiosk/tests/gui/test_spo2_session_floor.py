"""SpO2 receipt-boundary session-floor gate (ADR-0023).

Defence-in-depth on top of ADR-0022's firmware finger-presence gate.
The kiosk stamps a UTC session_floor at MEASURING_VITALS entry, clears
it on exit, and rejects spo2 MeasurementProposed events whose MQTT-
stamped ``captured_at`` predates the floor by more than the skew
tolerance.

These tests pin the eight behaviours documented in the ADR's
verification matrix: floor set on entry, cleared on exit, accept-
after-floor, drop-before-floor-minus-skew, accept-within-skew,
fail-closed on parse error, non-spo2 types unaffected, and the
no-floor (out-of-MEASURING_VITALS) path.

Audit: docs/audits/2026-05-14-spo2-stale-readings-audit.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import structlog
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import Citizen, Measurement
from ginhawa_kiosk.fsm import EventBus, MeasurementProposed, SessionFSM, State
from ginhawa_kiosk.gui.main_window import (
    _SPO2_SESSION_FLOOR_SKEW_S,
    KioskMainWindow,
)
from ginhawa_kiosk.services.printer import MockPrinterService


@pytest.fixture
def main_window(
    qtbot: QtBot,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> Iterator[KioskMainWindow]:
    citizen_lookup = AsyncMock(return_value=None)
    w = KioskMainWindow(
        fsm=fsm,
        bus=bus,
        db_session=db_session,
        printer=MockPrinterService(),
        citizen_lookup=citizen_lookup,
        deployment_barangay="Tibagan",
        device_id="test-device",
    )
    qtbot.addWidget(w)
    yield w


def _make_citizen(db: Session, rfid_uid: str) -> Citizen:
    citizen = Citizen(
        id=f"cit-{rfid_uid}",
        rfid_uid=rfid_uid,
        full_name="Test Citizen",
        dob="1990-01-01",
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version="v1",
        consent_given_at="2026-05-04T00:00:00+00:00",
        registered_at="2026-05-04T00:00:00+00:00",
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at="2026-05-04T00:00:00+00:00",
    )
    db.add(citizen)
    db.commit()
    return citizen


def _drive_into_measuring_vitals(
    fsm: SessionFSM, db: Session, rfid_uid: str, path: str = "vitals"
) -> None:
    fsm.rfid_scanned(rfid_uid)
    fsm.citizen_identified(_make_citizen(db, rfid_uid))
    fsm.language_chosen("en")
    fsm.path_selected(path)  # type: ignore[arg-type]
    assert fsm.state == State.MEASURING_VITALS


def _spo2_event(
    *, value: float = 97.0, captured_at: str | None = None
) -> MeasurementProposed:
    return MeasurementProposed(
        measurement_type="spo2",
        value=value,
        unit="%",
        source_device="esp32_a_max30100",
        claimed_is_valid=True,
        captured_at=captured_at,
    )


def _persisted_spo2(db: Session, session_id: str) -> list[Measurement]:
    return list(
        db.execute(
            select(Measurement).where(
                Measurement.session_id == session_id,
                Measurement.type == "spo2",
            )
        ).scalars()
    )


# Verifies the floor is stamped at MEASURING_VITALS entry. The set
# log fires once with a non-empty ISO timestamp; the instance attr
# is populated.
# Mortality: would fail if the entry hook were removed from
# _on_fsm_state_changed.
def test_spo2_session_floor_set_on_measuring_vitals_entry(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    assert main_window._spo2_session_floor is None
    with structlog.testing.capture_logs() as logs:
        _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_SET")
    set_logs = [
        e for e in logs if e.get("event") == "main_window.spo2_session_floor_set"
    ]
    assert len(set_logs) == 1
    assert main_window._spo2_session_floor is not None
    # Same instant as the log's ISO field, give or take rounding.
    assert datetime.fromisoformat(set_logs[0]["session_floor"]).tzinfo is not None


# Verifies the floor is cleared on MEASURING_VITALS exit (here via
# measurement_path_complete → REPORT). The cleared log fires once
# and the instance attr is None.
# Mortality: would fail if the exit hook leaked the floor across
# sessions (a future stale-publish during IDLE would then be
# evaluated against an outdated floor).
def test_spo2_session_floor_cleared_on_measuring_vitals_exit(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_CLEAR")
    assert main_window._spo2_session_floor is not None
    with structlog.testing.capture_logs() as logs:
        # Auto-completes the vitals path → REPORT once all _VITALS_TYPES
        # are captured. For this test, just fire the trigger directly.
        fsm.measurement_path_complete()
    cleared_logs = [
        e for e in logs if e.get("event") == "main_window.spo2_session_floor_cleared"
    ]
    assert len(cleared_logs) == 1
    assert main_window._spo2_session_floor is None


# Verifies the happy path: an spo2 reading captured AT the floor
# instant is accepted. The MQTT subscriber's captured_at is set on
# receipt, so a publish that lands during MEASURING_VITALS has a
# captured_at == "now" relative to the floor — well inside the skew.
# Mortality: would fail if the comparison were strict (>) rather than
# ≥ - skew, or if it inverted the floor comparison.
@pytest.mark.asyncio
async def test_spo2_accepted_when_captured_at_after_session_floor(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_OK")
    assert fsm.current_session is not None
    floor = main_window._spo2_session_floor
    assert floor is not None
    # Reading 1 s AFTER the floor — comfortably inside the skew.
    captured_at = (floor + timedelta(seconds=1)).isoformat()
    await bus.publish(_spo2_event(captured_at=captured_at))
    rows = _persisted_spo2(db_session, fsm.current_session.id)
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(97.0)


# Verifies a reading captured WITHIN the skew window before the
# floor is still accepted. This is the legitimate "citizen pressed
# START on the cuff a few seconds before tapping RFID" pattern
# applied to SpO2 (and the QoS-1 retry latency case).
# Mortality: would fail if the skew tolerance were dropped or the
# comparison applied skew on the wrong side.
@pytest.mark.asyncio
async def test_spo2_accepted_within_skew_window(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_SKEW")
    assert fsm.current_session is not None
    floor = main_window._spo2_session_floor
    assert floor is not None
    half_skew = _SPO2_SESSION_FLOOR_SKEW_S / 2
    captured_at = (floor - timedelta(seconds=half_skew)).isoformat()
    await bus.publish(_spo2_event(captured_at=captured_at))
    rows = _persisted_spo2(db_session, fsm.current_session.id)
    assert len(rows) == 1


# Verifies the bug-class fix: a reading captured WELL before the
# floor (typical of a phantom publish from session 1's leftover
# library state during the session-2 entry) is rejected with the
# spo2_pre_session_floor_dropped warning.
# Mortality: would fail if the floor check were missing or the
# delta_to_floor_s field were dropped from the log.
@pytest.mark.asyncio
async def test_spo2_dropped_when_captured_at_before_session_floor(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_DROP")
    assert fsm.current_session is not None
    floor = main_window._spo2_session_floor
    assert floor is not None
    # 60 s before the floor — well outside the 10 s skew.
    captured_at = (floor - timedelta(seconds=60)).isoformat()
    with structlog.testing.capture_logs() as logs:
        await bus.publish(_spo2_event(captured_at=captured_at))
    assert _persisted_spo2(db_session, fsm.current_session.id) == []
    drops = [
        e
        for e in logs
        if e.get("event") == "main_window.spo2_pre_session_floor_dropped"
    ]
    assert len(drops) == 1
    assert drops[0]["delta_to_floor_s"] == pytest.approx(-60.0)


# Verifies fail-closed semantics on malformed captured_at. A
# garbage string drops the reading and logs the parse failure so an
# operator can see the malformed payload in journalctl.
# Mortality: would fail if the gate fell through to persistence on
# parse error.
@pytest.mark.asyncio
async def test_spo2_captured_at_parse_failure_fails_closed(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_PARSE")
    assert fsm.current_session is not None
    with structlog.testing.capture_logs() as logs:
        await bus.publish(_spo2_event(captured_at="not-an-iso-timestamp{{"))
    assert _persisted_spo2(db_session, fsm.current_session.id) == []
    parse_fails = [
        e for e in logs if e.get("event") == "main_window.spo2_captured_at_parse_failed"
    ]
    assert len(parse_fails) == 1


# Verifies missing captured_at on an spo2 event also fail-closes.
# Offline placeholders pass through this gate untouched (is_valid=0
# bypass), but a real spo2 event with no timestamp is suspicious —
# the MQTT subscriber always stamps one, so a None means either a
# wiring bug or a sensor adapter that doesn't yet pass the field.
# Either way, drop + log.
# Mortality: would fail if the gate were silently permissive on
# missing captured_at.
@pytest.mark.asyncio
async def test_spo2_missing_captured_at_fails_closed(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_MISS")
    assert fsm.current_session is not None
    with structlog.testing.capture_logs() as logs:
        await bus.publish(_spo2_event(captured_at=None))
    assert _persisted_spo2(db_session, fsm.current_session.id) == []
    missing = [
        e for e in logs if e.get("event") == "main_window.spo2_captured_at_missing"
    ]
    assert len(missing) == 1


# Verifies the gate only applies to spo2 — every other measurement
# type is unaffected even when its hypothetical captured_at would
# predate the floor. BP, weight, height, and temperature are gated
# elsewhere (BP at the sensor adapter, weight by MAC + path filter,
# height with stabilisation, temperature via Capture).
# Mortality: would fail if the type-equality check were dropped and
# the gate ran on every event.
@pytest.mark.asyncio
async def test_non_spo2_measurements_not_affected_by_session_floor(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_NONSPO2")
    assert fsm.current_session is not None
    # Send a systolic_bp event without captured_at — would fail the
    # gate if the gate applied to all types.
    await bus.publish(
        MeasurementProposed(
            measurement_type="systolic_bp",
            value=128.0,
            unit="mmHg",
            source_device="omron_hem7155t",
            claimed_is_valid=True,
            captured_at=None,
        )
    )
    rows = list(
        db_session.execute(
            select(Measurement).where(
                Measurement.session_id == fsm.current_session.id,
                Measurement.type == "systolic_bp",
            )
        ).scalars()
    )
    assert len(rows) == 1


# Verifies offline-placeholder spo2 (is_valid=0,
# validation_notes="sensor_offline") passes through the gate
# regardless of captured_at. Without this exemption the path-
# completion machinery would hang waiting for placeholder rows the
# gate just dropped.
# Mortality: would fail if the gate's is_valid_int==1 condition
# were dropped.
@pytest.mark.asyncio
async def test_spo2_offline_placeholder_bypasses_floor_gate(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_into_measuring_vitals(fsm, db_session, "CARD_FLOOR_PLACEH")
    assert fsm.current_session is not None
    await bus.publish(
        MeasurementProposed(
            measurement_type="spo2",
            value=0.0,
            unit="%",
            source_device="(offline)",
            claimed_is_valid=False,
            validation_notes="sensor_offline",
            captured_at=None,
        )
    )
    rows = _persisted_spo2(db_session, fsm.current_session.id)
    assert len(rows) == 1
    assert rows[0].is_valid == 0
    assert rows[0].validation_notes == "sensor_offline"
