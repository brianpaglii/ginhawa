"""HTTP client for the cloud sync API.

Wraps ``httpx.AsyncClient`` with three POST methods that talk to
``/api/v1/sync/{citizens,sessions,measurements}``. The cloud's
contract: a JSON array goes in, a ``BatchSyncResponse`` comes back
with a per-record result. The client validates the response shape
through Pydantic before handing it to the daemon.

Failure semantics mirror the operational reality of an offline-first
kiosk:

* Connection refused, DNS failure, TLS handshake failure, request
  timeout, or any 5xx → ``CloudUnavailable``. The daemon treats this
  as "wait for the next periodic run" rather than escalating.
* 401 → ``CloudCredentialError``. The kiosk's API key is bad or the
  credential was revoked. The daemon stops on this signal — there
  is no retry that fixes a misconfigured key, and continuing to hit
  the cloud with an invalid key is noise that obscures the real
  problem in the audit log.
* Other 4xx (e.g., 413 oversize batch) → ``CloudClientError`` with
  the response body in the message. The daemon batches at 100, so
  413 (cloud caps at 500) means a programming error and should not
  silently retry.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from .schemas import BatchSyncResponse, CitizenSync, MeasurementSync, SessionSync


class CloudSyncError(Exception):
    """Base class for sync-client errors. Never raised directly."""


class CloudUnavailable(CloudSyncError):
    """Network failure or 5xx — the daemon should wait and retry."""


class CloudCredentialError(CloudSyncError):
    """401 from the cloud — credential is bad or revoked. Stop the daemon."""


class CloudClientError(CloudSyncError):
    """Unexpected 4xx (not 401). Programming error; do not silently retry."""


# Connect: 10 s. Read: 60 s — sync batches under load on the cloud
# Postgres can take meaningfully longer than a CRUD request because the
# cloud verifies every record's idempotency key and writes audit rows.
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


class CloudClient:
    """Async HTTP client for the kiosk-to-cloud sync endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        device_id: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._device_id = device_id
        # Allow tests to inject an httpx.AsyncClient backed by
        # pytest-httpx's mock transport. Production constructs its own.
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
        self._owns_client = client is None

    @property
    def device_id(self) -> str:
        """The device_id this client is attributed as. Read-only."""
        return self._device_id

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CloudClient:  # pragma: no cover - context sugar
        return self

    async def __aexit__(  # pragma: no cover - context sugar
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ---- public API ----------------------------------------------------

    async def sync_citizens(self, records: list[CitizenSync]) -> BatchSyncResponse:
        return await self._post_batch("/api/v1/sync/citizens", records)

    async def sync_sessions(self, records: list[SessionSync]) -> BatchSyncResponse:
        return await self._post_batch("/api/v1/sync/sessions", records)

    async def sync_measurements(
        self, records: list[MeasurementSync]
    ) -> BatchSyncResponse:
        return await self._post_batch("/api/v1/sync/measurements", records)

    # ---- internals -----------------------------------------------------

    async def _post_batch(
        self,
        path: str,
        records: list[Any],
    ) -> BatchSyncResponse:
        body = [r.model_dump(mode="json") for r in records]
        try:
            response = await self._client.post(path, json=body)
        except (
            httpx.TimeoutException,
            httpx.TransportError,
            httpx.NetworkError,
        ) as exc:
            # Subclass order matters: NetworkError is a TransportError, but
            # we list it explicitly so the intent is documented at the
            # call site even if httpx reorders its hierarchy later.
            raise CloudUnavailable(
                f"network error talking to cloud at {path}: {exc}"
            ) from exc

        if response.status_code == 200:
            return BatchSyncResponse.model_validate(response.json())
        if response.status_code == 401:
            raise CloudCredentialError(
                f"cloud rejected credential on {path}: {response.text}"
            )
        if 500 <= response.status_code < 600:
            raise CloudUnavailable(
                f"cloud {response.status_code} on {path}: {response.text}"
            )
        raise CloudClientError(
            f"unexpected {response.status_code} on {path}: {response.text}"
        )
