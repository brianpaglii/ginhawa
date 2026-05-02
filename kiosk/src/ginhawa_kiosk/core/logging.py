"""Structured logging configuration.

The kiosk emits JSON to stdout in production (parsed by systemd journal
on the Pi) and a human-readable console renderer during development.
Selection is driven by ``Settings.MOCK_HARDWARE``: mock = console
renderer, real hardware = JSON.

A ``bind_session_id`` helper attaches the active kiosk session UUID to
every log line emitted within its with-block, so triaging an incident
no longer requires correlating timestamps by hand.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from structlog.types import Processor

from .config import Settings


_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def configure_logging(settings: Settings) -> None:
    """Wire structlog. Idempotent — safe to call multiple times in tests."""
    level = _LEVEL_MAP.get(settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Mock mode = developer at a laptop, prefers human-readable.
    # Production = systemd journal swallows stdout, prefers JSON.
    renderer: Processor
    if settings.MOCK_HARDWARE:
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Thin wrapper so call sites import from one place."""
    return structlog.get_logger(name) if name else structlog.get_logger()


@contextmanager
def bind_session_id(session_id: str) -> Iterator[None]:
    """Tag every log line emitted within the with-block with the session id.

    Usage::

        with bind_session_id(session.id):
            logger.info("session.start", citizen_id=citizen.id)
            run_session(session)
            logger.info("session.end")

    Implemented via ``structlog.contextvars`` so it is safe under
    asyncio: bindings are per-task, not global.
    """
    structlog.contextvars.bind_contextvars(session_id=session_id)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars("session_id")
