"""Authentication endpoints: login and logout."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import security
from ..core.config import get_settings
from ..core.security import (
    create_access_token,
    get_current_active_user,
    scopes_for_role,
)
from ..db.models import User
from ..db.session import get_db
from ..services.audit import record_audit


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: str  # ISO 8601 UTC


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _credentials_failure(
    db: Session,
    *,
    user_id: str | None,
    username: str,
    reason: str,
) -> HTTPException:
    record_audit(
        db,
        action="login_failed",
        actor_type="bhw",
        actor_id=user_id,
        details={"username": username, "reason": reason},
    )
    db.commit()
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="incorrect credentials",
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()

    if user is None:
        # Run verify_password against a constant dummy hash so the wall time
        # of an unknown-username 401 matches a wrong-password 401. Without
        # this, argon2 verification only happens on the bad_password branch
        # and an attacker can probe for valid usernames by timing responses.
        # The result is discarded; it will always be False.
        security.verify_password(payload.password, security._DUMMY_HASH)
        raise _credentials_failure(
            db, user_id=None, username=payload.username, reason="unknown_user"
        )

    if not security.verify_password(payload.password, user.password_hash):
        raise _credentials_failure(
            db,
            user_id=user.id,
            username=payload.username,
            reason="bad_password",
        )

    if user.is_active != 1:
        raise _credentials_failure(
            db,
            user_id=user.id,
            username=payload.username,
            reason="inactive_user",
        )

    settings = get_settings()
    now = _utc_now()
    expires = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    scopes = scopes_for_role(user.role)
    token = create_access_token(subject=user.id, scopes=list(scopes))
    user.last_login_at = now.isoformat()

    record_audit(
        db,
        action="login",
        actor_type="bhw",
        actor_id=user.id,
        details={"role": user.role, "scopes": list(scopes)},
    )
    db.commit()

    return LoginResponse(access_token=token, expires_at=expires.isoformat())


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Log out the current bearer.

    Note: JWTs are stateless and we do NOT maintain a server-side token
    blacklist. Calling logout writes an ``action='logout'`` audit row but
    does not actually invalidate the token — the holder can keep using it
    until ``exp``. If true revocation becomes a requirement, add a
    Redis-backed denylist or move to opaque session tokens.
    """
    record_audit(db, action="logout", actor_type="bhw", actor_id=current_user.id)
    db.commit()
    return {"status": "logged_out"}
