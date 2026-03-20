"""Tests for client features: TradeResult, SymbolInfo, symbol cache,
high-level trading helpers, volume conversion, context manager, heartbeat,
stop-limit orders, close-by, push handlers, new constants, crypto roundtrip,
account info, symbol groups, order book, corporate links, trade transaction,
trade result push, symbol details push, spread schema."""

import asyncio
import struct
from unittest.mock import AsyncMock

import pytest

from pymt5.client import (
    TRADE_RESPONSE_SCHEMA,
    AccountInfo,
    MT5WebClient,
    SymbolInfo,
    TradeResult,
    _parse_counted_records,
    _parse_rate_bars,
)
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_GET_ACCOUNT,
    CMD_GET_CORPORATE_LINKS,
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_SYMBOL_GROUPS,
    CMD_SUBSCRIBE_BOOK,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    DEAL_ENTRY_IN,
    DEAL_ENTRY_OUT,
    DEAL_ENTRY_OUT_BY,
    DEAL_TYPE_BALANCE,
    DEAL_TYPE_BUY,
    DEAL_TYPE_SELL,
    ORDER_FILLING_FOK,
    ORDER_STATE_FILLED,
    ORDER_STATE_PLACED,
    ORDER_STATE_STARTED,
    ORDER_TIME_GTC,
    ORDER_TIME_SPECIFIED_DAY,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    ORDER_TYPE_SELL_STOP_LIMIT,
    POSITION_TYPE_BUY,
    POSITION_TYPE_SELL,
    SYMBOL_TRADE_MODE_DISABLED,
    SYMBOL_TRADE_MODE_FULL,
    TRADE_ACTION_CLOSE_BY,
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
from pymt5.crypto import AESCipher, initial_cipher, initial_key_bytes
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    ACCOUNT_BASE_FIELD_NAMES,
    ACCOUNT_BASE_SCHEMA,
    BOOK_HEADER_FIELD_NAMES,
    BOOK_HEADER_SCHEMA,
    BOOK_LEVEL_FIELD_NAMES,
    BOOK_LEVEL_SCHEMA,
    CORPORATE_LINK_FIELD_NAMES,
    CORPORATE_LINK_SCHEMA,
    FULL_SYMBOL_FIELD_NAMES,
    FULL_SYMBOL_SCHEMA,
    RATE_BAR_SCHEMA_EXT,
    SPREAD_FIELD_NAMES,
    SPREAD_SCHEMA,
    SYMBOL_DETAILS_FIELD_NAMES,
    SYMBOL_DETAILS_SCHEMA,
    SYMBOL_GROUP_FIELD_NAMES,
    SYMBOL_GROUP_SCHEMA,
    TRADE_RESULT_PUSH_FIELD_NAMES,
    TRADE_RESULT_PUSH_SCHEMA,
    TRADE_RESULT_RESPONSE_FIELD_NAMES,
    TRADE_RESULT_RESPONSE_SCHEMA,
    TRADE_TRANSACTION_FIELD_NAMES,
    TRADE_TRANSACTION_SCHEMA,
    TRADE_UPDATE_BALANCE_FIELD_NAMES,
    TRADE_UPDATE_BALANCE_SCHEMA,
)
from pymt5.transport import CommandResult

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
        retcode=TRADE_RETCODE_DONE,
        description="done",
        success=True,
        deal=123456,
        order=789012,
        volume=100,
        price=1.12345,
        bid=1.12340,
        ask=1.12350,
        comment="test",
        request_id=42,
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


def test_volume_to_lots_default_precision():
    assert MT5WebClient._volume_to_lots(0.01) == 1_000_000
    assert MT5WebClient._volume_to_lots(0.1) == 10_000_000
    assert MT5WebClient._volume_to_lots(1.0) == 100_000_000
    assert MT5WebClient._volume_to_lots(10.0) == 1_000_000_000


