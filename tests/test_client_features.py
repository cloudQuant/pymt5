"""Tests for new client features: TradeResult, SymbolInfo, symbol cache,
high-level trading helpers, volume conversion, context manager, heartbeat."""

import struct

import pytest

from pymt5.client import (
    AccountInfo,
    MT5WebClient,
    SymbolInfo,
    TradeResult,
    TRADE_RESPONSE_SCHEMA,
    _parse_counted_records,
)
from pymt5.constants import (
    ORDER_FILLING_FOK,
    ORDER_TIME_GTC,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_DESCRIPTIONS,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_REJECT,
)
from pymt5.protocol import get_series_size
from pymt5.schemas import FULL_SYMBOL_SCHEMA, FULL_SYMBOL_FIELD_NAMES


# ---- TradeResult ----

def test_trade_result_success():
    tr = TradeResult(retcode=TRADE_RETCODE_DONE, description="done", success=True)
    assert tr.success is True
    assert tr.retcode == 10009
    assert "done" in repr(tr)


def test_trade_result_failure():
    tr = TradeResult(retcode=TRADE_RETCODE_REJECT, description="rejected", success=False)
    assert tr.success is False
    assert tr.retcode == 10006


def test_trade_result_extended_fields():
    tr = TradeResult(
        retcode=TRADE_RETCODE_DONE, description="done", success=True,
        deal=123456, order=789012, volume=100, price=1.12345,
        bid=1.12340, ask=1.12350, comment="test", request_id=42,
    )
    assert tr.deal == 123456
    assert tr.order == 789012
    assert tr.volume == 100
    assert tr.price == 1.12345
    assert tr.bid == 1.12340
    assert tr.ask == 1.12350
    assert tr.comment == "test"
    assert tr.request_id == 42
    r = repr(tr)
    assert "deal=123456" in r
    assert "order=789012" in r
    assert "price=1.12345" in r


def test_trade_result_defaults():
    tr = TradeResult(retcode=0, description="ok", success=True)
    assert tr.deal == 0
    assert tr.order == 0
    assert tr.volume == 0
    assert tr.price == 0.0
    assert tr.comment == ""
    assert tr.request_id == 0


def test_trade_retcode_descriptions_complete():
    assert TRADE_RETCODE_DONE in TRADE_RETCODE_DESCRIPTIONS
    assert TRADE_RETCODE_PLACED in TRADE_RETCODE_DESCRIPTIONS
    assert TRADE_RETCODE_DONE_PARTIAL in TRADE_RETCODE_DESCRIPTIONS
    assert len(TRADE_RETCODE_DESCRIPTIONS) >= 30


# ---- SymbolInfo ----

def test_symbol_info_creation():
    si = SymbolInfo(name="EURUSD", symbol_id=42, digits=5, description="Euro vs US Dollar")
    assert si.name == "EURUSD"
    assert si.symbol_id == 42
    assert si.digits == 5
    assert si.description == "Euro vs US Dollar"


def test_symbol_info_defaults():
    si = SymbolInfo(name="X", symbol_id=1, digits=2)
    assert si.path == ""
    assert si.trade_calc_mode == 0
    assert si.basis == ""
    assert si.sector == 0


# ---- Symbol Cache ----

def test_symbol_cache_manual():
    client = MT5WebClient()
    # Manually populate cache (simulating load_symbols)
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    client._symbols["GBPUSD"] = SymbolInfo(name="GBPUSD", symbol_id=99, digits=5)
    client._symbols_by_id[42] = client._symbols["EURUSD"]
    client._symbols_by_id[99] = client._symbols["GBPUSD"]

    assert client.get_symbol_info("EURUSD").symbol_id == 42
    assert client.get_symbol_id("GBPUSD") == 99
    assert client.get_symbol_info("MISSING") is None
    assert client.get_symbol_id("MISSING") is None
    assert set(client.symbol_names) == {"EURUSD", "GBPUSD"}


# ---- Volume Conversion ----

