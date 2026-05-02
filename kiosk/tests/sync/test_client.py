"""Cloud HTTP client behaviour."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ginhawa_kiosk.sync import (
    CitizenSync,
    CloudClient,
    CloudClientError,
    CloudCredentialError,
    CloudUnavailable,
)

from .conftest import TEST_API_KEY, TEST_BASE_URL, ok_response_body


def _make_citizen_sync(citizen_id: str | None = None) -> CitizenSync:
    now = datetime.now(timezone.utc).isoformat()
    dob = (date.today() - timedelta(days=365 * 30)).isoformat()
    return CitizenSync(
        id=citizen_id or str(uuid.uuid4()),
        rfid_uid=f"CARD_{uuid.uuid4().hex[:8].upper()}",
        full_name="Test Citizen",
        dob=dob,
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version="v1",
        consent_given_at=now,
        registered_at=now,
        registered_by=None,
        is_active=1,
        updated_at=now,
    )


# Verifies the happy path: the client serialises records to a JSON
# array, attaches Bearer auth, and parses the cloud's response body
# into a BatchSyncResponse with per-record results. Also asserts that
# the Authorization header carries the api_key, so the auth wiring
# works on the production code path (not just the injected client).
# Would fail if model_dump used the wrong mode (datetime objects in
# JSON), the Authorization header were dropped, or the response
# parsing were skipped.
@pytest.mark.asyncio
async def test_sync_citizens_returns_parsed_response(
    cloud_client: CloudClient, httpx_mock: HTTPXMock
) -> None:
    record = _make_citizen_sync()
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json=ok_response_body([record.id], status="created"),
        status_code=200,
    )

    response = await cloud_client.sync_citizens([record])
    assert len(response.results) == 1
    assert response.results[0].id == record.id
    assert response.results[0].status == "created"
    assert response.results[0].error is None

    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.headers["Authorization"] == f"Bearer {TEST_API_KEY}"


# Verifies CloudUnavailable on 5xx. The cloud's 500 path fires when
# Postgres connection drops or migration fails mid-flight; the kiosk
# treats this as transient and waits for the next cycle. Would fail
# if the client raised a different exception type, or if it accepted
# 500 as a successful response.
@pytest.mark.asyncio
async def test_sync_citizens_raises_cloud_unavailable_on_500(
    cloud_client: CloudClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        status_code=500,
        text="internal server error",
    )
    with pytest.raises(CloudUnavailable, match="500"):
        await cloud_client.sync_citizens([_make_citizen_sync()])


# Verifies CloudCredentialError on 401. This is the signal the daemon
# uses to STOP — there is no retry that fixes a bad key. Would fail
# if the client conflated 401 with the 5xx CloudUnavailable bucket
# (which would cause the daemon to retry forever with a bad key).
@pytest.mark.asyncio
async def test_sync_citizens_raises_credential_error_on_401(
    cloud_client: CloudClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        status_code=401,
        json={"detail": "invalid kiosk credential"},
    )
    with pytest.raises(CloudCredentialError):
        await cloud_client.sync_citizens([_make_citizen_sync()])


# Verifies CloudUnavailable on raw connection errors. We exercise
# httpx.ConnectError (the most common offline case — DNS/TCP refusal)
# via pytest-httpx's exception injection. Would fail if the client
# let the httpx exception escape unwrapped, or if it caught it but
# raised the wrong type (which would muddle the daemon's "wait vs
# stop" decision).
@pytest.mark.asyncio
async def test_sync_citizens_raises_cloud_unavailable_on_connection_error(
    cloud_client: CloudClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(
        httpx.ConnectError("Connection refused"),
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
    )
    with pytest.raises(CloudUnavailable, match="network error"):
        await cloud_client.sync_citizens([_make_citizen_sync()])


# Verifies an unexpected 4xx (here, 413 — oversize batch) is reported
# distinctly as CloudClientError, not bucketed with CloudUnavailable.
# The daemon batches at 100 vs cloud's 500 cap, so a 413 is a
# programming error, not a transient network issue, and silently
# retrying would mask the bug. Would fail if the client's status
# discrimination was broadened to 4xx-as-unavailable.
@pytest.mark.asyncio
async def test_sync_citizens_raises_client_error_on_413(
    cloud_client: CloudClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        status_code=413,
        json={"detail": "batch size 600 exceeds maximum of 500"},
    )
    with pytest.raises(CloudClientError, match="413"):
        await cloud_client.sync_citizens([_make_citizen_sync()])
