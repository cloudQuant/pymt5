"""Optional pandas DataFrame integration.

Provides :func:`to_dataframe` for converting record lists returned by
market data methods (``get_rates``, ``copy_rates_*``, ``copy_ticks_*``)
into pandas DataFrames.

Requires ``pip install mt5-web-client[pandas]``.
"""

from __future__ import annotations

from typing import Any


def to_dataframe(records: list[dict[str, Any]]) -> Any:
    """Convert a list of record dicts to a pandas DataFrame.

    Raises :class:`ImportError` if pandas is not installed.

    Example::

        rates = await client.get_rates("EURUSD", 60, from_ts, to_ts)
        df = to_dataframe(rates)
    """
    try:
        import pandas as pd  # type: ignore[import-untyped]
    except ImportError:
        raise ImportError(
            "pandas is required for DataFrame conversion. Install it with: pip install mt5-web-client[pandas]"
        ) from None
    return pd.DataFrame(records)