def test_volume_to_lots_standard():
    # Default digits=2: volume * 10^2
    assert MT5WebClient._volume_to_lots(0.01) == 1
    assert MT5WebClient._volume_to_lots(0.1) == 10
    assert MT5WebClient._volume_to_lots(1.0) == 100
    assert MT5WebClient._volume_to_lots(10.0) == 1000


def test_volume_to_lots_custom_digits():
    assert MT5WebClient._volume_to_lots(1.0, digits=0) == 1
    assert MT5WebClient._volume_to_lots(1.0, digits=1) == 10
    assert MT5WebClient._volume_to_lots(1.0, digits=3) == 1000


def test_volume_to_lots_rounding():
    # 0.015 * 100 = 1.5 → rounds to 2
    assert MT5WebClient._volume_to_lots(0.015) == 2
    # 0.999 * 100 = 99.9 → rounds to 100
    assert MT5WebClient._volume_to_lots(0.999) == 100


# ---- Digits Resolution ----

def test_resolve_digits_explicit():
    client = MT5WebClient()
    assert client._resolve_digits("EURUSD", 3) == 3


def test_resolve_digits_from_cache():
    client = MT5WebClient()
    client._symbols["EURUSD"] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
    assert client._resolve_digits("EURUSD", None) == 5


def test_resolve_digits_fallback():
    client = MT5WebClient()
    # No cache, no explicit → defaults to 5
    assert client._resolve_digits("UNKNOWN", None) == 5


# ---- Trade Constants ----

def test_trade_action_constants():
    assert TRADE_ACTION_DEAL == 1
    assert TRADE_ACTION_PENDING == 5
    assert TRADE_ACTION_SLTP == 6
    assert TRADE_ACTION_MODIFY == 7
    assert TRADE_ACTION_REMOVE == 8


def test_order_type_constants():
    assert ORDER_TYPE_BUY == 0
    assert ORDER_TYPE_SELL == 1
    assert ORDER_TYPE_BUY_LIMIT == 2
    assert ORDER_TYPE_SELL_LIMIT == 3
    assert ORDER_TYPE_BUY_STOP == 4
    assert ORDER_TYPE_SELL_STOP == 5


def test_filling_constants():
    assert ORDER_FILLING_FOK == 0


def test_time_constants():
    assert ORDER_TIME_GTC == 0


# ---- Client Init ----

def test_client_init_defaults():
    client = MT5WebClient()
    assert client.uri == "wss://web.metatrader.app/terminal"
    assert client.timeout == 30.0
    assert client._heartbeat_interval == 30.0
    assert client._logged_in is False
    assert client._symbols == {}
    assert client._symbols_by_id == {}
    assert client._auto_reconnect is False
    assert client._max_reconnect_attempts == 5
    assert client._reconnect_delay == 3.0
    assert client._login_kwargs is None
    assert client._subscribed_ids == []


def test_client_init_custom():
    client = MT5WebClient(
        uri="wss://custom.server/ws",
        timeout=10.0,
        heartbeat_interval=60.0,
        auto_reconnect=True,
        max_reconnect_attempts=10,
        reconnect_delay=5.0,
    )
    assert client.uri == "wss://custom.server/ws"
    assert client.timeout == 10.0
    assert client._heartbeat_interval == 60.0
    assert client._auto_reconnect is True
    assert client._max_reconnect_attempts == 10
    assert client._reconnect_delay == 5.0


# ---- is_connected ----

def test_is_connected_default():
    client = MT5WebClient()
    assert client.is_connected is False


# ---- subscribe_symbols validation ----

def test_subscribe_symbols_missing_raises():
    client = MT5WebClient()
    # No symbols loaded → should raise
    with pytest.raises(ValueError, match="symbols not found in cache"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            client.subscribe_symbols(["NOSYMBOL"])
        )


# ---- on_disconnect callback ----

def test_on_disconnect_callback():
    client = MT5WebClient()
    called = []
    client.on_disconnect(lambda: called.append(True))
    assert client._on_disconnect is not None
    # Simulate disconnect
    client._handle_disconnect()
    assert called == [True]
    assert client._logged_in is False


