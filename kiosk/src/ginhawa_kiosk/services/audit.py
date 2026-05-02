"""Application-side writer for kiosk-local audit_log entries.

Mirror of ``cloud/src/ginhawa_cloud/services/audit.py``. This is the
single sanctioned path for kiosk code to record audit events. Every
mutation on patient-data tables (citizens / sessions / measurements)
and every sensitive read (RFID lookup that resolves to a citizen,
print event, sync confirmation) flows through here.

The local ``audit_log`` is append-only by convention on the kiosk —
unlike the cloud, SQLite under SQLCipher does not maintain Postgres-
style triggers / role permissions. Defence-in-depth on the kiosk is:
this is the only writer, the table has no UPDATE/DELETE handlers in
this module, and the disk file is itself encrypted (so direct
tampering requires the key).
"""

from __future__ import annotations

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

    Caller controls the surrounding transaction — this function only
    flushes so the row's autoincrement id is populated for return.
    Caller MUST commit (or roll back) for the row to persist.
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
        synced=0,
    )
    db.add(entry)
    db.flush()
    return entry
