"""Structured logging helper.

If ``structlog`` is installed (``pip install pymt5[structlog]``), loggers
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
    If ``PYMT5_LOG_LEVEL`` is set, the stdlib logger level is configured
    accordingly.
    """
    try:
        import structlog  # type: ignore[import-untyped]

        return structlog.get_logger(name)
    except ImportError:
        logger = logging.getLogger(name)
        if _LOG_LEVEL is not None:
            level = getattr(logging, _LOG_LEVEL.upper(), None)
            if isinstance(level, int):
                logger.setLevel(level)
        return logger
