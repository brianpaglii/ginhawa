"""Shared fixtures for kiosk db tests.

Each test gets a SQLCipher-encrypted database under ``tmp_path`` with
all tables provisioned via ``init_database``. The encrypt key is the
constant ``TEST_KEY`` so tests can re-open with the same value to
verify round-tripping.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.session import (
    create_engine_for_kiosk,
    init_database,
    make_session_factory,
)


# 64-hex-char key, the same shape provision_db generates. Test-only.
TEST_KEY = "0" * 64  # pragma: allowlist secret


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "kiosk.db"


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    eng = create_engine_for_kiosk(db_path, TEST_KEY)
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
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
