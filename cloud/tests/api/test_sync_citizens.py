"""Kiosk-to-cloud citizen sync — POST /api/v1/sync/citizens.

These tests exercise the sync endpoint via the production app (unlike
test_kiosk_auth.py, which uses an isolated probe app for the auth
dependency in isolation). They use the admin ``client`` fixture to
provision a device credential, then swap the Authorization header on
the same TestClient to the kiosk's plaintext API key for the sync
calls.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ginhawa_cloud.db.models import AuditLog, Citizen


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(days: int = 365 * 30) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _make_record(
    *,
    citizen_id: str | None = None,
    rfid_uid: str | None = None,
    full_name: str = "Juan Dela Cruz",
    dob: str | None = None,
    sex: str = "M",
    barangay: str = "Tibagan",
    phone: str | None = None,
    consent_version: str = "v1",
    consent_given_at: str | None = None,
    registered_at: str | None = None,
    registered_by: str | None = None,
    is_active: int = 1,
    updated_at: str | None = None,
) -> dict:
    now = _utc_now_iso()
    return {
        "id": citizen_id or str(uuid.uuid4()),
        "rfid_uid": rfid_uid or f"CARD_{uuid.uuid4().hex[:8].upper()}",
        "full_name": full_name,
        "dob": dob or _past_iso(),
        "sex": sex,
        "barangay": barangay,
        "phone": phone,
        "consent_version": consent_version,
        "consent_given_at": consent_given_at or now,
        "registered_at": registered_at or now,
        "registered_by": registered_by,
        "is_active": is_active,
        "updated_at": updated_at or now,
    }


@pytest.fixture
def kiosk_credential(client: TestClient) -> dict:
    """Provision a device credential via admin POST and return the
    response body. Includes the plaintext api_key (only here, never
    stored)."""
    response = client.post(
        "/api/v1/device-credentials",
        json={"description": f"sync_test_{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def kiosk_client(
    client_unauthed: TestClient, kiosk_credential: dict
) -> Iterator[TestClient]:
    """A TestClient whose Authorization header carries the kiosk's
    Bearer API key. Note: ``client`` fixture has already mutated
    ``client_unauthed.headers`` to set the admin token; we overwrite
    it here so the sync call hits ``get_current_kiosk`` instead of
    the JWT path."""
    client_unauthed.headers["Authorization"] = f"Bearer {kiosk_credential['api_key']}"
    yield client_unauthed


# Verifies the bulk-create happy path: three brand-new UUIDs go in,
# all three come back status='created', and three new citizen rows
# plus three audit rows (actor_type='kiosk' for the self-service
# default) appear.
# Would fail if the bulk insert path were broken or if audit rows
# were not written.
def test_kiosk_uploads_three_new_citizens_all_created(
    kiosk_client: TestClient, db_session: Session
) -> None:
    batch = [_make_record() for _ in range(3)]
    response = kiosk_client.post("/api/v1/sync/citizens", json=batch)
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["results"]) == 3
    assert all(r["status"] == "created" for r in body["results"])

    db_session.expire_all()
    citizen_ids = [r["id"] for r in batch]
    rows = (
        db_session.execute(select(Citizen).where(Citizen.id.in_(citizen_ids)))
        .scalars()
        .all()
    )
    assert len(rows) == 3

    audit_rows = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "create",
                AuditLog.actor_type == "kiosk",
                AuditLog.object_id.in_(citizen_ids),
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 3


# Verifies idempotency: re-POSTing the exact same batch (same id and
# same updated_at) reports every record as 'conflict_stale' the
# second time and does not create any duplicate rows.
# Would fail if idempotency key (uuid + updated_at) were not checked.
def test_kiosk_re_uploads_same_batch_no_duplicates(
    kiosk_client: TestClient, db_session: Session
) -> None:
    batch = [_make_record() for _ in range(3)]

    first = kiosk_client.post("/api/v1/sync/citizens", json=batch)
    assert first.status_code == 200
    assert all(r["status"] == "created" for r in first.json()["results"])

    second = kiosk_client.post("/api/v1/sync/citizens", json=batch)
    assert second.status_code == 200
    # incoming.updated_at == stored.updated_at -> NOT newer -> conflict_stale
    assert all(r["status"] == "conflict_stale" for r in second.json()["results"])

    db_session.expire_all()
    total = db_session.execute(
        select(func.count(Citizen.id)).where(Citizen.id.in_([r["id"] for r in batch]))
    ).scalar_one()
    assert total == 3


# Verifies updated_at-newer wins: a second POST with the same id but
# a later updated_at produces status='updated' and the stored row
# reflects the new field values.
# Would fail if updated_at comparison were reversed or skipped.
def test_kiosk_uploads_with_newer_updated_at_updates(
    kiosk_client: TestClient, db_session: Session
) -> None:
    citizen_id = str(uuid.uuid4())
    earlier = "2026-01-01T00:00:00+00:00"
    later = "2026-02-01T00:00:00+00:00"

    initial = _make_record(
        citizen_id=citizen_id, full_name="Original", updated_at=earlier
    )
    first = kiosk_client.post("/api/v1/sync/citizens", json=[initial])
    assert first.status_code == 200
    assert first.json()["results"][0]["status"] == "created"

    revised = _make_record(
        citizen_id=citizen_id,
        rfid_uid=initial["rfid_uid"],
        full_name="Renamed",
        barangay="UpdatedBarangay",
        updated_at=later,
    )
    second = kiosk_client.post("/api/v1/sync/citizens", json=[revised])
    assert second.status_code == 200
    assert second.json()["results"][0]["status"] == "updated"

    db_session.expire_all()
    stored = db_session.get(Citizen, citizen_id)
    assert stored is not None
    assert stored.full_name == "Renamed"
    assert stored.barangay == "UpdatedBarangay"
    assert stored.updated_at == later


# Verifies stale-write rejection: a second POST with the same id but
# an earlier updated_at produces status='conflict_stale' and leaves
# the stored row unchanged.
# Would fail if stale check were inverted.
def test_kiosk_uploads_with_stale_updated_at_skipped(
    kiosk_client: TestClient, db_session: Session
) -> None:
    citizen_id = str(uuid.uuid4())
    later = "2026-02-01T00:00:00+00:00"
    earlier = "2026-01-01T00:00:00+00:00"

    fresh = _make_record(citizen_id=citizen_id, full_name="Fresh", updated_at=later)
    assert kiosk_client.post("/api/v1/sync/citizens", json=[fresh]).status_code == 200

    stale = _make_record(
        citizen_id=citizen_id,
        rfid_uid=fresh["rfid_uid"],
        full_name="StaleAttempt",
        updated_at=earlier,
    )
    response = kiosk_client.post("/api/v1/sync/citizens", json=[stale])
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "conflict_stale"

    db_session.expire_all()
    stored = db_session.get(Citizen, citizen_id)
    assert stored is not None
    assert stored.full_name == "Fresh"
    assert stored.updated_at == later


# Verifies the self-service audit attribution path: a record with
# registered_by=NULL produces an audit row with actor_type='kiosk',
# actor_id=<device_id>, and details.registration_type='self_service'.
# Would fail if self-service detection or audit attribution were
# broken.
def test_kiosk_uploads_self_service_registration_audit_attributed_correctly(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    db_session: Session,
) -> None:
    record = _make_record(registered_by=None)
    response = kiosk_client.post("/api/v1/sync/citizens", json=[record])
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "created"

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "create",
            AuditLog.object_id == record["id"],
        )
    ).scalar_one()
    assert audit.actor_type == "kiosk"
    assert audit.actor_id == kiosk_credential["device_id"]
    details = json.loads(audit.details)
    assert details["registration_type"] == "self_service"


# Verifies the BHW-assisted audit attribution path: a record with
# registered_by=<bhw_uuid> produces an audit row with
# actor_type='bhw', actor_id=<bhw_uuid>, and the kiosk device_id
# captured in details.
# Would fail if registered_by were ignored in audit attribution.
def test_kiosk_uploads_bhw_assisted_registration_audit_attributed_correctly(
    kiosk_client: TestClient,
    kiosk_credential: dict,
    make_user,
    db_session: Session,
) -> None:
    bhw = make_user(
        username="bhw_for_assisted_reg",
        password="x",  # pragma: allowlist secret
        role="bhw",
        assigned_barangay="Tibagan",
    )

    record = _make_record(registered_by=bhw.id)
    response = kiosk_client.post("/api/v1/sync/citizens", json=[record])
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "created"

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "create",
            AuditLog.object_id == record["id"],
        )
    ).scalar_one()
    assert audit.actor_type == "bhw"
    assert audit.actor_id == bhw.id
    details = json.loads(audit.details)
    assert details["registration_type"] == "bhw_assisted"
    assert details["kiosk_device_id"] == kiosk_credential["device_id"]


# Verifies per-record validation does not poison the batch: one
# record with a future dob is reported as 'rejected', the other two
# proceed normally and are 'created'. Confirmed end-to-end by
# counting committed rows.
# Would fail if a single bad record rolled back the whole batch.
def test_kiosk_uploads_invalid_record_rejected_others_succeed(
    kiosk_client: TestClient, db_session: Session
) -> None:
    good_a = _make_record()
    bad = _make_record(dob=(date.today() + timedelta(days=1)).isoformat())
    good_b = _make_record()

    response = kiosk_client.post("/api/v1/sync/citizens", json=[good_a, bad, good_b])
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 3

    by_id = {r["id"]: r for r in results}
    assert by_id[good_a["id"]]["status"] == "created"
    assert by_id[bad["id"]]["status"] == "rejected"
    assert "dob" in (by_id[bad["id"]]["error"] or "").lower()
    assert by_id[good_b["id"]]["status"] == "created"

    db_session.expire_all()
    committed = (
        db_session.execute(
            select(Citizen.id).where(
                Citizen.id.in_([good_a["id"], bad["id"], good_b["id"]])
            )
        )
        .scalars()
        .all()
    )
    assert set(committed) == {good_a["id"], good_b["id"]}


# Verifies that revoked credentials are rejected by the
# get_current_kiosk dependency before any sync logic runs.
# Would fail if the get_current_kiosk dependency did not check
# revoked_at.
def test_kiosk_uploads_with_revoked_credential_returns_401(
    client: TestClient,
    client_unauthed: TestClient,
    kiosk_credential: dict,
) -> None:
    revoke = client.patch(
        f"/api/v1/device-credentials/{kiosk_credential['device_id']}",
        json={"revoke": True},
    )
    assert revoke.status_code == 200

    client_unauthed.headers["Authorization"] = f"Bearer {kiosk_credential['api_key']}"
    response = client_unauthed.post("/api/v1/sync/citizens", json=[_make_record()])
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid kiosk credential"


# Verifies the batch-size guard: 501 records returns 413, no records
# are inserted.
# Would fail if the batch size limit were removed.
def test_kiosk_uploads_oversize_batch_returns_413(
    kiosk_client: TestClient, db_session: Session
) -> None:
    # 501 is the smallest violation; build cheap records (no DB hit
    # since the whole batch is rejected before processing).
    batch = [_make_record() for _ in range(501)]
    response = kiosk_client.post("/api/v1/sync/citizens", json=batch)
    assert response.status_code == 413
    assert "500" in response.json()["detail"]

    db_session.expire_all()
    inserted = db_session.execute(
        select(func.count(Citizen.id)).where(Citizen.id.in_([r["id"] for r in batch]))
    ).scalar_one()
    assert inserted == 0


# Verifies the RFID uniqueness guard at the sync layer: when an
# incoming record's rfid_uid collides with a DIFFERENT existing
# UUID's rfid_uid, the record is reported as 'conflict_constraint'.
# Would fail if RFID uniqueness check were skipped or broken.
def test_kiosk_uploads_rfid_collision_returns_constraint_error(
    kiosk_client: TestClient, db_session: Session
) -> None:
    # Pre-populate one citizen with rfid_uid='CARD_X'.
    pre = _make_record(rfid_uid="CARD_X")
    first = kiosk_client.post("/api/v1/sync/citizens", json=[pre])
    assert first.status_code == 200
    assert first.json()["results"][0]["status"] == "created"

    # New UUID, same rfid -> conflict_constraint.
    collider = _make_record(citizen_id=str(uuid.uuid4()), rfid_uid="CARD_X")
    response = kiosk_client.post("/api/v1/sync/citizens", json=[collider])
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "conflict_constraint"
    assert "CARD_X" in (result["error"] or "")

    db_session.expire_all()
    # Only the original row exists.
    rows = (
        db_session.execute(select(Citizen).where(Citizen.rfid_uid == "CARD_X"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].id == pre["id"]
