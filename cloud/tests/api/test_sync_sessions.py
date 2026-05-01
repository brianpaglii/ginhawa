"""Kiosk-to-cloud session sync — POST /api/v1/sync/sessions."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ginhawa_cloud.db.models import AuditLog
from ginhawa_cloud.db.models import Session as SessionModel


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_citizen_record(citizen_id: str | None = None) -> dict:
    """A CitizenSync payload — used to seed via /sync/citizens before
    the sessions tests so the FK lands."""
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


def _make_session_record(
    *,
    citizen_id: str,
    device_id: str,
    session_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    status: str = "completed",
    error_reason: str | None = None,
    measurement_path: str | None = "vitals",
    printed_status: str = "printed_ok",
    updated_at: str | None = None,
) -> dict:
    now = _utc_now_iso()
    return {
        "id": session_id or str(uuid.uuid4()),
        "citizen_id": citizen_id,
        "device_id": device_id,
        "started_at": started_at or now,
        "ended_at": ended_at or now,
        "status": status,
        "error_reason": error_reason,
        "measurement_path": measurement_path,
        "printed_status": printed_status,
        "synced": 1,
        "updated_at": updated_at or now,
    }


@pytest.fixture
def kiosk_credential(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/device-credentials",
        json={"description": f"sync_session_kiosk_{uuid.uuid4().hex[:8]}"},
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
def seeded_citizen_id(kiosk_client: TestClient) -> str:
    """Seed one citizen via /sync/citizens so session FKs resolve."""
    citizen = _make_citizen_record()
    response = kiosk_client.post("/api/v1/sync/citizens", json=[citizen])
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "created"
    return citizen["id"]


# Verifies the bulk-create happy path: three sessions with the kiosk's
# device_id and a known citizen all return 'created'; rows land in
# the sessions table; three audit rows attribute to actor_type='kiosk'
# with action='sync_create'.
# Would fail if _apply_create skipped audit, dropped the row, or set
# the wrong actor_type.
def test_kiosk_uploads_three_new_sessions_all_created(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    device_id = kiosk_credential["device_id"]
    batch = [
        _make_session_record(citizen_id=seeded_citizen_id, device_id=device_id)
        for _ in range(3)
    ]
    response = kiosk_client.post("/api/v1/sync/sessions", json=batch)
    assert response.status_code == 200, response.text
    assert all(r["status"] == "created" for r in response.json()["results"])

    db_session.expire_all()
    ids = [r["id"] for r in batch]
    rows = (
        db_session.execute(select(SessionModel).where(SessionModel.id.in_(ids)))
        .scalars()
        .all()
    )
    assert len(rows) == 3

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


# Verifies idempotency: re-POSTing the same batch reports every record
# as 'conflict_stale' on the second pass and does not duplicate rows.
# Would fail if the (id, updated_at) idempotency check were skipped or
# if a re-upload created a fresh row.
def test_kiosk_re_uploads_same_batch_no_duplicates(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    device_id = kiosk_credential["device_id"]
    batch = [
        _make_session_record(citizen_id=seeded_citizen_id, device_id=device_id)
        for _ in range(3)
    ]
    first = kiosk_client.post("/api/v1/sync/sessions", json=batch)
    assert first.status_code == 200
    assert all(r["status"] == "created" for r in first.json()["results"])

    second = kiosk_client.post("/api/v1/sync/sessions", json=batch)
    assert second.status_code == 200
    assert all(r["status"] == "conflict_stale" for r in second.json()["results"])

    db_session.expire_all()
    total = db_session.execute(
        select(func.count(SessionModel.id)).where(
            SessionModel.id.in_([r["id"] for r in batch])
        )
    ).scalar_one()
    assert total == 3


# Verifies the newer-wins update path: same session id, later
# updated_at, mutates the stored row and returns 'updated'.
# Would fail if the updated_at comparison were inverted or if
# _apply_update dropped fields.
def test_kiosk_uploads_session_with_newer_updated_at_updates(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    device_id = kiosk_credential["device_id"]
    session_id = str(uuid.uuid4())
    earlier = "2026-01-01T00:00:00+00:00"
    later = "2026-02-01T00:00:00+00:00"

    initial = _make_session_record(
        citizen_id=seeded_citizen_id,
        device_id=device_id,
        session_id=session_id,
        status="in_progress",
        ended_at=None,
        printed_status="not_requested",
        updated_at=earlier,
    )
    assert kiosk_client.post("/api/v1/sync/sessions", json=[initial]).status_code == 200

    revised = _make_session_record(
        citizen_id=seeded_citizen_id,
        device_id=device_id,
        session_id=session_id,
        status="completed",
        printed_status="printed_ok",
        updated_at=later,
    )
    response = kiosk_client.post("/api/v1/sync/sessions", json=[revised])
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "updated"

    db_session.expire_all()
    stored = db_session.get(SessionModel, session_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.printed_status == "printed_ok"
    assert stored.updated_at == later


# Verifies the stale-write rejection path: same id, earlier
# updated_at, returns 'conflict_stale' and leaves the stored row
# untouched.
# Would fail if the stale check were inverted (older incoming
# overwriting newer stored).
def test_kiosk_uploads_session_with_stale_updated_at_skipped(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    device_id = kiosk_credential["device_id"]
    session_id = str(uuid.uuid4())
    later = "2026-02-01T00:00:00+00:00"
    earlier = "2026-01-01T00:00:00+00:00"

    fresh = _make_session_record(
        citizen_id=seeded_citizen_id,
        device_id=device_id,
        session_id=session_id,
        status="completed",
        updated_at=later,
    )
    assert kiosk_client.post("/api/v1/sync/sessions", json=[fresh]).status_code == 200

    stale = _make_session_record(
        citizen_id=seeded_citizen_id,
        device_id=device_id,
        session_id=session_id,
        status="in_progress",
        updated_at=earlier,
    )
    response = kiosk_client.post("/api/v1/sync/sessions", json=[stale])
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "conflict_stale"

    db_session.expire_all()
    stored = db_session.get(SessionModel, session_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.updated_at == later


# Verifies the FK guard: a session whose citizen_id is unknown to the
# cloud is reported as 'rejected' with error='citizen_not_found' and
# is NOT inserted. Other valid records in the same batch succeed.
# Would fail if the citizen lookup were dropped or if a single bad
# record rolled back the whole batch.
def test_kiosk_uploads_session_for_unknown_citizen_returns_rejected(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    device_id = kiosk_credential["device_id"]
    good = _make_session_record(citizen_id=seeded_citizen_id, device_id=device_id)
    orphan = _make_session_record(citizen_id=str(uuid.uuid4()), device_id=device_id)

    response = kiosk_client.post("/api/v1/sync/sessions", json=[good, orphan])
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()["results"]}
    assert by_id[good["id"]]["status"] == "created"
    assert by_id[orphan["id"]]["status"] == "rejected"
    assert by_id[orphan["id"]]["error"] == "citizen_not_found"

    db_session.expire_all()
    assert db_session.get(SessionModel, orphan["id"]) is None
    assert db_session.get(SessionModel, good["id"]) is not None


# Verifies the spoof guard: a session whose device_id does NOT match
# the authenticated kiosk's device_id is rejected with
# error='device_id_mismatch'.
# Would fail if the device_id comparison were skipped — a compromised
# kiosk could attribute sessions to other devices and confuse audit
# attribution.
def test_kiosk_uploads_session_claiming_different_device_id_returns_rejected(
    kiosk_client: TestClient,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    impostor_device_id = str(uuid.uuid4())
    record = _make_session_record(
        citizen_id=seeded_citizen_id, device_id=impostor_device_id
    )
    response = kiosk_client.post("/api/v1/sync/sessions", json=[record])
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "rejected"
    assert result["error"] == "device_id_mismatch"

    db_session.expire_all()
    assert db_session.get(SessionModel, record["id"]) is None


# Verifies enum validation at the per-record layer: a session with a
# status not in {in_progress, completed, aborted, error} is reported
# as 'rejected' (validation error) without poisoning the batch.
# Would fail if SessionSync's Literal type were widened or if
# validation errors propagated as 422 across the whole batch.
def test_kiosk_uploads_session_with_invalid_status_enum_returns_rejected(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    seeded_citizen_id: str,
    db_session: Session,
) -> None:
    device_id = kiosk_credential["device_id"]
    good = _make_session_record(citizen_id=seeded_citizen_id, device_id=device_id)
    bad = _make_session_record(citizen_id=seeded_citizen_id, device_id=device_id)
    bad["status"] = "totally_invalid_status"

    response = kiosk_client.post("/api/v1/sync/sessions", json=[good, bad])
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()["results"]}
    assert by_id[good["id"]]["status"] == "created"
    assert by_id[bad["id"]]["status"] == "rejected"
    assert "status" in (by_id[bad["id"]]["error"] or "").lower()

    db_session.expire_all()
    assert db_session.get(SessionModel, bad["id"]) is None
    assert db_session.get(SessionModel, good["id"]) is not None
