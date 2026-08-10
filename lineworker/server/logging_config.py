"""Logging: one JSON pipeline for structlog AND stdlib loggers.

AD-11 PHI ban applies from the first line: log entries carry identifiers
and event names — never claim field values, prompt bodies, or model output.

Everything that logs — structlog calls, uvicorn access/error, alembic,
sqlalchemy — renders through the same ProcessorFormatter on the root
handler, so no plain-text side channel exists for PHI to leak into.
"""

import logging

import structlog

_shared_processors: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_logging(log_level: str) -> None:
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            *_shared_processors,
            structlog.processors.StackInfoRenderer(),
            # Hand off to the root handler's ProcessorFormatter below.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs for records from stdlib loggers
        # (uvicorn, alembic, sqlalchemy, …) so they get the same shape.
        foreign_pre_chain=_shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own (colorized plain-text) handlers before the
    # app factory runs; strip them and let records propagate to root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
