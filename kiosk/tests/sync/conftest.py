"""Shared fixtures for sync tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.session import (
    create_engine_for_kiosk,
    init_database,
    make_session_factory,
)
from ginhawa_kiosk.sync import CloudClient


_TEST_KEY = "0" * 64  # pragma: allowlist secret
_TEST_DEVICE_ID = "00000000-0000-0000-0000-000000000401"
_TEST_API_KEY = "test-bearer-key"  # pragma: allowlist secret
_TEST_BASE_URL = "https://cloud.test.local"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine_for_kiosk(tmp_path / "kiosk.db", _TEST_KEY)
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


@pytest_asyncio.fixture
async def cloud_client() -> AsyncIterator[CloudClient]:
    """A CloudClient pointed at the pytest-httpx mock base URL.

    pytest-httpx's ``httpx_mock`` fixture intercepts all real network
    calls — we don't need a real server. The client is constructed
    with the production code path (no httpx.AsyncClient injection)
    so the test exercises real header / timeout configuration.
    """
    client = CloudClient(
        base_url=_TEST_BASE_URL,
        api_key=_TEST_API_KEY,
        device_id=_TEST_DEVICE_ID,
    )
    try:
        yield client
    finally:
        await client.aclose()


# Constants exported for test bodies that need to assert on URL prefix
# or on the device_id the daemon should attach to outgoing sessions.
TEST_BASE_URL = _TEST_BASE_URL
TEST_DEVICE_ID = _TEST_DEVICE_ID
TEST_API_KEY = _TEST_API_KEY


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ok_response_body(record_ids: list[str], status: str = "created") -> dict[str, Any]:
    return {
        "results": [{"id": rid, "status": status, "error": None} for rid in record_ids]
    }


class CapturingLogger:
    """Records structlog-style log calls into a list for assertion.

    Only the names/keywords are captured — not the formatted output —
    because structlog's renderer is configurable and we don't want
    the tests coupled to its current shape.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kwargs: Any) -> None:
        self.events.append((level, event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record("error", event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._record("debug", event, **kwargs)


# Make pytest-asyncio happy with module-scoped tests.
@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
