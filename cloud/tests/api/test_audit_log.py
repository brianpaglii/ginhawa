from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_cloud.db.models import AuditLog


def _citizen_payload(rfid: str, barangay: str = "Tibagan") -> dict:
    return {
        "rfid_uid": rfid,
        "full_name": "Test Citizen",
        "dob": "1990-01-01",
        "sex": "M",
        "barangay": barangay,
        "consent_version": "1.0",
    }


def _login_as_role(client_unauthed: TestClient, make_user, login, *, role: str) -> None:
    make_user(
        username=f"user_{role}",
        password="pw",  # pragma: allowlist secret
        role=role,
        assigned_barangay="Tibagan" if role == "bhw" else None,
    )
    token = login(f"user_{role}", "pw")
    client_unauthed.headers["Authorization"] = f"Bearer {token}"


# Verifies the happy path: an admin can list the audit log and gets a
# paginated response. Trips wire the fixture's admin login already
# wrote a 'login' audit row, and the citizen we POST below adds at
# least one 'create' row, so total >= 1 is guaranteed regardless of
# meta-audit ordering.
# Would fail if the audit_log router were not registered in
# api/__init__.py (the route would 404), or if require_scope rejected
# the admin (the admin role's scope tuple no longer includes
# 'audit_log:read').
def test_admin_can_list_audit_log(client: TestClient) -> None:
    create_resp = client.post(
        "/api/v1/citizens", json=_citizen_payload(rfid="ADM-LIST-1")
    )
    assert create_resp.status_code == 201

    response = client.get("/api/v1/audit-log")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert isinstance(body["items"], list)
    assert len(body["items"]) > 0
    assert "id" in body["items"][0]
    assert "action" in body["items"][0]
    assert "actor_type" in body["items"][0]


# Verifies that BHW users — who legitimately write to the audit log via
# their own actions — cannot read it. They get 403, not 404, because
# the endpoint exists; what they lack is the scope.
# Would fail if 'audit_log:read' were added to the bhw role's scope
# tuple in core/security._ROLE_SCOPES.
def test_bhw_listing_audit_log_returns_403(
    client_unauthed: TestClient, make_user, login
) -> None:
    _login_as_role(client_unauthed, make_user, login, role="bhw")

    response = client_unauthed.get("/api/v1/audit-log")
    assert response.status_code == 403
    assert "audit_log:read" in response.json()["detail"]


# Verifies the same restriction for data_viewer. A data_viewer is the
# read-only role; they can read citizen/session/measurement records but
# not the audit trail itself.
# Would fail if 'audit_log:read' were added to the data_viewer role's
# scope tuple in core/security._ROLE_SCOPES.
def test_data_viewer_listing_audit_log_returns_403(
    client_unauthed: TestClient, make_user, login
) -> None:
    _login_as_role(client_unauthed, make_user, login, role="data_viewer")

    response = client_unauthed.get("/api/v1/audit-log")
    assert response.status_code == 403
    assert "audit_log:read" in response.json()["detail"]


# Verifies that an unauthenticated request gets 401 (not 200, not 403).
# The require_scope dependency chains through get_current_active_user
# and _decode_token_dep, so a missing bearer token short-circuits to
# 401 before scope evaluation.
# Would fail if the require_scope dependency on the route were removed,
# in which case the listing would return 200 with all rows visible to
# anyone.
def test_unauthenticated_audit_log_returns_401(
    client_unauthed: TestClient,
) -> None:
    response = client_unauthed.get("/api/v1/audit-log")
    assert response.status_code == 401


