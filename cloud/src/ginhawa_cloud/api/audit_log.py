"""Audit log read endpoint.

Admin-restricted listing of the append-only audit_log table. The
application is the sole writer (see services.audit.record_audit); this
module exposes a read-only API surface so administrators and the Data
Protection Officer can satisfy the DPA's accountability requirement.

Reading the audit log itself emits an audit row with
``action='read_audit_log'``: the meta-audit. Without it, an admin could
inspect citizen records via the audit trail without leaving any trace
of having done so, which defeats the point of the trail.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.security import require_scope
from ..db.models import AuditLog, User
from ..db.session import get_db
from ..services.audit import record_audit
from .schemas import ActorType, AuditLogRead, Page

router = APIRouter(prefix="/api/v1/audit-log", tags=["audit_log"])


@router.get("", response_model=Page[AuditLogRead])
def list_audit_log(
    current_user: User = Depends(require_scope("audit_log:read")),
    db: Session = Depends(get_db),
    actor_type_filter: ActorType | None = Query(default=None, alias="actor_type"),
    actor_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    action_prefix: str | None = Query(default=None),
    object_type: str | None = Query(default=None),
    object_id: str | None = Query(default=None),
    timestamp_after: str | None = Query(default=None),
    timestamp_before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AuditLogRead]:
    for value, name in (
        (timestamp_after, "timestamp_after"),
        (timestamp_before, "timestamp_before"),
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

    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))

    if actor_type_filter is not None:
        stmt = stmt.where(AuditLog.actor_type == actor_type_filter)
        count_stmt = count_stmt.where(AuditLog.actor_type == actor_type_filter)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
        count_stmt = count_stmt.where(AuditLog.actor_id == actor_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)
    if action_prefix is not None:
        # Prefix-match the action string. The BHW portal's audit page
        # uses this to filter by namespace ("fsm.", "citizen.", etc.)
        # without having to enumerate every leaf action server-side.
        # SQL LIKE 'pfx%' — pfx_prefix is escaped here only minimally
        # because actions are server-issued symbols (no user-provided
        # data) and the filter is a free-text input on an admin
        # surface; the worst-case is wildcard expansion in the user's
        # own query, which is harmless.
        like_pattern = f"{action_prefix}%"
        stmt = stmt.where(AuditLog.action.like(like_pattern))
        count_stmt = count_stmt.where(AuditLog.action.like(like_pattern))
    if object_type is not None:
        stmt = stmt.where(AuditLog.object_type == object_type)
        count_stmt = count_stmt.where(AuditLog.object_type == object_type)
    if object_id is not None:
        stmt = stmt.where(AuditLog.object_id == object_id)
        count_stmt = count_stmt.where(AuditLog.object_id == object_id)
    if timestamp_after is not None:
        stmt = stmt.where(AuditLog.timestamp >= timestamp_after)
        count_stmt = count_stmt.where(AuditLog.timestamp >= timestamp_after)
    if timestamp_before is not None:
        stmt = stmt.where(AuditLog.timestamp <= timestamp_before)
        count_stmt = count_stmt.where(AuditLog.timestamp <= timestamp_before)

    total = db.execute(count_stmt).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    # Meta-audit: record that this admin queried the audit log, and
    # what they filtered by. Required for DPA accountability — every
    # access of the audit trail must itself be traceable.
    record_audit(
        db,
        action="read_audit_log",
        actor_type=current_user.role,
        actor_id=current_user.id,
        details={
            "actor_type": actor_type_filter,
            "actor_id": actor_id,
            "action": action,
            "action_prefix": action_prefix,
            "object_type": object_type,
            "object_id": object_id,
            "timestamp_after": timestamp_after,
            "timestamp_before": timestamp_before,
            "limit": limit,
            "offset": offset,
            "total": total,
        },
    )
    db.commit()

    return Page[AuditLogRead](
        items=[AuditLogRead.model_validate(r) for r in rows],
        total=total,
    )
