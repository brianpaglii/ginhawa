"""Shared fixtures for FSM tests.

Each test gets an in-test SQLite database with all tables created
via SQLAlchemy DDL. The FSM is bound to that database with a fixed
``device_id`` and ``current_consent_version`` so test-side assertions
on audit attribution and Session row state are deterministic.

The FSM logic itself doesn't depend on SQLCipher — the SQLCipher
integration is exercised by the dedicated tests in ``tests/db/``.
Using plain SQLite here means the suite runs on a dev laptop without
the system ``libsqlcipher`` package; the assertions are about FSM
behaviour, not the encryption-at-rest contract.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.base import Base
from ginhawa_kiosk.db.models import Citizen
from ginhawa_kiosk.fsm import SessionFSM


TEST_DEVICE_ID = "00000000-0000-0000-0000-000000000401"
CURRENT_CONSENT_VERSION = "v2"
STALE_CONSENT_VERSION = "v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def fsm(db_session: Session) -> SessionFSM:
    return SessionFSM(
        db_session,
        device_id=TEST_DEVICE_ID,
        current_consent_version=CURRENT_CONSENT_VERSION,
    )


def make_citizen(
    db: Session,
    *,
    consent_version: str = CURRENT_CONSENT_VERSION,
    citizen_id: str | None = None,
) -> Citizen:
    cid = citizen_id or str(uuid.uuid4())
    citizen = Citizen(
        id=cid,
        rfid_uid=f"CARD_{uuid.uuid4().hex[:8].upper()}",
        full_name="FSM Probe",
        dob=(date.today() - timedelta(days=365 * 30)).isoformat(),
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version=consent_version,
        consent_given_at=utc_now_iso(),
        registered_at=utc_now_iso(),
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at=utc_now_iso(),
    )
    db.add(citizen)
    db.commit()
    return citizen
