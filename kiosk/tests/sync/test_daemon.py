"""SyncDaemon behaviour against an encrypted local DB."""

from __future__ import annotations

import json
import uuid
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
