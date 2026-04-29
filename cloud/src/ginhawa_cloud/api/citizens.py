"""Citizen registry CRUD endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.security import require_scope
from ..db.models import Citizen, User
from ..db.session import get_db
from ..services.audit import record_audit
from ._authz import (
    assert_barangay_write,
    assert_citizen_access,
    scope_citizens_query,
)
from .schemas import CitizenCreate, CitizenRead, CitizenUpdate, Page

router = APIRouter(prefix="/api/v1/citizens", tags=["citizens"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post(
    "",
    response_model=CitizenRead,
    status_code=status.HTTP_201_CREATED,
)
def register_citizen(
    payload: CitizenCreate,
    current_user: User = Depends(require_scope("citizens:write")),
    db: Session = Depends(get_db),
) -> Citizen:
    assert_barangay_write(payload.barangay, current_user)

    now = _utc_now_iso()
    citizen = Citizen(
        id=str(uuid.uuid4()),
        rfid_uid=payload.rfid_uid,
        full_name=payload.full_name,
        dob=payload.dob,
        sex=payload.sex,
        barangay=payload.barangay,
        phone=payload.phone,
        consent_version=payload.consent_version,
        consent_given_at=now,
        registered_at=now,
        registered_by=payload.registered_by or current_user.id,
        is_active=1,
        synced=0,
        updated_at=now,
    )
    db.add(citizen)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"rfid_uid {payload.rfid_uid!r} already exists",
        ) from None
    record_audit(
        db,
        action="create",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="citizen",
        object_id=citizen.id,
        details={"rfid_uid": citizen.rfid_uid, "barangay": citizen.barangay},
    )
    db.commit()
    db.refresh(citizen)
    return citizen


@router.get("/{citizen_id}", response_model=CitizenRead)
def get_citizen(
    citizen_id: str,
    current_user: User = Depends(require_scope("citizens:read")),
    db: Session = Depends(get_db),
) -> Citizen:
    # Mirror the list endpoint's is_active filter: a soft-deleted
    # citizen is indistinguishable from one that never existed (ADR-0008).
    citizen = db.execute(
        select(Citizen).where(
            Citizen.id == citizen_id,
            Citizen.is_active == 1,
        )
    ).scalar_one_or_none()
    if citizen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"citizen {citizen_id} not found",
        )
    assert_citizen_access(citizen, current_user)
    record_audit(
        db,
        action="read",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="citizen",
        object_id=citizen_id,
    )
    db.commit()
    return citizen


@router.get("", response_model=Page[CitizenRead])
def list_citizens(
    current_user: User = Depends(require_scope("citizens:read")),
    db: Session = Depends(get_db),
    barangay: str | None = Query(default=None),
    is_active: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CitizenRead]:
    is_active_int = 1 if is_active else 0
    stmt = select(Citizen).where(Citizen.is_active == is_active_int)
    count_stmt = select(func.count(Citizen.id)).where(
        Citizen.is_active == is_active_int
    )
    if barangay is not None:
        stmt = stmt.where(Citizen.barangay == barangay)
        count_stmt = count_stmt.where(Citizen.barangay == barangay)

    # BHWs are silently restricted to their assigned barangay regardless
    # of any client-supplied filter.
    stmt = scope_citizens_query(stmt, current_user)
    count_stmt = scope_citizens_query(count_stmt, current_user)

    total = db.execute(count_stmt).scalar_one()
    rows = (
        db.execute(stmt.order_by(Citizen.registered_at).offset(offset).limit(limit))
        .scalars()
        .all()
    )

    record_audit(
        db,
        action="list",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="citizen",
        details={
            "barangay": barangay,
            "is_active": is_active,
            "limit": limit,
            "offset": offset,
            "total": total,
        },
    )
    db.commit()

    return Page[CitizenRead](
        items=[CitizenRead.model_validate(r) for r in rows],
        total=total,
    )


_PROTECTED_FIELDS = frozenset(
    {"id", "rfid_uid", "consent_version", "consent_given_at", "registered_at"}
)


@router.patch("/{citizen_id}", response_model=CitizenRead)
def update_citizen(
    citizen_id: str,
    payload: CitizenUpdate,
    current_user: User = Depends(require_scope("citizens:write")),
    db: Session = Depends(get_db),
) -> Citizen:
    citizen = db.get(Citizen, citizen_id)
    if citizen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"citizen {citizen_id} not found",
        )
    assert_citizen_access(citizen, current_user)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return citizen

    applied: dict[str, object] = {}
    for field, value in changes.items():
        if field in _PROTECTED_FIELDS:
            continue
        setattr(citizen, field, value)
        applied[field] = value

    citizen.updated_at = _utc_now_iso()
    record_audit(
        db,
        action="update",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="citizen",
        object_id=citizen.id,
        details={"changed": list(applied)},
    )
    db.commit()
    db.refresh(citizen)
    return citizen


@router.delete("/{citizen_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete_citizen(
    citizen_id: str,
    current_user: User = Depends(require_scope("citizens:write")),
    db: Session = Depends(get_db),
) -> Response:
    citizen = db.get(Citizen, citizen_id)
    if citizen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"citizen {citizen_id} not found",
        )
    assert_citizen_access(citizen, current_user)

    citizen.is_active = 0
    citizen.updated_at = _utc_now_iso()
    record_audit(
        db,
        action="soft_delete",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="citizen",
        object_id=citizen.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
