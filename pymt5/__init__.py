"""
pymt5 - Python client for MT5 Web Terminal
Reverse-engineered WebSocket binary protocol implementation.
"""

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("pymt5")
except ImportError:
    __version__ = "0.9.0"

from pymt5._dataframe import to_dataframe
from pymt5._metrics import MetricsCollector
from pymt5._order_manager import OrderManager, OrderState, PositionSummary, TrackedOrder
from pymt5._pool import MT5ConnectionPool, PoolAccount
from pymt5._subscription import SubscriptionHandle
from pymt5.client import (
    AccountDocument,
    AccountInfo,
    AccountOpeningRequest,
    DemoAccountRequest,
    MT5WebClient,
    OpenAccountResult,
    RealAccountRequest,
    SymbolInfo,
    TradeResult,
    VerificationStatus,
)
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    # Command IDs
    CMD_GET_ACCOUNT,
    CMD_GET_CORPORATE_LINKS,
    CMD_GET_SYMBOL_GROUPS,
    CMD_OPEN_REAL,
    CMD_OTP_SETUP,
    CMD_SEND_VERIFY_CODES,
    CMD_SUBSCRIBE_BOOK,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    COPY_TICKS_ALL,
    COPY_TICKS_INFO,
    COPY_TICKS_TRADE,
    # Deal entry
    DEAL_ENTRY_IN,
    DEAL_ENTRY_INOUT,
    DEAL_ENTRY_OUT,
    DEAL_ENTRY_OUT_BY,
    # Deal types
    DEAL_TYPE_BALANCE,
    DEAL_TYPE_BONUS,
    DEAL_TYPE_BUY,
    DEAL_TYPE_BUY_CANCELED,
    DEAL_TYPE_CHARGE,
    DEAL_TYPE_COMMISSION,
    DEAL_TYPE_COMMISSION_AGENT_DAILY,
    DEAL_TYPE_COMMISSION_AGENT_MONTHLY,
    DEAL_TYPE_COMMISSION_DAILY,
    DEAL_TYPE_COMMISSION_MONTHLY,
    DEAL_TYPE_CORRECTION,
    DEAL_TYPE_CREDIT,
    DEAL_TYPE_INTEREST,
    DEAL_TYPE_SELL,
    DEAL_TYPE_SELL_CANCELED,
    # Filling modes
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_FILLING_RETURN,
    # Order states
    ORDER_STATE_CANCELED,
    ORDER_STATE_EXPIRED,
    ORDER_STATE_FILLED,
    ORDER_STATE_PARTIAL,
    ORDER_STATE_PLACED,
    ORDER_STATE_REJECTED,
    ORDER_STATE_STARTED,
    # Time modes
    ORDER_TIME_DAY,
    ORDER_TIME_GTC,
    ORDER_TIME_SPECIFIED,
    ORDER_TIME_SPECIFIED_DAY,
    # Order types
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    ORDER_TYPE_SELL_STOP_LIMIT,
    # Periods
    PERIOD_D1,
    PERIOD_H1,
    PERIOD_H4,
    PERIOD_M1,
    PERIOD_M5,
    PERIOD_M15,
    PERIOD_M30,
    PERIOD_MN1,
    PERIOD_W1,
    # Position types
    POSITION_TYPE_BUY,
    POSITION_TYPE_SELL,
    # Symbol trade modes
    SYMBOL_TRADE_MODE_CLOSEONLY,
    SYMBOL_TRADE_MODE_DISABLED,
    SYMBOL_TRADE_MODE_FULL,
    SYMBOL_TRADE_MODE_LONGONLY,
    SYMBOL_TRADE_MODE_SHORTONLY,
    # Trade actions
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    # Return codes
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_PLACED,
)
from pymt5.events import AccountEvent, BookEvent, HealthStatus, TickEvent, TradeResultEvent
from pymt5.exceptions import (
    AuthenticationError,
    MT5ConnectionError,
    MT5TimeoutError,
    ProtocolError,
    PyMT5Error,
    SessionError,
    SymbolNotFoundError,
    TradeError,
    ValidationError,
)
from pymt5.transport import TransportState