def test_volume_to_lots_custom_precision():
    assert MT5WebClient._volume_to_lots(1.0, precision=0) == 1
    assert MT5WebClient._volume_to_lots(1.0, precision=2) == 100
    assert MT5WebClient._volume_to_lots(1.0, precision=4) == 10_000


def test_volume_to_lots_rounding():
    assert MT5WebClient._volume_to_lots(0.015) == 1_500_000
    assert MT5WebClient._volume_to_lots(0.999) == 99_900_000


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
    assert client.last_error() == (0, "")


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


def test_send_raw_command_marks_bootstrap_state_dirty_for_cmd52():
    client = MT5WebClient()
    client._bootstrap_pristine = True
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=52, code=0, body=b""),
    )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.send_raw_command(52))
    finally:
        loop.close()

    assert result.command == 52
    assert client._bootstrap_pristine is False
    client.transport.send_command.assert_awaited_once_with(52, b"")


def test_send_bootstrap_command_52_requires_pristine_connection():
    client = MT5WebClient()
    client.transport.is_ready = True

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(RuntimeError, match="fresh bootstrap-only connection"):
            loop.run_until_complete(client.send_bootstrap_command_52())
    finally:
        loop.close()


def test_send_bootstrap_command_52_sends_reserved_command():
    client = MT5WebClient()
    client.transport.is_ready = True
    client._bootstrap_pristine = True
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=52, code=0, body=b""),
    )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client.send_bootstrap_command_52())
    finally:
        loop.close()

    assert result.code == 0
    assert client._bootstrap_pristine is False
    client.transport.send_command.assert_awaited_once_with(52)


# ---- subscribe_symbols validation ----


def test_subscribe_symbols_missing_raises():
    client = MT5WebClient()
    import asyncio

    with pytest.raises(ValueError, match="symbols not found in cache"):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.subscribe_symbols(["NOSYMBOL"]))
        finally:
            loop.close()


# ---- on_disconnect callback ----


def test_on_disconnect_callback():
    client = MT5WebClient()
    called = []
    client.on_disconnect(lambda: called.append(True))
    assert client._on_disconnect is not None
    client._handle_disconnect()
    assert called == [True]
    assert client._logged_in is False


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
        balance=10000.0,
        equity=10500.0,
        margin=200.0,
        margin_free=10300.0,
        profit=500.0,
        leverage=100,
        currency="USD",
        positions_count=3,
        orders_count=1,
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
    body = struct.pack("<I", TRADE_RETCODE_DONE)
    client = MT5WebClient()
    tr = client._parse_trade_response(body, "EURUSD", 1, 100)
    assert tr.retcode == TRADE_RETCODE_DONE
    assert tr.success is True
    assert tr.deal == 0


def test_trade_response_schema_size():
    size = get_series_size(TRADE_RESPONSE_SCHEMA)
    assert size == 120


# ---- Full Symbol Schema ----


def test_full_symbol_schema_fields():
    assert len(FULL_SYMBOL_SCHEMA) == 49
    assert len(FULL_SYMBOL_FIELD_NAMES) == 49
    assert FULL_SYMBOL_FIELD_NAMES[0] == "trade_symbol"
    assert "contract_size" in FULL_SYMBOL_FIELD_NAMES
    assert "tick_size" in FULL_SYMBOL_FIELD_NAMES
    assert "tick_value" in FULL_SYMBOL_FIELD_NAMES
    assert "currency_base" in FULL_SYMBOL_FIELD_NAMES
    assert "face_value" in FULL_SYMBOL_FIELD_NAMES
    assert "accrued_interest" in FULL_SYMBOL_FIELD_NAMES
    assert "trade" in FULL_SYMBOL_FIELD_NAMES
    assert "schedule" in FULL_SYMBOL_FIELD_NAMES
    assert "subscription" in FULL_SYMBOL_FIELD_NAMES