# ---- handle_disconnect without auto_reconnect ----

def test_handle_disconnect_no_auto_reconnect():
    client = MT5WebClient(auto_reconnect=False)
    client._logged_in = True
    client._handle_disconnect()
    assert client._logged_in is False
    assert client._reconnect_task is None


# ---- AccountInfo ----

def test_account_info_defaults():
    ai = AccountInfo()
    assert ai.balance == 0.0
    assert ai.equity == 0.0
    assert ai.margin == 0.0
    assert ai.margin_free == 0.0
    assert ai.profit == 0.0
    assert ai.leverage == 0
    assert ai.currency == ""
    assert ai.positions_count == 0
    assert ai.orders_count == 0


def test_account_info_with_values():
    ai = AccountInfo(
        balance=10000.0, equity=10500.0, margin=200.0,
        margin_free=10300.0, profit=500.0, leverage=100,
        currency="USD", positions_count=3, orders_count=1,
    )
    assert ai.balance == 10000.0
    assert ai.equity == 10500.0
    assert ai.positions_count == 3


# ---- Trade Response Parsing ----

def test_parse_trade_response_empty():
    client = MT5WebClient()
    tr = client._parse_trade_response(b"", "EURUSD", 1, 100)
    assert tr.retcode == -1
    assert tr.success is False
    assert "Empty" in tr.description


def test_parse_trade_response_retcode_only():
    # Only 4 bytes (retcode), no extended fields
    body = struct.pack("<I", TRADE_RETCODE_DONE)
    client = MT5WebClient()
    tr = client._parse_trade_response(body, "EURUSD", 1, 100)
    assert tr.retcode == TRADE_RETCODE_DONE
    assert tr.success is True
    assert tr.deal == 0  # No extended fields


def test_trade_response_schema_size():
    size = get_series_size(TRADE_RESPONSE_SCHEMA)
    # u32 + u64 + u64 + u64 + f64 + f64 + f64 + fs64 + u32 = 4+8+8+8+8+8+8+64+4 = 120
    assert size == 120


# ---- Full Symbol Schema ----

def test_full_symbol_schema_fields():
    assert len(FULL_SYMBOL_SCHEMA) == 28
    assert len(FULL_SYMBOL_FIELD_NAMES) == 28
    assert FULL_SYMBOL_FIELD_NAMES[0] == "trade_symbol"
    assert "contract_size" in FULL_SYMBOL_FIELD_NAMES
    assert "tick_size" in FULL_SYMBOL_FIELD_NAMES
    assert "tick_value" in FULL_SYMBOL_FIELD_NAMES
    assert "margin_initial" in FULL_SYMBOL_FIELD_NAMES
    assert "volume_min" in FULL_SYMBOL_FIELD_NAMES
    assert "currency_base" in FULL_SYMBOL_FIELD_NAMES


def test_full_symbol_schema_size():
    size = get_series_size(FULL_SYMBOL_SCHEMA)
    # Same first 8 fields as SYMBOL_BASIC = 526 bytes
    # + 7*f64(56) + 4*u32(16) + f64(8) + f64(8) + 3*fs64(192) + 3*u32(12)
    assert size > 526  # Must be bigger than basic schema


# ---- Package Exports ----

def test_package_exports():
    import pymt5
    assert pymt5.__version__ == "0.3.0"
    assert hasattr(pymt5, "MT5WebClient")
    assert hasattr(pymt5, "TradeResult")
    assert hasattr(pymt5, "SymbolInfo")
    assert hasattr(pymt5, "AccountInfo")
    assert hasattr(pymt5, "TRADE_ACTION_DEAL")
    assert hasattr(pymt5, "ORDER_TYPE_BUY")
    assert hasattr(pymt5, "ORDER_FILLING_FOK")
    assert hasattr(pymt5, "ORDER_TIME_GTC")
    assert hasattr(pymt5, "TRADE_RETCODE_DONE")
