from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ginhawa_cloud.db.models import AuditLog, Measurement


def _make_session(client: TestClient, rfid: str = "ABC123") -> str:
    citizen = client.post(
        "/api/v1/citizens",
        json={
            "rfid_uid": rfid,
            "full_name": "Test Citizen",
            "dob": "1990-01-01",
            "sex": "M",
            "barangay": "Test Barangay",
            "consent_version": "1.0",
        },
    ).json()
    session = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen["id"], "device_id": "kiosk-001"},
    ).json()
    return session["id"]


def _systolic(session_id: str, **overrides: object) -> dict:
    body = {
        "session_id": session_id,
        "type": "systolic_bp",
        "value": 120.0,
        "unit": "mmHg",
        "source_device": "omron_hem7155t",
    }
    body.update(overrides)
    return body


# Utility helpers (not pytest fixtures) used by the BHW / barangay-aware
# tests below. They extend the existing _make_session helper, which
# hardcodes barangay="Test Barangay" and hides the citizen_id.
def _authenticate(
    client_unauthed: TestClient,
    make_user,
    login,
    *,
    username: str,
    role: str,
    assigned_barangay: str | None = None,
) -> None:
    make_user(
        username=username,
        password="pw",
        role=role,
        assigned_barangay=assigned_barangay,
    )
    token = login(username, "pw")
    client_unauthed.headers["Authorization"] = f"Bearer {token}"


def _make_session_in_barangay(
    client: TestClient, *, rfid: str, barangay: str
) -> tuple[str, str]:
    citizen_id = client.post(
        "/api/v1/citizens",
        json={
            "rfid_uid": rfid,
            "full_name": "Test Citizen",
            "dob": "1990-01-01",
            "sex": "M",
            "barangay": barangay,
            "consent_version": "1.0",
        },
    ).json()["id"]
    session_id = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "kiosk-001"},
    ).json()["id"]
    return citizen_id, session_id


def test_create_measurement_succeeds(client: TestClient) -> None:
    session_id = _make_session(client)
    response = client.post("/api/v1/measurements", json=_systolic(session_id))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["session_id"] == session_id
    assert body["type"] == "systolic_bp"
    assert body["value"] == 120.0
    assert body["is_valid"] == 1
    assert body["validation_notes"] is None
    assert "id" in body and len(body["id"]) == 36
    assert "measured_at" in body


