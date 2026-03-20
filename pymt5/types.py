"""Type definitions, dataclasses, and TypedDicts for pymt5."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, TypedDict

from pymt5.constants import (
    PROP_BYTES,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I64,
    PROP_U8,
    PROP_U32,
    PROP_U64,
)

# Type aliases
Record = dict[str, Any]
RecordList = list[Record]


@dataclass
class TradeResult:
    """Result of a trade request."""

    retcode: int
    description: str
    success: bool
    deal: int = 0
    order: int = 0
    volume: int = 0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""
    request_id: int = 0

    def __repr__(self) -> str:
        parts = [f"retcode={self.retcode}", f"success={self.success}", f"desc='{self.description}'"]
        if self.deal:
            parts.append(f"deal={self.deal}")
        if self.order:
            parts.append(f"order={self.order}")
        if self.price:
            parts.append(f"price={self.price}")
        return f"TradeResult({', '.join(parts)})"


@dataclass
class AccountInfo:
    """Account summary computed from positions and deals."""

    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    credit: float = 0.0
    leverage: int = 0
    currency: str = ""
    server: str = ""
    positions_count: int = 0
    orders_count: int = 0


@dataclass
class SymbolInfo:
    """Cached symbol info for quick lookup."""

    name: str
    symbol_id: int
    digits: int
    description: str = ""
    path: str = ""
    trade_calc_mode: int = 0
    basis: str = ""
    sector: int = 0


@dataclass
class VerificationStatus:
    """Two-channel verification state used by account-opening flows."""

    email: bool = False
    phone: bool = False

    def __bool__(self) -> bool:
        return self.email or self.phone


@dataclass
class OpenAccountResult:
    """Result returned by demo/real account opening commands."""

    code: int
    login: int
    password: str
    investor_password: str

    @property
    def success(self) -> bool:
        return self.code == 0


@dataclass
class AccountOpeningRequest:
    """Shared fields used by the Web Terminal's account opening payloads."""

    first_name: str
    second_name: str
    email: str = ""
    phone: str = ""
    group: str = ""
    deposit: float = 100000.0
    leverage: int = 100
    agreements: int = 0
    country: str = ""
    city: str = ""
    state: str = ""
    zipcode: str = ""
    address: str = ""
    domain: str = ""
    phone_password: str = ""
    email_confirm_code: int = 0
    phone_confirm_code: int = 0
    language: str = ""
    utm_campaign: str = ""
    utm_source: str = ""


@dataclass
class DemoAccountRequest(AccountOpeningRequest):
    """Demo account opening request for cmd=30."""


@dataclass
class AccountDocument:
    """Document upload payload for real account opening.

    ``data_type`` and ``document_type`` are frontend/broker-defined enums.
    """

    data_type: int
    document_type: int
    front_name: str
    front_buffer: bytes
    back_name: str = ""
    back_buffer: bytes = b""


@dataclass
class RealAccountRequest(AccountOpeningRequest):
    """Real account opening request for cmd=39.

    ``birth_date_ms`` uses Unix milliseconds, matching the frontend's
    ``propType=9`` date encoding.
    """

    middle_name: str = ""
    birth_date_ms: int = 0
    gender: int = 0
    citizenship: str = ""
    tax_id: str = ""
    employment: int = 0
    industry: int = 0
    education: int = 0
    wealth: int = 0
    annual_income: int = 0
    net_worth: int = 0
    annual_deposit: int = 0
    experience_fx: int = 0
    experience_cfd: int = 0
    experience_futures: int = 0
    experience_stocks: int = 0
    documents: list[AccountDocument] = field(default_factory=list)


# ---- Schemas that are local to client logic ----

LOGIN_RESPONSE_SCHEMA = [
    {"propType": PROP_BYTES, "propLength": 160},
    {"propType": PROP_U64},
]

TRADER_PARAMS_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 32},
    {"propType": PROP_FIXED_STRING, "propLength": 32},
]

TRADE_RESPONSE_SCHEMA = [
    {"propType": PROP_U32},
    {"propType": PROP_U64},
    {"propType": PROP_U64},
    {"propType": PROP_U64},
    {"propType": PROP_F64},
    {"propType": PROP_F64},
    {"propType": PROP_F64},
    {"propType": PROP_FIXED_STRING, "propLength": 64},
    {"propType": PROP_U32},
]

OPEN_ACCOUNT_RESPONSE_SCHEMA = [
    {"propType": PROP_U32},
    {"propType": PROP_I64},
    {"propType": PROP_FIXED_STRING, "propLength": 32},
    {"propType": PROP_FIXED_STRING, "propLength": 32},
]

VERIFICATION_STATUS_SCHEMA = [
    {"propType": PROP_U8},
    {"propType": PROP_U8},
]

REAL_ACCOUNT_RESERVED_PAYLOAD = struct.pack("<128i", *([0] * 128))


# ---- TypedDicts for common record structures ----


class TickRecord(TypedDict, total=False):
    """Tick data pushed by the server."""

    symbol_id: int
    tick_time: int
    time_ms_delta: int
    bid: float
    ask: float
    last: float
    tick_volume: int
    flags: int
    tick_time_ms: int
    symbol: str


class BarRecord(TypedDict, total=False):
    """OHLCV bar/candle data."""

    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int


class PositionRecord(TypedDict, total=False):
    """Open position data."""

    position_id: int
    trade_symbol: str
    trade_action: int
    volume: int
    price_open: float
    price_current: float
    price_sl: float
    price_tp: float
    profit: float
    commission: float
    storage: float


class OrderRecord(TypedDict, total=False):
    """Pending order data."""

    trade_order: int
    trade_symbol: str
    order_type: int
    volume_initial: int
    volume_current: int
    price_order: float
    price_trigger: float
    price_sl: float
    price_tp: float


class DealRecord(TypedDict, total=False):
    """Historical deal/trade data."""

    trade_order: int
    trade_symbol: str
    deal_action: int
    volume: int
    price: float
    profit: float
    commission: float
    storage: float
    time_create: int
    time_update: int


class BookLevelRecord(TypedDict, total=False):
    """Order book level data."""

    price: float
    volume: int


class SymbolGroupRecord(TypedDict, total=False):
    """Symbol group name."""

    name: str
