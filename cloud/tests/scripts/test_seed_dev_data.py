from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_cloud.core.security import verify_password
from ginhawa_cloud.db.base import Base
from ginhawa_cloud.db.models import (
    AuditLog,
    Citizen,
    DeviceCredential,
    Measurement,
    User,
)
from ginhawa_cloud.db.models import Session as SessionModel
from ginhawa_cloud.scripts.seed_dev_data import seed


@pytest.fixture
def db_session(tmp_path) -> Iterator[Session]:
    """Per-test SQLite database. Tables created via Base.metadata.create_all
    so the test sees the same schema the API integration tests use."""
    engine = create_engine(
        f"sqlite:///{tmp_path}/seed.db",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _scalar_count(db: Session, model) -> int:
    return db.execute(select(func.count(model.id))).scalar_one()


# Verifies the seeder writes the expected complement of users (1 admin
# + 3 BHWs = 4), citizens (20), sessions (5), measurements (15), one
# device credential, and at least 45 audit rows (one create row per
# seeded entity, including the device credential).
# Would fail if any of the seed_*() helpers were skipped or if
# record_audit() were not called for one of the entity types.
def test_seed_creates_expected_counts(db_session: Session) -> None:
    seed(db_session)

    assert _scalar_count(db_session, User) == 4
    assert _scalar_count(db_session, Citizen) == 20
    assert _scalar_count(db_session, SessionModel) == 5
    assert _scalar_count(db_session, Measurement) == 15
    # DeviceCredential's primary key is device_id (not id), so use a
    # direct count rather than _scalar_count (which assumes .id).
    assert (
        db_session.execute(select(func.count(DeviceCredential.device_id))).scalar_one()
        == 1
    )
    # 4 users + 20 citizens + 5 sessions + 15 measurements + 1 device
    # credential = 45. Use >= to allow for additional audit rows the
    # helper may emit (none today, but keeps the test robust to future
    # record_audit behaviour changes).
    assert _scalar_count(db_session, AuditLog) >= 45


# Verifies that running the seeder a second time produces no duplicate
# rows and no extra audit rows. Each seed_*() helper checks for the
# hardcoded UUID before inserting and skips on hit.
# Would fail if the existence-check on hardcoded UUIDs were removed
# (the second run would attempt to re-insert and either duplicate or
# crash on the unique constraints).
def test_seed_is_idempotent(db_session: Session) -> None:
    seed(db_session)
    counts_first = (
        _scalar_count(db_session, User),
        _scalar_count(db_session, Citizen),
        _scalar_count(db_session, SessionModel),
        _scalar_count(db_session, Measurement),
        _scalar_count(db_session, AuditLog),
    )

    seed(db_session)
    counts_second = (
        _scalar_count(db_session, User),
        _scalar_count(db_session, Citizen),
        _scalar_count(db_session, SessionModel),
        _scalar_count(db_session, Measurement),
        _scalar_count(db_session, AuditLog),
    )

    assert counts_first == counts_second


# Verifies that the seeded admin's password verifies via the same
# argon2id helper used by login.py. Confirms hash_password was called
# rather than storing plaintext.
# Would fail if hash_password were swapped for a no-op or if the
# password were stored plaintext.
def test_admin_password_verifies(db_session: Session) -> None:
    seed(db_session)

    admin = db_session.execute(
        select(User).where(User.username == "admin")
    ).scalar_one()

    assert verify_password(
        "seed_admin_password_change_me",  # pragma: allowlist secret
        admin.password_hash,
    )
    # Belt-and-suspenders: the stored hash must NOT be the plaintext.
    plaintext = "seed_admin_password_change_me"  # pragma: allowlist secret
    assert admin.password_hash != plaintext
