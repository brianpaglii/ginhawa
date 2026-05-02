"""Operational kiosk scripts.

* ``provision_db.py`` — first-boot script that creates the encrypted
  DB file at ``Settings.KIOSK_DB_PATH`` and applies the Alembic
  migration tree.
* ``continuous_capture.py`` — long-running diagnostic / demo CLI that
  prints every kiosk sensor event to stdout (and optionally JSONL).
  NOT part of the production runtime; for commissioning, hardware
  testing, demos, and field debug only. Read-only — never writes
  to the kiosk database.
* ``rotate_db_key.py`` — re-encrypt the kiosk DB under a new
  passphrase (``PRAGMA rekey``). Used during the kiosk
  re-commissioning flow. (Lands in a later prompt.)

Scripts here are CLI utilities, NOT importable application code; they
must remain separable from the runtime path so accidentally importing
``provision_db`` from the FSM cannot trigger destructive operations.
"""
