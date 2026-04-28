from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from ginhawa_cloud.core.security import (
    create_access_token,
    scopes_for_role,
)
from ginhawa_cloud.db.models import AuditLog


def _citizen_payload(rfid: str = "04A1B2C3D4", barangay: str = "Tibagan") -> dict:
    return {
        "rfid_uid": rfid,
        "full_name": "Test Citizen",
        "dob": "1990-01-01",
        "sex": "M",
        "barangay": barangay,
        "consent_version": "1.0",
    }


def test_login_with_valid_credentials_returns_token(
    client_unauthed: TestClient, make_user, login
) -> None:
    make_user(username="bhw_anna", password="correct-pw", role="bhw")
    response = client_unauthed.post(
        "/api/v1/auth/login",
        json={"username": "bhw_anna", "password": "correct-pw"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert len(body["access_token"]) > 0
    # expires_at parses as ISO 8601
    datetime.fromisoformat(body["expires_at"])


def test_login_wrong_password_returns_401_and_audits(
    client_unauthed: TestClient, make_user, db_session
) -> None:
    make_user(username="bhw_anna", password="correct-pw", role="bhw")
    response = client_unauthed.post(
        "/api/v1/auth/login",
        json={"username": "bhw_anna", "password": "wrong-pw"},
    )
    assert response.status_code == 401

    # An audit row was written with the failure reason.
    rows = (
        db_session.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].actor_type == "bhw"
    assert "bad_password" in (rows[0].details or "")


def test_login_unknown_user_returns_401_and_audits(
    client_unauthed: TestClient, db_session
) -> None:
    response = client_unauthed.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "x"},
    )
    assert response.status_code == 401

    rows = (
        db_session.execute(select(AuditLog).where(AuditLog.action == "login_failed"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert "unknown_user" in (rows[0].details or "")


def test_login_inactive_user_returns_401(
    client_unauthed: TestClient, make_user
) -> None:
    make_user(username="suspended", password="x", role="bhw", is_active=0)
    response = client_unauthed.post(
        "/api/v1/auth/login",
        json={"username": "suspended", "password": "x"},
    )
    assert response.status_code == 401


def test_expired_token_returns_401(client_unauthed: TestClient, make_user) -> None:
    user = make_user(username="someone", password="x", role="bhw")

    # Hand-build an already-expired token by signing claims with exp in the past.
    from jose import jwt

    from ginhawa_cloud.core.config import get_settings

    settings = get_settings()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": user.id,
        "scopes": list(scopes_for_role("bhw")),
        "iat": int((past - timedelta(minutes=1)).timestamp()),
        "exp": int(past.timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    response = client_unauthed.get(
        "/api/v1/citizens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_missing_scope_returns_403(client_unauthed: TestClient, make_user) -> None:
    """data_viewer cannot create citizens (no citizens:write scope)."""
    user = make_user(username="viewer", password="x", role="data_viewer")
    token = create_access_token(
        subject=user.id, scopes=list(scopes_for_role("data_viewer"))
    )
    client_unauthed.headers["Authorization"] = f"Bearer {token}"

    response = client_unauthed.post("/api/v1/citizens", json=_citizen_payload())
    assert response.status_code == 403
    assert "citizens:write" in response.json()["detail"]


def test_no_token_returns_401(client_unauthed: TestClient) -> None:
    response = client_unauthed.get("/api/v1/citizens")
    assert response.status_code == 401


def test_bhw_lists_only_own_barangay(
    client_unauthed: TestClient, make_user, login
) -> None:
    # Admin seeds two citizens in different barangays via direct DB? No —
    # use the API as admin to keep the test path realistic.
    admin = make_user(username="admin1", password="x", role="admin")
    admin_token = create_access_token(
        subject=admin.id, scopes=list(scopes_for_role("admin"))
    )
    client_unauthed.headers["Authorization"] = f"Bearer {admin_token}"
    client_unauthed.post(
        "/api/v1/citizens",
        json=_citizen_payload(rfid="A1", barangay="Tibagan"),
    )
    client_unauthed.post(
        "/api/v1/citizens",
        json=_citizen_payload(rfid="A2", barangay="Tibagan"),
    )
    client_unauthed.post(
        "/api/v1/citizens",
        json=_citizen_payload(rfid="B1", barangay="Pinaglabanan"),
    )

    bhw = make_user(
        username="bhw_t", password="x", role="bhw", assigned_barangay="Tibagan"
    )
    bhw_token = create_access_token(subject=bhw.id, scopes=list(scopes_for_role("bhw")))
    client_unauthed.headers["Authorization"] = f"Bearer {bhw_token}"

    response = client_unauthed.get("/api/v1/citizens")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert all(item["barangay"] == "Tibagan" for item in body["items"])


def test_bhw_cross_barangay_get_returns_404_not_403(
    client_unauthed: TestClient, make_user
) -> None:
    admin = make_user(username="admin1", password="x", role="admin")
    admin_token = create_access_token(
        subject=admin.id, scopes=list(scopes_for_role("admin"))
    )
    client_unauthed.headers["Authorization"] = f"Bearer {admin_token}"
    other = client_unauthed.post(
        "/api/v1/citizens",
        json=_citizen_payload(rfid="OTHER", barangay="Pinaglabanan"),
    ).json()

    bhw = make_user(
        username="bhw_t", password="x", role="bhw", assigned_barangay="Tibagan"
    )
    bhw_token = create_access_token(subject=bhw.id, scopes=list(scopes_for_role("bhw")))
    client_unauthed.headers["Authorization"] = f"Bearer {bhw_token}"

    response = client_unauthed.get(f"/api/v1/citizens/{other['id']}")
    assert response.status_code == 404, (
        "must hide cross-barangay citizens behind 404, never 403"
    )


def test_admin_sees_all_barangays(client_unauthed: TestClient, make_user) -> None:
    admin = make_user(username="admin1", password="x", role="admin")
    admin_token = create_access_token(
        subject=admin.id, scopes=list(scopes_for_role("admin"))
    )
    client_unauthed.headers["Authorization"] = f"Bearer {admin_token}"
    client_unauthed.post(
        "/api/v1/citizens",
        json=_citizen_payload(rfid="A1", barangay="Tibagan"),
    )
    client_unauthed.post(
        "/api/v1/citizens",
        json=_citizen_payload(rfid="B1", barangay="Pinaglabanan"),
    )

    response = client_unauthed.get("/api/v1/citizens")
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_logout_writes_audit_log(client: TestClient, db_session) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200

    rows = (
        db_session.execute(select(AuditLog).where(AuditLog.action == "logout"))
        .scalars()
        .all()
    )
    assert len(rows) >= 1