def test_create_measurement_unexpected_unit_marks_invalid(
    client: TestClient,
) -> None:
    session_id = _make_session(client)
    response = client.post(
        "/api/v1/measurements",
        json=_systolic(session_id, unit="kPa"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_valid"] == 0
    assert body["validation_notes"] is not None
    assert "kPa" in body["validation_notes"]


def test_create_measurement_out_of_range_returns_422(
    client: TestClient,
) -> None:
    session_id = _make_session(client)
    response = client.post(
        "/api/v1/measurements",
        json=_systolic(session_id, value=300.0),
    )
    assert response.status_code == 422


def test_create_measurement_unknown_session_returns_400(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/measurements",
        json=_systolic("00000000-0000-0000-0000-000000000000"),
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_create_measurement_terminal_session_returns_400(
    client: TestClient,
) -> None:
    session_id = _make_session(client)
    client.patch(f"/api/v1/sessions/{session_id}", json={"status": "completed"})
    response = client.post("/api/v1/measurements", json=_systolic(session_id))
    assert response.status_code == 400
    assert "in_progress" in response.json()["detail"]


def test_get_measurement_by_id(client: TestClient) -> None:
    session_id = _make_session(client)
    created = client.post("/api/v1/measurements", json=_systolic(session_id)).json()
    response = client.get(f"/api/v1/measurements/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_nonexistent_measurement_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/measurements/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_measurements_filters_by_session(client: TestClient) -> None:
    session_a = _make_session(client, rfid="A")
    session_b = _make_session(client, rfid="B")
    client.post("/api/v1/measurements", json=_systolic(session_a))
    client.post("/api/v1/measurements", json=_systolic(session_a, value=130.0))
    client.post("/api/v1/measurements", json=_systolic(session_b))

    response = client.get("/api/v1/measurements", params={"session_id": session_a})
    body = response.json()
    assert body["total"] == 2
    assert all(item["session_id"] == session_a for item in body["items"])


def test_invalidate_measurement(client: TestClient) -> None:
    session_id = _make_session(client)
    measurement_id = client.post(
        "/api/v1/measurements", json=_systolic(session_id)
    ).json()["id"]

    response = client.patch(
        f"/api/v1/measurements/{measurement_id}/invalidate",
        json={"reason": "operator error"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] == 0
    assert "operator error" in body["validation_notes"]

    default_list = client.get(
        "/api/v1/measurements", params={"session_id": session_id}
    ).json()
    assert default_list["total"] == 0

    invalid_list = client.get(
        "/api/v1/measurements",
        params={"session_id": session_id, "is_valid": "false"},
    ).json()
    assert invalid_list["total"] == 1


def test_invalidate_requires_non_empty_reason(client: TestClient) -> None:
    session_id = _make_session(client)
    measurement_id = client.post(
        "/api/v1/measurements", json=_systolic(session_id)
    ).json()["id"]

    response = client.patch(
        f"/api/v1/measurements/{measurement_id}/invalidate",
        json={"reason": ""},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Phase B — coverage-gap behaviour tests (one per BEHAVIOUR block in
# /tmp/measurements_triage.md).
# ---------------------------------------------------------------------------


# Verifies triage block 62-66: _measurement_in_scope returns True for a
# BHW reading a measurement whose citizen lives in the BHW's assigned
# barangay (the success path through the helper).
# Would fail if the equality comparison at line 66
# (`citizen.barangay == user.assigned_barangay`) were flipped to `!=`,
# because the BHW would then be denied access to their own barangay.
def test_bhw_can_get_measurement_in_own_barangay(
    client_unauthed: TestClient, make_user, login
) -> None:
    _authenticate(client_unauthed, make_user, login, username="adm", role="admin")
    _, session_id = _make_session_in_barangay(
        client_unauthed, rfid="OWN-1", barangay="Tibagan"
    )
    measurement_id = client_unauthed.post(
        "/api/v1/measurements", json=_systolic(session_id)
    ).json()["id"]

    _authenticate(
        client_unauthed,
        make_user,
        login,
        username="bhw_t",
        role="bhw",
        assigned_barangay="Tibagan",
    )

    response = client_unauthed.get(f"/api/v1/measurements/{measurement_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == measurement_id
    assert body["session_id"] == session_id
    assert body["type"] == "systolic_bp"


# Verifies triage block 83-85: a BHW POSTing a measurement against a
# session whose citizen lives in another barangay is rejected with 400
# "session not found"; no measurement row and no audit_log row are
# written. The 400 (rather than 403) is intentional — see _authz.py.
# Would fail if the BHW barangay guard at lines 82-88 of
# api/measurements.py were removed, because the measurement would be
# created against the foreign session and a 'create' audit row would
# follow.
def test_bhw_create_measurement_against_other_barangay_session_returns_400(
    client_unauthed: TestClient, make_user, login, db_session: Session
) -> None:
    _authenticate(client_unauthed, make_user, login, username="adm", role="admin")
    _, foreign_session_id = _make_session_in_barangay(
        client_unauthed, rfid="P-1", barangay="Pinaglabanan"
    )

    _authenticate(
        client_unauthed,
        make_user,
        login,
        username="bhw_t",
        role="bhw",
        assigned_barangay="Tibagan",
    )

    response = client_unauthed.post(
        "/api/v1/measurements", json=_systolic(foreign_session_id)
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]
    assert foreign_session_id in response.json()["detail"]

    # No measurement persisted, no audit row emitted by record_audit.
    measurement_count = db_session.execute(
        select(func.count(Measurement.id))
    ).scalar_one()
    assert measurement_count == 0
    create_audits = db_session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "create",
            AuditLog.object_type == "measurement",
        )
    ).scalar_one()
    assert create_audits == 0


# Verifies triage block 101: when a client supplies validation_notes on
# POST /measurements with a correct unit, the supplied note is
# preserved verbatim in the persisted row (no unit-mismatch suffix is
# appended, since the unit is correct). The mutation succeeds, so a
# 'create' audit row IS expected.
# Would fail if line 101 — `notes_parts.append(payload.validation_notes)` —
# were deleted, because the client's note would be silently dropped and
# validation_notes would come back null.
def test_create_measurement_persists_supplied_validation_notes(
    client_unauthed: TestClient, make_user, login, db_session: Session
) -> None:
    _authenticate(client_unauthed, make_user, login, username="adm", role="admin")
    _, session_id = _make_session_in_barangay(
        client_unauthed, rfid="VN-1", barangay="Tibagan"
    )

    response = client_unauthed.post(
        "/api/v1/measurements",
        json=_systolic(session_id, validation_notes="patient was anxious"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["validation_notes"] == "patient was anxious"
    assert body["is_valid"] == 1  # correct unit, so still valid

    create_audits = db_session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "create",
            AuditLog.object_type == "measurement",
            AuditLog.object_id == body["id"],
        )
    ).scalar_one()
    assert create_audits == 1


# Verifies triage block 153: a BHW reading a measurement that belongs
# to a citizen in another barangay receives 404 (not 403), so existence
# of cross-barangay records is not leaked.
# Would fail if the `_measurement_in_scope` check at lines 152-156 of
# api/measurements.py were removed, because the BHW would receive 200
# with the measurement body.
def test_bhw_get_measurement_in_other_barangay_returns_404(
    client_unauthed: TestClient, make_user, login
) -> None:
    _authenticate(client_unauthed, make_user, login, username="adm", role="admin")
    _, foreign_session_id = _make_session_in_barangay(
        client_unauthed, rfid="P-2", barangay="Pinaglabanan"
    )
    measurement_id = client_unauthed.post(
        "/api/v1/measurements", json=_systolic(foreign_session_id)
    ).json()["id"]

    _authenticate(
        client_unauthed,
        make_user,
        login,
        username="bhw_t",
        role="bhw",
        assigned_barangay="Tibagan",
    )

    response = client_unauthed.get(f"/api/v1/measurements/{measurement_id}")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not found" in detail
    # The 404 message MUST NOT reveal that the resource exists in another
    # barangay; it should be byte-equivalent to the 404 raised when the
    # measurement genuinely doesn't exist.
    assert detail == f"measurement {measurement_id} not found"


# Verifies triage block 188-191: when measured_after is given but is not
# a parseable ISO 8601 timestamp, the list endpoint returns 422 with a
# message that names the offending field.
# Would fail if the ISO 8601 try/except at lines 187-194 of
# api/measurements.py were removed, because the malformed string would
# either be passed straight to the WHERE clause (returning unfiltered
# rows) or crash with a 500.
def test_list_measurements_with_malformed_measured_after_returns_422(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/measurements", params={"measured_after": "not-a-date"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "measured_after" in detail
    assert "ISO 8601" in detail


# Verifies triage block 206-207: ?type=<one-of-eight> restricts the
# list response to rows of that measurement type.
# Would fail if the `if type_filter is not None` block at lines 205-207
# of api/measurements.py were removed, because the request would return
# both the systolic_bp and the spo2 rows seeded below.
def test_list_measurements_filters_by_type(
    client: TestClient,
) -> None:
    session_id = _make_session(client, rfid="TYPE-1")
    client.post("/api/v1/measurements", json=_systolic(session_id))
    client.post(
        "/api/v1/measurements",
        json=_systolic(session_id, type="spo2", value=98.0, unit="%"),
    )

    response = client.get("/api/v1/measurements", params={"type": "systolic_bp"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["type"] == "systolic_bp"


# Verifies triage block 209-210: ?measured_after=<future ts> excludes
# rows whose measured_at predates the supplied bound, so a future
# bound returns total=0.
# Would fail if the `if measured_after is not None` block at lines
# 208-210 of api/measurements.py were removed, because the seeded
# measurement would still appear in the result.
def test_list_measurements_filters_by_measured_after(
    client: TestClient,
) -> None:
    session_id = _make_session(client, rfid="AFT-1")
    seeded = client.post("/api/v1/measurements", json=_systolic(session_id)).json()
    assert seeded["measured_at"]  # baseline: the row exists

    response = client.get(
        "/api/v1/measurements",
        params={"measured_after": "2099-01-01T00:00:00Z"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


# Verifies triage block 212-213: ?measured_before=<past ts> excludes
# rows whose measured_at postdates the supplied bound, so a far-past
# bound returns total=0.
# Would fail if the `if measured_before is not None` block at lines
# 211-213 of api/measurements.py were removed, because the seeded
# measurement would still appear in the result.
def test_list_measurements_filters_by_measured_before(
    client: TestClient,
) -> None:
    session_id = _make_session(client, rfid="BEF-1")
    client.post("/api/v1/measurements", json=_systolic(session_id))

    response = client.get(
        "/api/v1/measurements",
        params={"measured_before": "2000-01-01T00:00:00Z"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


# Verifies triage block 219-230: when a BHW supplies citizen_id for a
# citizen in another barangay, the listing collapses through both the
# citizen_id filter and the BHW barangay filter — total=0. This single
# request exercises every line in the join block (the join, the
# citizen_id WHERE, and the assigned_barangay WHERE).
# Would fail if the BHW barangay filter at lines 228-232 of
# api/measurements.py were removed, because the listing would return
# the foreign-barangay citizen's measurement (citizen_id matches even
# though barangay does not).
def test_bhw_list_with_citizen_id_in_other_barangay_returns_empty(
    client_unauthed: TestClient, make_user, login
) -> None:
    _authenticate(client_unauthed, make_user, login, username="adm", role="admin")
    foreign_citizen_id, foreign_session_id = _make_session_in_barangay(
        client_unauthed, rfid="J-1", barangay="Pinaglabanan"
    )
    client_unauthed.post(
        "/api/v1/measurements", json=_systolic(foreign_session_id)
    ).raise_for_status()

    _authenticate(
        client_unauthed,
        make_user,
        login,
        username="bhw_t",
        role="bhw",
        assigned_barangay="Tibagan",
    )

    response = client_unauthed.get(
        "/api/v1/measurements", params={"citizen_id": foreign_citizen_id}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


# Verifies triage block 276: PATCH /measurements/{id}/invalidate against
# an unknown measurement id returns 404 with a clear message; no
# 'invalidate' audit row is written.
# Would fail if the missing-measurement guard at lines 275-279 of
# api/measurements.py were removed, because the handler would proceed
# to mutate `None` and crash with AttributeError → 500 (not 404).
def test_invalidate_nonexistent_measurement_returns_404(
    client: TestClient, db_session: Session
) -> None:
    unknown_id = "00000000-0000-0000-0000-000000000000"
    response = client.patch(
        f"/api/v1/measurements/{unknown_id}/invalidate",
        json={"reason": "typo in id"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == f"measurement {unknown_id} not found"

    invalidate_audits = db_session.execute(
        select(func.count(AuditLog.id)).where(AuditLog.action == "invalidate")
    ).scalar_one()
    assert invalidate_audits == 0


# Verifies triage block 281: a BHW invalidating a measurement whose
# citizen lives in another barangay receives 404, the measurement
# remains is_valid=1 in the database, and no 'invalidate' audit row is
# written.
# Would fail if the cross-barangay scope check at lines 280-284 of
# api/measurements.py were removed, because the BHW would invalidate
# the foreign measurement (is_valid would flip to 0) and an
# 'invalidate' audit row would be written.
def test_bhw_invalidate_measurement_in_other_barangay_returns_404(
    client_unauthed: TestClient, make_user, login, db_session: Session
) -> None:
    _authenticate(client_unauthed, make_user, login, username="adm", role="admin")
    _, foreign_session_id = _make_session_in_barangay(
        client_unauthed, rfid="P-3", barangay="Pinaglabanan"
    )
    measurement_id = client_unauthed.post(
        "/api/v1/measurements", json=_systolic(foreign_session_id)
    ).json()["id"]

    _authenticate(
        client_unauthed,
        make_user,
        login,
        username="bhw_t",
        role="bhw",
        assigned_barangay="Tibagan",
    )

    response = client_unauthed.patch(
        f"/api/v1/measurements/{measurement_id}/invalidate",
        json={"reason": "should be rejected"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == (f"measurement {measurement_id} not found")

    # The measurement must remain valid in the database.
    db_session.expire_all()
    measurement = db_session.get(Measurement, measurement_id)
    assert measurement is not None
    assert measurement.is_valid == 1

    invalidate_audits = db_session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "invalidate",
            AuditLog.object_id == measurement_id,
        )
    ).scalar_one()
    assert invalidate_audits == 0
