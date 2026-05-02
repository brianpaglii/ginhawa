"""SQLCipher-aware engine and session factory.

The kiosk's local database is SQLite encrypted by SQLCipher (AES-256).
Every new connection MUST issue ``PRAGMA key = '<key>'`` BEFORE any
other SQL. Omitting this leaves the encryption layer inactive and
SQLCipher silently writes plaintext to disk — a catastrophic patient-
data leak that will not surface until a forensic inspection. We hook
SQLAlchemy's ``connect`` event so the PRAGMA is applied automatically
on every new connection; downstream code uses sessions normally and
never touches the key.

Public surface:
* :func:`create_engine_for_kiosk` — engine bound to the SQLCipher
  driver with the PRAGMA key wired into the connect event.
* :func:`init_database` — create tables on first boot.
* :func:`get_db` — generator dependency, mirrors the cloud's pattern.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import Settings, get_settings
from ..core.security import apply_sqlcipher_pragma
from .base import Base


def create_engine_for_kiosk(db_path: Path, key: str) -> Engine:
    """Build a SQLAlchemy Engine that opens ``db_path`` under SQLCipher.

    The PRAGMA key is applied via the ``connect`` event so every new
    connection (pool grow, reconnect after restart) gets the right key
    automatically. The cursor that runs the PRAGMA is closed before
    SQLAlchemy issues any queries against the connection — SQLCipher
    requires the key be set BEFORE any data SQL.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # We use sqlcipher3 as the DB-API driver via the explicit `module`
    # kwarg; the ``sqlite://`` URL stays as-is so SQLAlchemy still
    # treats this as a SQLite dialect for query compilation.
    import sqlcipher3  # imported lazily so tests can stub

    engine = create_engine(
        f"sqlite:///{db_path}",
        module=sqlcipher3,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        # The key MUST be applied before any other statement on this
        # connection. Failing to do so doesn't error — it silently
        # treats the file as plaintext SQLite, which is the worst
        # possible failure mode (data leak).
        apply_sqlcipher_pragma(dbapi_conn, key)
        # Foreign keys are off by default in SQLite; turn them on so
        # ON DELETE RESTRICT / CASCADE in the schema is enforced.
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()

    return engine


def make_session_factory(
    engine: Engine | None = None,
    settings: Settings | None = None,
) -> sessionmaker[Session]:
    """Build a session factory.

    Either pass an explicit engine (test path) or rely on the default
    settings-driven engine (production path).
    """
    if engine is None:
        settings = settings or get_settings()
        engine = create_engine_for_kiosk(settings.KIOSK_DB_PATH, settings.KIOSK_DB_KEY)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def init_database(engine: Engine) -> None:
    """Create tables if the database is empty.

    On a fresh install the kiosk runs once before any migrations exist;
    the tables come from ``Base.metadata.create_all`` (which is the
    SQLAlchemy reflection of ``schema.sql``). Subsequent boots skip
    this step — Alembic owns schema evolution from that point on.

    The provision flow on a fresh Pi is:
    1. ``provision_db`` generates a key and creates the file.
    2. ``init_database`` creates tables from the model metadata.
    3. ``alembic stamp head`` records that the database is at the
       latest migration. Subsequent code runs ``alembic upgrade head``
       which is then a no-op until a real migration arrives.

    We deliberately do NOT call ``alembic upgrade`` from inside this
    function — chaining migrations into a runtime path makes startup
    failures harder to diagnose and couples the app to the migration
    tree's import-time side effects.
    """
    # Touch every model so the metadata table list is populated even if
    # the caller imported only ``init_database`` and not the models.
    from . import models  # noqa: F401  ensures tables are registered

    inspector = inspect(engine)
    if inspector.get_table_names():
        return  # already provisioned; nothing to do
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Production singleton + dependency-style accessor
# ---------------------------------------------------------------------------
_SESSION_FACTORY: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY  # pragma: no cover - production lazy init
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = make_session_factory()
    return _SESSION_FACTORY


def get_db() -> Iterator[Session]:  # pragma: no cover - generator wrapper
    """Yield a session bound to the lazily-built singleton factory.

    Mirrors the cloud's FastAPI ``Depends(get_db)`` pattern even though
    the kiosk has no web framework — adopting the same shape keeps the
    two data-access layers superficially similar so a developer reading
    one can navigate the other without re-orienting.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