def test_full_symbol_schema_size():
    size = get_series_size(FULL_SYMBOL_SCHEMA)
    assert size == 3068


# ---- Package Exports ----


def test_package_exports():
    import pymt5

    assert isinstance(pymt5.__version__, str)
    assert len(pymt5.__version__) > 0
    assert hasattr(pymt5, "MT5WebClient")
    assert hasattr(pymt5, "TradeResult")
    assert hasattr(pymt5, "SymbolInfo")
    assert hasattr(pymt5, "AccountInfo")
    assert hasattr(pymt5, "TRADE_ACTION_DEAL")
    assert hasattr(pymt5, "ORDER_TYPE_BUY")
    assert hasattr(pymt5, "ORDER_FILLING_FOK")
    assert hasattr(pymt5, "ORDER_TIME_GTC")
    assert hasattr(pymt5, "TRADE_RETCODE_DONE")


def test_package_exports_new_constants():
    import pymt5

    assert hasattr(pymt5, "TRADE_ACTION_CLOSE_BY")
    assert pymt5.TRADE_ACTION_CLOSE_BY == 10
    assert hasattr(pymt5, "ORDER_TYPE_BUY_STOP_LIMIT")
    assert hasattr(pymt5, "ORDER_TYPE_SELL_STOP_LIMIT")
    assert pymt5.ORDER_TYPE_BUY_STOP_LIMIT == 6
    assert pymt5.ORDER_TYPE_SELL_STOP_LIMIT == 7
    assert hasattr(pymt5, "ORDER_TIME_SPECIFIED_DAY")
    assert pymt5.ORDER_TIME_SPECIFIED_DAY == 3
    assert hasattr(pymt5, "POSITION_TYPE_BUY")
    assert hasattr(pymt5, "POSITION_TYPE_SELL")
    assert pymt5.POSITION_TYPE_BUY == 0
    assert pymt5.POSITION_TYPE_SELL == 1
    assert hasattr(pymt5, "DEAL_TYPE_BUY")
    assert hasattr(pymt5, "DEAL_TYPE_SELL")
    assert hasattr(pymt5, "DEAL_TYPE_BALANCE")
    assert hasattr(pymt5, "DEAL_ENTRY_IN")
    assert hasattr(pymt5, "DEAL_ENTRY_OUT")
    assert hasattr(pymt5, "DEAL_ENTRY_OUT_BY")
    assert hasattr(pymt5, "ORDER_STATE_STARTED")
    assert hasattr(pymt5, "ORDER_STATE_PLACED")
    assert hasattr(pymt5, "ORDER_STATE_FILLED")
    assert hasattr(pymt5, "ORDER_STATE_CANCELED")
    assert hasattr(pymt5, "TRADE_RETCODE_DONE_PARTIAL")
    assert hasattr(pymt5, "SYMBOL_TRADE_MODE_FULL")
    assert hasattr(pymt5, "SYMBOL_TRADE_MODE_DISABLED")


def test_package_exports_v050_commands():
    import pymt5

    assert hasattr(pymt5, "CMD_GET_ACCOUNT")
    assert pymt5.CMD_GET_ACCOUNT == 3
    assert hasattr(pymt5, "CMD_GET_SYMBOL_GROUPS")
    assert pymt5.CMD_GET_SYMBOL_GROUPS == 9
    assert hasattr(pymt5, "CMD_TRADE_UPDATE_PUSH")
    assert pymt5.CMD_TRADE_UPDATE_PUSH == 10
    assert hasattr(pymt5, "CMD_ACCOUNT_UPDATE_PUSH")
    assert pymt5.CMD_ACCOUNT_UPDATE_PUSH == 14
    assert hasattr(pymt5, "CMD_SYMBOL_DETAILS_PUSH")
    assert pymt5.CMD_SYMBOL_DETAILS_PUSH == 17
    assert hasattr(pymt5, "CMD_TRADE_RESULT_PUSH")
    assert pymt5.CMD_TRADE_RESULT_PUSH == 19
    assert hasattr(pymt5, "CMD_SUBSCRIBE_BOOK")
    assert pymt5.CMD_SUBSCRIBE_BOOK == 22
    assert hasattr(pymt5, "CMD_BOOK_PUSH")
    assert pymt5.CMD_BOOK_PUSH == 23
    assert hasattr(pymt5, "CMD_GET_CORPORATE_LINKS")
    assert pymt5.CMD_GET_CORPORATE_LINKS == 44


