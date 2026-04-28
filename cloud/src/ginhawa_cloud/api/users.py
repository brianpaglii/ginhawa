"""BHW account management (admin-restricted, except ``/me``)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.security import (
    get_current_active_user,
    hash_password,
    require_scope,
)
from ..db.models import User
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import Page, UserCreate, UserRead, UserUpdate


router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("users:admin"))],
)
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        assigned_barangay=payload.assigned_barangay,
        is_active=1,
        created_at=_utc_now_iso(),
        last_login_at=None,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username {payload.username!r} already exists",
        ) from None
    record_audit(
        db,
        action="create",
        actor_type="admin",
        object_type="user",
        object_id=user.id,
        details={"username": user.username, "role": user.role},
    )
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserRead)
def read_me(
    current_user: User = Depends(get_current_active_user),
) -> User:
    return current_user


@router.get(
    "",
    response_model=Page[UserRead],
    dependencies=[Depends(require_scope("users:admin"))],
)
def list_users(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[UserRead]:
    total = db.execute(select(func.count(User.id))).scalar_one()
    rows = (
        db.execute(select(User).order_by(User.created_at).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return Page[UserRead](items=[UserRead.model_validate(r) for r in rows], total=total)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(require_scope("users:admin"))],
)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user {user_id} not found",
        )

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return user

    if "password" in changes:
        changes["password_hash"] = hash_password(changes.pop("password"))

    for field, value in changes.items():
        setattr(user, field, value)

    record_audit(
        db,
        action="update",
        actor_type="admin",
        object_type="user",
        object_id=user.id,
        details={"changed": list(changes.keys())},
    )
    db.commit()
    db.refresh(user)
    return user
