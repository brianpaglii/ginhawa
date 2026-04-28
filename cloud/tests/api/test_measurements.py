from fastapi.testclient import TestClient


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