def test_package_exports_all_deal_types():
    import pymt5

    assert pymt5.DEAL_TYPE_CHARGE == 4
    assert pymt5.DEAL_TYPE_CORRECTION == 5
    assert pymt5.DEAL_TYPE_BONUS == 6
    assert pymt5.DEAL_TYPE_COMMISSION == 7
    assert pymt5.DEAL_TYPE_COMMISSION_DAILY == 8
    assert pymt5.DEAL_TYPE_COMMISSION_MONTHLY == 9
    assert pymt5.DEAL_TYPE_COMMISSION_AGENT_DAILY == 10
    assert pymt5.DEAL_TYPE_COMMISSION_AGENT_MONTHLY == 11
    assert pymt5.DEAL_TYPE_INTEREST == 12
    assert pymt5.DEAL_TYPE_BUY_CANCELED == 13
    assert pymt5.DEAL_TYPE_SELL_CANCELED == 14


def test_package_exports_order_state_expired():
    import pymt5

    assert hasattr(pymt5, "ORDER_STATE_EXPIRED")
    assert pymt5.ORDER_STATE_EXPIRED == 6


# ---- New Constants Values ----


def test_position_type_constants():
    assert POSITION_TYPE_BUY == 0
    assert POSITION_TYPE_SELL == 1


def test_deal_type_constants():
    assert DEAL_TYPE_BUY == 0
    assert DEAL_TYPE_SELL == 1
    assert DEAL_TYPE_BALANCE == 2


def test_deal_entry_constants():
    assert DEAL_ENTRY_IN == 0
    assert DEAL_ENTRY_OUT == 1
    assert DEAL_ENTRY_OUT_BY == 3


def test_order_state_constants():
    assert ORDER_STATE_STARTED == 0
    assert ORDER_STATE_PLACED == 1
    assert ORDER_STATE_FILLED == 4


def test_stop_limit_order_type_constants():
    assert ORDER_TYPE_BUY_STOP_LIMIT == 6
    assert ORDER_TYPE_SELL_STOP_LIMIT == 7


def test_close_by_action_constant():
    assert TRADE_ACTION_CLOSE_BY == 10


def test_order_time_specified_day():
    assert ORDER_TIME_SPECIFIED_DAY == 3


def test_symbol_trade_mode_constants():
    assert SYMBOL_TRADE_MODE_DISABLED == 0
    assert SYMBOL_TRADE_MODE_FULL == 4


# ---- AccountInfo server field ----


def test_account_info_server_field():
    ai = AccountInfo(server="MetaQuotes-Demo", currency="USD")
    assert ai.server == "MetaQuotes-Demo"
    assert ai.currency == "USD"


def test_account_info_server_default():
    ai = AccountInfo()
    assert ai.server == ""


# ---- Crypto Roundtrip ----


def test_aes_cipher_roundtrip():
    key = initial_key_bytes()
    cipher = AESCipher(key)
    plaintext = b"Hello MT5 World! This is a test message for roundtrip."
    encrypted = cipher.encrypt(plaintext)
    assert encrypted != plaintext
    decrypted = cipher.decrypt(encrypted)
    assert decrypted == plaintext


def test_aes_cipher_roundtrip_empty():
    cipher = initial_cipher()
    encrypted = cipher.encrypt(b"")
    decrypted = cipher.decrypt(encrypted)
    assert decrypted == b""


