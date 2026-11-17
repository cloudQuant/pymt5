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
    stdlib_logger = logging.getLogger(name)
    if isinstance(level, int) and not isinstance(level, bool):
        stdlib_logger.setLevel(level)
    try:
        import structlog  # type: ignore[import-untyped]

        is_configured = getattr(structlog, "is_configured", None)
        if callable(is_configured) and not is_configured() and hasattr(structlog, "stdlib"):
            return structlog.wrap_logger(
                stdlib_logger,
                wrapper_class=structlog.stdlib.BoundLogger,
                processors=[
                    structlog.stdlib.PositionalArgumentsFormatter(),
                    structlog.stdlib.render_to_log_kwargs,
                ],
            )
        if isinstance(level, int) and not isinstance(level, bool):
            return structlog.wrap_logger(
                None,
                wrapper_class=structlog.make_filtering_bound_logger(level),
                logger_factory_args=(name,),
            )
        return structlog.get_logger(name)
    except ImportError:
        return stdlib_logger
