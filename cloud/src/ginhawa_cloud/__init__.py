from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from .api import api_router

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
app.include_router(api_router)