__all__ = [
    "MT5WebClient",
    "TradeResult",
    "SymbolInfo",
    "AccountInfo",
    "VerificationStatus",
    "OpenAccountResult",
    "AccountOpeningRequest",
    "DemoAccountRequest",
    "RealAccountRequest",
    "AccountDocument",
    # Exceptions
    "PyMT5Error",
    "MT5ConnectionError",
    "AuthenticationError",
    "TradeError",
    "ProtocolError",
    "SymbolNotFoundError",
    "ValidationError",
    "SessionError",
    "MT5TimeoutError",
    # Command IDs
    "CMD_GET_ACCOUNT",
    "CMD_GET_SYMBOL_GROUPS",
    "CMD_TRADE_UPDATE_PUSH",
    "CMD_ACCOUNT_UPDATE_PUSH",
    "CMD_SYMBOL_DETAILS_PUSH",
    "CMD_TRADE_RESULT_PUSH",
    "CMD_SUBSCRIBE_BOOK",
    "CMD_BOOK_PUSH",
    "CMD_GET_CORPORATE_LINKS",
    "CMD_OPEN_REAL",
    "CMD_SEND_VERIFY_CODES",
    "CMD_OTP_SETUP",
    "COPY_TICKS_INFO",
    "COPY_TICKS_TRADE",
    "COPY_TICKS_ALL",
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
    "TRADE_ACTION_CLOSE_BY",
    # Order types
    "ORDER_TYPE_BUY",
    "ORDER_TYPE_SELL",
    "ORDER_TYPE_BUY_LIMIT",
    "ORDER_TYPE_SELL_LIMIT",
    "ORDER_TYPE_BUY_STOP",
    "ORDER_TYPE_SELL_STOP",
    "ORDER_TYPE_BUY_STOP_LIMIT",
    "ORDER_TYPE_SELL_STOP_LIMIT",
    # Filling modes
    "ORDER_FILLING_FOK",
    "ORDER_FILLING_IOC",
    "ORDER_FILLING_RETURN",
    # Time modes
    "ORDER_TIME_GTC",
    "ORDER_TIME_DAY",
    "ORDER_TIME_SPECIFIED",
    "ORDER_TIME_SPECIFIED_DAY",
    # Order states
    "ORDER_STATE_STARTED",
    "ORDER_STATE_PLACED",
    "ORDER_STATE_CANCELED",
    "ORDER_STATE_PARTIAL",
    "ORDER_STATE_FILLED",
    "ORDER_STATE_REJECTED",
    "ORDER_STATE_EXPIRED",
    # Position types
    "POSITION_TYPE_BUY",
    "POSITION_TYPE_SELL",
    # Deal types (all)
    "DEAL_TYPE_BUY",
    "DEAL_TYPE_SELL",
    "DEAL_TYPE_BALANCE",
    "DEAL_TYPE_CREDIT",
    "DEAL_TYPE_CHARGE",
    "DEAL_TYPE_CORRECTION",
    "DEAL_TYPE_BONUS",
    "DEAL_TYPE_COMMISSION",
    "DEAL_TYPE_COMMISSION_DAILY",
    "DEAL_TYPE_COMMISSION_MONTHLY",
    "DEAL_TYPE_COMMISSION_AGENT_DAILY",
    "DEAL_TYPE_COMMISSION_AGENT_MONTHLY",
    "DEAL_TYPE_INTEREST",
    "DEAL_TYPE_BUY_CANCELED",
    "DEAL_TYPE_SELL_CANCELED",
    # Deal entry
    "DEAL_ENTRY_IN",
    "DEAL_ENTRY_OUT",
    "DEAL_ENTRY_INOUT",
    "DEAL_ENTRY_OUT_BY",
    # Symbol trade modes
    "SYMBOL_TRADE_MODE_DISABLED",
    "SYMBOL_TRADE_MODE_LONGONLY",
    "SYMBOL_TRADE_MODE_SHORTONLY",
    "SYMBOL_TRADE_MODE_CLOSEONLY",
    "SYMBOL_TRADE_MODE_FULL",
    # Return codes
    "TRADE_RETCODE_DONE",
    "TRADE_RETCODE_DONE_PARTIAL",
    "TRADE_RETCODE_PLACED",
    # Transport
    "TransportState",
    # Subscriptions
    "SubscriptionHandle",
    # DataFrame integration
    "to_dataframe",
    # Events
    "TickEvent",
    "BookEvent",
    "TradeResultEvent",
    "AccountEvent",
    "HealthStatus",
    # Metrics
    "MetricsCollector",
    # Order Manager
    "OrderManager",
    "OrderState",
    "TrackedOrder",
    "PositionSummary",
    # Connection Pool
    "MT5ConnectionPool",
    "PoolAccount",
]
