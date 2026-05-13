"""Background sync daemon.

Periodically scans the local SQLite for ``synced=0`` rows, batches up
to 100 of each type, and posts them to the cloud through ``CloudClient``.
Per-record results dictate the local action:

* ``created`` / ``updated`` → mark ``synced=1`` (cloud has it).
* ``conflict_stale`` → mark ``synced=1``. The cloud already has a
  newer version of this record; nothing for the kiosk to do. Leaving
  ``synced=0`` would just re-upload the same stale record forever.
* ``conflict_constraint`` → leave ``synced=0`` and log. Operator has
  to intervene (e.g., another citizen already holds this RFID).
  Re-running on the next pass will produce the same result; the log
  is the durable record of the issue.
* ``rejected`` → leave ``synced=0`` and log with the error. Same
  shape as ``conflict_constraint`` — operator review needed.

FK ordering matters: citizens must land on the cloud before sessions
that reference them, and sessions before measurements. We process the
three types in that order in each cycle.

Failure handling:

* ``CloudUnavailable`` → log and wait for the next cycle. No retry-
  with-backoff; the cycle interval is the "backoff".
* ``CloudCredentialError`` → log loudly and stop the daemon. There
  is no retry that fixes a bad key; continuing to hammer the cloud
  with bad credentials wastes resources and obscures the real problem
  in the audit log. The systemd unit will not auto-restart on this
  exit code (deployment concern, captured in the runbook).

Every cycle writes one ``audit_log`` row with
``actor_type='system'``, ``action='sync_attempt'``, and details
capturing the per-type batch sizes and outcome counts.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import (
    Citizen,
    Measurement,
)
from ..db.models import Session as SessionModel
from ..services.audit import record_audit
from .client import CloudClient, CloudCredentialError, CloudUnavailable
from .schemas import (
    BatchSyncResponse,
    CitizenSync,
    MeasurementSync,
    SessionSync,
)


# Cap on records per request. The cloud accepts up to 500; we send
# 100 to keep latency predictable on the kiosk's modest uplink and to
# bound the size of any single failure (a 5xx mid-batch causes the
# whole batch to be retried; 100 records is a small enough retry).
_BATCH_LIMIT = 100

# Marks records that successful upload — the cloud confirmed they
# made it. ``conflict_stale`` is included because the cloud rejects
# the kiosk's row in favour of its own newer version; nothing for
# the kiosk to do but stop trying.
_TERMINAL_OK_STATUSES: frozenset[str] = frozenset(
    {"created", "updated", "conflict_stale"}
)


# ---------------------------------------------------------------------------
# Per-table conversion: ORM → wire schema
# ---------------------------------------------------------------------------


def _citizen_to_wire(c: Citizen) -> CitizenSync:
    return CitizenSync(
        id=c.id,
        rfid_uid=c.rfid_uid,
        full_name=c.full_name,
        dob=c.dob,
        sex=c.sex,  # type: ignore[arg-type]  # Literal narrowing
        barangay=c.barangay,
        phone=c.phone,
        consent_version=c.consent_version,
        consent_given_at=c.consent_given_at,
        registered_at=c.registered_at,
        registered_by=c.registered_by,
        is_active=c.is_active,
        updated_at=c.updated_at,
    )


def _session_to_wire(s: SessionModel, device_id: str) -> SessionSync:
    return SessionSync(
        id=s.id,
        citizen_id=s.citizen_id,
        # The kiosk's authoritative device_id is the credential's
        # device_id. We override whatever was stored locally so the
        # cloud's spoof guard accepts the row.
        device_id=device_id,
        started_at=s.started_at,
        ended_at=s.ended_at,
        status=s.status,  # type: ignore[arg-type]
        error_reason=s.error_reason,
        measurement_path=s.measurement_path,  # type: ignore[arg-type]
        printed_status=s.printed_status,  # type: ignore[arg-type]
        synced=1,
        updated_at=s.updated_at,
    )


def _measurement_to_wire(m: Measurement) -> MeasurementSync:
    return MeasurementSync(
        id=m.id,
        session_id=m.session_id,
        type=m.type,  # type: ignore[arg-type]
        value=m.value,
        unit=m.unit,
        source_device=m.source_device,
        measured_at=m.measured_at,
        is_valid=m.is_valid,
        validation_notes=m.validation_notes,
        raw_json=m.raw_json,
        synced=1,
        updated_at=m.updated_at,
    )


# ---------------------------------------------------------------------------
# Per-table fetch: pending rows
# ---------------------------------------------------------------------------


def _fetch_unsynced_citizens(session: Session, limit: int) -> list[Citizen]:
    return list(
        session.execute(
            select(Citizen)
            .where(Citizen.synced == 0)
            .order_by(Citizen.registered_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _fetch_unsynced_sessions(session: Session, limit: int) -> list[SessionModel]:
    return list(
        session.execute(
            select(SessionModel)
            .where(SessionModel.synced == 0)
            .order_by(SessionModel.started_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _fetch_unsynced_measurements(session: Session, limit: int) -> list[Measurement]:
    return list(
        session.execute(
            select(Measurement)
            .where(Measurement.synced == 0)
            .order_by(Measurement.measured_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Result handling
# ---------------------------------------------------------------------------


def _apply_results(
    session: Session,
    rows_by_id: dict[str, Any],
    response: BatchSyncResponse,
    type_label: str,
    logger: Any,
) -> Counter[str]:
    """Apply per-record results to local rows. Return outcome counts."""
    counts: Counter[str] = Counter()
    for result in response.results:
        counts[result.status] += 1
        row = rows_by_id.get(result.id)
        if row is None:
            # Cloud returned a result for a record we didn't send.
            # That's a contract violation; log and skip.
            logger.error(
                "sync.unknown_id_in_response",
                type=type_label,
                id=result.id,
                status=result.status,
            )
            continue
        if result.status in _TERMINAL_OK_STATUSES:
            row.synced = 1
        else:
            # conflict_constraint or rejected — leave synced=0 so the
            # operator can investigate and the daemon will retry on
            # the next cycle (where it will likely fail the same way
            # until the underlying issue is fixed).
            logger.warning(
                "sync.record_not_uploaded",
                type=type_label,
                id=result.id,
                status=result.status,
                error=result.error,
            )
    return counts


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


class SyncDaemon:
    """Periodic background uploader.

    Owns no DB or cloud handles directly — both are passed in so tests
    can drive the daemon synchronously without a running event loop.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        cloud: CloudClient,
        interval_seconds: float = 30.0,
        logger: Any | None = None,
        on_cycle_complete: Callable[[bool], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cloud = cloud
        self._interval = interval_seconds
        self._logger = logger or _default_logger()
        self._stop = asyncio.Event()
        self._stopped_due_to_credential_error = False
        # Optional hook fired after every cycle that made at least one
        # HTTP attempt; the bool is True when every attempt reached the
        # cloud, False when any ``CloudUnavailable`` was caught OR a
        # ``CloudCredentialError`` terminated the loop. Empty cycles
        # (no pending records → no HTTP) do NOT fire — they carry no
        # signal about cloud reachability, so the caller keeps the
        # last known status. Used by main_window to drive the
        # BrandedFooter's network indicator; tests pass a recorder.
        self._on_cycle_complete = on_cycle_complete

    @property
    def stopped_due_to_credential_error(self) -> bool:
        return self._stopped_due_to_credential_error

    async def run(self) -> None:
        """Run forever (until ``stop()`` is called or a credential error
        terminates the loop)."""
        while not self._stop.is_set():
            try:
                await self.run_once()
            except CloudCredentialError as exc:
                self._logger.error(
                    "sync.credential_error_stopping_daemon", reason=str(exc)
                )
                self._stopped_due_to_credential_error = True
                # Surface the failure to the status hook before
                # exiting — 401 means we reached the cloud but auth
                # failed, which from the citizen-facing "is sync
                # working" perspective is indistinguishable from
                # offline. The daemon won't fire again after this.
                self._fire_status(False)
                return
            except OperationalError as exc:
                # SQLite write contention with the FSM (e.g., the citizen
                # taps "Finish without printing" the same instant the
                # daemon is staging a sync_attempt audit row). The
                # 2026-05-08 bench captured exactly this collision:
                # 8 successful cycles, then a "database is locked"
                # OperationalError that previously escaped to the
                # add_done_callback in __main__ and surfaced as
                # kiosk.sync_daemon_crashed. The contention is transient
                # — the next cycle (interval seconds away) is the retry,
                # so we just log + continue here. Truncate the error
                # message because SQLAlchemy's str(exc) embeds the full
                # offending SQL which makes journalctl noisy.
                self._logger.warning(
                    "sync.db_locked_retrying",
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    def _fire_status(self, online: bool) -> None:
        # Defensive try/except — a misbehaving GUI hook must not
        # crash the daemon. The whole point of this callback is
        # surfacing a UX status; failing the sync loop over a Qt
        # label update would be the wrong trade.
        if self._on_cycle_complete is None:
            return
        try:
            self._on_cycle_complete(online)
        except Exception as exc:
            self._logger.warning(
                "sync.on_cycle_complete_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )

    async def run_once(self) -> None:
        """One sync pass: citizens, then sessions, then measurements.

        Raises ``CloudCredentialError`` to the caller (so ``run()`` can
        terminate the loop). ``CloudUnavailable`` is caught and logged;
        the next cycle retries.
        """
        with self._session_factory() as session:
            citizen_counts = await self._sync_citizens(session)
            session_counts = await self._sync_sessions(session)
            measurement_counts = await self._sync_measurements(session)

            # Network-status signal: an HTTP attempt happened iff at
            # least one Counter is non-empty (the ``_sync_*`` helpers
            # short-circuit before calling the cloud when there are no
            # rows). If every attempt landed cleanly, we're online;
            # any ``unavailable`` entry flips to offline. Empty cycle
            # = no signal = no callback (keep prior status).
            all_counts = (citizen_counts, session_counts, measurement_counts)
            attempted = any(sum(c.values()) > 0 for c in all_counts)
            had_unavailable = any(c.get("unavailable", 0) > 0 for c in all_counts)
            if attempted:
                self._fire_status(not had_unavailable)

            record_audit(
                session,
                action="sync_attempt",
                actor_type="system",
                actor_id="sync_daemon",
                details={
                    "citizens": dict(citizen_counts),
                    "sessions": dict(session_counts),
                    "measurements": dict(measurement_counts),
                },
            )
            session.commit()

        # Heartbeat. The sync_attempt audit row above is the durable
        # trace, but it lives inside the encrypted SQLite — invisible
        # to journalctl. After a cycle completes, surface the per-type
        # counts at INFO so an operator tailing the unit can answer
        # "is sync alive?" without opening the kiosk DB. An empty-
        # counts emission (kiosk caught up) is intentional — it's
        # the liveness signal.
        self._logger.info(
            "sync.cycle_complete",
            citizens=dict(citizen_counts),
            sessions=dict(session_counts),
            measurements=dict(measurement_counts),
        )

    # ---- per-type cycle helpers ---------------------------------------

    async def _sync_citizens(self, session: Session) -> Counter[str]:
        rows = _fetch_unsynced_citizens(session, _BATCH_LIMIT)
        if not rows:
            return Counter()
        wire = [_citizen_to_wire(r) for r in rows]
        try:
            response = await self._cloud.sync_citizens(wire)
        except CloudUnavailable as exc:
            self._logger.warning(
                "sync.cloud_unavailable",
                type="citizens",
                pending=len(rows),
                reason=str(exc),
            )
            return Counter({"unavailable": len(rows)})
        return _apply_results(
            session,
            {r.id: r for r in rows},
            response,
            type_label="citizen",
            logger=self._logger,
        )

    async def _sync_sessions(self, session: Session) -> Counter[str]:
        rows = _fetch_unsynced_sessions(session, _BATCH_LIMIT)
        if not rows:
            return Counter()
        wire = [_session_to_wire(r, self._cloud.device_id) for r in rows]
        try:
            response = await self._cloud.sync_sessions(wire)
        except CloudUnavailable as exc:
            self._logger.warning(
                "sync.cloud_unavailable",
                type="sessions",
                pending=len(rows),
                reason=str(exc),
            )
            return Counter({"unavailable": len(rows)})
        return _apply_results(
            session,
            {r.id: r for r in rows},
            response,
            type_label="session",
            logger=self._logger,
        )

    async def _sync_measurements(self, session: Session) -> Counter[str]:
        rows = _fetch_unsynced_measurements(session, _BATCH_LIMIT)
        if not rows:
            return Counter()
        wire = [_measurement_to_wire(r) for r in rows]
        try:
            response = await self._cloud.sync_measurements(wire)
        except CloudUnavailable as exc:
            self._logger.warning(
                "sync.cloud_unavailable",
                type="measurements",
                pending=len(rows),
                reason=str(exc),
            )
            return Counter({"unavailable": len(rows)})
        return _apply_results(
            session,
            {r.id: r for r in rows},
            response,
            type_label="measurement",
            logger=self._logger,
        )


def _default_logger() -> Any:
    """Return the structlog logger used by the daemon when the caller
    doesn't inject one. Imported lazily so tests can replace structlog
    with a stub without paying the configure-on-import cost."""
    import structlog

    return structlog.get_logger("sync_daemon")
