"""Application-side writer for kiosk-local audit_log entries.

Mirror of ``cloud/src/ginhawa_cloud/services/audit.py``. This is the
single sanctioned path for kiosk code to record audit events. Every
mutation on patient-data tables (citizens / sessions / measurements)
and every sensitive read (RFID lookup that resolves to a citizen,
print event, sync confirmation) flows through here.

The local ``audit_log`` is append-only by convention on the kiosk —
unlike the cloud, SQLite under SQLCipher does not maintain Postgres-
style triggers / role permissions (ADR-0011). Defence-in-depth on
the kiosk is: this is the only writer, the table has no UPDATE/DELETE
handlers in this module, and the disk file is itself encrypted (so
direct tampering requires the key).

CLOUD vs KIOSK SIGNATURE ASYMMETRY
----------------------------------
The cloud's ``record_audit`` accepts a FastAPI ``Request`` and
extracts ``client.host`` for ``ip_address``. The kiosk's does NOT —
the kiosk runs no HTTP server, so there is no Request object to
extract from. Callers that have an IP at hand pass it via
``ip_address``; callers that don't, leave it unset. We deliberately
do not duck-type a request-shaped parameter on the kiosk side
because every kiosk-internal call would pass ``None`` and the
parameter would be dead weight.

KIOSK AUDIT vs CLOUD AUDIT (see ADR-0016)
-----------------------------------------
The kiosk's local ``audit_log`` is forensic-only. Rows here are NOT
uploaded to the cloud as a separate stream. The cloud's canonical
audit trail is rebuilt from the sync endpoints' attribution: each
``POST /api/v1/sync/{citizens,sessions,measurements}`` writes one
``audit_log`` row on the cloud side with ``actor_type='kiosk'`` and
``actor_id=<device_credentials.device_id>``.

Consequence: if a compromised kiosk tampers with its local
``audit_log`` between writing and syncing the underlying data, the
local forensic record can disappear, but the cloud's audit row
(written by the cloud at sync time) survives. A divergence between
local kiosk audit count and cloud kiosk audit count is itself a
useful forensic signal.

This decision is revisited in Phase 3 once the threat model fully
covers offline kiosk compromise.

# Convention: each layer audits its own actions.
#
#   - FSM (fsm/session_fsm.py) audits state transitions only.
#   - Lookup services audit reads (citizen.read on RFID lookup, etc).
#   - Sensor adapters (sensors/) audit captures.
#   - Sync daemon (sync/daemon.py) audits sync attempts.
#
# No layer audits another layer's actions on its behalf. If the FSM
# caused a citizen lookup, the lookup service still emits the read
# audit — not the FSM. This keeps actor_type accurate and prevents
# double-audited actions during refactors.

"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from ..db.models import AuditLog


# Tighter than the schema CHECK constraint: the audit_log table allows
# 'admin' too, but the kiosk has no admin principal — the BHW portal is
# cloud-only. Excluding 'admin' here catches a bug at type-check time
# instead of letting an unreachable value slip through to a CHECK fail
# at runtime.
ActorType = Literal["citizen", "bhw", "system", "kiosk"]


def record_audit(
    db: Session,
    *,
    actor_type: ActorType,
    actor_id: str | None,
    action: str,
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
