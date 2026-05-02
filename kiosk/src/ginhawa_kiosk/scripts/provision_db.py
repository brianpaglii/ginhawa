"""Provision a fresh kiosk database.

Run once at install time on a new Pi:

    KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \\
    uv run python -m ginhawa_kiosk.scripts.provision_db

Steps performed:

1. Generate a 32-byte random key, hex-encoded (64 ASCII chars).
2. Print the key ONCE to stdout under a clear "DO NOT LOSE THIS"
   warning. The script does NOT persist the key — that's deployment
   responsibility (TPM-sealed file, system keyring, or root-owned env
   file consumed by systemd).
3. Create the database file at ``--db-path`` (defaults to
   ``$KIOSK_DB_PATH`` or ``~/.ginhawa/kiosk.db``) under SQLCipher
   with the generated key.
4. Initialize the schema via ``init_database`` (idempotent — exits
   with a non-zero status if the database already has tables).
5. Optionally seed ``device_config`` rows from a JSON file passed
   via ``--config``.

The script refuses to run against a non-empty database; that is a
"the kiosk has already been provisioned" condition and re-running
would risk overwriting a key that was already deployed.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect

from ..db.models import DeviceConfig
from ..db.session import create_engine_for_kiosk, init_database, make_session_factory


_DEFAULT_DB_PATH_ENV = "KIOSK_DB_PATH"


def _generate_key() -> str:
    """32 bytes of OS randomness, hex-encoded. SQLCipher accepts this
    format directly via ``PRAGMA key`` without further derivation."""
    return secrets.token_hex(32)


def _print_key_banner(key: str) -> None:
    bar = "=" * 78
    print(bar)
    print("  GINHAWA kiosk SQLCipher key — DO NOT LOSE THIS")
    print("  ------------------------------------------------")
    print("  This key is shown ONCE. The kiosk database cannot be opened")
    print("  without it. There is no recovery path.")
    print()
    print(f"  KIOSK_DB_KEY={key}")
    print()
    print("  Capture it into:")
    print("   * a TPM-sealed file (production), OR")
    print("   * the system keyring (root-only), OR")
    print("   * a root-owned environment file read by the systemd unit")
    print("  before this session ends.")
    print(bar)


def _seed_device_config(db_path: Path, key: str, config_path: Path) -> int:
    """Optional: load JSON {key: value} mapping into device_config."""
    payload = json.loads(config_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{config_path}: must be a JSON object")

    factory = make_session_factory(create_engine_for_kiosk(db_path, key))
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()
    with factory() as session:
        for k, v in payload.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError(
                    f"{config_path}: device_config keys/values must be strings"
                )
            session.add(DeviceConfig(key=k, value=v, updated_at=now))
            inserted += 1
        session.commit()
    return inserted


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI surface
    parser = argparse.ArgumentParser(description="Provision a kiosk database.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Database file path; defaults to ${_DEFAULT_DB_PATH_ENV} or "
        f"~/.ginhawa/kiosk.db",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional JSON file with initial device_config entries.",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or _resolve_default_db_path()
    if db_path.exists() and db_path.stat().st_size > 0:
        print(
            f"refusing to provision: {db_path} exists and is non-empty",
            file=sys.stderr,
        )
        return 2

    key = _generate_key()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine_for_kiosk(db_path, key)
    inspector = inspect(engine)
    if inspector.get_table_names():
        print(
            f"refusing to provision: {db_path} already has tables",
            file=sys.stderr,
        )
        return 2
    init_database(engine)

    seeded = 0
    if args.config is not None:
        seeded = _seed_device_config(db_path, key, args.config)

    _print_key_banner(key)
    print()
    print(f"Provisioned {db_path}")
    if seeded:
        print(f"Seeded {seeded} device_config rows from {args.config}")
    return 0


def _resolve_default_db_path() -> Path:  # pragma: no cover - CLI surface
    import os

    raw = os.environ.get(_DEFAULT_DB_PATH_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".ginhawa" / "kiosk.db"


if __name__ == "__main__":  # pragma: no cover - CLI surface
    sys.exit(main())
