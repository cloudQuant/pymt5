"""Structured logging helper.

If ``structlog`` is installed (``pip install mt5-web-client[structlog]``), loggers
returned by :func:`get_logger` will use structlog's bound-logger API.
Otherwise, the standard :mod:`logging` module is used as a zero-dependency
fallback.

The log level can be controlled via the ``PYMT5_LOG_LEVEL`` environment
variable (e.g. ``PYMT5_LOG_LEVEL=DEBUG``).  When the variable is not set,
the default logging level is used (WARNING for stdlib).
"""

from __future__ import annotations

import logging
import os
from typing import Any

_LOG_LEVEL: str | None = os.environ.get("PYMT5_LOG_LEVEL")


def get_logger(name: str) -> Any:
    """Return a structured logger for *name*.

    Tries ``structlog`` first; falls back to :func:`logging.getLogger`.
    ``PYMT5_LOG_LEVEL`` filters either logger type without changing global
    structlog configuration. Invalid level names are ignored.
    """
    level = getattr(logging, _LOG_LEVEL.upper(), None) if _LOG_LEVEL is not None else None
    try:
        import structlog  # type: ignore[import-untyped]

        if isinstance(level, int):
            return structlog.wrap_logger(
                None,
                wrapper_class=structlog.make_filtering_bound_logger(level),
                logger_factory_args=(name,),
            )
        return structlog.get_logger(name)
    except ImportError:
        logger = logging.getLogger(name)
        if isinstance(level, int):
            logger.setLevel(level)
        return logger
