"""Sync watermark resync behavior (ADR-0024).

The sync daemon's row-selection moved from ``WHERE synced=0`` (an
INSERT-once semantic that froze every cloud row at create time) to a
``last_synced_at`` watermark. These tests pin the watermark contract:

1. Never-synced rows are picked up.
2. Synced rows with no subsequent update stay invisible.
3. Synced rows mutated after sync are re-picked up.
4. The fetch captures ``updated_at`` AT FETCH TIME for race-free
   stamping.
5. ``_apply_results`` stamps with the captured value, not the row's
   current value, so concurrent FSM mutations are correctly
   reselected on the next cycle.
6-8. FSM transitions that mutate the session row each trigger
     resync (finalise, abort, error).
9. Append-only tables (citizens) don't re-fire after first sync.
10. Migration backfill: pre-existing rows with last_synced_at=NULL
    are picked up regardless of their legacy ``synced`` value.

Audit: docs/audits/2026-05-14-session-sync-create-update-gap-audit.md.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.models import Citizen, Measurement
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.fsm import SessionFSM
from ginhawa_kiosk.sync import CloudClient
from ginhawa_kiosk.sync.daemon import (
    SyncDaemon,
    _apply_results,
    _fetch_pending_citizens,
    _fetch_pending_measurements,
    _fetch_pending_sessions,
)
from ginhawa_kiosk.sync.schemas import BatchSyncRecordResult, BatchSyncResponse

from .conftest import (
    TEST_BASE_URL,
    TEST_DEVICE_ID,
    CapturingLogger,
    ok_response_body,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_citizen(
    db: Session,
    *,
    citizen_id: str | None = None,
    last_synced_at: str | None = None,
    synced: int = 0,
    updated_at: str | None = None,
) -> str:
    cid = citizen_id or str(uuid.uuid4())
    db.add(
        Citizen(
            id=cid,
            rfid_uid=f"CARD_{uuid.uuid4().hex[:8].upper()}",
            full_name="Resync Probe",
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
            updated_at=updated_at or _utc_now_iso(),
            last_synced_at=last_synced_at,
        )
    )
    db.commit()
    return cid


def _seed_session(
    db: Session,
    citizen_id: str,
    *,
    session_id: str | None = None,
    last_synced_at: str | None = None,
    synced: int = 0,
    updated_at: str | None = None,
    status: str = "in_progress",
) -> str:
    sid = session_id or str(uuid.uuid4())
    db.add(
        SessionModel(
            id=sid,
            citizen_id=citizen_id,
            device_id=TEST_DEVICE_ID,
            started_at=_utc_now_iso(),
            ended_at=None if status == "in_progress" else _utc_now_iso(),
            status=status,
            error_reason=None,
            measurement_path="vitals",
            printed_status="not_requested",
            synced=synced,
            updated_at=updated_at or _utc_now_iso(),
            last_synced_at=last_synced_at,
        )
    )
    db.commit()
    return sid


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


# ---------- direct fetch tests ----------------------------------------


# Verifies a row with last_synced_at=NULL is picked up by the
# daemon's pending-row query. This is the "never synced" case and
# the migration-backfill case (existing rows post-migration have
# last_synced_at=NULL).
# Mortality: would fail if the predicate were last_synced_at < updated_at
# alone (NULL comparisons in SQL return NULL/false, missing the row).
def test_never_synced_row_is_selected_for_sync(db_session: Session) -> None:
    citizen_id = _seed_citizen(db_session, last_synced_at=None)
    fetched = _fetch_pending_citizens(db_session, limit=100)
    ids = [row.id for row, _ts in fetched]
    assert citizen_id in ids


# Verifies a row whose last_synced_at equals its updated_at is
# invisible to the daemon. This is the post-successful-sync steady
# state — without the predicate filtering it out, the daemon would
# re-push the same row every 30 s forever.
# Mortality: would fail if the comparison used <= instead of < (the
# stamp would never satisfy the inequality and rows would re-sync
# forever).
def test_synced_row_with_no_subsequent_update_is_not_selected(
    db_session: Session,
) -> None:
    ts = _utc_now_iso()
    citizen_id = _seed_citizen(db_session, last_synced_at=ts, updated_at=ts)
    fetched = _fetch_pending_citizens(db_session, limit=100)
    ids = [row.id for row, _ts in fetched]
    assert citizen_id not in ids


# Verifies the bug-class fix: a row whose updated_at has been bumped
# after the last sync IS re-picked up by the daemon. This is the
# exact case the audit identified (kiosk-side UPDATE bumps
# updated_at without flipping any sync flag).
# Mortality: would fail if the predicate didn't compare the two
# timestamps — the audit's headline bug returns.
def test_synced_row_with_subsequent_update_is_reselected(
    db_session: Session,
) -> None:
    # Sync at t1; FSM mutation bumps updated_at to t2 > t1.
    t1 = "2026-05-14T02:43:50.652827+00:00"
    t2 = "2026-05-14T02:45:05.053774+00:00"
    citizen = Citizen(
        id=str(uuid.uuid4()),
        rfid_uid="CARD_RESYNC_BUG",
        full_name="Resync Probe",
        dob="1990-01-01",
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version="v1",
        consent_given_at=t1,
        registered_at=t1,
        registered_by=None,
        is_active=1,
        synced=1,
        updated_at=t2,
        last_synced_at=t1,
    )
    db_session.add(citizen)
    db_session.commit()

    fetched = _fetch_pending_citizens(db_session, limit=100)
    ids = [row.id for row, _ts in fetched]
    assert citizen.id in ids


# Verifies the fetch returns the row's updated_at value at the moment
# of the query — what _apply_results needs to stamp race-free. The
# tuple's second element is the snapshot.
# Mortality: would fail if the fetch returned the row alone or a
# fixed "now" timestamp instead of the row's actual updated_at.
def test_fetch_returns_updated_at_snapshot(db_session: Session) -> None:
    t = "2026-05-14T02:43:50.652827+00:00"
    cid = _seed_citizen(db_session, updated_at=t)
    fetched = _fetch_pending_citizens(db_session, limit=100)
    for row, fetch_ts in fetched:
        if row.id == cid:
            assert fetch_ts == t
            return
    pytest.fail(f"citizen {cid} not in fetched")


# Verifies the race-free stamp behavior. Sequence:
# 1. Daemon fetches row R with updated_at=T1 (captures T1).
# 2. Concurrent FSM mutates R to updated_at=T2.
# 3. Daemon's _apply_results runs with the original T1 captured.
# 4. _apply_results stamps last_synced_at=T1 (not T2).
# 5. Next cycle: predicate last_synced_at < updated_at → T1 < T2 →
#    row is reselected. Bug-class avoided.
# Mortality: would fail if _apply_results read the row's current
# updated_at instead of the captured fetch_ts — the FSM's T2 mutation
# would be silently consumed.
def test_stamp_uses_fetch_time_value_not_current_value(
    db_session: Session,
) -> None:
    t1 = "2026-05-14T02:43:50.652827+00:00"
    t2 = "2026-05-14T02:45:05.053774+00:00"
    cid = _seed_citizen(db_session, last_synced_at=None, updated_at=t1)

    # Step 1: fetch captures T1.
    fetched = _fetch_pending_citizens(db_session, limit=100)
    rows = [row for row, _ts in fetched]
    fetch_ts_by_id = {row.id: ts for row, ts in fetched}
    assert fetch_ts_by_id[cid] == t1

    # Step 2: concurrent FSM bumps the row to T2.
    citizen = db_session.get(Citizen, cid)
    assert citizen is not None
    citizen.updated_at = t2
    db_session.flush()

    # Step 3-4: _apply_results stamps with T1, not T2.
    response = BatchSyncResponse(
        results=[BatchSyncRecordResult(id=cid, status="created", error=None)]
    )
    _apply_results(
        db_session,
        {r.id: r for r in rows},
        fetch_ts_by_id,
        response,
        type_label="citizen",
        logger=CapturingLogger(),
    )
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.get(Citizen, cid)
    assert refreshed is not None
    assert refreshed.last_synced_at == t1, (
        f"stamp must capture fetch-time T1 ({t1}); got {refreshed.last_synced_at}"
    )

    # Step 5: next cycle re-selects because T1 < T2.
    fetched_next = _fetch_pending_citizens(db_session, limit=100)
    next_ids = [row.id for row, _ts in fetched_next]
    assert cid in next_ids


# ---------- end-to-end FSM-mutation tests -----------------------------


# Verifies the round-trip the audit motivated: a session synced at
# creation, finalised on REPORT, is re-selected on the next cycle.
# Drives the real FSM transitions and the real daemon (with a mocked
# cloud transport) to confirm the watermark closes the gap.
# Mortality: would fail if _finalise_session_completed didn't bump
# updated_at (it does, see session_fsm.py:834) or if the daemon's
# predicate didn't compare the two timestamps.
@pytest.mark.asyncio
async def test_finalise_session_completed_triggers_resync(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)
    session_id = _seed_session(db_session, citizen_id, status="in_progress")

    # First sync: row goes up as created, last_synced_at stamped.
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
    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    s = db_session.get(SessionModel, session_id)
    assert s is not None
    assert s.last_synced_at == s.updated_at
    first_sync_ts = s.last_synced_at

    # FSM finalises the session — bumps status, ended_at,
    # printed_status, and updated_at.
    fsm = SessionFSM(
        db_session,
        device_id=TEST_DEVICE_ID,
        current_consent_version="v1",
    )
    fsm.current_session = s
    fsm._finalise_session_completed(printed_status="printed_ok")
    db_session.commit()

    db_session.expire_all()
    s_after = db_session.get(SessionModel, session_id)
    assert s_after is not None
    assert s_after.status == "completed"
    assert s_after.updated_at > first_sync_ts
    # last_synced_at is still the first-sync stamp (the FSM mutation
    # does not — and does not need to — touch it).
    assert s_after.last_synced_at == first_sync_ts

    # The daemon's pending-row query picks the row up again.
    fetched = _fetch_pending_sessions(db_session, limit=100)
    pending_ids = [row.id for row, _ts in fetched]
    assert session_id in pending_ids


# Same as the finalise test but for the abort path. The FSM's
# _after_aborted writes status="aborted" and bumps updated_at.
# Mortality: would fail if _after_aborted's updated_at bump regressed.
@pytest.mark.asyncio
async def test_after_aborted_triggers_resync(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)
    session_id = _seed_session(db_session, citizen_id, status="in_progress")

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
    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    s = db_session.get(SessionModel, session_id)
    assert s is not None
    first_sync_ts = s.last_synced_at

    fsm = SessionFSM(
        db_session,
        device_id=TEST_DEVICE_ID,
        current_consent_version="v1",
    )
    fsm.current_session = s
    fsm._after_aborted()
    db_session.commit()

    db_session.expire_all()
    fetched = _fetch_pending_sessions(db_session, limit=100)
    pending_ids = [row.id for row, _ts in fetched]
    assert session_id in pending_ids
    # The mutation bumped updated_at past last_synced_at.
    s_after = db_session.get(SessionModel, session_id)
    assert s_after is not None
    assert s_after.updated_at > (first_sync_ts or "")


# Same as the abort test but for the error path. The FSM's
# _after_error writes status="error", error_reason, updated_at.
@pytest.mark.asyncio
async def test_after_error_triggers_resync(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)
    session_id = _seed_session(db_session, citizen_id, status="in_progress")

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
    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    s = db_session.get(SessionModel, session_id)
    assert s is not None

    fsm = SessionFSM(
        db_session,
        device_id=TEST_DEVICE_ID,
        current_consent_version="v1",
    )
    fsm.current_session = s
    fsm._after_error("kiosk_test_error")
    db_session.commit()

    db_session.expire_all()
    fetched = _fetch_pending_sessions(db_session, limit=100)
    assert session_id in [row.id for row, _ts in fetched]


# Verifies append-only tables don't re-fire after first sync. Citizens
# have no kiosk-side post-creation mutation path today, so once
# last_synced_at >= updated_at the row stays invisible until something
# bumps updated_at (which currently nothing does).
# Mortality: would fail if the predicate were inverted or if first
# sync forgot to stamp the watermark.
@pytest.mark.asyncio
async def test_citizens_append_only_synced_once(
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
    fetched_after = _fetch_pending_citizens(db_session, limit=100)
    assert all(row.id != citizen_id for row, _ts in fetched_after)


# Verifies the migration backfill: rows with synced=1 but
# last_synced_at=NULL (the post-migration state for the 58 already-
# completed sessions in the bench-observed kiosk) ARE picked up by the
# daemon. After the migration upload completes, last_synced_at is
# stamped and subsequent cycles ignore them.
# Mortality: would fail if the predicate consulted the legacy synced
# column — the row would never resync and the bug class persists.
@pytest.mark.asyncio
async def test_migration_backfill_picks_up_legacy_synced_rows(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    # Simulate the post-migration state: row was completed before the
    # migration ran, had synced=1 stamped by the legacy daemon, now has
    # last_synced_at=NULL because the migration added the column without
    # backfilling.
    citizen_id = _seed_citizen(db_session, synced=1, last_synced_at=None)

    fetched = _fetch_pending_citizens(db_session, limit=100)
    assert citizen_id in [row.id for row, _ts in fetched]

    httpx_mock.add_response(
        method="POST",
        url=f"{TEST_BASE_URL}/api/v1/sync/citizens",
        json=ok_response_body([citizen_id], status="updated"),
        status_code=200,
    )
    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    fetched_after = _fetch_pending_citizens(db_session, limit=100)
    assert all(row.id != citizen_id for row, _ts in fetched_after)
    refreshed = db_session.get(Citizen, citizen_id)
    assert refreshed is not None
    assert refreshed.last_synced_at is not None
    assert refreshed.last_synced_at == refreshed.updated_at


# Verifies measurements behave the same as citizens under the
# watermark — append-only on the kiosk, picked up once, then quiet
# until updated_at moves (which today never happens). Round-trip
# proof that the predicate works for the third synced table too.
@pytest.mark.asyncio
async def test_measurements_synced_once_then_quiet(
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    db_session: Session,
    httpx_mock: HTTPXMock,
) -> None:
    citizen_id = _seed_citizen(db_session)
    session_id = _seed_session(db_session, citizen_id)
    m = Measurement(
        id=str(uuid.uuid4()),
        session_id=session_id,
        type="systolic_bp",
        value=128.0,
        unit="mmHg",
        source_device="omron_hem7155t",
        measured_at=_utc_now_iso(),
        is_valid=1,
        validation_notes=None,
        raw_json=None,
        synced=0,
        updated_at=_utc_now_iso(),
        last_synced_at=None,
    )
    db_session.add(m)
    db_session.commit()

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
        json=ok_response_body([m.id], status="created"),
        status_code=200,
    )
    daemon, _ = _make_daemon(session_factory, cloud_client)
    await daemon.run_once()

    db_session.expire_all()
    assert _fetch_pending_measurements(db_session, limit=100) == []


# Sanity-check the daemon's full round-trip audit row still records
# the per-type counts as before — the watermark refactor must not
# change the externally-visible behaviour of sync_attempt audit logs,
# which operators rely on for liveness signals.
@pytest.mark.asyncio
async def test_sync_attempt_audit_row_still_written(
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

    from ginhawa_kiosk.db.models import AuditLog

    audit = db_session.execute(
        select(AuditLog).where(AuditLog.action == "sync_attempt")
    ).scalar_one()
    assert audit.details is not None
    details = json.loads(audit.details)
    assert details["citizens"]["created"] == 1
