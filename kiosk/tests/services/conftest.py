"""Shared fixtures for services tests."""

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


_TEST_KEY = "0" * 64  # pragma: allowlist secret


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine_for_kiosk(tmp_path / "services.db", _TEST_KEY)
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
