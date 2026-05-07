from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .core.config import get_settings

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ],
)

logger = structlog.get_logger("ginhawa_cloud")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("ginhawa_cloud.startup", version=app.version)
    yield
    logger.info("ginhawa_cloud.shutdown")


app = FastAPI(title="GINHAWA Cloud", version="0.1.0", lifespan=lifespan)

# CORS for the BHW portal. The portal authenticates via Authorization
# bearer header (no cookies), so allow_credentials stays False — that
# also lets us avoid the wildcard-incompatible flag. Allowed methods /
# headers are explicit rather than ``*`` so a misconfigured deployment
# fails loud at the preflight rather than letting an unintended verb
# through. Origin allowlist is driven by Settings.CORS_ALLOW_ORIGINS so
# production can pin the portal's deployed origin.
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

app.include_router(api_router)
