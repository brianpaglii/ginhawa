"""SQLCipher connection-time PRAGMAs.

Pins the three pragmas the kiosk applies on every new connection:
encryption-mode flag (``foreign_keys``), reader-writer concurrency
(``journal_mode = WAL``), and the routine-contention absorber
(``busy_timeout = 5000``). The encryption key itself is exercised
in ``test_session.py`` — covering both here would duplicate the
cryptography assertion.

ADR-0021 / docs/audits/2026-05-14-db-lock-contention-audit.md.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine


# Verifies foreign_keys is enabled on every fresh connection. The
# kiosk schema relies on ON DELETE RESTRICT / CASCADE on sessions
# and measurements, which only fire when FKs are on (SQLite defaults
# to off). Pinned so a future refactor of _on_connect doesn't
# silently drop the pragma and let cross-table referential
# integrity rot.
def test_foreign_keys_enabled(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.exec_driver_sql("PRAGMA foreign_keys").fetchone()
    assert row is not None
    assert row[0] == 1


# Verifies WAL journal mode is in effect. WAL is the standard
# remediation for the multi-connection SQLITE_BUSY pattern documented
# in the 2026-05-14 audit; without it the sync daemon and main app
# serialise their writes through an EXCLUSIVE lock and contend on
# the REPORT-screen window.
# Mortality: would fail if a future refactor dropped the pragma or
# downgraded to DELETE / TRUNCATE / MEMORY.
def test_journal_mode_is_wal(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()
    assert row is not None
    assert str(row[0]).lower() == "wal"


# Verifies busy_timeout is 5000 ms. Combined with WAL, this absorbs
# routine sub-second contention inside SQLite rather than surfacing
# it as a Python OperationalError. The sync daemon's exception
# handler stays as defence-in-depth for >5 s deadlocks; this test
# pins the routine-contention budget.
# Mortality: would fail if the pragma were dropped (defaults to 0)
# or if the value drifted out of the audit's "≥ REPORT-window"
# regime.
def test_busy_timeout_set_to_5000(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.exec_driver_sql("PRAGMA busy_timeout").fetchone()
    assert row is not None
    assert row[0] == 5000
