"""One log format for everything the process writes: the app's own loggers
(stdlib `logging`), structlog, uvicorn and arq. JSON lines by default
(LOG_FORMAT=console for reading in a terminal), each carrying the request id,
job, user and item bound in structlog's context vars."""

from __future__ import annotations

import logging
import sys

import structlog

from app.config import get_settings

_SHARED: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.log_format == "console"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*_SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn set up its loggers before importing the app; the API logs its own
    # access lines (app/observability.py), so uvicorn's are dropped.
    for name in ("uvicorn", "uvicorn.error"):
        adopt(logging.getLogger(name))
    logging.getLogger("uvicorn.access").disabled = True


def adopt(logger: logging.Logger) -> None:
    """Sends a logger that set up its own handler through ours instead."""
    logger.handlers = []
    logger.propagate = True


configure_logging()