# Verifies that ?actor_id=<id> restricts the response to rows whose
# actor_id matches exactly. Setup uses two real actors (the fixture
# admin and a fresh second admin) so we can compare counts and prove
# the filter is narrowing the result.
# Would fail if the `if actor_id is not None` filter at lines 60-62 of
# api/audit_log.py were removed: the endpoint would return rows for
# every actor regardless of the query parameter.
def test_audit_log_filter_by_actor_id(
    client: TestClient, client_unauthed: TestClient, make_user, login
) -> None:
    me = client.get("/api/v1/users/me").json()
    fixture_admin_id = me["id"]
    client.post("/api/v1/citizens", json=_citizen_payload(rfid="FILT-1"))

    # Filtering by the fixture admin's id must return only their rows.
    response = client.get("/api/v1/audit-log", params={"actor_id": fixture_admin_id})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["actor_id"] == fixture_admin_id for item in body["items"])

    # Filtering by an actor_id that has never written must return zero.
    response = client.get(
        "/api/v1/audit-log",
        params={"actor_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


# Verifies the actor_type query-parameter filter (lines 61-62).
# Would fail if the `if actor_type is not None: stmt = stmt.where(AuditLog.actor_type == actor_type)`
# clause were removed — the response would include rows from other
# actor_types instead of being scoped.
def test_audit_log_filter_by_actor_type(client: TestClient) -> None:
    # Trigger an audit row authored by the fixture admin so we have at
    # least one row with actor_type='admin' to find.
    create_resp = client.post(
        "/api/v1/citizens", json=_citizen_payload(rfid="ACTYPE-1")
    )
    assert create_resp.status_code == 201

    response = client.get("/api/v1/audit-log", params={"actor_type": "admin"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["actor_type"] == "admin" for item in body["items"])


# Verifies the action_prefix LIKE filter — the BHW portal's audit
# page uses this to narrow by action namespace ("create", "login",
# etc.) without enumerating every leaf action. Asserts that prefix
# 'create' matches the create row but not the seeded 'login' rows.
# Would fail if action_prefix were dropped, or if the LIKE clause
# regressed to exact match.
def test_audit_log_filter_by_action_prefix(client: TestClient) -> None:
    create_resp = client.post(
        "/api/v1/citizens", json=_citizen_payload(rfid="ACTPFX-1")
    )
    assert create_resp.status_code == 201

    response = client.get("/api/v1/audit-log", params={"action_prefix": "create"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["action"].startswith("create") for item in body["items"])
    # Sanity: a prefix that no real action matches returns nothing.
    none_resp = client.get(
        "/api/v1/audit-log", params={"action_prefix": "no-such-prefix"}
    )
    assert none_resp.status_code == 200
    assert none_resp.json()["total"] == 0


# Verifies that ?object_type=citizen narrows the response to citizen
# audit rows only. The fixture seeds 'login' rows (no object_type) and
# the POST below seeds 'create' rows for object_type='citizen', so the
# filter must exclude the login rows.
# Would fail if the `if object_type is not None` filter at lines 66-68
# of api/audit_log.py were removed: login rows would leak through.
def test_audit_log_filter_by_object_type_citizen(
    client: TestClient,
) -> None:
    client.post("/api/v1/citizens", json=_citizen_payload(rfid="OBJ-1"))

    response = client.get("/api/v1/audit-log", params={"object_type": "citizen"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["object_type"] == "citizen" for item in body["items"])


# Verifies the object_id query-parameter filter (lines 73-74).
# Would fail if the `if object_id is not None: stmt = stmt.where(AuditLog.object_id == object_id)`
# clause were removed — the response would include rows for other
# objects instead of being scoped to one.
def test_audit_log_filter_by_object_id(client: TestClient) -> None:
    # Create a citizen and capture its UUID — that becomes the
    # object_id we'll filter on.
    created = client.post(
        "/api/v1/citizens", json=_citizen_payload(rfid="OBJID-1")
    ).json()
    citizen_id = created["id"]

    response = client.get("/api/v1/audit-log", params={"object_id": citizen_id})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["object_id"] == citizen_id for item in body["items"])


# Verifies both halves of the time-range filter:
# (a) timestamp_after in the future returns 0 rows;
# (b) timestamp_before in the far past returns 0 rows.
# Together these prove the >= and <= clauses are wired correctly.
# Would fail if either the `if timestamp_after` filter (lines 72-74) or
# the `if timestamp_before` filter (lines 75-77) were removed: the
# corresponding probe would return all rows instead of zero.
def test_audit_log_filter_by_time_range(client: TestClient) -> None:
    client.post("/api/v1/citizens", json=_citizen_payload(rfid="TIME-1"))

    after_future = client.get(
        "/api/v1/audit-log",
        params={"timestamp_after": "2099-01-01T00:00:00Z"},
    )
    assert after_future.status_code == 200
    assert after_future.json()["total"] == 0

    before_past = client.get(
        "/api/v1/audit-log",
        params={"timestamp_before": "2000-01-01T00:00:00Z"},
    )
    assert before_past.status_code == 200
    assert before_past.json()["total"] == 0


# Verifies that ?limit=<n>&offset=<m> returns at most n items and
# offsets the window by m. We filter by action='create' so the
# meta-audit rows (action='read_audit_log') the endpoint emits on
# every read don't enter the result set — without that filter, each
# request would shift the DESC-ordered window by one and the two
# pages would overlap.
# Would fail if either `.limit(limit)` or `.offset(offset)` were
# removed from the order_by chain in api/audit_log.py:
# - drop limit → page 1 returns more than 2 rows;
# - drop offset → page 1 and page 2 return the same id set (overlap).
def test_audit_log_pagination_respects_limit_and_offset(
    client: TestClient,
) -> None:
    # Generate a handful of action='create' audit rows by registering
    # several citizens. Five rows is enough to support two non-overlapping
    # pages of size two.
    for i in range(5):
        client.post(
            "/api/v1/citizens",
            json=_citizen_payload(rfid=f"PAGE-{i}"),
        )

    page_one = client.get(
        "/api/v1/audit-log",
        params={"action": "create", "limit": 2, "offset": 0},
    ).json()
    assert len(page_one["items"]) == 2
    assert page_one["total"] >= 5

    page_two = client.get(
        "/api/v1/audit-log",
        params={"action": "create", "limit": 2, "offset": 2},
    ).json()
    assert len(page_two["items"]) == 2

    first_ids = {item["id"] for item in page_one["items"]}
    second_ids = {item["id"] for item in page_two["items"]}
    assert first_ids.isdisjoint(second_ids)


# Verifies that ?timestamp_after=<not-iso-8601> is rejected with 422
# and that the response identifies timestamp_after as the offending
# field, mirroring the malformed-date pattern in
# test_measurements.test_list_measurements_with_malformed_measured_after_returns_422.
# Would fail if the ISO 8601 validation on timestamp_after were
# removed from the endpoint signature: the malformed string would be
# passed straight to the WHERE clause.
def test_audit_log_with_malformed_timestamp_after_returns_422(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/audit-log", params={"timestamp_after": "not-a-date"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "timestamp_after" in detail
    assert "ISO 8601" in detail


# Verifies the meta-audit invariant: every read of the audit log
# itself produces a row with action='read_audit_log', authored by the
# admin who issued the read. We prove this by making two reads filtered
# by action='read_audit_log' and asserting the second read sees exactly
# one more row than the first (the row recorded by the first read; the
# second read's own meta-audit is committed AFTER the count is
# computed, so it doesn't appear in the second response).
# Would fail if the record_audit(..., action='read_audit_log', ...)
# call near the end of list_audit_log were removed, in which case
# reading the audit log would leave no trace and DPA accountability
# would be broken.
def test_reading_audit_log_emits_meta_audit_row(
    client: TestClient, db_session: Session
) -> None:
    first = client.get("/api/v1/audit-log", params={"action": "read_audit_log"})
    assert first.status_code == 200
    first_total = first.json()["total"]

    second = client.get("/api/v1/audit-log", params={"action": "read_audit_log"})
    assert second.status_code == 200
    assert second.json()["total"] == first_total + 1

    # Belt-and-suspenders DB-side check: the meta-audit row was
    # written by the fixture admin (actor_type='admin'), not by
    # 'system'.
    db_session.expire_all()
    me = client.get("/api/v1/users/me").json()
    rows = (
        db_session.execute(select(AuditLog).where(AuditLog.action == "read_audit_log"))
        .scalars()
        .all()
    )
    assert len(rows) >= 2
    for row in rows:
        assert row.actor_type == "admin"
        assert row.actor_id == me["id"]
