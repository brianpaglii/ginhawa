"""Kiosk-to-cloud sync endpoint for sessions.

Mirrors POST /api/v1/sync/citizens. Authenticates via Bearer API key
(``get_current_kiosk``); accepts a batch of SessionSync records and
returns per-record results.

Rejection semantics (status='rejected') are richer here than for
citizens: in addition to validation failures, a session is rejected
if its ``citizen_id`` does not resolve to an existing cloud citizen,
or if its ``device_id`` does not match the authenticated kiosk's
``device_id``. These are reported per-record so a single orphaned
session does not poison the rest of the batch.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session as SAOrmSession

from ..core.security import get_current_kiosk
from ..db.models import Citizen, DeviceCredential
from ..db.models import Session as SessionModel
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import BatchSyncRecordResult, BatchSyncResponse, SessionSync


router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


_MAX_BATCH_SIZE = 500


def _apply_create(
    record: SessionSync, kiosk: DeviceCredential, db: SAOrmSession
) -> BatchSyncRecordResult:
    session = SessionModel(
        id=record.id,
        citizen_id=record.citizen_id,
        device_id=record.device_id,
        started_at=record.started_at,
        ended_at=record.ended_at,
        status=record.status,
        error_reason=record.error_reason,
        measurement_path=record.measurement_path,
        printed_status=record.printed_status,
        synced=1,
        updated_at=record.updated_at,
    )
    db.add(session)
    record_audit(
        db,
        action="sync_create",
        actor_type="kiosk",
        actor_id=kiosk.device_id,
        object_type="session",
        object_id=record.id,
        details={
            "citizen_id": record.citizen_id,
            "status": record.status,
        },
    )
    return BatchSyncRecordResult(id=record.id, status="created")


def _apply_update(
    record: SessionSync,
    existing: SessionModel,
    kiosk: DeviceCredential,
    db: SAOrmSession,
) -> BatchSyncRecordResult:
    existing.citizen_id = record.citizen_id
    existing.device_id = record.device_id
    existing.started_at = record.started_at
    existing.ended_at = record.ended_at
    existing.status = record.status
    existing.error_reason = record.error_reason
    existing.measurement_path = record.measurement_path
    existing.printed_status = record.printed_status
    existing.synced = 1
    existing.updated_at = record.updated_at

    record_audit(
        db,
        action="sync_update",
        actor_type="kiosk",
        actor_id=kiosk.device_id,
        object_type="session",
        object_id=record.id,
        details={
            "citizen_id": record.citizen_id,
            "status": record.status,
        },
    )
    return BatchSyncRecordResult(id=record.id, status="updated")


def _process_record(
    record: SessionSync, kiosk: DeviceCredential, db: SAOrmSession
) -> BatchSyncRecordResult:
    # Spoof guard: a kiosk cannot upload sessions claiming a different
    # device_id. Without this, a compromised kiosk could attribute
    # sessions to other kiosks and confuse audit attribution.
    if record.device_id != kiosk.device_id:
        return BatchSyncRecordResult(
            id=record.id,
            status="rejected",
            error="device_id_mismatch",
        )

    # FK guard: report missing citizen as a per-record reject so a single
    # orphan does not break the rest of the batch. The kiosk should
    # always upload citizens before sessions, but if ordering slips
    # (e.g., a partial earlier batch) we surface it cleanly.
    if db.get(Citizen, record.citizen_id) is None:
        return BatchSyncRecordResult(
            id=record.id,
            status="rejected",
            error="citizen_not_found",
        )

    existing = db.get(SessionModel, record.id)
    if existing is None:
        return _apply_create(record, kiosk, db)

    # ISO 8601 UTC strings sort lexicographically the same as
    # chronologically, so direct string comparison is correct.
    if record.updated_at <= existing.updated_at:
        return BatchSyncRecordResult(
            id=record.id,
            status="conflict_stale",
            error=(
                f"incoming updated_at {record.updated_at} is not newer "
                f"than stored {existing.updated_at}"
            ),
        )

    return _apply_update(record, existing, kiosk, db)


def _try_validate(raw: Any) -> tuple[SessionSync | None, str, str | None]:
    record_id = raw.get("id", "<missing>") if isinstance(raw, dict) else "<missing>"
    if not isinstance(record_id, str) or not record_id:
        record_id = "<missing>"
    try:
        return SessionSync.model_validate(raw), record_id, None
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        msg = first.get("msg", "validation error")
        return None, record_id, f"{loc}: {msg}" if loc else msg


@router.post("/sessions", response_model=BatchSyncResponse)
async def sync_sessions(
    request: Request,
    kiosk: DeviceCredential = Depends(get_current_kiosk),
    db: SAOrmSession = Depends(get_db),
) -> BatchSyncResponse:
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request body must be a JSON array of SessionSync records",
        )
    if len(body) > _MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"batch size {len(body)} exceeds maximum of "
                f"{_MAX_BATCH_SIZE} records per request"
            ),
        )

    results: list[BatchSyncRecordResult] = []
    for index, raw in enumerate(body):
        try:
            record, record_id, validation_err = _try_validate(raw)
            if record is None:
                results.append(
                    BatchSyncRecordResult(
                        id=record_id,
                        status="rejected",
                        error=validation_err,
                    )
                )
                continue
            results.append(_process_record(record, kiosk, db))
        except Exception as exc:  # pragma: no cover - real DB error path
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"database error processing record at index {index}: "
                    f"{type(exc).__name__}"
                ),
            ) from exc

    db.commit()
    return BatchSyncResponse(results=results)
