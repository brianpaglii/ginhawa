from fastapi import APIRouter

from .auth import router as auth_router
from .citizens import router as citizens_router
from .health import router as health_router
from .measurements import router as measurements_router
from .sessions import router as sessions_router
from .users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(citizens_router)
api_router.include_router(sessions_router)
api_router.include_router(measurements_router)

__all__ = ["api_router"]
