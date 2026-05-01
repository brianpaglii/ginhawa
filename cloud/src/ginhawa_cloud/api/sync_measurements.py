"""Kiosk-to-cloud sync endpoint for measurements.

Mirrors POST /api/v1/sync/citizens and /sync/sessions. Authenticates
via Bearer API key (``get_current_kiosk``); accepts a batch of
MeasurementSync records and returns per-record results.

Out-of-range readings are STORED, not rejected at the API layer. The
kiosk has already made the clinical judgment about what counts as a
captured-but-implausible reading; the cloud preserves both the value
and the clinical decision (is_valid=0 with validation_notes filled
in) for diagnostic auditability. Rejecting at the cloud would lose
data that may be the only record of a sensor calibration drift or
operator error worth investigating later.

Session FK is checked: a measurement whose ``session_id`` does not
resolve to an existing cloud session is reported as
``status='rejected'`` with ``error='session_not_found'``. By
transitivity, this also guarantees the measurement's session points
at a kiosk-uploaded citizen (sessions.citizen_id has its own FK to
citizens, so a session cannot exist without its citizen).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session as SAOrmSession

from ..core.security import get_current_kiosk
from ..db.models import DeviceCredential, Measurement
from ..db.models import Session as SessionModel
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import BatchSyncRecordResult, BatchSyncResponse, MeasurementSync


router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


_MAX_BATCH_SIZE = 500


# Same physiological ranges enforced by MeasurementCreate. Sourced
# from schemas._MEASUREMENT_RANGES for parity, duplicated here so this
# module's validation can be inspected without dereferencing through
# the schema module's private internals.
_RANGES: dict[str, tuple[float, float]] = {
    "systolic_bp": (70.0, 250.0),
    "diastolic_bp": (40.0, 150.0),
    "spo2": (70.0, 100.0),
    "heart_rate": (30.0, 220.0),
    "temperature": (30.0, 45.0),
    "height": (80.0, 220.0),
    "weight": (20.0, 250.0),
    "bmi": (10.0, 60.0),
}


def _resolve_validity(record: MeasurementSync) -> tuple[int, str | None]:
    """Compute (is_valid, validation_notes) for a sync record.

    The cloud does NOT reject out-of-range values; it stores them with
    is_valid=0 and an appended note describing the violation. If the
    kiosk has already marked the row as invalid (is_valid=0), the
    cloud honours that decision unconditionally.
    """
    lo, hi = _RANGES[record.type]
    in_range = lo <= record.value <= hi
    if record.is_valid == 0 or not in_range:
        notes_parts: list[str] = []
        if record.validation_notes:
            notes_parts.append(record.validation_notes)
        if not in_range:
            notes_parts.append(
                f"{record.type} value {record.value} outside physiological "
                f"range [{lo}, {hi}]"
            )
        return 0, "; ".join(notes_parts) if notes_parts else None
    return 1, record.validation_notes


def _apply_create(
    record: MeasurementSync, kiosk: DeviceCredential, db: SAOrmSession
) -> BatchSyncRecordResult:
    is_valid, notes = _resolve_validity(record)
    measurement = Measurement(
        id=record.id,
        session_id=record.session_id,
        type=record.type,
        value=record.value,
        unit=record.unit,
        source_device=record.source_device,
        measured_at=record.measured_at,
        is_valid=is_valid,
        validation_notes=notes,
        raw_json=record.raw_json,
        synced=1,
        updated_at=record.updated_at,
    )
    db.add(measurement)
    record_audit(
        db,
        action="sync_create",
        actor_type="kiosk",
        actor_id=kiosk.device_id,
        object_type="measurement",
        object_id=record.id,
        details={
            "session_id": record.session_id,
            "type": record.type,
            "is_valid": is_valid,
        },
    )
    return BatchSyncRecordResult(id=record.id, status="created")


def _apply_update(
    record: MeasurementSync,
    existing: Measurement,
    kiosk: DeviceCredential,
    db: SAOrmSession,
) -> BatchSyncRecordResult:
    is_valid, notes = _resolve_validity(record)
    existing.session_id = record.session_id
    existing.type = record.type
    existing.value = record.value
    existing.unit = record.unit
    existing.source_device = record.source_device
    existing.measured_at = record.measured_at
    existing.is_valid = is_valid
    existing.validation_notes = notes
    existing.raw_json = record.raw_json
    existing.synced = 1
    existing.updated_at = record.updated_at

    record_audit(
        db,
        action="sync_update",
        actor_type="kiosk",
        actor_id=kiosk.device_id,
        object_type="measurement",
        object_id=record.id,
        details={
            "session_id": record.session_id,
            "type": record.type,
            "is_valid": is_valid,
        },
    )
    return BatchSyncRecordResult(id=record.id, status="updated")


def _process_record(
    record: MeasurementSync, kiosk: DeviceCredential, db: SAOrmSession
) -> BatchSyncRecordResult:
    # FK guard: a measurement requires its session to already exist. By
    # the sessions FK to citizens, this also implies the measurement's
    # citizen exists in the cloud.
    if db.get(SessionModel, record.session_id) is None:
        return BatchSyncRecordResult(
            id=record.id,
            status="rejected",
            error="session_not_found",
        )

    existing = db.get(Measurement, record.id)
    if existing is None:
        return _apply_create(record, kiosk, db)

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


def _try_validate(
    raw: Any,
) -> tuple[MeasurementSync | None, str, str | None]:
    record_id = raw.get("id", "<missing>") if isinstance(raw, dict) else "<missing>"
    if not isinstance(record_id, str) or not record_id:
        record_id = "<missing>"
    try:
        return MeasurementSync.model_validate(raw), record_id, None
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        msg = first.get("msg", "validation error")
        return None, record_id, f"{loc}: {msg}" if loc else msg


@router.post("/measurements", response_model=BatchSyncResponse)
async def sync_measurements(
    request: Request,
    kiosk: DeviceCredential = Depends(get_current_kiosk),
    db: SAOrmSession = Depends(get_db),
) -> BatchSyncResponse:
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request body must be a JSON array of MeasurementSync records",
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
