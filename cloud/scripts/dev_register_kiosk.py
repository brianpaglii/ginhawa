"""Mint or re-mint a device_credentials row for a dev kiosk.

Mirrors what the admin API at ``POST /api/v1/device-credentials`` does,
except (a) it lets you specify the device_id (the API auto-generates a
UUID, which doesn't work when an existing kiosk already has its
``device_config.kiosk_id`` baked in), and (b) it transparently
overwrites a revoked or stale row for the same device_id so re-running
this on the same Pi is idempotent.

Prints the plaintext API key ONCE on stdout — copy it into the Pi's
``KIOSK_API_KEY`` env var, since the DB only stores the argon2id hash.

Usage:

    cd cloud
    uv run python scripts/dev_register_kiosk.py \\
        --device-id 00000000-0000-0000-0000-000000000401 \\
        --description "laptop-dev-kiosk"

If --device-id is omitted, the firmware default UUID
``00000000-0000-0000-0000-000000000401`` is used.

This script is dev-only — production credentials should be issued via
the admin API so the actor is captured in audit_log.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone

import psycopg

from ginhawa_cloud.core.config import get_settings
from ginhawa_cloud.core.security import hash_password


_DEFAULT_DEVICE_ID = "00000000-0000-0000-0000-000000000401"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _psycopg_url(database_url: str) -> str:
    # SQLAlchemy URL uses ``postgresql+psycopg://`` to pick the driver;
    # psycopg.connect wants the bare ``postgresql://`` form.
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def register(device_id: str, description: str) -> str:
    plaintext_key = secrets.token_urlsafe(32)
    api_key_hash = hash_password(plaintext_key)
    now = _utc_now_iso()
    url = _psycopg_url(get_settings().DATABASE_URL)

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # Drop any prior row for this device_id (handles the revoked
        # case) and any other row using the same description (the
        # ``idx_device_credentials_description`` unique index would
        # otherwise reject the insert).
        cur.execute(
            "DELETE FROM device_credentials WHERE device_id = %s OR description = %s",
            (device_id, description),
        )
        cur.execute(
            "INSERT INTO device_credentials "
            "(device_id, api_key_hash, description, created_at, "
            " created_by, revoked_at, revoked_by, last_seen_at) "
            "VALUES (%s, %s, %s, %s, %s, NULL, NULL, NULL)",
            (device_id, api_key_hash, description, now, "dev-script"),
        )
        conn.commit()

    return plaintext_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", default=_DEFAULT_DEVICE_ID)
    parser.add_argument("--description", default="laptop-dev-kiosk")
    args = parser.parse_args()

    key = register(args.device_id, args.description)
    print(f"device_id   = {args.device_id}")
    print(f"description = {args.description}")
    print(f"api_key     = {key}")
    print()
    print("Copy `api_key` into the Pi's KIOSK_API_KEY env var. The")
    print("plaintext is printed ONCE — re-running this script issues a")
    print("new key and invalidates the previous one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
