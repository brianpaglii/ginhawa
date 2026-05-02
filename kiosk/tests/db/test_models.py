"""SQLAlchemy model behaviour for the kiosk's encrypted SQLite store."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ginhawa_kiosk.db.models import (
    AuditLog,
    Citizen,
    DeviceConfig,
    Measurement,
)
from ginhawa_kiosk.db.models import Session as SessionModel


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_citizen(**overrides: object) -> Citizen:
    now = _utc_now()
    defaults: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "rfid_uid": f"CARD_{uuid.uuid4().hex[:8].upper()}",
        "full_name": "Maria Dela Cruz",
        "dob": "1980-01-01",
        "sex": "F",
        "barangay": "Tibagan",
        "phone": None,
        "consent_version": "v1",
        "consent_given_at": now,
        "registered_at": now,
        "registered_by": None,
        "is_active": 1,
        "synced": 0,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Citizen(**defaults)


def _make_session(citizen_id: str, **overrides: object) -> SessionModel:
    now = _utc_now()
    defaults: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "citizen_id": citizen_id,
        "device_id": "00000000-0000-0000-0000-000000000401",
        "started_at": now,
        "ended_at": None,
        "status": "in_progress",
        "error_reason": None,
        "measurement_path": "vitals",
        "printed_status": "not_requested",
        "synced": 0,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SessionModel(**defaults)


def _make_measurement(session_id: str, **overrides: object) -> Measurement:
    now = _utc_now()
    defaults: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "type": "systolic_bp",
        "value": 120.0,
        "unit": "mmHg",
        "source_device": "omron_hem7155t",
        "measured_at": now,
        "is_valid": 1,
        "validation_notes": None,
        "raw_json": None,
        "synced": 0,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Measurement(**defaults)


# Verifies every kiosk model can round-trip through a SQLCipher-encrypted
# session: insert, commit, fresh query, assert equality on identifying
# fields. Hits all five tables (citizens / sessions / measurements /
# audit_log / device_config) plus the citizen → sessions → measurements
# relationship chain.
# Would fail if any model dropped a column, mistyped a Mapped[],
# misspelled a __tablename__, or broke the FK chain.
def test_models_match_schema(db_session: Session) -> None:
    citizen = _make_citizen()
    db_session.add(citizen)
    db_session.flush()

    session = _make_session(citizen.id)
    db_session.add(session)
    db_session.flush()

    measurement = _make_measurement(session.id, type="diastolic_bp", value=80.0)
    db_session.add(measurement)
    db_session.flush()

    audit = AuditLog(
        timestamp=_utc_now(),
        actor_type="kiosk",
        actor_id="00000000-0000-0000-0000-000000000401",
        action="create",
        object_type="citizen",
        object_id=citizen.id,
        details=None,
        synced=0,
    )
    db_session.add(audit)

    cfg = DeviceConfig(key="kiosk_id", value="00000000-0000-0000-0000-000000000401")
    db_session.add(cfg)

    db_session.commit()
    db_session.expire_all()

    # Re-query everything from scratch via the relationships.
    fetched = db_session.execute(
        select(Citizen).where(Citizen.id == citizen.id)
    ).scalar_one()
    assert fetched.full_name == "Maria Dela Cruz"
    assert len(fetched.sessions) == 1
    assert fetched.sessions[0].id == session.id
    assert len(fetched.sessions[0].measurements) == 1
    assert fetched.sessions[0].measurements[0].type == "diastolic_bp"

    audit_row = db_session.execute(
        select(AuditLog).where(AuditLog.action == "create")
    ).scalar_one()
    assert audit_row.actor_type == "kiosk"

    cfg_row = db_session.get(DeviceConfig, "kiosk_id")
    assert cfg_row is not None
    assert cfg_row.value == "00000000-0000-0000-0000-000000000401"


# Verifies value is a Python float at the model layer, not a string.
# SQLite's type affinity is permissive — it would accept "128.5" as the
# value column without complaint — so the only thing standing between
# us and string-typed measurements is the SQLAlchemy mapping.
# Would fail if value were declared as Mapped[str] instead of
# Mapped[float].
def test_value_column_is_real(db_session: Session) -> None:
    citizen = _make_citizen()
    db_session.add(citizen)
    db_session.flush()
    session = _make_session(citizen.id)
    db_session.add(session)
    db_session.flush()

    measurement = _make_measurement(session.id, value=128.5)
    db_session.add(measurement)
    db_session.commit()
    db_session.expire_all()

    fetched = db_session.execute(
        select(Measurement).where(Measurement.id == measurement.id)
    ).scalar_one()
    assert isinstance(fetched.value, float)
    assert fetched.value == pytest.approx(128.5)


# Verifies the kiosk's "synced=0 means awaiting upload" invariant: a
# freshly-inserted citizen has synced=0 even when the caller doesn't
# pass a value. The sync daemon flips this to 1 only on confirmed
# cloud receipt, so any default of 1 would ship rows that never
# actually synced.
# Would fail if the synced default were changed to 1.
def test_synced_flag_defaults_to_zero(db_session: Session) -> None:
    citizen = Citizen(
        id=str(uuid.uuid4()),
        rfid_uid="CARD_DEFAULT_TEST",
        full_name="Default Test",
        dob="1990-01-01",
        sex="M",
        barangay="Tibagan",
        phone=None,
        consent_version="v1",
        # consent_given_at, registered_at, updated_at, is_active, synced
        # all rely on column defaults.
    )
    db_session.add(citizen)
    db_session.commit()
    db_session.expire_all()

    fetched = db_session.get(Citizen, citizen.id)
    assert fetched is not None
    assert fetched.synced == 0
    assert fetched.is_active == 1


# Documentation test (per the prompt): the kiosk does NOT use Postgres-
# style audit-log triggers (ADR-0011) — append-only on the kiosk is a
# convention enforced by the data-access layer (services.audit.record_audit
# is the only writer; nothing else issues UPDATE/DELETE on audit_log).
# This test pins the convention by sweeping kiosk source files for ORM
# or raw-SQL mutations against the AuditLog model / audit_log table.
# It excludes the trusted audit module itself (services/audit.py) and
# the test tree.
# This is a documentation-style test, not a runtime enforcement: SQLite
# under SQLCipher does not provide the Postgres trigger mechanism we use
# on the cloud. If any application module ever learns to mutate
# audit_log, this test fails and the reviewer must add the new module
# to TRUSTED_MODULES (and justify the addition in the commit message).
# Would fail if a code path added direct UPDATE/DELETE on audit_log.
def test_audit_log_append_only_at_app_layer() -> None:
    import re
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[2] / "src" / "ginhawa_kiosk"
    # The audit writer itself; its docstring references UPDATE/DELETE.
    TRUSTED_MODULES: set[Path] = {src_root / "services" / "audit.py"}

    # Look for SQLAlchemy ORM mutations against AuditLog or raw SQL that
    # mutates the audit_log table. The patterns avoid free-form English
    # like "UPDATE/DELETE handlers" in docstrings.
    orm_pattern = re.compile(
        r"(query\(AuditLog\)|update\(AuditLog|delete\(AuditLog)",
    )
    sql_pattern = re.compile(
        r"""(?ix)
        (["'])                               # opening quote
        \s*(UPDATE|DELETE\s+FROM)\s+audit_log\b  # SQL on audit_log
        """
    )

    offenders: list[str] = []
    for py in src_root.rglob("*.py"):
        if py in TRUSTED_MODULES:
            continue
        text = py.read_text()
        for match in orm_pattern.finditer(text):
            offenders.append(f"{py}: ORM mutation {match.group(0)!r}")
        for match in sql_pattern.finditer(text):
            offenders.append(f"{py}: raw SQL {match.group(0)!r}")

    assert not offenders, (
        "audit_log is append-only at the app layer (ADR-0011); the "
        "following stray UPDATE/DELETE statements break that "
        "convention:\n  " + "\n  ".join(offenders)
    )
