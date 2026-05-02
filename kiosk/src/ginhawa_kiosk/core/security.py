"""SQLCipher passphrase handling.

The DB key never appears in source — it lives in ``Settings.KIOSK_DB_KEY``
loaded from the environment / `.env`, which on a deployed Pi is
populated by systemd from a root-only credentials file derived at
install time from the Pi's machine-id plus an installation-time salt.

This module exists to centralise the rules surrounding the key:

1. Every new SQLite connection opened against the kiosk DB MUST issue
   ``PRAGMA key = '<key>'`` BEFORE any other SQL. The data-access
   layer in ``db/session.py`` is the only sanctioned caller of
   :func:`apply_sqlcipher_pragma`.
2. The passphrase is single-quoted and SQL-escaped — passing user
   input here is a hard error (we do, for that matter, refuse keys
   that contain a literal single quote).
3. The passphrase is never logged, never printed, never returned from
   any public API.
"""

from __future__ import annotations

from typing import Any


def apply_sqlcipher_pragma(connection: Any, key: str) -> None:
    """Issue ``PRAGMA key`` on a freshly-opened SQLCipher connection.

    Call this immediately after the connection is opened and BEFORE any
    other SQL — SQLCipher reports the database as encrypted and will
    fail with "file is not a database" on any prior statement.

    Refuses keys containing a literal single quote because the PRAGMA
    statement is not parameterised; an apostrophe inside the key would
    truncate the literal and let the rest run as SQL. Real keys are
    derived material with no apostrophes; rejecting outright is safer
    than escaping.
    """
    if "'" in key:
        raise ValueError(
            "SQLCipher key must not contain a single quote — derive a "
            "fresh key without one"
        )
    cursor = connection.cursor()
    try:
        cursor.execute(f"PRAGMA key = '{key}'")
    finally:
        cursor.close()
