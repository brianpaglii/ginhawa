"""Alembic environment for the kiosk.

Differs from the cloud's env.py in one important way: the engine is
built via :func:`ginhawa_kiosk.db.session.create_engine_for_kiosk`
rather than ``engine_from_config``, because Alembic's default factory
does not apply the SQLCipher PRAGMA key. Without that PRAGMA the
migration would either run against a plaintext DB (silently dropping
encryption) or fail with "file is not a database" on the first SQL.

Configuration sources, in priority order:
1. ``KIOSK_DB_PATH`` env var → the database file path
2. ``KIOSK_DB_KEY`` env var → the SQLCipher passphrase
3. ``alembic.ini`` → fallback for development only

The DB key is required. If it is missing we fail loud rather than
silently fall back to an unencrypted store.
"""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context

from ginhawa_kiosk.db import models  # noqa: F401  ensures tables are registered
from ginhawa_kiosk.db.base import Base
from ginhawa_kiosk.db.session import create_engine_for_kiosk

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_db_path() -> Path:
    raw = os.environ.get("KIOSK_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".ginhawa" / "kiosk.db"


def _resolve_db_key() -> str:
    key = os.environ.get("KIOSK_DB_KEY")
    if not key:
        raise RuntimeError(
            "KIOSK_DB_KEY env var must be set to run kiosk migrations. "
            "There is no fallback — running migrations against an "
            "unencrypted database would defeat at-rest encryption."
        )
    return key


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    The kiosk's offline mode is mostly for generating SQL scripts in CI;
    the real Pi runs online. We still apply the same URL the engine
    would use so the generated SQL targets the correct file.
    """
    db_path = _resolve_db_path()
    context.configure(
        url=f"sqlite:///{db_path}",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the SQLCipher-encrypted DB."""
    db_path = _resolve_db_path()
    key = _resolve_db_key()
    engine = create_engine_for_kiosk(db_path, key)

    with engine.connect() as connection:
        # render_as_batch=True is necessary for SQLite: ALTER COLUMN and
        # several other operations require Alembic's batch-mode rebuild
        # because SQLite itself doesn't support them.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