def test_aes_cipher_roundtrip_block_aligned():
    cipher = initial_cipher()
    plaintext = b"A" * 16
    encrypted = cipher.encrypt(plaintext)
    decrypted = cipher.decrypt(encrypted)
    assert decrypted == plaintext


def test_aes_cipher_roundtrip_large():
    cipher = initial_cipher()
    plaintext = b"X" * 10000
    encrypted = cipher.encrypt(plaintext)
    decrypted = cipher.decrypt(encrypted)
    assert decrypted == plaintext


def test_aes_cipher_invalid_key_length():
    with pytest.raises(ValueError, match="invalid AES key length"):
        AESCipher(b"short")


# ---- Extended Rate Bar Schema ----


def test_rate_bar_ext_schema_size():
    assert get_series_size(RATE_BAR_SCHEMA_EXT) == 56


def test_parse_rate_bars_extended():
    bar = struct.pack("<iddddqiq", 1773293460, 1.15, 1.16, 1.14, 1.155, 100, 5, 50000)
    bars = _parse_rate_bars(bar)
    assert len(bars) == 1
    assert bars[0]["time"] == 1773293460
    assert "real_volume" in bars[0]
    assert bars[0]["real_volume"] == 50000


def test_parse_rate_bars_standard_still_works():
    bar = struct.pack("<iddddqi", 1773293460, 1.15, 1.16, 1.14, 1.155, 100, 5)
    bars = _parse_rate_bars(bar)
    assert len(bars) == 1
    assert bars[0]["time"] == 1773293460
    assert "real_volume" not in bars[0]


# ---- Close Position Direction Detection ----


def test_detect_close_direction_defaults_to_sell():
    client = MT5WebClient()
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(client._detect_close_direction(999))
        assert result == ORDER_TYPE_SELL
    finally:
        loop.close()


# ---- Push Handler Registration ----


def test_on_trade_update_handler_registration():
    client = MT5WebClient()
    called = []
    client.on_trade_update(lambda data: called.append(data))
    assert CMD_GET_POSITIONS_ORDERS in client.transport._listeners
    assert len(client.transport._listeners[CMD_GET_POSITIONS_ORDERS]) == 1


def test_on_symbol_update_handler_registration():
    from pymt5.constants import CMD_SYMBOL_UPDATE_PUSH

    client = MT5WebClient()
    client.on_symbol_update(lambda result: None)
    assert CMD_SYMBOL_UPDATE_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_SYMBOL_UPDATE_PUSH]) == 1


def test_on_login_status_handler_registration():
    from pymt5.constants import CMD_LOGIN_STATUS_PUSH

    client = MT5WebClient()
    client.on_login_status(lambda result: None)
    assert CMD_LOGIN_STATUS_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_LOGIN_STATUS_PUSH]) == 1


def test_on_account_update_handler_registration():
    client = MT5WebClient()
    client.on_account_update(lambda data: None)
    assert CMD_ACCOUNT_UPDATE_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_ACCOUNT_UPDATE_PUSH]) == 1


def test_on_symbol_details_handler_registration():
    client = MT5WebClient()
    client.on_symbol_details(lambda data: None)
    assert CMD_SYMBOL_DETAILS_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_SYMBOL_DETAILS_PUSH]) == 1


def test_on_trade_result_handler_registration():
    client = MT5WebClient()
    client.on_trade_result(lambda data: None)
    assert CMD_TRADE_RESULT_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_TRADE_RESULT_PUSH]) == 1


def test_on_trade_transaction_handler_registration():
    client = MT5WebClient()
    client.on_trade_transaction(lambda data: None)
    assert CMD_TRADE_UPDATE_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_TRADE_UPDATE_PUSH]) == 1


def test_on_book_update_handler_registration():
    client = MT5WebClient()
    client.on_book_update(lambda data: None)
    assert CMD_BOOK_PUSH in client.transport._listeners
    assert len(client.transport._listeners[CMD_BOOK_PUSH]) == 2


