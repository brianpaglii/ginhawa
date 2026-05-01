"""Kiosk authentication via Bearer API key.

Exercises the ``get_current_kiosk`` dependency and the underlying
``verify_kiosk_credential`` helper. Kiosks are a separate principal
type from BHW JWT users — these tests use an isolated FastAPI app
with a single probe route so the dependency can be hit end-to-end
without polluting the production app's OpenAPI surface.
"""

import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ginhawa_cloud.core import security as security_module
from ginhawa_cloud.core.security import (
    get_current_kiosk,
    hash_password,
    verify_kiosk_credential,
)
from ginhawa_cloud.db.models import DeviceCredential
from ginhawa_cloud.db.session import get_db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def kiosk_probe_client(session_factory) -> TestClient:
    """A TestClient backed by an isolated FastAPI app with a single probe
    route that exercises ``Depends(get_current_kiosk)``. Lets these
    tests verify the dependency end-to-end without polluting the
    production app's OpenAPI surface or sharing state with the main
    ``client`` fixture's overrides."""

    test_app = FastAPI()

    @test_app.get("/_probe")
    def _probe(
        kiosk: DeviceCredential = Depends(get_current_kiosk),
    ) -> dict[str, str | None]:
        return {
            "device_id": kiosk.device_id,
            "last_seen_at": kiosk.last_seen_at,
        }

    def _override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    return TestClient(test_app)


def _create_credential_via_api(client: TestClient, description: str) -> dict:
    """Helper: POST to the admin endpoint and return {device_id, api_key}."""
    response = client.post(
        "/api/v1/device-credentials", json={"description": description}
    )
    assert response.status_code == 201, response.text
    return response.json()


