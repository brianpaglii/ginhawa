from fastapi.testclient import TestClient


def _payload(rfid: str = "04A1B2C3D4", barangay: str = "Barangay 1") -> dict:
    return {
        "rfid_uid": rfid,
        "full_name": "Juan Dela Cruz",
        "dob": "1990-05-15",
        "sex": "M",
        "barangay": barangay,
        "phone": "+639171234567",
        "consent_version": "1.0",
    }


def test_register_citizen_succeeds(client: TestClient) -> None:
    response = client.post("/api/v1/citizens", json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["rfid_uid"] == "04A1B2C3D4"
    assert body["full_name"] == "Juan Dela Cruz"
    assert body["is_active"] == 1
    assert body["synced"] == 0
    assert "id" in body and len(body["id"]) == 36  # UUID v4
    assert "consent_given_at" in body and body["consent_given_at"] != ""
    assert "registered_at" in body


def test_duplicate_rfid_returns_409(client: TestClient) -> None:
    client.post("/api/v1/citizens", json=_payload(rfid="DUPLICATE-UID"))
    response = client.post("/api/v1/citizens", json=_payload(rfid="DUPLICATE-UID"))
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_fetch_by_id_returns_citizen(client: TestClient) -> None:
    created = client.post("/api/v1/citizens", json=_payload(rfid="FETCH-1"))
    citizen_id = created.json()["id"]

    response = client.get(f"/api/v1/citizens/{citizen_id}")
    assert response.status_code == 200
    assert response.json()["id"] == citizen_id
    assert response.json()["rfid_uid"] == "FETCH-1"


def test_fetch_nonexistent_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/citizens/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_with_barangay_filter_returns_subset(client: TestClient) -> None:
    client.post("/api/v1/citizens", json=_payload(rfid="A1", barangay="Pinaglabanan"))
    client.post("/api/v1/citizens", json=_payload(rfid="A2", barangay="Pinaglabanan"))
    client.post("/api/v1/citizens", json=_payload(rfid="B1", barangay="Tibagan"))

    response = client.get("/api/v1/citizens", params={"barangay": "Pinaglabanan"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert all(c["barangay"] == "Pinaglabanan" for c in body["items"])


def test_pagination_respects_limit(client: TestClient) -> None:
    for i in range(5):
        client.post("/api/v1/citizens", json=_payload(rfid=f"PAGE-{i}"))

    response = client.get("/api/v1/citizens", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5


def test_update_changes_only_allowed_fields(client: TestClient) -> None:
    created = client.post("/api/v1/citizens", json=_payload(rfid="UPDATE-1")).json()
    citizen_id = created["id"]
    original_rfid = created["rfid_uid"]
    original_consent = created["consent_version"]

    # Send a mix of allowed and protected fields. Protected fields aren't even
    # declared on CitizenUpdate so Pydantic drops them; the response must still
    # carry the original values.
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}",
        json={
            "full_name": "Maria Clara",
            "barangay": "New Barangay",
            "rfid_uid": "SHOULD-NOT-CHANGE",
            "consent_version": "999.0",
            "id": "different-id",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Maria Clara"
    assert body["barangay"] == "New Barangay"
    assert body["rfid_uid"] == original_rfid
    assert body["consent_version"] == original_consent
    assert body["id"] == citizen_id


# Verifies the soft-delete-makes-invisible contract from ADR-0008: once
# is_active=0, the citizen is indistinguishable from a citizen that
# never existed at the GET-by-id surface. The 404 message must be
# identical to the missing-citizen message so callers cannot probe.
# Would fail if the get_citizen handler dropped its is_active filter
# and reverted to a plain primary-key lookup.
def test_soft_deleted_citizen_returns_404_on_get(client: TestClient) -> None:
    created = client.post("/api/v1/citizens", json=_payload(rfid="DEL-GET-1")).json()
    citizen_id = created["id"]

    delete_resp = client.delete(f"/api/v1/citizens/{citizen_id}")
    assert delete_resp.status_code == 204

    response = client.get(f"/api/v1/citizens/{citizen_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == f"citizen {citizen_id} not found"


def test_soft_delete_removes_from_default_list(client: TestClient) -> None:
    created = client.post("/api/v1/citizens", json=_payload(rfid="DEL-1")).json()
    citizen_id = created["id"]

    delete_resp = client.delete(f"/api/v1/citizens/{citizen_id}")
    assert delete_resp.status_code == 204
    assert delete_resp.text == ""

    list_resp = client.get("/api/v1/citizens")
    assert list_resp.status_code == 200
    listed_ids = [c["id"] for c in list_resp.json()["items"]]
    assert citizen_id not in listed_ids

    # The record itself still exists with is_active=0 — verifiable by passing
    # is_active=false on the list endpoint.
    inactive_resp = client.get("/api/v1/citizens", params={"is_active": "false"})
    inactive_ids = {c["id"] for c in inactive_resp.json()["items"]}
    assert citizen_id in inactive_ids
    inactive_match = next(
        c for c in inactive_resp.json()["items"] if c["id"] == citizen_id
    )
    assert inactive_match["is_active"] == 0
