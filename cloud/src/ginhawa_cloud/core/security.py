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

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DeviceCredential, User
from ..db.session import get_db
from .config import get_settings


_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Dummy argon2id hash used to equalize timing on the unknown-username branch
# of the login flow (see api/auth.login). When the username is not found, we
# still run verify_password against this constant so the response time is
# indistinguishable from a wrong-password attempt against a real account.
#
# This is NOT a secret. It is the deterministic output of
#   argon2.PasswordHasher().hash('x')
# captured once and hardcoded so every instance of the application uses the
# same constant. Do not regenerate it: the value is irrelevant as long as it
# is a syntactically valid argon2id hash that no real password will match.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$1dtcFgk4yetcS3mKCQ0AUQ"
    "$cT3QGVxFOGwznWs5xipoClwv4GEGOGnmj1XeYD4e214"
)

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
        "audit_log:read",
        "device_credentials:admin",
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


# ---------------------------------------------------------------------------
# Kiosk authentication — separate principal type from BHW JWT auth
# ---------------------------------------------------------------------------
# Kiosks present a Bearer API key whose argon2id hash lives in
# device_credentials.api_key_hash. This is a wholly distinct auth path
# from the user-facing JWT flow (no overlap of dependencies, no shared
# scope tuples) — kiosks are not users.
#
# Authorization: a kiosk principal carries an implicit, fixed scope set
# scoped to self-service writes (citizens:write_self_service,
# sessions:write, measurements:write). Scope checking is the
# responsibility of the sync endpoint handlers; this module only
# authenticates.


def verify_kiosk_credential(api_key: str, db: Session) -> DeviceCredential | None:
    """Look up the active ``DeviceCredential`` matching ``api_key``.

    The function iterates every active credential and runs
    :func:`verify_password` against each one, even after a match is
    found. This is intentional: short-circuiting on first match would
    let an attacker enumerate the number of active credentials by
    timing 401 responses against differently-positioned guesses
    (mirrors the dummy-hash pattern used in :mod:`api.auth.login`).
    The "constant time" promise here is *constant relative to the
    population of active credentials*; the function still returns
    immediately when no active credentials exist.

    Scaling boundary: O(N) over active credentials. Comfortable for
    dozens of kiosks; for thousands a different lookup mechanism
    would be needed (e.g., a non-secret key-id prefix stored
    alongside the hash, indexed for direct lookup).

    TODO(phase4+): replace the linear scan with a key-prefix index
    once the active-credential count exceeds ~100, or sooner if
    auth latency on the sync path becomes user-visible.
    """
    active = (
        db.execute(
            select(DeviceCredential).where(DeviceCredential.revoked_at.is_(None))
        )
        .scalars()
        .all()
    )

    matched: DeviceCredential | None = None
    for credential in active:
        if verify_password(api_key, credential.api_key_hash):
            # Capture the first match but keep iterating so timing is
            # determined by the population size, not the match position.
            if matched is None:
                matched = credential
    return matched


def _kiosk_credentials_401() -> HTTPException:
    # Single generic message used for malformed header, missing header,
    # and no-such-credential. The client cannot tell which case
    # occurred, just like the BHW login flow's "incorrect credentials".
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid kiosk credential",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_kiosk(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> DeviceCredential:
    """FastAPI dependency that authenticates a kiosk via Bearer API key.

    Note on the parameter shape: the original spec called for
    ``Header(...)`` (required), but FastAPI converts a missing required
    header to HTTP 422 (validation error). The contract for this
    endpoint is to return 401 — indistinguishable from a malformed
    header — so a missing header is treated as just another bad
    request. Hence ``Header(default=None, ...)`` plus an explicit
    ``None`` check below.

    Updates ``last_seen_at`` to now on every successful authentication.
    """
    if not authorization:
        raise _kiosk_credentials_401()

    # RFC 6750 §2.1 specifies the Bearer scheme is case-insensitive.
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _kiosk_credentials_401()
    api_key = parts[1].strip()
    if not api_key:
        raise _kiosk_credentials_401()

    credential = verify_kiosk_credential(api_key, db)
    if credential is None:
        raise _kiosk_credentials_401()

    credential.last_seen_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return credential
