"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = logging.DEBUG if settings.app_env == "development" else logging.INFO

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.app_env == "development":
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "lms") -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
