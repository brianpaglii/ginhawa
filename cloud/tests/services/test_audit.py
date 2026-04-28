import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_cloud.db.base import Base
from ginhawa_cloud.db.models import AuditLog
from ginhawa_cloud.services.audit import record_audit


@pytest.fixture
def db(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/audit.db", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_record_audit_persists_minimal_fields(db: Session) -> None:
    entry = record_audit(db, action="login", actor_type="bhw", actor_id="user-123")
    db.commit()
    assert entry.id is not None
    assert entry.action == "login"
    assert entry.actor_type == "bhw"
    assert entry.actor_id == "user-123"
    assert entry.object_type is None
    assert entry.object_id is None
    assert entry.ip_address is None
    assert entry.details is None


def test_record_audit_serializes_details_to_json(db: Session) -> None:
    entry = record_audit(
        db,
        action="export",
        actor_type="admin",
        actor_id="admin-1",
        details={"format": "csv", "rows": 42, "filters": {"barangay": "Tibagan"}},
    )
    db.commit()
    assert entry.details is not None
    parsed = json.loads(entry.details)
    assert parsed == {
        "format": "csv",
        "rows": 42,
        "filters": {"barangay": "Tibagan"},
    }


def test_record_audit_writes_one_row_to_db(db: Session) -> None:
    record_audit(
        db,
        action="read",
        actor_type="system",
        object_type="citizen",
        object_id="cit-1",
        ip_address="10.0.0.5",
    )
    db.commit()

    rows = db.execute(select(AuditLog)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "read"
    assert row.actor_type == "system"
    assert row.object_type == "citizen"
    assert row.object_id == "cit-1"
    assert row.ip_address == "10.0.0.5"


def test_record_audit_assigns_iso_8601_utc_timestamp(db: Session) -> None:
    entry = record_audit(db, action="ping", actor_type="system")
    db.commit()
    # Round-trips through datetime.fromisoformat without raising and the
    # encoded offset is UTC.
    from datetime import datetime

    parsed = datetime.fromisoformat(entry.timestamp)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0
