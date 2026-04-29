"""Device-credential management — admin only.

A device credential is the API key a kiosk uses to authenticate against
the cloud sync endpoints (Phase 1.5). The plaintext key is shown to the
admin **once** at creation time and is never persisted — the database
stores only the argon2id hash, mirroring how user passwords are
handled.

Revocation is the soft-delete pathway: ``revoked_at`` and ``revoked_by``
are set on revoke and the row stays for audit. Reactivation is
intentionally not supported; new kiosks get new credentials.

There is no DELETE endpoint — credentials are append-only at the API
surface, just like the audit log.
"""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.security import hash_password, require_scope
from ..db.models import DeviceCredential, User
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import (
    DeviceCredentialCreate,
    DeviceCredentialCreateResponse,
    DeviceCredentialRead,
    DeviceCredentialUpdate,
    Page,
)


router = APIRouter(prefix="/api/v1/device-credentials", tags=["device_credentials"])


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@router.post(
    "",
    response_model=DeviceCredentialCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_device_credential(
    payload: DeviceCredentialCreate,
    current_user: User = Depends(require_scope("device_credentials:admin")),
    db: Session = Depends(get_db),
) -> DeviceCredentialCreateResponse:
    device_id = str(uuid.uuid4())
    plaintext_key = secrets.token_urlsafe(32)
    now = _utc_now_iso()

    credential = DeviceCredential(
        device_id=device_id,
        api_key_hash=hash_password(plaintext_key),
        description=payload.description,
        created_at=now,
        created_by=current_user.id,
        revoked_at=None,
        revoked_by=None,
        last_seen_at=None,
    )
    db.add(credential)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"description {payload.description!r} already exists",
        ) from None

    record_audit(
        db,
        action="create_device_credential",
        actor_type="admin",
        actor_id=current_user.id,
        object_type="device_credential",
        object_id=device_id,
        details={"description": payload.description},
    )
    db.commit()

    return DeviceCredentialCreateResponse(
        device_id=device_id,
        api_key=plaintext_key,
        description=payload.description,
        created_at=now,
    )


@router.get("", response_model=Page[DeviceCredentialRead])
def list_device_credentials(
    current_user: User = Depends(require_scope("device_credentials:admin")),
    db: Session = Depends(get_db),
    active: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[DeviceCredentialRead]:
    stmt = select(DeviceCredential)
    count_stmt = select(func.count(DeviceCredential.device_id))

    if active:
        stmt = stmt.where(DeviceCredential.revoked_at.is_(None))
        count_stmt = count_stmt.where(DeviceCredential.revoked_at.is_(None))

    total = db.execute(count_stmt).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(DeviceCredential.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    record_audit(
        db,
        action="list",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="device_credential",
        details={
            "active": active,
            "limit": limit,
            "offset": offset,
            "total": total,
        },
    )
    db.commit()

    return Page[DeviceCredentialRead](
        items=[DeviceCredentialRead.model_validate(r) for r in rows],
        total=total,
    )


@router.get("/{device_id}", response_model=DeviceCredentialRead)
def get_device_credential(
    device_id: str,
    current_user: User = Depends(require_scope("device_credentials:admin")),
    db: Session = Depends(get_db),
) -> DeviceCredential:
    credential = db.get(DeviceCredential, device_id)
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device credential {device_id} not found",
        )

    record_audit(
        db,
        action="read",
        actor_type=current_user.role,
        actor_id=current_user.id,
        object_type="device_credential",
        object_id=device_id,
    )
    db.commit()
    return credential


@router.patch("/{device_id}", response_model=DeviceCredentialRead)
def revoke_device_credential(
    device_id: str,
    payload: DeviceCredentialUpdate,
    current_user: User = Depends(require_scope("device_credentials:admin")),
    db: Session = Depends(get_db),
) -> DeviceCredential:
    credential = db.get(DeviceCredential, device_id)
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device credential {device_id} not found",
        )

    if not payload.revoke:
        # Setting revoke=False is a documented no-op (reactivation is
        # intentionally not supported; the contract surface accepts the
        # value but nothing changes).
        return credential

    if credential.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"device credential {device_id} is already revoked "
                f"(revoked_at={credential.revoked_at})"
            ),
        )

    credential.revoked_at = _utc_now_iso()
    credential.revoked_by = current_user.id

    record_audit(
        db,
        action="revoke_device_credential",
        actor_type="admin",
        actor_id=current_user.id,
        object_type="device_credential",
        object_id=device_id,
        details={"description": credential.description},
    )
    db.commit()
    db.refresh(credential)
    return credential
