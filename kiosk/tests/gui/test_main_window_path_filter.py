"""Path-vs-type filter at the measurement-proposed boundary.

Per docs/audits/2026-05-13-scale-prefiring-audit.md, the Xiaomi scale's
BLE scanner and the ESP32 MQTT publishers are always-on and have no
knowledge of the active session's measurement_path. Real measurements
arriving outside the active path (e.g., a stray weight advert during a
vitals_only session) must be logged and dropped before persistence so
they never accumulate as audit-trail noise tagged to a session the
citizen never opted into.

These tests pin the four documented behaviors:

1. Real measurement of the wrong type for the path → dropped + warning
2. Real measurement of the right type → persisted (regression guard)
3. Offline placeholders (is_valid=0) → persisted regardless of path
4. No active session → existing "measurement_without_session" path
   stays, the new filter is never reached
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
import structlog
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import Citizen, Measurement
from ginhawa_kiosk.fsm import EventBus, MeasurementProposed, SessionFSM
from ginhawa_kiosk.gui.main_window import KioskMainWindow
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


def _drive_to_path(
    fsm: SessionFSM, db_session: Session, rfid_uid: str, path: str
) -> None:
    fsm.rfid_scanned(rfid_uid)
    fsm.citizen_identified(_make_citizen(db_session, rfid_uid))
    fsm.language_chosen("en")
    fsm.path_selected(path)  # type: ignore[arg-type]
    assert fsm.current_session is not None
    assert fsm.current_session.measurement_path == path


def _rows_for(db: Session, session_id: str, measurement_type: str) -> list[Measurement]:
    return list(
        db.execute(
            select(Measurement).where(
                Measurement.session_id == session_id,
                Measurement.type == measurement_type,
            )
        ).scalars()
    )


# Verifies a weight reading arriving during a vitals_only session is
# dropped, not persisted, and the structured warning is emitted so a
# bench operator can grep for path mismatches. Models the scale-
# prefire bug documented in the 2026-05-13 audit.
# Mortality: would fail if the path filter were removed or if it ran
# *after* the DB insert.
@pytest.mark.asyncio
async def test_weight_during_vitals_only_dropped(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_to_path(fsm, db_session, "CARD_VITALS_WEIGHT", "vitals")
    assert fsm.current_session is not None

    with structlog.testing.capture_logs() as logs:
        await bus.publish(
            MeasurementProposed(
                measurement_type="weight",
                value=70.0,
                unit="kg",
                source_device="xiaomi_s200_ble",
                claimed_is_valid=True,
            )
        )

    assert _rows_for(db_session, fsm.current_session.id, "weight") == []
    dropped = [
        e
        for e in logs
        if e.get("event") == "main_window.measurement_path_mismatch_dropped"
    ]
    assert len(dropped) == 1
    assert dropped[0]["measurement_type"] == "weight"
    assert dropped[0]["measurement_path"] == "vitals"


# Verifies a height reading arriving during a vitals_only session is
# also dropped (Section 4 of the audit: ESP32-B prefires height the
# same way the Xiaomi scale prefires weight).
# Mortality: would fail if the filter only covered the weight type and
# left the rest of _ANTHRO_TYPES leaking through.
@pytest.mark.asyncio
async def test_height_during_vitals_only_dropped(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_to_path(fsm, db_session, "CARD_VITALS_HEIGHT", "vitals")
    assert fsm.current_session is not None

    await bus.publish(
        MeasurementProposed(
            measurement_type="height",
            value=170.0,
            unit="cm",
            source_device="esp32_b_vl53l0x",
            claimed_is_valid=True,
        )
    )

    assert _rows_for(db_session, fsm.current_session.id, "height") == []


# Verifies an SpO2 reading arriving during an anthropometric_only
# session is dropped — ESP32-A keeps publishing SpO2 even when the
# citizen isn't using the finger sensor. Mirror of the weight-during-
# vitals case.
# Mortality: would fail if the filter were one-sided (only blocking
# anthro types during vitals, not vitals types during anthro).
@pytest.mark.asyncio
async def test_spo2_during_anthro_only_dropped(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_to_path(fsm, db_session, "CARD_ANTHRO_SPO2", "anthropometric")
    assert fsm.current_session is not None

    await bus.publish(
        MeasurementProposed(
            measurement_type="spo2",
            value=97.0,
            unit="%",
            source_device="esp32_a_max30100",
            claimed_is_valid=True,
        )
    )

    assert _rows_for(db_session, fsm.current_session.id, "spo2") == []


# Regression guard: a weight reading arriving during the correct
# (anthropometric) path must still persist exactly as before the
# filter was added.
# Mortality: would fail if the filter incorrectly rejected matching
# types — i.e., the legitimate happy path stops working.
@pytest.mark.asyncio
async def test_weight_during_anthro_only_accepted(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_to_path(fsm, db_session, "CARD_ANTHRO_WEIGHT", "anthropometric")
    assert fsm.current_session is not None

    await bus.publish(
        MeasurementProposed(
            measurement_type="weight",
            value=70.0,
            unit="kg",
            source_device="xiaomi_s200_ble",
            claimed_is_valid=True,
        )
    )

    rows = _rows_for(db_session, fsm.current_session.id, "weight")
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(70.0)
    assert rows[0].is_valid == 1


# Full-check sessions accept every measurement type — they are the
# pre-fix happy path for a complete visit.
# Mortality: would fail if the helper falsely rejected "full" as an
# unknown path and the entire full-check flow stopped persisting.
@pytest.mark.asyncio
async def test_full_check_accepts_all(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_to_path(fsm, db_session, "CARD_FULL", "full")
    assert fsm.current_session is not None
    session_id = fsm.current_session.id

    # SpO2's receipt-boundary session_floor gate (ADR-0023) requires
    # the MQTT-stamped captured_at — for the kiosk-stamped fake we
    # use "now" so it sits at the floor, well inside the skew.
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    payloads: list[tuple[str, float, str, str]] = [
        ("systolic_bp", 128.0, "mmHg", "omron_hem7155t"),
        ("diastolic_bp", 82.0, "mmHg", "omron_hem7155t"),
        ("heart_rate", 72.0, "bpm", "omron_hem7155t"),
        ("spo2", 97.0, "%", "esp32_a_max30100"),
        ("temperature", 36.7, "C", "esp32_a_mlx90640"),
        ("weight", 70.0, "kg", "xiaomi_s200_ble"),
        ("height", 170.0, "cm", "esp32_b_vl53l0x"),
    ]
    for measurement_type, value, unit, source in payloads:
        await bus.publish(
            MeasurementProposed(
                measurement_type=measurement_type,
                value=value,
                unit=unit,
                source_device=source,
                claimed_is_valid=True,
                captured_at=now_iso if measurement_type == "spo2" else None,
            )
        )

    for measurement_type, *_ in payloads:
        rows = _rows_for(db_session, session_id, measurement_type)
        assert len(rows) == 1, (
            f"full_check path should persist {measurement_type!r} exactly once"
        )


# Offline placeholders ride is_valid=0 to bypass validation; the path
# filter must let them through too because the FSM itself seeds them
# with full state knowledge on MEASURING_* entry. Without this
# exemption the path-completion machinery would hang waiting for
# placeholder rows the filter just dropped.
# Mortality: would fail if the filter were applied unconditionally
# (not gated on is_valid_int == 1).
@pytest.mark.asyncio
async def test_offline_placeholder_during_wrong_path_still_persisted(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    _drive_to_path(fsm, db_session, "CARD_PLACEHOLDER", "vitals")
    assert fsm.current_session is not None

    # A vitals_only session would never SEE an anthro placeholder
    # seeded for real (the seeder picks types by state), but we
    # publish one synthetically to pin that the exemption is purely
    # is_valid-driven and not type-driven.
    await bus.publish(
        MeasurementProposed(
            measurement_type="weight",
            value=0.0,
            unit="kg",
            source_device="(offline)",
            claimed_is_valid=False,
            validation_notes="sensor_offline",
        )
    )

    rows = _rows_for(db_session, fsm.current_session.id, "weight")
    assert len(rows) == 1
    assert rows[0].is_valid == 0
    assert rows[0].validation_notes == "sensor_offline"


# A measurement arriving before the session row exists (e.g., during
# IDLE or LANGUAGE_SELECT) takes the pre-existing "no session" branch
# and is dropped with the original warning. The new path filter must
# not run for this case — it would crash on a None current_session.
# Mortality: would fail if the path filter were placed BEFORE the
# current_session-None guard.
@pytest.mark.asyncio
async def test_no_session_drops_via_existing_warning(
    main_window: KioskMainWindow,
    fsm: SessionFSM,
    bus: EventBus,
    db_session: Session,
) -> None:
    # FSM is still in IDLE — no session row has been created.
    assert fsm.current_session is None

    with structlog.testing.capture_logs() as logs:
        await bus.publish(
            MeasurementProposed(
                measurement_type="weight",
                value=70.0,
                unit="kg",
                source_device="xiaomi_s200_ble",
                claimed_is_valid=True,
            )
        )

    no_session_warnings = [
        e for e in logs if e.get("event") == "main_window.measurement_without_session"
    ]
    path_warnings = [
        e
        for e in logs
        if e.get("event") == "main_window.measurement_path_mismatch_dropped"
    ]
    assert len(no_session_warnings) == 1
    assert path_warnings == []