# ---- Transport listener management ----


def test_transport_off_removes_listener():
    from pymt5.constants import CMD_TICK_PUSH

    client = MT5WebClient()

    def handler(result):
        return None

    client.transport.on(CMD_TICK_PUSH, handler)
    assert handler in client.transport._listeners[CMD_TICK_PUSH]
    client.transport.off(CMD_TICK_PUSH, handler)
    assert handler not in client.transport._listeners[CMD_TICK_PUSH]


def test_transport_off_clears_all():
    from pymt5.constants import CMD_TICK_PUSH

    client = MT5WebClient()
    client.transport.on(CMD_TICK_PUSH, lambda r: None)
    client.transport.on(CMD_TICK_PUSH, lambda r: None)
    assert len(client.transport._listeners[CMD_TICK_PUSH]) == 3
    client.transport.off(CMD_TICK_PUSH)
    assert len(client.transport._listeners[CMD_TICK_PUSH]) == 0


# ---- New Schema Sizes and Field Counts ----


def test_account_base_schema():
    assert len(ACCOUNT_BASE_SCHEMA) == 25
    assert len(ACCOUNT_BASE_FIELD_NAMES) == 25
    assert ACCOUNT_BASE_FIELD_NAMES[0] == "login"
    assert "balance" in ACCOUNT_BASE_FIELD_NAMES
    assert "equity" in ACCOUNT_BASE_FIELD_NAMES
    assert "margin" in ACCOUNT_BASE_FIELD_NAMES
    assert "leverage" in ACCOUNT_BASE_FIELD_NAMES
    assert "currency" in ACCOUNT_BASE_FIELD_NAMES
    assert "company" in ACCOUNT_BASE_FIELD_NAMES
    size = get_series_size(ACCOUNT_BASE_SCHEMA)
    assert size > 200


def test_symbol_group_schema():
    assert len(SYMBOL_GROUP_SCHEMA) == 1
    assert len(SYMBOL_GROUP_FIELD_NAMES) == 1
    assert SYMBOL_GROUP_FIELD_NAMES[0] == "group_name"
    assert get_series_size(SYMBOL_GROUP_SCHEMA) == 256


def test_spread_schema():
    assert len(SPREAD_SCHEMA) == 6
    assert len(SPREAD_FIELD_NAMES) == 6
    assert "spread_id" in SPREAD_FIELD_NAMES
    assert "trade_symbol" in SPREAD_FIELD_NAMES
    assert "spread_value" in SPREAD_FIELD_NAMES


def test_book_header_schema():
    assert len(BOOK_HEADER_SCHEMA) == 6
    assert len(BOOK_HEADER_FIELD_NAMES) == 6
    assert "symbol_id" in BOOK_HEADER_FIELD_NAMES
    assert "bid_count" in BOOK_HEADER_FIELD_NAMES
    assert "ask_count" in BOOK_HEADER_FIELD_NAMES


def test_book_level_schema():
    assert len(BOOK_LEVEL_SCHEMA) == 2
    assert len(BOOK_LEVEL_FIELD_NAMES) == 2
    assert BOOK_LEVEL_FIELD_NAMES[0] == "price"
    assert BOOK_LEVEL_FIELD_NAMES[1] == "volume"
    assert get_series_size(BOOK_LEVEL_SCHEMA) == 16


