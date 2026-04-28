"""Authentication and authorization primitives.

Public surface:
* :func:`hash_password` / :func:`verify_password` — argon2id via passlib.
* :func:`create_access_token` / :func:`decode_token` — JWT (HS256 by
  default) signed with ``settings.JWT_SECRET``.
* :func:`scopes_for_role` — role → tuple of scope strings.
* FastAPI dependencies: :data:`oauth2_scheme`, :func:`get_current_user`,
  :func:`get_current_active_user`, and the parametrised
  :func:`require_scope`.

Token payload claims (RFC 7519):
* ``sub`` — opaque user id (UUID)
* ``scopes`` — list of scope strings granted to this token
* ``iat`` — issued-at unix timestamp
* ``exp`` — expiry unix timestamp

The audit_log table records login successes and failures from
``api/auth.py`` — this module never writes to the database.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from ..db.models import User
from ..db.session import get_db
from .config import get_settings


_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class CredentialsError(Exception):
    """Raised when an access token cannot be validated."""


@dataclass(frozen=True)
class TokenData:
    subject: str
    scopes: tuple[str, ...]


_ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "admin": (
        "citizens:read",
        "citizens:write",
        "sessions:read",
        "sessions:write",
        "measurements:read",
        "measurements:write",
        "users:admin",
    ),
    "bhw": (
        "citizens:read",
        "citizens:write",
        "sessions:read",
        "sessions:write",
        "measurements:read",
        "measurements:write",
    ),
    "data_viewer": (
        "citizens:read",
        "sessions:read",
        "measurements:read",
    ),
}


def scopes_for_role(role: str) -> tuple[str, ...]:
    """Return the scope tuple granted to ``role``; empty for unknown roles."""
    return _ROLE_SCOPES.get(role, ())


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(subject: str, scopes: list[str]) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, object] = {
        "sub": subject,
        "scopes": list(scopes),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> TokenData:
    """Decode an access token. Raises ``CredentialsError`` on any failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise CredentialsError(f"could not decode token: {exc}") from exc

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise CredentialsError("token missing subject")

    raw_scopes = payload.get("scopes", [])
    if not isinstance(raw_scopes, list):
        raise CredentialsError("token scopes must be a list")
    scopes = tuple(s for s in raw_scopes if isinstance(s, str))

    return TokenData(subject=sub, scopes=scopes)


def _credentials_401(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_token_dep(token: str = Depends(oauth2_scheme)) -> TokenData:
    try:
        return decode_token(token)
    except CredentialsError as exc:
        raise _credentials_401(str(exc)) from exc


def get_current_user(
    token_data: TokenData = Depends(_decode_token_dep),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, token_data.subject)
    if user is None:
        raise _credentials_401("user not found")
    return user


def get_current_active_user(
    user: User = Depends(get_current_user),
) -> User:
    if user.is_active != 1:
        raise _credentials_401("inactive user")
    return user


def require_scope(scope: str):
    """Return a FastAPI dependency that asserts the token grants ``scope``."""

    def dependency(
        token_data: TokenData = Depends(_decode_token_dep),
        user: User = Depends(get_current_active_user),
    ) -> User:
        if scope not in token_data.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing required scope: {scope}",
            )
        return user

    return dependency
