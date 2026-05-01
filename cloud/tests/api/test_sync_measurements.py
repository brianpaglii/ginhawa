"""Kiosk-to-cloud measurement sync — POST /api/v1/sync/measurements."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_cloud.db.models import AuditLog, Measurement


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_citizen_record(citizen_id: str | None = None) -> dict:
    now = _utc_now_iso()
    return {
        "id": citizen_id or str(uuid.uuid4()),
        "rfid_uid": f"CARD_{uuid.uuid4().hex[:8].upper()}",
        "full_name": "Test Citizen",
        "dob": (date.today() - timedelta(days=365 * 30)).isoformat(),
        "sex": "M",
        "barangay": "Tibagan",
        "phone": None,
        "consent_version": "v1",
        "consent_given_at": now,
        "registered_at": now,
        "registered_by": None,
        "is_active": 1,
        "updated_at": now,
    }


def _make_session_record(*, citizen_id: str, device_id: str) -> dict:
    now = _utc_now_iso()
    return {
        "id": str(uuid.uuid4()),
        "citizen_id": citizen_id,
        "device_id": device_id,
        "started_at": now,
        "ended_at": now,
        "status": "completed",
        "error_reason": None,
        "measurement_path": "vitals",
        "printed_status": "printed_ok",
        "synced": 1,
        "updated_at": now,
    }


def _make_measurement_record(
    *,
    session_id: str,
    measurement_id: str | None = None,
    type: str = "systolic_bp",
    value: float = 120.0,
    unit: str = "mmHg",
    is_valid: int = 1,
    validation_notes: str | None = None,
    updated_at: str | None = None,
) -> dict:
    now = _utc_now_iso()
    return {
        "id": measurement_id or str(uuid.uuid4()),
        "session_id": session_id,
        "type": type,
        "value": value,
        "unit": unit,
        "source_device": "omron_hem7155t",
        "measured_at": now,
        "is_valid": is_valid,
        "validation_notes": validation_notes,
        "raw_json": None,
        "synced": 1,
        "updated_at": updated_at or now,
    }


@pytest.fixture
def kiosk_credential(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/device-credentials",
        json={"description": f"sync_meas_kiosk_{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def kiosk_client(
    client_unauthed: TestClient, kiosk_credential: dict
) -> Iterator[TestClient]:
    client_unauthed.headers["Authorization"] = f"Bearer {kiosk_credential['api_key']}"
    yield client_unauthed


@pytest.fixture
def seeded_session_id(kiosk_client: TestClient, kiosk_credential: dict) -> str:
    """Seed one citizen + one session via /sync so measurement FKs land."""
    citizen = _make_citizen_record()
    citizen_resp = kiosk_client.post("/api/v1/sync/citizens", json=[citizen])
    assert citizen_resp.status_code == 200, citizen_resp.text
    assert citizen_resp.json()["results"][0]["status"] == "created"

    session = _make_session_record(
        citizen_id=citizen["id"],
        device_id=kiosk_credential["device_id"],
    )
    session_resp = kiosk_client.post("/api/v1/sync/sessions", json=[session])
    assert session_resp.status_code == 200, session_resp.text
    assert session_resp.json()["results"][0]["status"] == "created"
    return session["id"]


# Verifies the bulk-create happy path: three measurements with a
# known session_id all return 'created'; rows land in measurements;
# audit rows attribute to actor_type='kiosk'.
# Would fail if _apply_create skipped the row, dropped the audit, or
# set a wrong actor_type.
def test_kiosk_uploads_three_new_measurements_all_created(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_session_id: str,
    db_session: Session,
) -> None:
    batch = [
        _make_measurement_record(session_id=seeded_session_id, type=t, value=v, unit=u)
        for t, v, u in [
            ("systolic_bp", 120.0, "mmHg"),
            ("diastolic_bp", 80.0, "mmHg"),
            ("heart_rate", 72.0, "bpm"),
        ]
    ]
    response = kiosk_client.post("/api/v1/sync/measurements", json=batch)
    assert response.status_code == 200, response.text
    assert all(r["status"] == "created" for r in response.json()["results"])

    db_session.expire_all()
    ids = [r["id"] for r in batch]
    rows = (
        db_session.execute(select(Measurement).where(Measurement.id.in_(ids)))
        .scalars()
        .all()
    )
    assert len(rows) == 3
    assert all(r.is_valid == 1 for r in rows)

    audit = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "sync_create",
                AuditLog.actor_type == "kiosk",
                AuditLog.object_id.in_(ids),
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 3


# Verifies the FK guard: a measurement whose session_id is unknown
# returns 'rejected' with error='session_not_found'. Other valid
# records in the same batch still succeed.
# Would fail if the session lookup were skipped, or if a single bad
# record rolled back the whole batch.
def test_kiosk_uploads_measurement_for_unknown_session_returns_rejected(
    kiosk_client: TestClient,
    seeded_session_id: str,
    db_session: Session,
) -> None:
    good = _make_measurement_record(session_id=seeded_session_id)
    orphan = _make_measurement_record(session_id=str(uuid.uuid4()))

    response = kiosk_client.post("/api/v1/sync/measurements", json=[good, orphan])
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()["results"]}
    assert by_id[good["id"]]["status"] == "created"
    assert by_id[orphan["id"]]["status"] == "rejected"
    assert by_id[orphan["id"]]["error"] == "session_not_found"

    db_session.expire_all()
    assert db_session.get(Measurement, orphan["id"]) is None
    assert db_session.get(Measurement, good["id"]) is not None


# Verifies the design choice that out-of-range readings are STORED
# (not rejected): a systolic_bp of 999 mmHg is well outside the
# physiological range [70, 250], yet the row lands with is_valid=0
# and validation_notes describing the violation. status='created'
# regardless.
# Would fail if the cloud rejected out-of-range values at the API
# layer — that would discard data the kiosk had already chosen to
# capture.
def test_kiosk_uploads_out_of_range_measurement_stores_with_is_valid_zero(
    kiosk_client: TestClient,
    seeded_session_id: str,
    db_session: Session,
) -> None:
    record = _make_measurement_record(
        session_id=seeded_session_id,
        type="systolic_bp",
        value=999.0,
        unit="mmHg",
    )
    response = kiosk_client.post("/api/v1/sync/measurements", json=[record])
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "created"

    db_session.expire_all()
    stored = db_session.get(Measurement, record["id"])
    assert stored is not None
    assert stored.value == 999.0
    assert stored.is_valid == 0
    assert stored.validation_notes is not None
    assert "outside" in stored.validation_notes.lower()


# Verifies idempotent replay: same id + same updated_at returns
# 'conflict_stale' on the second pass and does not duplicate the row.
# Would fail if the (id, updated_at) idempotency check were dropped,
# or if a re-upload created a fresh measurement.
def test_kiosk_uploads_measurement_idempotent_on_replay(
    kiosk_client: TestClient,
    seeded_session_id: str,
    db_session: Session,
) -> None:
    record = _make_measurement_record(session_id=seeded_session_id)
    first = kiosk_client.post("/api/v1/sync/measurements", json=[record])
    assert first.status_code == 200
    assert first.json()["results"][0]["status"] == "created"

    second = kiosk_client.post("/api/v1/sync/measurements", json=[record])
    assert second.status_code == 200
    assert second.json()["results"][0]["status"] == "conflict_stale"

    db_session.expire_all()
    rows = (
        db_session.execute(select(Measurement).where(Measurement.id == record["id"]))
        .scalars()
        .all()
    )
    assert len(rows) == 1


# Verifies the newer-wins update path: same measurement id, later
# updated_at, mutates the stored row and returns 'updated'. Also
# confirms re-validation on update — flipping the value to out-of-
# range while is_valid=1 still ends up with is_valid=0 stored.
# Would fail if _apply_update were skipped, dropped fields, or
# bypassed _resolve_validity.
def test_kiosk_uploads_measurement_with_newer_updated_at_updates(
    kiosk_client: TestClient,
    seeded_session_id: str,
    db_session: Session,
) -> None:
    measurement_id = str(uuid.uuid4())
    earlier = "2026-01-01T00:00:00+00:00"
    later = "2026-02-01T00:00:00+00:00"

    initial = _make_measurement_record(
        session_id=seeded_session_id,
        measurement_id=measurement_id,
        type="systolic_bp",
        value=120.0,
        unit="mmHg",
        updated_at=earlier,
    )
    assert (
        kiosk_client.post("/api/v1/sync/measurements", json=[initial]).status_code
        == 200
    )

    revised = _make_measurement_record(
        session_id=seeded_session_id,
        measurement_id=measurement_id,
        type="systolic_bp",
        value=999.0,  # out of range -> is_valid should flip to 0
        unit="mmHg",
        updated_at=later,
    )
    response = kiosk_client.post("/api/v1/sync/measurements", json=[revised])
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "updated"

    db_session.expire_all()
    stored = db_session.get(Measurement, measurement_id)
    assert stored is not None
    assert stored.value == 999.0
    assert stored.is_valid == 0
    assert stored.updated_at == later
    assert "outside" in (stored.validation_notes or "").lower()


# Verifies enum validation at the per-record layer: a measurement
# with a type not in the allowed Literal set is reported as
# 'rejected' (validation error) without poisoning the batch.
# Would fail if MeasurementSync's Literal were widened or if
# validation errors propagated as a batch-level 422.
def test_kiosk_uploads_measurement_with_invalid_type_enum_returns_rejected(
    kiosk_client: TestClient,
    seeded_session_id: str,
    db_session: Session,
) -> None:
    good = _make_measurement_record(session_id=seeded_session_id)
    bad = _make_measurement_record(session_id=seeded_session_id)
    bad["type"] = "definitely_not_a_real_type"

    response = kiosk_client.post("/api/v1/sync/measurements", json=[good, bad])
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()["results"]}
    assert by_id[good["id"]]["status"] == "created"
    assert by_id[bad["id"]]["status"] == "rejected"
    assert "type" in (by_id[bad["id"]]["error"] or "").lower()

    db_session.expire_all()
    assert db_session.get(Measurement, bad["id"]) is None
    assert db_session.get(Measurement, good["id"]) is not None
