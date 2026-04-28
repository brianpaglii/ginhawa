"""Application-side writer for audit_log entries.

This module is the single sanctioned path for application code to record
audit events. Mutations on patient-data tables (citizens / sessions /
measurements) and reads of those records both flow through here. The
audit_log table itself is append-only at the database layer — the
``audit_log_no_update`` / ``audit_log_no_delete`` triggers reject any
attempt to modify an existing row.
"""

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import AuditLog


def record_audit(
    db: Session,
    *,
    action: str,
    actor_type: str,
    actor_id: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    ip_address: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Append one audit_log row.

    The caller controls the surrounding transaction — this function only
    flushes so the row's autoincrement id is populated for return. Caller
    must commit (or roll back) for the row to persist.
    """
    entry = AuditLog(
        timestamp=datetime.now(timezone.utc).isoformat(),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        ip_address=ip_address,
        details=json.dumps(dict(details)) if details is not None else None,
    )
    db.add(entry)
    db.flush()
    return entry
