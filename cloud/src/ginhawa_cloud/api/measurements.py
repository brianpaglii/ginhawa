"""Measurement capture endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SAOrmSession

from ..core.security import require_scope
from ..db.models import Citizen, Measurement, User
from ..db.models import Session as SessionModel
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import (
    MeasurementCreate,
    MeasurementInvalidate,
    MeasurementRead,
    MeasurementType,
    Page,
)

router = APIRouter(prefix="/api/v1/measurements", tags=["measurements"])


_EXPECTED_UNITS: dict[str, frozenset[str]] = {
    "systolic_bp": frozenset({"mmHg"}),
    "diastolic_bp": frozenset({"mmHg"}),
    "spo2": frozenset({"%"}),
    "heart_rate": frozenset({"bpm"}),
    "temperature": frozenset({"C", "°C"}),
    "height": frozenset({"cm"}),
    "weight": frozenset({"kg"}),
    "bmi": frozenset({"kg/m^2", "kg/m²", ""}),
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_scoped_bhw(user: User) -> bool:
    return user.role == "bhw" and user.assigned_barangay is not None


def _check_unit(measurement_type: str, unit: str) -> tuple[int, str | None]:
    expected = _EXPECTED_UNITS.get(measurement_type, frozenset())
    if unit in expected:
        return 1, None
    return (
        0,
        f"unit {unit!r} not in expected units {sorted(expected)} "
        f"for type {measurement_type!r}",
    )


def _measurement_in_scope(
    measurement: Measurement, user: User, db: SAOrmSession
) -> bool:
    if not _is_scoped_bhw(user):
        return True
    session = db.get(SessionModel, measurement.session_id)
    if session is None:  # pragma: no cover
        # Defensive: ON DELETE CASCADE on measurements.session_id makes this
        # branch unreachable via the HTTP API — deleting a session removes
        # its measurements. Kept as belt-and-suspenders against future
        # schema changes that loosen the FK.
        return False
    citizen = db.get(Citizen, session.citizen_id)
    return citizen is not None and citizen.barangay == user.assigned_barangay


@router.post("", response_model=MeasurementRead, status_code=status.HTTP_201_CREATED)
def create_measurement(
    payload: MeasurementCreate,
    current_user: User = Depends(require_scope("measurements:write")),
    db: SAOrmSession = Depends(get_db),
) -> Measurement:
    session = db.get(SessionModel, payload.session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"session {payload.session_id} not found",
        )
    # Surface "session not found" instead of revealing cross-barangay state.
    if _is_scoped_bhw(current_user):
        citizen = db.get(Citizen, session.citizen_id)
        if citizen is None or citizen.barangay != current_user.assigned_barangay:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"session {payload.session_id} not found",
            )
    if session.status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"session {payload.session_id} is {session.status!r}; "
                "measurements may only be added to in_progress sessions"
            ),
        )

    is_valid, unit_note = _check_unit(payload.type, payload.unit)
    notes_parts: list[str] = []
    if payload.validation_notes:
        notes_parts.append(payload.validation_notes)
    if unit_note:
        notes_parts.append(unit_note)
    validation_notes = "; ".join(notes_parts) if notes_parts else None

    measurement = Measurement(
        id=str(uuid.uuid4()),
        session_id=payload.session_id,
        type=payload.type,
        value=payload.value,
        unit=payload.unit,
        source_device=payload.source_device,
        measured_at=_utc_now_iso(),
        is_valid=is_valid,
        validation_notes=validation_notes,
        raw_json=payload.raw_json,
        synced=0,
    )
    db.add(measurement)
    db.flush()
    record_audit(
        db,
        action="create",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="measurement",
        object_id=measurement.id,
        details={
            "session_id": measurement.session_id,
            "type": measurement.type,
            "source_device": measurement.source_device,
            "is_valid": measurement.is_valid,
        },
    )
    db.commit()
    db.refresh(measurement)
    return measurement


@router.get("/{measurement_id}", response_model=MeasurementRead)
def get_measurement(
    measurement_id: str,
    current_user: User = Depends(require_scope("measurements:read")),
    db: SAOrmSession = Depends(get_db),
) -> Measurement:
    measurement = db.get(Measurement, measurement_id)
    if measurement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"measurement {measurement_id} not found",
        )
    if not _measurement_in_scope(measurement, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"measurement {measurement_id} not found",
        )
    record_audit(
        db,
        action="read",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="measurement",
        object_id=measurement_id,
    )
    db.commit()
    return measurement


@router.get("", response_model=Page[MeasurementRead])
def list_measurements(
    current_user: User = Depends(require_scope("measurements:read")),
    db: SAOrmSession = Depends(get_db),
    session_id: str | None = Query(default=None),
    citizen_id: str | None = Query(default=None),
    type_filter: MeasurementType | None = Query(default=None, alias="type"),
    measured_after: str | None = Query(default=None),
    measured_before: str | None = Query(default=None),
    is_valid: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[MeasurementRead]:
    for value, name in (
        (measured_after, "measured_after"),
        (measured_before, "measured_before"),
    ):
        if value is None:
            continue
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{name} must be ISO 8601: {exc}",
            ) from exc

    is_valid_int = 1 if is_valid else 0
    stmt = select(Measurement).where(Measurement.is_valid == is_valid_int)
    count_stmt = select(func.count(Measurement.id)).where(
        Measurement.is_valid == is_valid_int
    )

    if session_id is not None:
        stmt = stmt.where(Measurement.session_id == session_id)
        count_stmt = count_stmt.where(Measurement.session_id == session_id)
    if type_filter is not None:
        stmt = stmt.where(Measurement.type == type_filter)
        count_stmt = count_stmt.where(Measurement.type == type_filter)
    if measured_after is not None:
        stmt = stmt.where(Measurement.measured_at >= measured_after)
        count_stmt = count_stmt.where(Measurement.measured_at >= measured_after)
    if measured_before is not None:
        stmt = stmt.where(Measurement.measured_at <= measured_before)
        count_stmt = count_stmt.where(Measurement.measured_at <= measured_before)

    # Join through session → citizen when we need to filter by citizen
    # OR enforce BHW barangay scoping. We collapse both into one join.
    needs_citizen_join = citizen_id is not None or _is_scoped_bhw(current_user)
    if needs_citizen_join:
        stmt = stmt.join(SessionModel, Measurement.session_id == SessionModel.id).join(
            Citizen, SessionModel.citizen_id == Citizen.id
        )
        count_stmt = count_stmt.join(
            SessionModel, Measurement.session_id == SessionModel.id
        ).join(Citizen, SessionModel.citizen_id == Citizen.id)
        if citizen_id is not None:
            stmt = stmt.where(SessionModel.citizen_id == citizen_id)
            count_stmt = count_stmt.where(SessionModel.citizen_id == citizen_id)
        if _is_scoped_bhw(current_user):
            stmt = stmt.where(Citizen.barangay == current_user.assigned_barangay)
            count_stmt = count_stmt.where(
                Citizen.barangay == current_user.assigned_barangay
            )

    total = db.execute(count_stmt).scalar_one()
    rows = (
        db.execute(stmt.order_by(Measurement.measured_at).offset(offset).limit(limit))
        .scalars()
        .all()
    )

    record_audit(
        db,
        action="list",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="measurement",
        details={
            "session_id": session_id,
            "citizen_id": citizen_id,
            "type": type_filter,
            "measured_after": measured_after,
            "measured_before": measured_before,
            "is_valid": is_valid,
            "limit": limit,
            "offset": offset,
            "total": total,
        },
    )
    db.commit()

    return Page[MeasurementRead](
        items=[MeasurementRead.model_validate(r) for r in rows],
        total=total,
    )


@router.patch("/{measurement_id}/invalidate", response_model=MeasurementRead)
def invalidate_measurement(
    measurement_id: str,
    payload: MeasurementInvalidate,
    current_user: User = Depends(require_scope("measurements:write")),
    db: SAOrmSession = Depends(get_db),
) -> Measurement:
    measurement = db.get(Measurement, measurement_id)
    if measurement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"measurement {measurement_id} not found",
        )
    if not _measurement_in_scope(measurement, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"measurement {measurement_id} not found",
        )

    measurement.is_valid = 0
    suffix = f"invalidated: {payload.reason}"
    measurement.validation_notes = (
        f"{measurement.validation_notes}; {suffix}"
        if measurement.validation_notes
        else suffix
    )

    record_audit(
        db,
        action="invalidate",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="measurement",
        object_id=measurement.id,
        details={"reason": payload.reason},
    )
    db.commit()
    db.refresh(measurement)
    return measurement
