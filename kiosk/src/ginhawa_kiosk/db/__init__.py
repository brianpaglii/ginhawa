"""Kiosk-side data access.

The kiosk's SQLite database mirrors the subset of ``/schema.sql`` that
exists locally: ``citizens``, ``sessions``, ``measurements``,
``audit_log``, and ``device_config``. ``users`` and
``device_credentials`` are CLOUD-ONLY — the kiosk authenticates
citizens by RFID and authenticates *itself* to the cloud via the
device API key carried in ``Settings.KIOSK_API_KEY``.

Models declared here MUST stay in sync with the canonical
``/schema.sql``. Schema changes that require a column or table edit
land matching Alembic migrations in BOTH ``kiosk/alembic/`` and
``cloud/alembic/`` — see CLAUDE.md.
"""
