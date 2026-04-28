"""Kiosk session lifecycle endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SAOrmSession

from ..core.security import require_scope
from ..db.models import Citizen, User
from ..db.models import Session as SessionModel
from ..db.session import get_db
from ..services.audit import record_audit
from ._authz import assert_session_access
from .schemas import (
    Page,
    SessionCreate,
    SessionRead,
    SessionStatus,
    SessionUpdate,
)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "aborted", "error"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_scoped_bhw(user: User) -> bool:
    return user.role == "bhw" and user.assigned_barangay is not None


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    current_user: User = Depends(require_scope("sessions:write")),
    db: SAOrmSession = Depends(get_db),
) -> SessionModel:
    citizen = db.get(Citizen, payload.citizen_id)
    if citizen is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"citizen {payload.citizen_id} not found",
        )
    # BHW barangay scoping: surface "citizen not found" rather than
    # leaking that a citizen exists in another barangay.
    if (
        _is_scoped_bhw(current_user)
        and citizen.barangay != current_user.assigned_barangay
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"citizen {payload.citizen_id} not found",
        )
    if citizen.is_active != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"citizen {payload.citizen_id} is inactive",
        )

    now = _utc_now_iso()
    session = SessionModel(
        id=str(uuid.uuid4()),
        citizen_id=payload.citizen_id,
        device_id=payload.device_id,
        started_at=now,
        ended_at=None,
        status="in_progress",
        error_reason=None,
        measurement_path=payload.measurement_path,
        printed_status="not_requested",
        synced=0,
    )
    db.add(session)
    db.flush()
    record_audit(
        db,
        action="create",
        actor_type="citizen",
        actor_id=citizen.id,
        object_type="session",
        object_id=session.id,
        details={
            "device_id": session.device_id,
            "measurement_path": session.measurement_path,
        },
    )
    db.commit()
    db.refresh(session)
    return session


@router.get("/{session_id}", response_model=SessionRead)
def get_session(
    session_id: str,
    current_user: User = Depends(require_scope("sessions:read")),
    db: SAOrmSession = Depends(get_db),
) -> SessionModel:
    session = db.get(SessionModel, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"session {session_id} not found",
        )
    assert_session_access(session, current_user, db)
    record_audit(
        db,
        action="read",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="session",
        object_id=session_id,
    )
    db.commit()
    return session


@router.get("", response_model=Page[SessionRead])
def list_sessions(
    current_user: User = Depends(require_scope("sessions:read")),
    db: SAOrmSession = Depends(get_db),
    citizen_id: str | None = Query(default=None),
    status_filter: SessionStatus | None = Query(default=None, alias="status"),
    started_after: str | None = Query(default=None),
    started_before: str | None = Query(default=None),
    barangay: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[SessionRead]:
    for value, name in (
        (started_after, "started_after"),
        (started_before, "started_before"),
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

    # BHW barangay override: ignore any client-supplied barangay and
    # force-filter to the BHW's assigned barangay.
    if _is_scoped_bhw(current_user):
        barangay = current_user.assigned_barangay

    stmt = select(SessionModel)
    count_stmt = select(func.count(SessionModel.id))

    if citizen_id is not None:
        stmt = stmt.where(SessionModel.citizen_id == citizen_id)
        count_stmt = count_stmt.where(SessionModel.citizen_id == citizen_id)
    if status_filter is not None:
        stmt = stmt.where(SessionModel.status == status_filter)
        count_stmt = count_stmt.where(SessionModel.status == status_filter)
    if started_after is not None:
        stmt = stmt.where(SessionModel.started_at >= started_after)
        count_stmt = count_stmt.where(SessionModel.started_at >= started_after)
    if started_before is not None:
        stmt = stmt.where(SessionModel.started_at <= started_before)
        count_stmt = count_stmt.where(SessionModel.started_at <= started_before)
    if barangay is not None:
        stmt = stmt.join(Citizen, SessionModel.citizen_id == Citizen.id).where(
            Citizen.barangay == barangay
        )
        count_stmt = count_stmt.join(
            Citizen, SessionModel.citizen_id == Citizen.id
        ).where(Citizen.barangay == barangay)

    total = db.execute(count_stmt).scalar_one()
    rows = (
        db.execute(stmt.order_by(SessionModel.started_at).offset(offset).limit(limit))
        .scalars()
        .all()
    )

    record_audit(
        db,
        action="list",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="session",
        details={
            "citizen_id": citizen_id,
            "status": status_filter,
            "started_after": started_after,
            "started_before": started_before,
            "barangay": barangay,
            "limit": limit,
            "offset": offset,
            "total": total,
        },
    )
    db.commit()

    return Page[SessionRead](
        items=[SessionRead.model_validate(r) for r in rows],
        total=total,
    )


@router.patch("/{session_id}", response_model=SessionRead)
def update_session(
    session_id: str,
    payload: SessionUpdate,
    current_user: User = Depends(require_scope("sessions:write")),
    db: SAOrmSession = Depends(get_db),
) -> SessionModel:
    session = db.get(SessionModel, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"session {session_id} not found",
        )
    assert_session_access(session, current_user, db)

    if session.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"session is in terminal status {session.status!r}; "
                "no further changes allowed"
            ),
        )

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return session

    audit_details: dict[str, object] = {"changed": list(changes.keys())}
    if "status" in changes and changes["status"] != session.status:
        audit_details["status_from"] = session.status
        audit_details["status_to"] = changes["status"]

    for field, value in changes.items():
        setattr(session, field, value)

    record_audit(
        db,
        action="update",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="session",
        object_id=session.id,
        details=audit_details,
    )
    db.commit()
    db.refresh(session)
    return session
