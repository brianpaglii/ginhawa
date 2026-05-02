"""SQLCipher-aware engine and session factory.

The kiosk's local database is SQLite encrypted by SQLCipher (AES-256).
Every new connection MUST issue ``PRAGMA key = '<key>'`` BEFORE any
other SQL — SQLCipher reports the database as encrypted and will fail
with "file is not a database" otherwise. We hook SQLAlchemy's
``connect`` event so the PRAGMA is applied automatically; downstream
code simply uses ``SessionLocal()`` and never touches the key.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import Settings, get_settings
from ..core.security import apply_sqlcipher_pragma


def _build_engine(settings: Settings) -> Engine:
    settings.KIOSK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # We use sqlcipher3 as the DB-API driver via the explicit `module`
    # kwarg; the `sqlite://` URL stays as-is so SQLAlchemy still treats
    # this as a SQLite dialect for query compilation.
    import sqlcipher3  # imported lazily so tests can stub

    engine = create_engine(
        f"sqlite:///{settings.KIOSK_DB_PATH}",
        module=sqlcipher3,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        apply_sqlcipher_pragma(dbapi_conn, settings.KIOSK_DB_KEY)
        # Foreign keys are off by default in SQLite; turn them on so
        # ON DELETE RESTRICT / CASCADE in the schema is enforced.
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()

    return engine


def make_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Build a session factory bound to a SQLCipher-aware engine.

    Tests typically inject a Settings with ``KIOSK_DB_PATH=:memory:`` —
    the connect-event handler still applies PRAGMA key but encrypts
    nothing on an in-memory DB; that's fine for unit tests.
    """
    settings = settings or get_settings()
    engine = _build_engine(settings)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


# Lazily-built singleton for production code paths. Tests should call
# ``make_session_factory(settings_under_test)`` directly.
_SESSION_FACTORY: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY  # pragma: no cover - production lazy init
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = make_session_factory()
    return _SESSION_FACTORY


def get_db() -> Iterator[Session]:  # pragma: no cover - generator wrapper
    """Compat with the cloud's get_db pattern; returns a fresh session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
