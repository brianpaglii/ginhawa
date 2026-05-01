"""Kiosk-to-cloud sync endpoint for citizens.

Authenticated by Bearer API key (``get_current_kiosk``); there is no
JWT path. Each request carries a batch of CitizenSync records that
the kiosk has accumulated locally; the server returns a per-record
result (created / updated / conflict_stale / conflict_constraint /
rejected) so the kiosk can mark synced rows.

Idempotency is keyed on (id, updated_at):
* unknown id -> insert -> ``created``
* known id, incoming.updated_at > stored.updated_at -> update -> ``updated``
* known id, incoming.updated_at <= stored.updated_at -> skip -> ``conflict_stale``

Per-record validation failures are reported in the response with
``status='rejected'`` and do not roll back the batch. A real
database-level error aborts the whole batch with HTTP 500 — the
kiosk should retry the entire batch rather than guess which records
made it.

Self-service registration (ADR-0014 Option A) is detected by
``registered_by IS NULL`` on the incoming record. The audit row for
a self-service create is attributed to ``actor_type='kiosk'`` and
``actor_id=<device_id>``; for BHW-assisted (registered_by present)
the audit row uses ``actor_type='bhw'`` and ``actor_id=registered_by``,
with the kiosk's device_id captured in the details JSON.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.security import get_current_kiosk
from ..db.models import Citizen, DeviceCredential
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import BatchSyncRecordResult, BatchSyncResponse, CitizenSync


router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


_MAX_BATCH_SIZE = 500


def _audit_details_for_create(
    record: CitizenSync, kiosk: DeviceCredential
) -> tuple[str, str | None, dict[str, Any]]:
    """Resolve (actor_type, actor_id, details) for a create-audit row.

    Self-service registrations carry ``registered_by IS NULL`` on the
    incoming record; the audit row attributes the action to the kiosk
    itself. BHW-assisted registrations carry a non-null ``registered_by``;
    the audit row attributes the action to that BHW user, with the
    kiosk device_id captured in details for traceability.
    """
    if record.registered_by is None:
        return (
            "kiosk",
            kiosk.device_id,
            {
                "registration_type": "self_service",
                "rfid_uid": record.rfid_uid,
                "barangay": record.barangay,
            },
        )
    return (
        "bhw",
        record.registered_by,
        {
            "registration_type": "bhw_assisted",
            "kiosk_device_id": kiosk.device_id,
            "rfid_uid": record.rfid_uid,
            "barangay": record.barangay,
        },
    )


def _rfid_collides(db: Session, rfid_uid: str, exclude_id: str) -> bool:
    """True iff some citizen other than ``exclude_id`` already holds ``rfid_uid``."""
    other = db.execute(
        select(Citizen.id).where(
            Citizen.rfid_uid == rfid_uid,
            Citizen.id != exclude_id,
        )
    ).scalar_one_or_none()
    return other is not None


def _apply_create(
    record: CitizenSync, kiosk: DeviceCredential, db: Session
) -> BatchSyncRecordResult:
    citizen = Citizen(
        id=record.id,
        rfid_uid=record.rfid_uid,
        full_name=record.full_name,
        dob=record.dob,
        sex=record.sex,
        barangay=record.barangay,
        phone=record.phone,
        consent_version=record.consent_version,
        consent_given_at=record.consent_given_at,
        registered_at=record.registered_at,
        registered_by=record.registered_by,
        is_active=record.is_active,
        synced=1,
        updated_at=record.updated_at,
    )
    db.add(citizen)

    actor_type, actor_id, details = _audit_details_for_create(record, kiosk)
    record_audit(
        db,
        action="create",
        actor_type=actor_type,
        actor_id=actor_id,
        object_type="citizen",
        object_id=record.id,
        details=details,
    )
    return BatchSyncRecordResult(id=record.id, status="created")


def _apply_update(
    record: CitizenSync,
    existing: Citizen,
    kiosk: DeviceCredential,
    db: Session,
) -> BatchSyncRecordResult:
    existing.rfid_uid = record.rfid_uid
    existing.full_name = record.full_name
    existing.dob = record.dob
    existing.sex = record.sex
    existing.barangay = record.barangay
    existing.phone = record.phone
    existing.consent_version = record.consent_version
    existing.consent_given_at = record.consent_given_at
    existing.registered_at = record.registered_at
    existing.registered_by = record.registered_by
    existing.is_active = record.is_active
    existing.synced = 1
    existing.updated_at = record.updated_at

    # Update attribution: an update arriving via sync is by definition
    # something the kiosk produced; the kiosk acts on its own behalf.
    record_audit(
        db,
        action="update",
        actor_type="kiosk",
        actor_id=kiosk.device_id,
        object_type="citizen",
        object_id=record.id,
        details={
            "rfid_uid": record.rfid_uid,
            "barangay": record.barangay,
        },
    )
    return BatchSyncRecordResult(id=record.id, status="updated")


def _process_record(
    record: CitizenSync, kiosk: DeviceCredential, db: Session
) -> BatchSyncRecordResult:
    existing = db.get(Citizen, record.id)

    if existing is None:
        if _rfid_collides(db, record.rfid_uid, exclude_id=record.id):
            return BatchSyncRecordResult(
                id=record.id,
                status="conflict_constraint",
                error=(
                    f"rfid_uid {record.rfid_uid!r} already belongs to a "
                    f"different citizen"
                ),
            )
        return _apply_create(record, kiosk, db)

    # Stale-write check. ISO 8601 UTC strings sort lexicographically the
    # same as chronologically, so direct string comparison is correct.
    if record.updated_at <= existing.updated_at:
        return BatchSyncRecordResult(
            id=record.id,
            status="conflict_stale",
            error=(
                f"incoming updated_at {record.updated_at} is not newer "
                f"than stored {existing.updated_at}"
            ),
        )

    if _rfid_collides(db, record.rfid_uid, exclude_id=record.id):
        return BatchSyncRecordResult(
            id=record.id,
            status="conflict_constraint",
            error=(
                f"rfid_uid {record.rfid_uid!r} already belongs to a different citizen"
            ),
        )

    return _apply_update(record, existing, kiosk, db)


def _try_validate(raw: Any) -> tuple[CitizenSync | None, str, str | None]:
    """Return (record, id_for_response, error). On success error is None."""
    record_id = raw.get("id", "<missing>") if isinstance(raw, dict) else "<missing>"
    if not isinstance(record_id, str) or not record_id:
        record_id = "<missing>"
    try:
        return CitizenSync.model_validate(raw), record_id, None
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        msg = first.get("msg", "validation error")
        return None, record_id, f"{loc}: {msg}" if loc else msg


@router.post("/citizens", response_model=BatchSyncResponse)
async def sync_citizens(
    request: Request,
    kiosk: DeviceCredential = Depends(get_current_kiosk),
    db: Session = Depends(get_db),
) -> BatchSyncResponse:
    """Idempotent batch upload of citizen records from a kiosk.

    The body MUST be a JSON array of CitizenSync objects. We accept the
    raw list (rather than ``payload: list[CitizenSync]``) so that a
    single bad record produces a per-record ``rejected`` result instead
    of failing the whole batch with FastAPI's body-validation 422.
    """
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request body must be a JSON array of CitizenSync records",
        )

    if len(body) > _MAX_BATCH_SIZE:
        # Use the literal 413 — Starlette/FastAPI's status alias for this
        # code is currently mid-rename ("REQUEST_ENTITY_TOO_LARGE" ->
        # "CONTENT_TOO_LARGE"), and pinning to a specific alias triggers
        # a DeprecationWarning on one or the other version.
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
            # Per spec: a real DB error rolls back the whole batch and
            # the kiosk retries. Validation errors and constraint
            # conflicts are per-record results above and never reach
            # this branch.
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
