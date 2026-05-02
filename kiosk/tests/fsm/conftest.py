"""Shared fixtures for FSM tests.

Each test gets a SQLCipher-encrypted in-test database with all tables
created via ``init_database``. The FSM is bound to that database with
a fixed ``device_id`` and ``current_consent_version`` so test-side
assertions on audit attribution and Session row state are
deterministic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.models import Citizen
from ginhawa_kiosk.db.session import (
    create_engine_for_kiosk,
    init_database,
    make_session_factory,
)
from ginhawa_kiosk.fsm import SessionFSM


_TEST_KEY = "0" * 64  # pragma: allowlist secret
TEST_DEVICE_ID = "00000000-0000-0000-0000-000000000401"
CURRENT_CONSENT_VERSION = "v2"
STALE_CONSENT_VERSION = "v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine_for_kiosk(tmp_path / "fsm.db", _TEST_KEY)
    init_database(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


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
