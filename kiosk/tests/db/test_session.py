"""SQLCipher engagement and engine wiring."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError

from ginhawa_kiosk.db.models import Citizen
from ginhawa_kiosk.db.session import (
    create_engine_for_kiosk,
    init_database,
    make_session_factory,
)


_GOOD_KEY = "a" * 64  # pragma: allowlist secret
_WRONG_KEY = "b" * 64  # pragma: allowlist secret


def _seed_one_citizen(engine) -> str:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine)
    citizen_id = str(uuid.uuid4())
    with factory() as session:
        session.add(
            Citizen(
                id=citizen_id,
                rfid_uid="CARD_CRYPTO_PROBE",
                full_name="Crypto Probe",
                dob="1980-01-01",
                sex="F",
                barangay="Tibagan",
                consent_version="v1",
            )
        )
        session.commit()
    return citizen_id


# Verifies SQLCipher is actually engaged. The test:
#   1. Creates an engine with _GOOD_KEY, writes a citizen row, disposes.
#   2. Re-opens the same file with _WRONG_KEY and asserts that any read
#      raises a DatabaseError ("file is not a database" — SQLCipher's
#      diagnostic for a wrong-key open).
#   3. Re-opens with _GOOD_KEY and asserts the read succeeds.
# Would fail if:
#   - the PRAGMA key statement were skipped (data would be plaintext,
#     wrong-key reads would still succeed),
#   - sqlcipher3 were swapped for stock sqlite3 (no encryption layer),
#   - or apply_sqlcipher_pragma silently no-op'd on bad input.
def test_sqlcipher_key_required(tmp_path: Path) -> None:
    db_path = tmp_path / "encrypted.db"

    # 1. Write under the correct key.
    good_engine = create_engine_for_kiosk(db_path, _GOOD_KEY)
    init_database(good_engine)
    citizen_id = _seed_one_citizen(good_engine)
    good_engine.dispose()

    # 2. Wrong-key open MUST fail. SQLCipher's typical error message
    # is "file is not a database" but the precise wording varies; we
    # assert on the SQLAlchemy error category, not the string.
    wrong_engine = create_engine_for_kiosk(db_path, _WRONG_KEY)
    with pytest.raises(DatabaseError):
        with wrong_engine.connect() as conn:
            conn.execute(select(Citizen)).first()
    wrong_engine.dispose()

    # 3. Re-opening with the right key recovers the row.
    recovered_engine = create_engine_for_kiosk(db_path, _GOOD_KEY)
    factory = make_session_factory(recovered_engine)
    with factory() as session:
        recovered = session.get(Citizen, citizen_id)
        assert recovered is not None
        assert recovered.full_name == "Crypto Probe"
    recovered_engine.dispose()


# Verifies init_database is idempotent: a second call against an
# already-populated database must NOT recreate tables (which would
# wipe data on a real SQLite store). Would fail if the inspector
# guard were removed.
def test_init_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotent.db"
    engine = create_engine_for_kiosk(db_path, _GOOD_KEY)
    init_database(engine)

    factory = make_session_factory(engine)
    with factory() as session:
        session.add(
            Citizen(
                id=str(uuid.uuid4()),
                rfid_uid="CARD_IDEM_TEST",
                full_name="Idempotency Probe",
                dob="1980-01-01",
                sex="M",
                barangay="Tibagan",
                consent_version="v1",
            )
        )
        session.commit()

    # Second call must not blow away the data.
    init_database(engine)
    with factory() as session:
        rows = session.execute(select(Citizen)).scalars().all()
        assert len(rows) == 1
    engine.dispose()


# Verifies the engine_for_kiosk wiring closes the on-connect cursor and
# applies foreign_keys = ON. SQLite ignores ON DELETE CASCADE / RESTRICT
# unless foreign_keys are explicitly enabled per connection.
# Would fail if the foreign_keys PRAGMA were dropped from on_connect.
def test_foreign_keys_pragma_is_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "fk.db"
    engine = create_engine_for_kiosk(db_path, _GOOD_KEY)
    init_database(engine)

    with engine.connect() as conn:
        result = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert result == 1
    engine.dispose()
