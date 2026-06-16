import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    log_level = logging.getLevelName(level.upper())

    # Configure stdlib so uvicorn and other libraries log at the right level
    logging.basicConfig(
        stream=sys.stdout,
        level=log_level,
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        # stdlib.LoggerFactory returns real logging.Logger objects (have .name)
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
