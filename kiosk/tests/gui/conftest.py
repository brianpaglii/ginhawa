"""Shared fixtures for GUI tests.

The kiosk's main window pulls the SQLCipher engine + sensors at
construction time on the Pi; in unit tests we substitute a plain
in-memory SQLite engine and mock printer / sensor surfaces so the
tests run on a dev laptop without libsqlcipher.

pytest-qt's ``qtbot`` fixture (consumed in individual tests) runs
the Qt event loop under offscreen mode automatically — no display
required.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Force Qt to the offscreen platform plugin so tests run headless
# in CI / tmux. Set BEFORE PyQt6 is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ginhawa_kiosk.db.base import Base  # noqa: E402
from ginhawa_kiosk.fsm import EventBus, SessionFSM  # noqa: E402
from ginhawa_kiosk.services.printer import (  # noqa: E402
    MockPrinterService,
    PrinterService,
)


_TEST_DEVICE_ID = "00000000-0000-0000-0000-000000000gui"
_TEST_CONSENT_VERSION = "v1"


@pytest.fixture
def in_memory_engine() -> Iterator[Any]:
    """Plain SQLite (no SQLCipher) for GUI tests.

    The schema is created via ``Base.metadata.create_all`` — this is
    sufficient for tests that exercise FSM/screen logic without the
    encryption-at-rest contract.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(in_memory_engine: Any) -> Iterator[Session]:
    factory = sessionmaker(bind=in_memory_engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def fsm(db_session: Session) -> SessionFSM:
    return SessionFSM(
        db_session,
        device_id=_TEST_DEVICE_ID,
        current_consent_version=_TEST_CONSENT_VERSION,
    )


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def printer() -> PrinterService:
    return MockPrinterService()


@pytest.fixture
def settings_with_test_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIOSK_DB_KEY", "test_key_for_gui_tests" + "_" * 30)
    monkeypatch.setenv("KIOSK_API_KEY", "test_api_key_for_gui" + "_" * 30)
    monkeypatch.setenv("KIOSK_DEVICE_ID", _TEST_DEVICE_ID)
    monkeypatch.setenv("MQTT_PASSWORD", "test_mqtt_pass_for_gui" + "_" * 20)
    from ginhawa_kiosk.core.config import get_settings

    get_settings.cache_clear()
