"""Cloud sync: HTTP client + sync daemon.

Concrete implementations land in subsequent prompts. The shape:

* ``client.py`` — typed httpx wrapper around the cloud's
  ``/api/v1/sync/{citizens,sessions,measurements}`` endpoints. Retries
  with exponential backoff on transient errors; surfaces per-record
  results from the cloud's ``BatchSyncResponse`` to the daemon.
* ``daemon.py`` — periodic worker that selects unsynced rows
  (``synced=0``), batches up to 500 per request, posts to the cloud,
  and marks ``synced=1`` only on explicit per-record confirmation.
  Failure is non-fatal: the kiosk continues to accumulate locally,
  the daemon retries with backoff, see CLAUDE.md.
"""
