from fastapi.testclient import TestClient


def _make_citizen(
    client: TestClient, rfid: str = "ABC123", barangay: str = "Test Barangay"
) -> str:
    response = client.post(
        "/api/v1/citizens",
        json={
            "rfid_uid": rfid,
            "full_name": "Test Citizen",
            "dob": "1990-01-01",
            "sex": "M",
            "barangay": barangay,
            "consent_version": "1.0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_create_session_succeeds(client: TestClient) -> None:
    citizen_id = _make_citizen(client)
    response = client.post(
        "/api/v1/sessions",
        json={
            "citizen_id": citizen_id,
            "device_id": "kiosk-001",
            "measurement_path": "vitals",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["printed_status"] == "not_requested"
    assert body["citizen_id"] == citizen_id
    assert body["device_id"] == "kiosk-001"
    assert body["measurement_path"] == "vitals"
    assert "id" in body and len(body["id"]) == 36
    assert "started_at" in body


def test_create_session_unknown_citizen_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sessions",
        json={
            "citizen_id": "00000000-0000-0000-0000-000000000000",
            "device_id": "kiosk-001",
        },
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_create_session_inactive_citizen_returns_400(client: TestClient) -> None:
    citizen_id = _make_citizen(client)
    delete_resp = client.delete(f"/api/v1/citizens/{citizen_id}")
    assert delete_resp.status_code == 204

    response = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "kiosk-001"},
    )
    assert response.status_code == 400
    assert "inactive" in response.json()["detail"]


def test_get_session_by_id(client: TestClient) -> None:
    citizen_id = _make_citizen(client)
    created = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "k1"},
    ).json()
    response = client.get(f"/api/v1/sessions/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_nonexistent_session_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/sessions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_sessions_filters_by_citizen(client: TestClient) -> None:
    a = _make_citizen(client, rfid="A")
    b = _make_citizen(client, rfid="B")
    client.post("/api/v1/sessions", json={"citizen_id": a, "device_id": "k"})
    client.post("/api/v1/sessions", json={"citizen_id": a, "device_id": "k"})
    client.post("/api/v1/sessions", json={"citizen_id": b, "device_id": "k"})

    response = client.get("/api/v1/sessions", params={"citizen_id": a})
    body = response.json()
    assert body["total"] == 2
    assert all(item["citizen_id"] == a for item in body["items"])


def test_list_sessions_filters_by_barangay(client: TestClient) -> None:
    a = _make_citizen(client, rfid="A", barangay="Tibagan")
    b = _make_citizen(client, rfid="B", barangay="Pinaglabanan")
    client.post("/api/v1/sessions", json={"citizen_id": a, "device_id": "k"})
    client.post("/api/v1/sessions", json={"citizen_id": b, "device_id": "k"})

    response = client.get("/api/v1/sessions", params={"barangay": "Tibagan"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["citizen_id"] == a


def test_patch_session_in_progress_to_completed(client: TestClient) -> None:
    citizen_id = _make_citizen(client)
    session_id = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "k1"},
    ).json()["id"]

    response = client.patch(
        f"/api/v1/sessions/{session_id}",
        json={
            "status": "completed",
            "ended_at": "2026-04-28T12:00:00Z",
            "printed_status": "printed_ok",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["printed_status"] == "printed_ok"
    assert body["ended_at"] == "2026-04-28T12:00:00Z"


def test_patch_terminal_session_returns_400(client: TestClient) -> None:
    citizen_id = _make_citizen(client)
    session_id = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "k1"},
    ).json()["id"]
    client.patch(f"/api/v1/sessions/{session_id}", json={"status": "completed"})

    response = client.patch(
        f"/api/v1/sessions/{session_id}", json={"status": "aborted"}
    )
    assert response.status_code == 400
    assert "terminal" in response.json()["detail"].lower()


# Verifies that protected session fields (id, citizen_id, device_id,
# started_at) cannot be changed via PATCH. Would fail if extra="forbid"
# were removed from the SessionUpdate schema or if the schema started
# accepting protected fields.
def test_patch_session_with_protected_field_returns_422(
    client: TestClient,
) -> None:
    citizen_id = _make_citizen(client)
    session_id = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "k1"},
    ).json()["id"]

    response = client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"citizen_id": "00000000-0000-0000-0000-deadbeefdead"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("citizen_id" in str(error.get("loc", [])) for error in detail)

    # The session must be unchanged: rejected PATCH leaks no partial state.
    fetch = client.get(f"/api/v1/sessions/{session_id}")
    assert fetch.status_code == 200
    assert fetch.json()["citizen_id"] == citizen_id


def test_patch_session_invalid_ended_at_returns_422(client: TestClient) -> None:
    citizen_id = _make_citizen(client)
    session_id = client.post(
        "/api/v1/sessions",
        json={"citizen_id": citizen_id, "device_id": "k1"},
    ).json()["id"]

    response = client.patch(
        f"/api/v1/sessions/{session_id}", json={"ended_at": "not-a-date"}
    )
    assert response.status_code == 422
