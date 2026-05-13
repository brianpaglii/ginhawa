"""SyncDaemon behaviour against an encrypted local DB."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.models import AuditLog, Citizen, Measurement
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.sync import CloudClient, SyncDaemon

from .conftest import (
    TEST_BASE_URL,
    TEST_DEVICE_ID,
    CapturingLogger,
    ok_response_body,
)


# ---------- helpers --------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_citizen(
    session: Session, *, citizen_id: str | None = None, synced: int = 0
) -> str:
    cid = citizen_id or str(uuid.uuid4())
    session.add(
        Citizen(
            id=cid,
            rfid_uid=f"CARD_{uuid.uuid4().hex[:8].upper()}",
            full_name="Daemon Probe",
            dob=(date.today() - timedelta(days=365 * 30)).isoformat(),
            sex="F",
            barangay="Tibagan",
            phone=None,
            consent_version="v1",
            consent_given_at=_utc_now_iso(),
            registered_at=_utc_now_iso(),
            registered_by=None,
            is_active=1,
            synced=synced,
            updated_at=_utc_now_iso(),
        )
    )
    session.commit()
    return cid


def _seed_session(
    session: Session, citizen_id: str, *, session_id: str | None = None
) -> str:
    sid = session_id or str(uuid.uuid4())
    session.add(
        SessionModel(
            id=sid,
            citizen_id=citizen_id,
            device_id=TEST_DEVICE_ID,
            started_at=_utc_now_iso(),
            ended_at=_utc_now_iso(),
            status="completed",
            error_reason=None,
            measurement_path="vitals",
            printed_status="printed_ok",
            synced=0,
            updated_at=_utc_now_iso(),
        )
    )
    session.commit()
    return sid


def _seed_measurement(session: Session, session_id: str) -> str:
    mid = str(uuid.uuid4())
    session.add(
        Measurement(
            id=mid,
            session_id=session_id,
            type="systolic_bp",
            value=120.0,
            unit="mmHg",
            source_device="omron_hem7155t",
            measured_at=_utc_now_iso(),
            is_valid=1,
            validation_notes=None,
            raw_json=None,
            synced=0,
            updated_at=_utc_now_iso(),
        )
    )
    session.commit()
    return mid


def _make_daemon(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
) -> tuple[SyncDaemon, CapturingLogger]:
    logger = CapturingLogger()
    daemon = SyncDaemon(
        session_factory=session_factory,
        cloud=cloud_client,
        interval_seconds=30.0,
        logger=logger,
    )
    return daemon, logger


# ---------- tests ----------------------------------------------------


# Verifies the daemon flips synced=1 on a 'created' result. The audit
# row attributed to actor_type='system'/action='sync_attempt' carries
# the per-type counts so an operator can reconstruct what happened.
# Would fail if the daemon failed to commit, or if it forgot to
# update synced on success.
@pytest.mark.asyncio
async def test_daemon_marks_synced_on_created(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)

    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json=ok_response_body([citizen_id], status="created"),
        status_code=200,
    )

    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    citizen = db_session.get(Citizen, citizen_id)
    assert citizen is not None and citizen.synced == 1

    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "sync_attempt")
    ).scalar_one()
    assert audit.actor_type == "system"
    details = json.loads(audit.details)
    assert details["citizens"]["created"] == 1


# Verifies that conflict_stale (cloud has a newer version) is treated
# as a successful upload from the kiosk's POV — the row is marked
# synced=1 so the daemon stops re-uploading the same stale record.
# Would fail if conflict_stale were left as synced=0 (the daemon
# would retry forever).
@pytest.mark.asyncio
async def test_daemon_marks_synced_on_conflict_stale(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)

    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json=ok_response_body([citizen_id], status="conflict_stale"),
        status_code=200,
    )

    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    citizen = db_session.get(Citizen, citizen_id)
    assert citizen is not None and citizen.synced == 1


# Verifies that 'rejected' (validation or other reject path) leaves
# synced=0 — the operator needs to investigate, but the daemon must
# NOT mark the record as uploaded. The rejection is logged so the
# audit trail records the attempt.
# Would fail if rejected were treated as terminal-OK (the row would
# silently move to synced=1 despite never reaching the cloud).
@pytest.mark.asyncio
async def test_daemon_leaves_unsynced_on_rejected(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)

    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json={
            "results": [
                {
                    "id": citizen_id,
                    "status": "rejected",
                    "error": "dob: must be in the past",
                }
            ]
        },
        status_code=200,
    )

    daemon, logger = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    citizen = db_session.get(Citizen, citizen_id)
    assert citizen is not None and citizen.synced == 0

    # Rejection is logged so the audit trail captures it even though
    # the kiosk DB row stays unsynced.
    rejected_logs = [e for e in logger.events if e[1] == "sync.record_not_uploaded"]
    assert len(rejected_logs) == 1
    assert rejected_logs[0][2]["status"] == "rejected"


# Verifies CloudUnavailable does not crash the daemon — it logs and
# returns from run_once, leaving synced=0 so the next cycle retries.
# This is the offline-resilient behaviour the kiosk depends on.
# Would fail if the daemon let CloudUnavailable propagate (which
# would terminate the asyncio task).
@pytest.mark.asyncio
async def test_daemon_handles_cloud_unavailable_gracefully(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)

    httpx_mock.add_exception(
        httpx.ConnectError("Connection refused"),
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
    )

    daemon, logger = _make_daemon(session_factory, cloud_client)
    # Should NOT raise.
    await daemon.run_once()

    db_session.expire_all()
    citizen = db_session.get(Citizen, citizen_id)
    assert citizen is not None and citizen.synced == 0

    unavailable = [e for e in logger.events if e[1] == "sync.cloud_unavailable"]
    assert len(unavailable) >= 1
    assert unavailable[0][2]["type"] == "citizens"


# Verifies FK ordering: citizens land first, then sessions, then
# measurements. The cloud rejects sessions referencing unknown
# citizens (citizen_not_found) and measurements referencing unknown
# sessions, so the daemon MUST upload in dependency order.
# We assert both via the request order observed by httpx_mock and
# via the per-type counts in the audit row.
# Would fail if the daemon issued posts in a different order or in
# parallel (which could let the cloud see a session before its
# citizen).
@pytest.mark.asyncio
async def test_daemon_processes_in_fk_order(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)
    session_id = _seed_session(db_session, citizen_id)
    meas_id = _seed_measurement(db_session, session_id)

    # All three endpoints respond 'created'.
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json=ok_response_body([citizen_id], status="created"),
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/sessions",
        json=ok_response_body([session_id], status="created"),
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/measurements",
        json=ok_response_body([meas_id], status="created"),
        status_code=200,
    )

    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    paths = [r.url.path for r in httpx_mock.get_requests()]
    assert paths == [
        "/api/v1/sync/citizens",
        "/api/v1/sync/sessions",
        "/api/v1/sync/measurements",
    ]

    db_session.expire_all()
    assert db_session.get(Citizen, citizen_id).synced == 1
    assert db_session.get(SessionModel, session_id).synced == 1
    assert db_session.get(Measurement, meas_id).synced == 1


# Verifies that a CloudCredentialError stops the daemon's run loop —
# the daemon does NOT spin retrying with bad credentials. The
# stopped_due_to_credential_error flag is the signal a supervising
# layer (or systemd) uses to decide whether to restart.
# Would fail if the daemon caught CloudCredentialError as if it were
# CloudUnavailable (resulting in infinite retry with a known-bad key).
@pytest.mark.asyncio
async def test_daemon_run_stops_on_credential_error(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    _seed_citizen(db_session)

    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        status_code=401,
        json={"detail": "invalid kiosk credential"},
    )

    daemon, logger = _make_daemon(session_factory, cloud_client)
    # run() exits cleanly when a credential error is encountered.
    await daemon.run()

    assert daemon.stopped_due_to_credential_error is True
    cred_errs = [
        e for e in logger.events if e[1] == "sync.credential_error_stopping_daemon"
    ]
    assert len(cred_errs) == 1


# ---------------------------------------------------------------------
# on_cycle_complete callback — drives the kiosk's network indicator
# ---------------------------------------------------------------------


# A successful sync cycle (cloud reachable, record uploaded) fires the
# callback with True. The bool maps to the BrandedFooter's "● Online"
# state in production wiring.
# Mortality: would fail if the daemon stopped calling the callback,
# or if it called with False after a clean sync.
@pytest.mark.asyncio
async def test_daemon_fires_on_cycle_complete_true_on_successful_sync(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)
    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json=ok_response_body([citizen_id], status="created"),
        status_code=200,
    )

    received: list[bool] = []
    daemon = SyncDaemon(
        session_factory=session_factory,
        cloud=cloud_client,
        interval_seconds=30.0,
        logger=CapturingLogger(),
        on_cycle_complete=received.append,
    )

    await daemon.run_once()

    assert received == [True]


# When the cloud is unreachable (httpx_mock raises a connection
# error), the daemon's per-record helpers return Counter({"unavailable": N}),
# and on_cycle_complete fires with False — the footer flips to
# "○ Offline".
# Mortality: would fail if CloudUnavailable were no longer mapped to
# the unavailable counter key, or if the False path of the callback
# regressed.
@pytest.mark.asyncio
async def test_daemon_fires_on_cycle_complete_false_when_cloud_unreachable(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    _seed_citizen(db_session)
    httpx_mock.add_exception(
        httpx.ConnectError("kiosk-test: cloud unreachable"),
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
    )

    received: list[bool] = []
    daemon = SyncDaemon(
        session_factory=session_factory,
        cloud=cloud_client,
        interval_seconds=30.0,
        logger=CapturingLogger(),
        on_cycle_complete=received.append,
    )

    await daemon.run_once()

    assert received == [False]


# An empty cycle (nothing to sync) must NOT fire the callback —
# the daemon has no signal about cloud reachability when it didn't
# make any HTTP attempt. Firing True or False would either flicker
# a stale value into the badge or wipe the previous true state.
# Mortality: would fail if the daemon started always firing the
# callback, or if it fired on no-op cycles.
@pytest.mark.asyncio
async def test_daemon_does_not_fire_on_cycle_complete_when_no_records(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
) -> None:
    received: list[bool] = []
    daemon = SyncDaemon(
        session_factory=session_factory,
        cloud=cloud_client,
        interval_seconds=30.0,
        logger=CapturingLogger(),
        on_cycle_complete=received.append,
    )

    # No records seeded — every _sync_* short-circuits, no HTTP
    # request, no callback.
    await daemon.run_once()

    assert received == []


# ---------------------------------------------------------------------
# run() loop resilience to per-cycle exceptions
# ---------------------------------------------------------------------
# These tests stub run_once directly so they can drive the loop's
# exception-handling branches without needing an HTTP fixture or
# write-contended SQLite — both run_once's behaviour and the
# session/cloud setup are covered by the tests above. The stub
# means we never dereference ``session_factory`` / ``cloud``, so we
# pass cast'd None placeholders instead of pulling in the
# encrypted-DB conftest fixture (which depends on the system
# sqlcipher3 module that isn't installed on every dev box).


def _stub_daemon() -> tuple[SyncDaemon, CapturingLogger]:
    from typing import cast

    logger = CapturingLogger()
    daemon = SyncDaemon(
        session_factory=cast(sessionmaker[Session], None),
        cloud=cast(CloudClient, None),
        interval_seconds=0.01,
        logger=logger,
    )
    return daemon, logger


# Verifies the daemon catches OperationalError from run_once,
# logs sync.db_locked_retrying, and continues into the next cycle.
# Bench evidence (2026-05-08): SyncDaemon ran 8 successful cycles
# then crashed when the FSM grabbed the SQLite write lock at the
# same instant the daemon was committing a sync_attempt row. The
# add_done_callback in __main__ surfaced it as
# kiosk.sync_daemon_crashed; the contention is transient so the
# next cycle is the right "retry".
# Mortality: would fail if the OperationalError branch were
# dropped from run() and the daemon resumed exiting on
# write contention.
@pytest.mark.asyncio
async def test_run_recovers_from_operational_error() -> None:
    from sqlalchemy.exc import OperationalError

    daemon, logger = _stub_daemon()

    calls = {"n": 0}

    async def fake_run_once() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            # SQLAlchemy raises OperationalError with (statement,
            # params, orig). Construct it the way SQLAlchemy itself
            # would when SQLite says "database is locked".
            raise OperationalError(
                "INSERT INTO audit_log (...) VALUES (...)",
                {},
                Exception("database is locked"),
            )
        # Second cycle succeeds; signal stop so run() exits cleanly.
        daemon.stop()

    daemon.run_once = fake_run_once  # type: ignore[method-assign]
    await daemon.run()

    assert calls["n"] == 2
    assert daemon.stopped_due_to_credential_error is False
    db_locked = [e for e in logger.events if e[1] == "sync.db_locked_retrying"]
    assert len(db_locked) == 1
    level, _event, kwargs = db_locked[0]
    assert level == "warning"
    assert "OperationalError" in kwargs["error_type"]


# Verifies the CloudCredentialError path: the daemon terminates
# run() and flips stopped_due_to_credential_error. This is also
# exercised end-to-end by test_daemon_run_stops_on_credential_error
# above; this version isolates the run-loop semantics from the
# HTTP fixture so a regression in the loop logic doesn't hide
# behind an HTTP-mock misconfiguration.
@pytest.mark.asyncio
async def test_run_propagates_credential_error() -> None:
    from ginhawa_kiosk.sync.client import CloudCredentialError

    daemon, logger = _stub_daemon()

    async def fake_run_once() -> None:
        raise CloudCredentialError("invalid kiosk credential")

    daemon.run_once = fake_run_once  # type: ignore[method-assign]
    await daemon.run()

    assert daemon.stopped_due_to_credential_error is True
    cred_errs = [
        e for e in logger.events if e[1] == "sync.credential_error_stopping_daemon"
    ]
    assert len(cred_errs) == 1


# Verifies that an unexpected exception (anything other than
# CloudCredentialError or OperationalError) escapes run() so the
# add_done_callback in __main__.py surfaces it as
# kiosk.sync_daemon_crashed. The opposite (silently swallowing
# every exception) would mean a programming bug in run_once is
# invisible until the daemon falls behind on the wall clock.
# Mortality: would fail if a future blanket "except Exception"
# crept into run() and started eating real bugs.
@pytest.mark.asyncio
async def test_run_propagates_unexpected_error() -> None:
    daemon, _logger = _stub_daemon()

    async def fake_run_once() -> None:
        raise ValueError("simulated programming bug")

    daemon.run_once = fake_run_once  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="simulated programming bug"):
        await daemon.run()

    # Daemon did not stop "cleanly" — the credential-error flag is
    # still False because the exception bypassed that branch.
    assert daemon.stopped_due_to_credential_error is False


# ---------------------------------------------------------------------
# run_once() heartbeat — sync.cycle_complete on success
# ---------------------------------------------------------------------
# These tests stub the per-type sync helpers so the heartbeat
# semantics are exercised without needing a real cloud round-trip
# or the encrypted-DB conftest fixture (which depends on the
# system sqlcipher3 module that isn't installed on every dev box).
# Behavioural coverage of the per-type helpers themselves is the
# job of test_daemon_marks_synced_on_created etc. above.


class _NullSession:
    """Stand-in for a SQLAlchemy Session inside run_once.

    Only the surface run_once touches is implemented: the context-
    manager protocol, ``commit()``, plus whatever record_audit
    pokes. ``record_audit`` is monkey-patched to a no-op in the
    tests below, so we don't need to model audit_log here.
    """

    def __enter__(self) -> "_NullSession":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        return None


def _stub_daemon_with_session_factory() -> tuple[SyncDaemon, CapturingLogger]:
    from typing import cast

    logger = CapturingLogger()

    def _factory() -> _NullSession:
        return _NullSession()

    daemon = SyncDaemon(
        session_factory=cast(sessionmaker[Session], _factory),
        cloud=cast(CloudClient, None),
        interval_seconds=0.01,
        logger=logger,
    )
    return daemon, logger


# Verifies the daemon emits sync.cycle_complete at INFO with the
# per-type counts after a successful cycle. The empty-Counter case
# is part of the contract — an idle kiosk should still produce a
# heartbeat line so journalctl shows the daemon is alive.
# Mortality: would fail if the heartbeat was dropped, demoted, or
# the count fields drifted to a different shape.
@pytest.mark.asyncio
async def test_run_once_logs_cycle_complete_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon, logger = _stub_daemon_with_session_factory()

    async def fake_citizens(_session: Session) -> Counter[str]:
        return Counter({"created": 2})

    async def fake_sessions(_session: Session) -> Counter[str]:
        return Counter()

    async def fake_measurements(_session: Session) -> Counter[str]:
        return Counter({"created": 5})

    daemon._sync_citizens = fake_citizens  # type: ignore[method-assign]
    daemon._sync_sessions = fake_sessions  # type: ignore[method-assign]
    daemon._sync_measurements = fake_measurements  # type: ignore[method-assign]
    # The heartbeat lives in run_once but the audit-row write
    # touches DB plumbing we don't want to model here. Patching
    # record_audit at its import site in the daemon module makes
    # the call a no-op.
    monkeypatch.setattr(
        "ginhawa_kiosk.sync.daemon.record_audit",
        lambda *args, **kwargs: None,
    )

    await daemon.run_once()

    heartbeats = [e for e in logger.events if e[1] == "sync.cycle_complete"]
    assert len(heartbeats) == 1
    level, _event, kwargs = heartbeats[0]
    assert level == "info"
    assert kwargs["citizens"] == {"created": 2}
    assert kwargs["sessions"] == {}
    assert kwargs["measurements"] == {"created": 5}


# Verifies that when one of the per-type helpers raises
# CloudUnavailable mid-cycle, the heartbeat does NOT fire — the
# warning emitted inside the helper is the trace for that case.
# A heartbeat saying "all good" after a partially-failed cycle
# would mislead the operator.
# Mortality: would fail if a future refactor moved the heartbeat
# to a finally-block or before the per-type calls.
@pytest.mark.asyncio
async def test_run_once_does_not_log_cycle_complete_on_failure() -> None:
    from ginhawa_kiosk.sync.client import CloudUnavailable

    daemon, logger = _stub_daemon_with_session_factory()

    async def fake_citizens(_session: Session) -> Counter[str]:
        raise CloudUnavailable("network down")

    daemon._sync_citizens = fake_citizens  # type: ignore[method-assign]

    with pytest.raises(CloudUnavailable):
        await daemon.run_once()

    heartbeats = [e for e in logger.events if e[1] == "sync.cycle_complete"]
    assert heartbeats == []
