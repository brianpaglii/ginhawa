"""Kiosk-side audit_log writer."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import AuditLog
from ginhawa_kiosk.services.audit import record_audit


# Verifies the helper inserts one audit_log row populated with the
# kwargs passed in. Confirms the synced flag defaults to 0 (the row
# is local-only until / unless the cloud rebuilds it from sync
# attribution — see ADR-0016).
# Mortality: would fail if record_audit stopped flushing (the row
# would still be pending when the test queried), or if it forgot
# to set synced=0 (the sync daemon would then skip the row even
# though it never reached the cloud).
def test_record_audit_inserts_row(db_session: Session) -> None:
    entry = record_audit(
        db_session,
        actor_type="kiosk",
        actor_id="00000000-0000-0000-0000-000000000401",
        action="fsm.menu",
        object_type="session",
        object_id="00000000-0000-0000-0000-000000000099",
    )
    db_session.commit()

    row = db_session.execute(
        select(AuditLog).where(AuditLog.id == entry.id)
    ).scalar_one()
    assert row.actor_type == "kiosk"
    assert row.actor_id == "00000000-0000-0000-0000-000000000401"
    assert row.action == "fsm.menu"
    assert row.object_type == "session"
    assert row.object_id == "00000000-0000-0000-0000-000000000099"
    assert row.synced == 0
    assert row.timestamp  # non-empty ISO string
    assert row.details is None  # none was passed
    assert row.ip_address is None  # none was passed


# Verifies an explicitly-passed ip_address lands on the audit row
# verbatim. The kiosk has no FastAPI Request object to extract from
# (unlike the cloud); callers that have an IP at hand pass it
# directly via this kwarg.
# Mortality: would fail if the ip_address kwarg were dropped or
# silently overridden.
def test_record_audit_records_explicit_ip(db_session: Session) -> None:
    entry = record_audit(
        db_session,
        actor_type="kiosk",
        actor_id=None,
        action="net.audit_explicit",
        ip_address="172.16.5.5",
    )
    db_session.commit()

    row = db_session.get(AuditLog, entry.id)
    assert row is not None
    assert row.ip_address == "172.16.5.5"


# Verifies details dicts round-trip through JSON. The schema stores
# details as TEXT (a JSON-encoded string); the helper is responsible
# for the encode side.
# Mortality: would fail if json.dumps were replaced with str()
# (which would emit Python repr that json.loads can't parse), or
# if details were dropped entirely.
def test_record_audit_serializes_details_as_json(db_session: Session) -> None:
    payload: dict[str, Any] = {
        "rfid_uid": "CARD_AUDIT_TEST",
        "barangay": "Tibagan",
        "step_count": 3,
        "consent_given": True,
    }
    entry = record_audit(
        db_session,
        actor_type="citizen",
        actor_id="00000000-0000-0000-0000-000000000101",
        action="fsm.consent_given",
        details=payload,
    )
    db_session.commit()

    row = db_session.get(AuditLog, entry.id)
    assert row is not None and row.details is not None
    decoded = json.loads(row.details)
    assert decoded == payload
