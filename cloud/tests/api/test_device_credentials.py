from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_cloud.core.security import (
    create_access_token,
    scopes_for_role,
    verify_password,
)
from ginhawa_cloud.db.models import AuditLog, DeviceCredential


def _create_payload(description: str = "kiosk_test_main") -> dict:
    return {"description": description}


# Verifies the happy path: admin POST creates a credential, the response
# carries the plaintext api_key (this is the only time it's ever
# returned), and an audit row with action='create_device_credential'
# is written.
# Would fail if the api_key were not returned in the create response,
# or if the audit row were not written.
def test_admin_creates_device_credential(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/api/v1/device-credentials",
        json=_create_payload("kiosk_create_1"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "device_id" in body and len(body["device_id"]) == 36
    assert "api_key" in body
    assert isinstance(body["api_key"], str)
    assert len(body["api_key"]) > 30  # token_urlsafe(32) → ~43 chars
    assert body["description"] == "kiosk_create_1"
    assert "created_at" in body

    audit_rows = (
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "create_device_credential",
                AuditLog.object_id == body["device_id"],
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].actor_type == "admin"


# Verifies the security invariant: the plaintext key returned in the
# create response is NEVER stored in the database. The stored
# api_key_hash is an argon2id hash that round-trips through
# verify_password against the plaintext.
# Would fail if the key were stored plaintext or if hashing were
# skipped.
def test_create_device_credential_hashes_the_key(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/api/v1/device-credentials",
        json=_create_payload("kiosk_hash_check"),
    )
    assert response.status_code == 201
    plaintext = response.json()["api_key"]
    device_id = response.json()["device_id"]

    db_session.expire_all()
    stored = db_session.get(DeviceCredential, device_id)
    assert stored is not None
    # Plaintext is NOT stored.
    assert stored.api_key_hash != plaintext
    # The stored hash verifies against the plaintext via the same
    # helper used for user passwords.
    assert verify_password(plaintext, stored.api_key_hash) is True
    # Stored hash carries argon2id's marker so we know it's not some
    # weaker scheme that happens to round-trip.
    assert stored.api_key_hash.startswith("$argon2id$")


# Verifies the scope guard: BHW users do not have device_credentials:admin
# and so cannot create credentials.
# Would fail if device_credentials:admin scope were granted to bhw role.
def test_bhw_creating_device_credential_returns_403(
    client_unauthed: TestClient, make_user, login
) -> None:
    bhw = make_user(
        username="bhw_dc_attempt",
        password="x",  # pragma: allowlist secret
        role="bhw",
        assigned_barangay="Tibagan",
    )
    bhw_token = create_access_token(subject=bhw.id, scopes=list(scopes_for_role("bhw")))
    client_unauthed.headers["Authorization"] = f"Bearer {bhw_token}"

    response = client_unauthed.post(
        "/api/v1/device-credentials",
        json=_create_payload("kiosk_bhw_attempt"),
    )
    assert response.status_code == 403
    assert "device_credentials:admin" in response.json()["detail"]


# Verifies the active filter on the list endpoint. Three credentials
# created, one revoked; ?active=true returns the two unrevoked ones.
# Would fail if the active filter (WHERE revoked_at IS NULL) were
# dropped — the response would include the revoked credential.
def test_admin_lists_active_credentials(client: TestClient) -> None:
    ids = [
        client.post(
            "/api/v1/device-credentials",
            json=_create_payload(f"kiosk_list_{i}"),
        ).json()["device_id"]
        for i in range(3)
    ]
    revoke_resp = client.patch(
        f"/api/v1/device-credentials/{ids[1]}", json={"revoke": True}
    )
    assert revoke_resp.status_code == 200

    active_resp = client.get("/api/v1/device-credentials", params={"active": "true"})
    assert active_resp.status_code == 200
    body = active_resp.json()
    returned_ids = {item["device_id"] for item in body["items"]}
    assert ids[0] in returned_ids
    assert ids[2] in returned_ids
    assert ids[1] not in returned_ids
    assert all(item["revoked_at"] is None for item in body["items"])


# Verifies the revocation contract: PATCH with {"revoke": true} sets
# revoked_at and revoked_by from current_user, and a subsequent GET
# shows the revoked_at timestamp.
# Would fail if PATCH did not set revoked_at and revoked_by from
# current_user.
def test_admin_revokes_credential(
    client: TestClient,
) -> None:
    me = client.get("/api/v1/users/me").json()
    admin_id = me["id"]

    created = client.post(
        "/api/v1/device-credentials",
        json=_create_payload("kiosk_revoke_target"),
    ).json()
    device_id = created["device_id"]

    response = client.patch(
        f"/api/v1/device-credentials/{device_id}",
        json={"revoke": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revoked_at"] is not None
    assert body["revoked_by"] == admin_id

    # Subsequent GET shows the revoked state — proves it was persisted,
    # not just reflected in the response body.
    fetch = client.get(f"/api/v1/device-credentials/{device_id}")
    assert fetch.status_code == 200
    assert fetch.json()["revoked_at"] is not None
    assert fetch.json()["revoked_by"] == admin_id


# Verifies that revoking a credential that is already revoked returns
# 409 instead of silently succeeding (which would shift the
# revoked_at timestamp and lose the original revocation time).
# Would fail if the already-revoked guard were removed.
def test_revoking_already_revoked_credential_returns_409(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/device-credentials",
        json=_create_payload("kiosk_double_revoke"),
    ).json()
    device_id = created["device_id"]

    first = client.patch(
        f"/api/v1/device-credentials/{device_id}",
        json={"revoke": True},
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/v1/device-credentials/{device_id}",
        json={"revoke": True},
    )
    assert second.status_code == 409
    assert "already revoked" in second.json()["detail"]


# Verifies authn (not authz) on every endpoint: a request with no
# Authorization header gets 401 from each route.
# Would fail if any route forgot to chain through require_scope, in
# which case missing tokens would silently succeed.
def test_unauthenticated_request_returns_401(
    client_unauthed: TestClient,
) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"

    post = client_unauthed.post("/api/v1/device-credentials", json=_create_payload())
    assert post.status_code == 401

    list_resp = client_unauthed.get("/api/v1/device-credentials")
    assert list_resp.status_code == 401

    get_resp = client_unauthed.get(f"/api/v1/device-credentials/{fake_id}")
    assert get_resp.status_code == 401

    patch_resp = client_unauthed.patch(
        f"/api/v1/device-credentials/{fake_id}",
        json={"revoke": True},
    )
    assert patch_resp.status_code == 401


# Verifies that empty-body PATCH is rejected. Would fail if revoke
# were made optional or if a default value were added to the schema.
def test_patch_with_empty_body_returns_422(client: TestClient) -> None:
    created = client.post(
        "/api/v1/device-credentials",
        json=_create_payload("kiosk_empty_body"),
    ).json()
    device_id = created["device_id"]

    response = client.patch(f"/api/v1/device-credentials/{device_id}", json={})
    assert response.status_code == 422
    detail = response.json()["detail"]
    # Pydantic returns a list of error objects; the missing required
    # field is named in `loc`. Asserting on the field name pins the
    # contract — a future refactor that renamed `revoke` would have to
    # update this test too.
    assert any("revoke" in str(error.get("loc", [])) for error in detail)