def test_symbol_details_schema():
    assert len(SYMBOL_DETAILS_SCHEMA) == 39
    assert len(SYMBOL_DETAILS_FIELD_NAMES) == 39
    assert "symbol_id" in SYMBOL_DETAILS_FIELD_NAMES
    assert "bid" in SYMBOL_DETAILS_FIELD_NAMES
    assert "ask" in SYMBOL_DETAILS_FIELD_NAMES
    assert "delta" in SYMBOL_DETAILS_FIELD_NAMES
    assert "gamma" in SYMBOL_DETAILS_FIELD_NAMES
    assert "theta" in SYMBOL_DETAILS_FIELD_NAMES
    assert "vega" in SYMBOL_DETAILS_FIELD_NAMES
    assert "rho" in SYMBOL_DETAILS_FIELD_NAMES
    assert "omega" in SYMBOL_DETAILS_FIELD_NAMES


def test_trade_result_push_schema():
    assert len(TRADE_RESULT_PUSH_SCHEMA) == 21
    assert len(TRADE_RESULT_PUSH_FIELD_NAMES) == 21
    assert "action_result_code" in TRADE_RESULT_PUSH_FIELD_NAMES
    assert "trade_symbol" in TRADE_RESULT_PUSH_FIELD_NAMES
    assert "trade_position" in TRADE_RESULT_PUSH_FIELD_NAMES


def test_trade_result_response_schema():
    assert len(TRADE_RESULT_RESPONSE_SCHEMA) == 7
    assert len(TRADE_RESULT_RESPONSE_FIELD_NAMES) == 7
    assert "retcode" in TRADE_RESULT_RESPONSE_FIELD_NAMES
    assert "price" in TRADE_RESULT_RESPONSE_FIELD_NAMES


def test_trade_transaction_schema():
    assert len(TRADE_TRANSACTION_SCHEMA) == 3
    assert len(TRADE_TRANSACTION_FIELD_NAMES) == 3
    assert "flag_mask" in TRADE_TRANSACTION_FIELD_NAMES
    assert "transaction_type" in TRADE_TRANSACTION_FIELD_NAMES


def test_trade_update_balance_schema():
    assert len(TRADE_UPDATE_BALANCE_SCHEMA) == 6
    assert len(TRADE_UPDATE_BALANCE_FIELD_NAMES) == 6
    assert "balance" in TRADE_UPDATE_BALANCE_FIELD_NAMES
    assert "equity" in TRADE_UPDATE_BALANCE_FIELD_NAMES
    assert "margin" in TRADE_UPDATE_BALANCE_FIELD_NAMES


def test_corporate_link_schema():
    assert len(CORPORATE_LINK_SCHEMA) == 5
    assert len(CORPORATE_LINK_FIELD_NAMES) == 5
    assert "link_type" in CORPORATE_LINK_FIELD_NAMES
    assert "url" in CORPORATE_LINK_FIELD_NAMES
    assert "label" in CORPORATE_LINK_FIELD_NAMES


# ---- Schema Roundtrip Parsing ----


def test_account_base_roundtrip():
    from pymt5.helpers import encode_utf16le

    login_bytes = (12345678).to_bytes(8, "little", signed=False)
    body = login_bytes
    body += struct.pack("<iiii", 0, 100, 200, 0)  # trade_mode, leverage, limit_orders, margin_so_mode
    body += struct.pack("<ii", 1, 1)  # trade_allowed, trade_expert
    body += struct.pack("<ddddddd", 10000.0, 500.0, 250.0, 10750.0, 200.0, 10550.0, 5375.0)
    body += struct.pack("<dddd", 50.0, 30.0, 100.0, 50.0)  # margin_so_call/so, initial, maint
    body += struct.pack("<ddd", 0.0, 0.0, 0.0)  # assets, liabilities, commission_blocked
    body += encode_utf16le("Test User", 64)
    body += encode_utf16le("MetaQuotes-Demo", 128)
    body += encode_utf16le("USD", 32)
    body += encode_utf16le("MetaQuotes", 64)
    vals = SeriesCodec.parse(body, ACCOUNT_BASE_SCHEMA)
    d = dict(zip(ACCOUNT_BASE_FIELD_NAMES, vals))
    assert d["login"] == 12345678
    assert d["leverage"] == 100
    assert d["balance"] == 10000.0
    assert d["equity"] == 10750.0
    assert d["margin"] == 200.0
    assert d["currency"] == "USD"
    assert d["server"] == "MetaQuotes-Demo"
    assert d["company"] == "MetaQuotes"