# Verifies the happy path: a credential's plaintext API key authenticates
# via the Bearer header, the probe returns 200, and last_seen_at is set
# (was None pre-auth, is a timestamp after).
# Would fail if get_current_kiosk did not look up by api_key_hash or
# did not update last_seen_at.
def test_valid_api_key_authenticates(
    client: TestClient,
    kiosk_probe_client: TestClient,
    db_session: Session,
) -> None:
    created = _create_credential_via_api(client, "kiosk_valid_auth")
    api_key = created["api_key"]

    # Pre-auth state: last_seen_at is NULL
    pre = db_session.get(DeviceCredential, created["device_id"])
    assert pre is not None and pre.last_seen_at is None

    response = kiosk_probe_client.get(
        "/_probe", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == created["device_id"]
    assert body["last_seen_at"] is not None

    # Persistence check: the update isn't only in the response body.
    db_session.expire_all()
    post = db_session.get(DeviceCredential, created["device_id"])
    assert post is not None and post.last_seen_at is not None


# Verifies the negative path: a Bearer header carrying a key that
# doesn't match any active credential returns 401 with the generic
# "invalid kiosk credential" message.
# Would fail if a non-matching key were accepted.
def test_invalid_api_key_returns_401(
    kiosk_probe_client: TestClient,
) -> None:
    response = kiosk_probe_client.get(
        "/_probe",
        headers={"Authorization": "Bearer this-key-does-not-exist"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid kiosk credential"


# Verifies that revoked credentials are excluded from the active-set
# lookup. We create + immediately revoke a credential, then attempt to
# authenticate with its (still valid-looking) plaintext key.
# Would fail if verify_kiosk_credential did not filter on revoked_at
# IS NULL — the revoked credential would still match.
def test_revoked_credential_returns_401(
    client: TestClient,
    kiosk_probe_client: TestClient,
) -> None:
    created = _create_credential_via_api(client, "kiosk_revoked_target")
    api_key = created["api_key"]
    device_id = created["device_id"]

    revoke_resp = client.patch(
        f"/api/v1/device-credentials/{device_id}", json={"revoke": True}
    )
    assert revoke_resp.status_code == 200

    response = kiosk_probe_client.get(
        "/_probe", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid kiosk credential"


# Verifies that headers without "Bearer ", empty headers, and wrong
# schemes all return 401 — and with the SAME message the no-match path
# uses, so a client cannot distinguish "I sent a malformed header"
# from "my key was wrong".
# Would fail if the Bearer prefix check were removed.
def test_malformed_authorization_header_returns_401(
    kiosk_probe_client: TestClient,
) -> None:
    cases = [
        "no-scheme-just-a-token",  # missing scheme entirely
        "Basic some-creds",  # wrong scheme
        "Bearer ",  # Bearer with no key
        "Bearer",  # scheme with no separator
        "",  # empty header value
    ]
    for header_value in cases:
        response = kiosk_probe_client.get(
            "/_probe", headers={"Authorization": header_value}
        )
        assert response.status_code == 401, (
            f"expected 401 for header value {header_value!r}, "
            f"got {response.status_code}"
        )
        assert response.json()["detail"] == "invalid kiosk credential"


# Verifies that omitting the Authorization header entirely returns 401
# (not 422 — FastAPI's default for missing required headers — because
# the contract is uniform "401 / invalid kiosk credential" across
# every authn failure mode).
# Would fail if the Header(...) requirement were made required at the
# FastAPI layer; FastAPI would then return 422 instead of 401.
def test_missing_authorization_header_returns_401(
    kiosk_probe_client: TestClient,
) -> None:
    response = kiosk_probe_client.get("/_probe")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid kiosk credential"


# Verifies the timing-leak mitigation: verify_kiosk_credential MUST run
# verify_password against every active credential, regardless of
# whether a match is found, so the work done is determined by the
# population size and not by the match position.
# Would fail if verify_kiosk_credential short-circuited on first
# match instead of completing all comparisons.
def test_credential_lookup_runs_constant_number_of_verifications(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    plaintexts: list[str] = []
    for i in range(3):
        plaintext = f"kiosk_const_time_key_{i}"  # pragma: allowlist secret
        plaintexts.append(plaintext)
        db_session.add(
            DeviceCredential(
                device_id=str(uuid.uuid4()),
                api_key_hash=hash_password(plaintext),
                description=f"kiosk_const_time_{i}",
                created_at=_utc_now_iso(),
                created_by="test_seed",
            )
        )
    db_session.commit()

    real_verify = security_module.verify_password
    counter = {"count": 0}

    def counting_verify(plain: str, hashed: str) -> bool:
        counter["count"] += 1
        return real_verify(plain, hashed)

    monkeypatch.setattr(security_module, "verify_password", counting_verify)

    # Unknown key: still must run the full N comparisons.
    counter["count"] = 0
    result = verify_kiosk_credential("a-key-that-matches-nothing", db_session)
    assert result is None
    assert counter["count"] == 3, (
        "unknown-key path must run verify_password against every active "
        "credential, not short-circuit when no match is found"
    )

    # Known key matching the second-position credential: must STILL run
    # all 3 comparisons rather than stop at #2.
    counter["count"] = 0
    result = verify_kiosk_credential(plaintexts[1], db_session)
    assert result is not None
    assert result.description == "kiosk_const_time_1"
    assert counter["count"] == 3, (
        "match found at position 2 of 3 — the loop must continue past "
        "the match so the work done equals the active-population size"
    )


# Verifies last_seen_at advances on subsequent authentications.
# Would fail if last_seen_at update were skipped or batched.
def test_last_seen_at_updates_on_each_request(
    client: TestClient,
    kiosk_probe_client: TestClient,
) -> None:
    created = _create_credential_via_api(client, "kiosk_last_seen")
    api_key = created["api_key"]
    headers = {"Authorization": f"Bearer {api_key}"}

    first = kiosk_probe_client.get("/_probe", headers=headers)
    assert first.status_code == 200
    first_seen = first.json()["last_seen_at"]
    assert first_seen is not None

    # 50 ms is comfortably larger than the microsecond resolution of
    # datetime.now(); much shorter than the 1-second figure in the
    # original spec which assumed second-precision timestamps.
    time.sleep(0.05)

    second = kiosk_probe_client.get("/_probe", headers=headers)
    assert second.status_code == 200
    second_seen = second.json()["last_seen_at"]
    assert second_seen is not None
    # ISO 8601 UTC strings sort lexicographically the same as
    # chronologically, so > on the strings is a valid ordering check.
    assert second_seen > first_seen, (
        f"expected second auth's last_seen_at ({second_seen}) to be "
        f"strictly after first ({first_seen})"
    )
