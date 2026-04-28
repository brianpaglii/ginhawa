"""Helpers for barangay-level authorization on patient-data endpoints.

BHW users have ``role='bhw'`` and an ``assigned_barangay``. All reads and
writes against citizens/sessions/measurements are silently restricted to
that barangay; attempts to access existing records in another barangay
return 404 (intentionally indistinguishable from "does not exist") so
the BHW cannot probe for citizens outside their scope.

Admin users (``role='admin'``) are unrestricted.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from ..db.models import Citizen
from ..db.models import Session as SessionModel
from ..db.models import User


def _is_scoped_bhw(user: User) -> bool:
    return user.role == "bhw" and user.assigned_barangay is not None


def scope_citizens_query(stmt: Select, user: User) -> Select:
    """Restrict a citizens SELECT to the BHW's barangay; admins pass through."""
    if _is_scoped_bhw(user):
        stmt = stmt.where(Citizen.barangay == user.assigned_barangay)
    return stmt


def assert_citizen_access(citizen: Citizen, user: User) -> None:
    """Raise 404 if a BHW is reaching for a citizen outside their barangay."""
    if _is_scoped_bhw(user) and citizen.barangay != user.assigned_barangay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"citizen {citizen.id} not found",
        )


def assert_barangay_write(target_barangay: str, user: User) -> None:
    """Raise 403 if a BHW is writing to a different barangay (explicit POST)."""
    if _is_scoped_bhw(user) and target_barangay != user.assigned_barangay:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cannot write to a different barangay",
        )


def assert_session_access(session: SessionModel, user: User, db: Session) -> None:
    """Raise 404 if a BHW touches a session whose citizen lives elsewhere."""
    if not _is_scoped_bhw(user):
        return
    citizen = db.get(Citizen, session.citizen_id)
    if citizen is None or citizen.barangay != user.assigned_barangay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"session {session.id} not found",
        )
