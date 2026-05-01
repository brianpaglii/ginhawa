from fastapi import APIRouter

from .audit_log import router as audit_log_router
from .auth import router as auth_router
from .citizens import router as citizens_router
from .device_credentials import router as device_credentials_router
from .health import router as health_router
from .measurements import router as measurements_router
from .sessions import router as sessions_router
from .sync_citizens import router as sync_citizens_router
from .sync_measurements import router as sync_measurements_router
from .sync_sessions import router as sync_sessions_router
from .users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(citizens_router)
api_router.include_router(sessions_router)
api_router.include_router(measurements_router)
api_router.include_router(audit_log_router)
api_router.include_router(device_credentials_router)
api_router.include_router(sync_citizens_router)
api_router.include_router(sync_sessions_router)
api_router.include_router(sync_measurements_router)

__all__ = ["api_router"]
