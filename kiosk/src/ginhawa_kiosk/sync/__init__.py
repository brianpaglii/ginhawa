"""Cloud sync: HTTP client + sync daemon.

* :class:`CloudClient` — async httpx wrapper around the cloud's
  ``/api/v1/sync/{citizens,sessions,measurements}`` endpoints.
* :class:`SyncDaemon` — periodic worker that selects unsynced rows
  (``synced=0``), batches up to 100 per request, posts to the cloud,
  and marks rows ``synced=1`` only on explicit per-record
  confirmation. ``CloudUnavailable`` is non-fatal — the next cycle
  retries; ``CloudCredentialError`` stops the daemon.
"""

from .client import (
    CloudClient,
    CloudClientError,
    CloudCredentialError,
    CloudSyncError,
    CloudUnavailable,
)
from .daemon import SyncDaemon
from .schemas import (
    BatchSyncRecordResult,
    BatchSyncResponse,
    CitizenSync,
    MeasurementSync,
    SessionSync,
    SyncStatus,
)

__all__ = [
    "BatchSyncRecordResult",
    "BatchSyncResponse",
    "CitizenSync",
    "CloudClient",
    "CloudClientError",
    "CloudCredentialError",
    "CloudSyncError",
    "CloudUnavailable",
    "MeasurementSync",
    "SessionSync",
    "SyncDaemon",
    "SyncStatus",
]