def test_spread_schema_roundtrip():
    from pymt5.helpers import encode_utf16le

    body = struct.pack("<I", 1)  # count = 1
    body += struct.pack("<II", 42, 0)  # spread_id, flags
    body += encode_utf16le("EURUSD", 64)
    body += struct.pack("<II", 0, 0)
    body += struct.pack("<d", 1.5)  # spread_value
    records = _parse_counted_records(body, SPREAD_SCHEMA, SPREAD_FIELD_NAMES)
    assert len(records) == 1
    assert records[0]["spread_id"] == 42
    assert records[0]["trade_symbol"] == "EURUSD"
    assert records[0]["spread_value"] == 1.5


def test_corporate_link_roundtrip():
    from pymt5.helpers import encode_utf16le

    body = struct.pack("<I", 1)  # count
    body += struct.pack("<I", 1)  # link_type
    body += encode_utf16le("https://support.example.com", 512)
    body += encode_utf16le("Support", 512)
    body += struct.pack("<I", 0)  # flags
    body += b"\x00" * 256  # icon_data
    records = _parse_counted_records(body, CORPORATE_LINK_SCHEMA, CORPORATE_LINK_FIELD_NAMES)
    assert len(records) == 1
    assert records[0]["link_type"] == 1
    assert records[0]["url"] == "https://support.example.com"
    assert records[0]["label"] == "Support"


def test_book_level_roundtrip():
    body = struct.pack("<dq", 1.12345, 500000)
    vals = SeriesCodec.parse(body, BOOK_LEVEL_SCHEMA)
    d = dict(zip(BOOK_LEVEL_FIELD_NAMES, vals))
    assert d["price"] == 1.12345
    assert d["volume"] == 500000


def test_trade_update_balance_roundtrip():
    body = struct.pack("<dddddd", 10000.0, 500.0, 250.0, 10750.0, 200.0, 10550.0)
    vals = SeriesCodec.parse(body, TRADE_UPDATE_BALANCE_SCHEMA)
    d = dict(zip(TRADE_UPDATE_BALANCE_FIELD_NAMES, vals))
    assert d["balance"] == 10000.0
    assert d["credit"] == 500.0
    assert d["equity"] == 10750.0
    assert d["margin"] == 200.0
    assert d["margin_free"] == 10550.0


def test_trade_transaction_roundtrip():
    body = struct.pack("<III", 2, 99, 0)
    vals = SeriesCodec.parse(body, TRADE_TRANSACTION_SCHEMA)
    d = dict(zip(TRADE_TRANSACTION_FIELD_NAMES, vals))
    assert d["flag_mask"] == 2
    assert d["transaction_id"] == 99
    assert d["transaction_type"] == 0  # add


# ---- subscribe_book_by_name validation ----


def test_subscribe_book_by_name_missing_raises():
    client = MT5WebClient()
    import asyncio

    with pytest.raises(ValueError, match="symbols not found in cache"):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.subscribe_book_by_name(["NOSYMBOL"]))
        finally:
            loop.close()


# ---- New CMD constant values ----


def test_new_cmd_constants():
    assert CMD_GET_ACCOUNT == 3
    assert CMD_GET_SYMBOL_GROUPS == 9
    assert CMD_TRADE_UPDATE_PUSH == 10
    assert CMD_ACCOUNT_UPDATE_PUSH == 14
    assert CMD_SYMBOL_DETAILS_PUSH == 17
    assert CMD_TRADE_RESULT_PUSH == 19
    assert CMD_SUBSCRIBE_BOOK == 22
    assert CMD_BOOK_PUSH == 23
    assert CMD_GET_CORPORATE_LINKS == 44
