"""
pymt5 - Python client for MT5 Web Terminal
Reverse-engineered WebSocket binary protocol implementation.
"""

__version__ = "0.3.0"

from pymt5.client import AccountInfo, MT5WebClient, SymbolInfo, TradeResult
from pymt5.constants import (
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_FILLING_RETURN,
    ORDER_TIME_DAY,
    ORDER_TIME_GTC,
    ORDER_TIME_SPECIFIED,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    PERIOD_D1,
    PERIOD_H1,
    PERIOD_H4,
    PERIOD_M1,
    PERIOD_M15,
    PERIOD_M30,
    PERIOD_M5,
    PERIOD_MN1,
    PERIOD_W1,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_PLACED,
)

__all__ = [
    "MT5WebClient",
    "TradeResult",
    "SymbolInfo",
    "AccountInfo",
    # Periods
    "PERIOD_M1",
    "PERIOD_M5",
    "PERIOD_M15",
    "PERIOD_M30",
    "PERIOD_H1",
    "PERIOD_H4",
    "PERIOD_D1",
    "PERIOD_W1",
    "PERIOD_MN1",
    # Trade actions
    "TRADE_ACTION_DEAL",
    "TRADE_ACTION_PENDING",
    "TRADE_ACTION_SLTP",
    "TRADE_ACTION_MODIFY",
    "TRADE_ACTION_REMOVE",
    # Order types
    "ORDER_TYPE_BUY",
    "ORDER_TYPE_SELL",
    "ORDER_TYPE_BUY_LIMIT",
    "ORDER_TYPE_SELL_LIMIT",
    "ORDER_TYPE_BUY_STOP",
    "ORDER_TYPE_SELL_STOP",
    # Filling modes
    "ORDER_FILLING_FOK",
    "ORDER_FILLING_IOC",
    "ORDER_FILLING_RETURN",
    # Time modes
    "ORDER_TIME_GTC",
    "ORDER_TIME_DAY",
    "ORDER_TIME_SPECIFIED",
    # Return codes
    "TRADE_RETCODE_DONE",
    "TRADE_RETCODE_PLACED",
]
