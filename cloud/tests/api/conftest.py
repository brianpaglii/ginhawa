"""Shared API-test fixtures.

Each test gets a fresh on-disk SQLite database (under ``tmp_path``), with
``Base.metadata.create_all`` to provision the schema. The FastAPI app's
``get_db`` dependency is overridden to return sessions bound to that
engine. The default ``client`` fixture is pre-authenticated as a freshly
created admin user; tests that need a different role use
``client_unauthed`` plus ``make_user`` and ``login``.
"""

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_cloud import app
from ginhawa_cloud.core.security import hash_password
from ginhawa_cloud.db.base import Base
from ginhawa_cloud.db.models import User
from ginhawa_cloud.db.session import get_db


@pytest.fixture
def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@pytest.fixture
def db_session(session_factory) -> Iterator[Session]:
    """A bare DB session for fixtures that seed data directly."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app_with_db(session_factory) -> Iterator[FastAPI]:
    """Override ``get_db`` to return sessions from the per-test engine."""

    def _override() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client_unauthed(app_with_db) -> Iterator[TestClient]:
    """A TestClient with no Authorization header set."""
    with TestClient(app_with_db) as c:
        yield c


@pytest.fixture
def make_user(
    db_session: Session,
) -> Callable[..., User]:
    """Factory that inserts a User row directly into the test DB."""

    def _make(
        *,
        username: str,
        password: str,
        role: str,
        assigned_barangay: str | None = None,
        full_name: str = "Test User",
        is_active: int = 1,
    ) -> User:
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            password_hash=hash_password(password),
            full_name=full_name,
            role=role,
            assigned_barangay=assigned_barangay,
            is_active=is_active,
            created_at=datetime.now(timezone.utc).isoformat(),
            last_login_at=None,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return _make


@pytest.fixture
def login(client_unauthed: TestClient) -> Callable[[str, str], str]:
    """Helper that hits ``/api/v1/auth/login`` and returns the access token."""

    def _login(username: str, password: str) -> str:
        response = client_unauthed.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    return _login


@pytest.fixture
def client(
    client_unauthed: TestClient,
    make_user: Callable[..., User],
    login: Callable[[str, str], str],
) -> TestClient:
    """A TestClient pre-authenticated as a freshly created admin user."""
    make_user(username="testadmin", password="admin-pw", role="admin")
    token = login("testadmin", "admin-pw")
    client_unauthed.headers["Authorization"] = f"Bearer {token}"
    return client_unauthed
