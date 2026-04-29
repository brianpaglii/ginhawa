from fastapi.testclient import TestClient

from ginhawa_cloud.core.security import (
    create_access_token,
    scopes_for_role,
)


def _user_payload(
    username: str = "bhw_carlos",
    password: str = "secret-pw",
    role: str = "bhw",
    assigned_barangay: str | None = "Tibagan",
) -> dict:
    return {
        "username": username,
        "password": password,
        "full_name": "Carlos Test",
        "role": role,
        "assigned_barangay": assigned_barangay,
    }


def test_admin_creates_user_and_password_is_not_in_response(
    client: TestClient,
) -> None:
    response = client.post("/api/v1/users", json=_user_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "bhw_carlos"
    assert body["role"] == "bhw"
    assert "password" not in body
    assert "password_hash" not in body
    # Sanity check on the JSON form too.
    raw = response.text
    assert "password_hash" not in raw
    assert "secret-pw" not in raw


def test_non_admin_cannot_create_user(client_unauthed: TestClient, make_user) -> None:
    bhw = make_user(
        username="bhw_a", password="x", role="bhw", assigned_barangay="Tibagan"
    )
    bhw_token = create_access_token(subject=bhw.id, scopes=list(scopes_for_role("bhw")))
    client_unauthed.headers["Authorization"] = f"Bearer {bhw_token}"

    response = client_unauthed.post(
        "/api/v1/users", json=_user_payload(username="bhw_carlos")
    )
    assert response.status_code == 403
    assert "users:admin" in response.json()["detail"]


def test_users_me_returns_current_user(client_unauthed: TestClient, make_user) -> None:
    bhw = make_user(
        username="bhw_anna",
        password="x",
        role="bhw",
        assigned_barangay="Tibagan",
        full_name="Anna Reyes",
    )
    token = create_access_token(subject=bhw.id, scopes=list(scopes_for_role("bhw")))
    client_unauthed.headers["Authorization"] = f"Bearer {token}"

    response = client_unauthed.get("/api/v1/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "bhw_anna"
    assert body["assigned_barangay"] == "Tibagan"
    assert "password_hash" not in body
    assert "password_hash" not in response.text


def test_admin_lists_users(client: TestClient, make_user) -> None:
    make_user(username="bhw_a", password="x", role="bhw")
    make_user(username="bhw_b", password="x", role="bhw")

    response = client.get("/api/v1/users")
    assert response.status_code == 200
    body = response.json()
    # admin (from `client` fixture) + 2 BHWs
    assert body["total"] == 3
    for item in body["items"]:
        assert "password_hash" not in item


def test_admin_patches_user_password_is_hashed(
    client: TestClient, make_user, client_unauthed: TestClient
) -> None:
    target = make_user(username="bhw_target", password="old-pw", role="bhw")

    response = client.patch(
        f"/api/v1/users/{target.id}",
        json={"password": "new-pw"},
    )
    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert "new-pw" not in response.text  # never echo plaintext

    # Login with the new password should succeed.
    login_resp = client_unauthed.post(
        "/api/v1/auth/login",
        json={"username": "bhw_target", "password": "new-pw"},
    )
    assert login_resp.status_code == 200

    # Login with the old password should fail.
    bad_login = client_unauthed.post(
        "/api/v1/auth/login",
        json={"username": "bhw_target", "password": "old-pw"},
    )
    assert bad_login.status_code == 401


# Verifies that protected user fields cannot be changed via PATCH.
# Would fail if extra="forbid" were removed from the UserUpdate schema
# or if the schema started accepting fields like username or
# password_hash. extra="forbid" is the upstream guard.
def test_patch_with_protected_field_returns_422(client: TestClient, make_user) -> None:
    target = make_user(username="bhw_target", password="x", role="bhw")

    response = client.patch(
        f"/api/v1/users/{target.id}",
        json={"username": "ATTACKER_NEW_NAME"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    # Pydantic returns a list of error objects; the offending field is
    # named in `loc` so the client knows which field broke the contract.
    assert any("username" in str(error.get("loc", [])) for error in detail)

    # The user must be unchanged: the rejected PATCH leaks no partial state.
    listed = client.get("/api/v1/users").json()
    target_data = next(u for u in listed["items"] if u["id"] == target.id)
    assert target_data["username"] == "bhw_target"


# Verifies the same contract for an attempt to set password_hash directly.
# A client must use the `password` field (plaintext) which the handler
# argon2-hashes; password_hash is server-managed and protected.
# Would fail if extra="forbid" were removed from UserUpdate or the
# schema started declaring password_hash as a writable field.
def test_patch_password_hash_directly_returns_422(
    client: TestClient, make_user
) -> None:
    target = make_user(username="bhw_target_2", password="x", role="bhw")

    response = client.patch(
        f"/api/v1/users/{target.id}",
        json={"password_hash": "$argon2id$v=19$forged"},  # pragma: allowlist secret
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("password_hash" in str(error.get("loc", [])) for error in detail)


def test_admin_soft_deletes_user_via_patch(client: TestClient, make_user) -> None:
    target = make_user(username="bhw_target", password="x", role="bhw")

    response = client.patch(f"/api/v1/users/{target.id}", json={"is_active": 0})
    assert response.status_code == 200
    assert response.json()["is_active"] == 0
