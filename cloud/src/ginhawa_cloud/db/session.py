from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings

_settings = get_settings()

engine = create_engine(_settings.DATABASE_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:  # pragma: no cover
    """Yield a request-scoped DB session bound to the production engine.

    This is the FastAPI dependency injected into route handlers via
    ``Depends(get_db)``. The test suite substitutes a SQLite-backed session
    with ``app.dependency_overrides[get_db] = ...`` (see tests/api/conftest.py),
    so this body is intentionally unreachable in tests — that is the
    correct dependency-injection pattern, not a coverage gap.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
