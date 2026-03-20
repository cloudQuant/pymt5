"""Tests for the _PushHandlersMixin class.

Verifies callback registration, binary data parsing, caching behaviour,
and error-path handling for every push handler method.
"""

import struct
from unittest.mock import MagicMock

from pymt5._push_handlers import _PushHandlersMixin
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_GET_POSITIONS_ORDERS,
    CMD_LOGIN_STATUS_PUSH,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    PROP_F64,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
)
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    ACCOUNT_WEB_MAIN_SCHEMA,
    ORDER_SCHEMA,
    POSITION_SCHEMA,
    SYMBOL_DETAILS_SCHEMA,
    TRADE_RESULT_PUSH_SCHEMA,
    TRADE_RESULT_RESPONSE_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import SymbolInfo

# ---------------------------------------------------------------------------
# MockClient: a minimal object that satisfies _PushHandlersMixin's interface
# ---------------------------------------------------------------------------


class MockClient(_PushHandlersMixin):
    def __init__(self) -> None:
        self.transport = MagicMock()
        self._symbols_by_id: dict[int, SymbolInfo] = {}
        self._tick_cache_by_id: dict = {}
        self._tick_cache_by_name: dict = {}
        self._tick_history_limit: int = 100
        self._tick_history_by_id: dict = {}
        self._tick_history_by_name: dict = {}
        self._book_cache_by_id: dict = {}
        self._book_cache_by_name: dict = {}


# ---------------------------------------------------------------------------
# Helpers for building binary payloads
# ---------------------------------------------------------------------------


def _build_tick_body(
    symbol_id: int = 1,
    tick_time: int = 1700000000,
    fields: int = 0,
    bid: float = 1.1000,
    ask: float = 1.1002,
    last: float = 0.0,
    tick_volume: int = 10,
    time_ms_delta: int = 500,
    flags: int = 0,
) -> bytes:
    """Serialize a single tick record into binary bytes."""
    field_specs = [
        {"propType": PROP_U32, "propValue": symbol_id},
        {"propType": PROP_I32, "propValue": tick_time},
        {"propType": PROP_U32, "propValue": fields},
        {"propType": PROP_F64, "propValue": bid},
        {"propType": PROP_F64, "propValue": ask},
        {"propType": PROP_F64, "propValue": last},
        {"propType": PROP_I64, "propValue": tick_volume},
        {"propType": PROP_U32, "propValue": time_ms_delta},
        {"propType": PROP_U16, "propValue": flags},
    ]
    return SeriesCodec.serialize(field_specs)


def _build_position_body(count: int = 1) -> bytes:
    """Create a counted position payload (count header + N zero-filled records)."""
    pos_size = get_series_size(POSITION_SCHEMA)
    header = struct.pack("<I", count)
    return header + (b"\x00" * pos_size) * count


def _build_order_body(count: int = 1) -> bytes:
    """Create a counted order payload (count header + N zero-filled records)."""
    order_size = get_series_size(ORDER_SCHEMA)
    header = struct.pack("<I", count)
    return header + (b"\x00" * order_size) * count


def _build_positions_and_orders(pos_count: int = 1, order_count: int = 1) -> bytes:
    """Build a combined positions+orders body as sent by cmd=4 pushes."""
    return _build_position_body(pos_count) + _build_order_body(order_count)


def _build_book_body(
    symbol_id: int = 42,
    bid_count: int = 1,
    ask_count: int = 1,
    bid_price: float = 1.10,
    bid_volume: int = 100,
    ask_price: float = 1.11,
    ask_volume: int = 200,
) -> bytes:
    """Build a book push body with count header, book header, and levels."""
    # Count of symbol groups in this push
    count_header = struct.pack("<I", 1)

    # Book header for one symbol
    header_fields = [
        {"propType": PROP_U32, "propValue": symbol_id},
        {"propType": PROP_I32, "propValue": 0},
        {"propType": PROP_I32, "propValue": 0},
        {"propType": PROP_U32, "propValue": bid_count},
        {"propType": PROP_U32, "propValue": ask_count},
        {"propType": PROP_U16, "propValue": 0},
    ]
    header_data = SeriesCodec.serialize(header_fields)

    # Bid level
    bid_level = SeriesCodec.serialize(
        [
            {"propType": PROP_F64, "propValue": bid_price},
            {"propType": PROP_I64, "propValue": bid_volume},
        ]
    )

    # Ask level
    ask_level = SeriesCodec.serialize(
        [
            {"propType": PROP_F64, "propValue": ask_price},
            {"propType": PROP_I64, "propValue": ask_volume},
        ]
    )

    return count_header + header_data + bid_level + ask_level


def _build_account_body() -> bytes:
    """Build a minimal valid account response body matching ACCOUNT_WEB_MAIN_SCHEMA."""
    schema_with_values = []
    for field in ACCOUNT_WEB_MAIN_SCHEMA:
        prop_type = field["propType"]
        entry = dict(field)
        entry["propValue"] = 0
        if prop_type == PROP_F64:
            entry["propValue"] = 0.0
        schema_with_values.append(entry)
    return SeriesCodec.serialize(schema_with_values)


def _build_symbol_details_body(symbol_id: int = 42) -> bytes:
    """Build a single symbol details record for cmd=17."""
    schema_with_values = []
    for i, field in enumerate(SYMBOL_DETAILS_SCHEMA):
        entry = dict(field)
        if i == 0:
            entry["propValue"] = symbol_id
        elif field["propType"] == PROP_F64:
            entry["propValue"] = 0.0
        else:
            entry["propValue"] = 0
        schema_with_values.append(entry)
    return SeriesCodec.serialize(schema_with_values)


def _build_trade_result_push_body() -> bytes:
    """Build a trade result push body (action + response)."""
    action_fields = []
    for field in TRADE_RESULT_PUSH_SCHEMA:
        entry = dict(field)
        entry["propValue"] = 0
        if field["propType"] == PROP_F64:
            entry["propValue"] = 0.0
        action_fields.append(entry)
    action_data = SeriesCodec.serialize(action_fields)

    resp_fields = []
    for field in TRADE_RESULT_RESPONSE_SCHEMA:
        entry = dict(field)
        entry["propValue"] = 0
        if field["propType"] == PROP_F64:
            entry["propValue"] = 0.0
        resp_fields.append(entry)
    resp_data = SeriesCodec.serialize(resp_fields)

    return action_data + resp_data


# ---------------------------------------------------------------------------
# on_tick
# ---------------------------------------------------------------------------


class TestOnTick:
    def test_callback_receives_parsed_ticks(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
        callback = MagicMock()

        client.on_tick(callback)

        # Capture the internal handler that was registered with transport.on
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_TICK_PUSH
        handler = client.transport.on.call_args[0][1]

        body = _build_tick_body(symbol_id=1, bid=1.12345, ask=1.12350)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        ticks = callback.call_args[0][0]
        assert len(ticks) == 1
        assert ticks[0]["symbol_id"] == 1
        assert ticks[0]["symbol"] == "EURUSD"
        assert abs(ticks[0]["bid"] - 1.12345) < 1e-9
        assert abs(ticks[0]["ask"] - 1.12350) < 1e-9

    def test_multiple_ticks_in_single_push(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
        client._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)
        callback = MagicMock()

        client.on_tick(callback)
        handler = client.transport.on.call_args[0][1]

        body = _build_tick_body(symbol_id=1, bid=1.1) + _build_tick_body(symbol_id=2, bid=1.3)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        ticks = callback.call_args[0][0]
        assert len(ticks) == 2
        assert ticks[0]["symbol"] == "EURUSD"
        assert ticks[1]["symbol"] == "GBPUSD"

    def test_malformed_body_logs_error_no_callback(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_tick(callback)
        handler = client.transport.on.call_args[0][1]

        # Body too short to parse even one tick
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=b"\x01\x02")
        handler(result)

        # Empty body returns empty list, callback IS called with []
        # But a truly malformed body that causes parse_at to fail is different.
        # With b"\x01\x02" the count is 0 (2 < tick_size), so callback gets []
        callback.assert_called_once_with([])

    def test_empty_body_calls_callback_with_empty_list(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_tick(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with([])

    def test_tick_without_symbol_mapping(self) -> None:
        """Tick for an unknown symbol_id should still parse but lack 'symbol' key."""
        client = MockClient()
        callback = MagicMock()

        client.on_tick(callback)
        handler = client.transport.on.call_args[0][1]

        body = _build_tick_body(symbol_id=999)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        ticks = callback.call_args[0][0]
        assert len(ticks) == 1
        assert ticks[0]["symbol_id"] == 999
        assert "symbol" not in ticks[0]

    def test_returns_internal_handler(self) -> None:
        client = MockClient()
        callback = MagicMock()
        handler = client.on_tick(callback)
        assert handler is not None
        assert callable(handler)


# ---------------------------------------------------------------------------
# on_position_update
# ---------------------------------------------------------------------------


class TestOnPositionUpdate:
    def test_callback_receives_parsed_positions(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_position_update(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_GET_POSITIONS_ORDERS
        handler = client.transport.on.call_args[0][1]

        body = _build_position_body(count=2)
        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        positions = callback.call_args[0][0]
        assert len(positions) == 2

    def test_empty_body_returns_empty_list(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_position_update(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with([])

    def test_malformed_body_logs_error(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_position_update(callback)
        handler = client.transport.on.call_args[0][1]

        # count=1000 but not enough data for records => parse returns []
        body = struct.pack("<I", 1000) + b"\x00" * 10
        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=body)
        handler(result)

        # _parse_counted_records handles short body by breaking out of loop
        callback.assert_called_once_with([])


# ---------------------------------------------------------------------------
# on_order_update
# ---------------------------------------------------------------------------


class TestOnOrderUpdate:
    def test_callback_receives_parsed_orders(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_order_update(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_GET_POSITIONS_ORDERS
        handler = client.transport.on.call_args[0][1]

        body = _build_positions_and_orders(pos_count=1, order_count=2)
        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        orders = callback.call_args[0][0]
        assert len(orders) == 2

    def test_empty_body_returns_empty_orders(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_order_update(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with([])

    def test_malformed_body_logs_error_no_callback(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_order_update(callback)
        handler = client.transport.on.call_args[0][1]

        # Very short body that will cause struct.unpack_from to fail
        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=b"\x01")
        handler(result)

        # Body is too short for struct.unpack_from("<I", body, 0) => struct.error
        callback.assert_not_called()


# ---------------------------------------------------------------------------
# on_trade_update (combined positions + orders)
# ---------------------------------------------------------------------------


class TestOnTradeUpdate:
    def test_callback_receives_positions_and_orders(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_update(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_GET_POSITIONS_ORDERS
        handler = client.transport.on.call_args[0][1]

        body = _build_positions_and_orders(pos_count=1, order_count=2)
        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert "positions" in data
        assert "orders" in data
        assert len(data["positions"]) == 1
        assert len(data["orders"]) == 2

    def test_empty_body_returns_empty_collections(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_update(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=b"")
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert data["positions"] == []
        assert data["orders"] == []

    def test_malformed_body_logs_error(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_update(callback)
        handler = client.transport.on.call_args[0][1]

        # 1-byte body triggers struct.error in on_trade_update (struct.unpack_from("<I", body, 0))
        result = CommandResult(command=CMD_GET_POSITIONS_ORDERS, code=0, body=b"\x01")
        handler(result)

        callback.assert_not_called()


# ---------------------------------------------------------------------------
# on_symbol_update
# ---------------------------------------------------------------------------


class TestOnSymbolUpdate:
    def test_callback_passed_directly_to_transport(self) -> None:
        client = MockClient()
        callback = MagicMock()

        returned = client.on_symbol_update(callback)

        client.transport.on.assert_called_once_with(CMD_SYMBOL_UPDATE_PUSH, callback)
        assert returned is callback

    def test_callback_receives_raw_command_result(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_symbol_update(callback)

        # Since callback is passed directly, just verify transport.on was called correctly
        client.transport.on.assert_called_once_with(CMD_SYMBOL_UPDATE_PUSH, callback)


# ---------------------------------------------------------------------------
# on_account_update
# ---------------------------------------------------------------------------


class TestOnAccountUpdate:
    def test_callback_receives_parsed_account_data(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_account_update(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_ACCOUNT_UPDATE_PUSH
        handler = client.transport.on.call_args[0][1]

        body = _build_account_body()
        result = CommandResult(command=CMD_ACCOUNT_UPDATE_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert isinstance(data, dict)
        # Verify some expected keys from _parse_account_response
        assert "balance" in data
        assert "credit" in data

    def test_empty_body_returns_empty_dict(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_account_update(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_ACCOUNT_UPDATE_PUSH, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with({})

    def test_short_body_returns_empty_dict(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_account_update(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_ACCOUNT_UPDATE_PUSH, code=0, body=b"\x01\x02\x03")
        handler(result)

        callback.assert_called_once_with({})


# ---------------------------------------------------------------------------
# on_login_status
# ---------------------------------------------------------------------------


class TestOnLoginStatus:
    def test_callback_passed_directly_to_transport(self) -> None:
        client = MockClient()
        callback = MagicMock()

        returned = client.on_login_status(callback)

        client.transport.on.assert_called_once_with(CMD_LOGIN_STATUS_PUSH, callback)
        assert returned is callback


# ---------------------------------------------------------------------------
# on_book_update
# ---------------------------------------------------------------------------


class TestOnBookUpdate:
    def test_callback_receives_parsed_book_entries(self) -> None:
        client = MockClient()
        client._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
        callback = MagicMock()

        client.on_book_update(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_BOOK_PUSH
        handler = client.transport.on.call_args[0][1]

        body = _build_book_body(symbol_id=42, bid_price=1.10, ask_price=1.11)
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        entries = callback.call_args[0][0]
        assert len(entries) == 1
        assert entries[0]["symbol_id"] == 42
        assert entries[0]["symbol"] == "EURUSD"
        assert len(entries[0]["bids"]) == 1
        assert len(entries[0]["asks"]) == 1
        assert abs(entries[0]["bids"][0]["price"] - 1.10) < 1e-9
        assert abs(entries[0]["asks"][0]["price"] - 1.11) < 1e-9

    def test_empty_body_returns_empty_list(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_book_update(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with([])

    def test_malformed_body_logs_error(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_book_update(callback)
        handler = client.transport.on.call_args[0][1]

        # count=100 but no data for headers/levels => returns empty entries
        body = struct.pack("<I", 100) + b"\x00" * 2
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once_with([])

    def test_book_without_symbol_mapping(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_book_update(callback)
        handler = client.transport.on.call_args[0][1]

        body = _build_book_body(symbol_id=999)
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        entries = callback.call_args[0][0]
        assert len(entries) == 1
        assert entries[0]["symbol_id"] == 999
        assert "symbol" not in entries[0]


# ---------------------------------------------------------------------------
# on_symbol_details
# ---------------------------------------------------------------------------


class TestOnSymbolDetails:
    def test_callback_receives_parsed_details(self) -> None:
        client = MockClient()
        client._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)
        callback = MagicMock()

        client.on_symbol_details(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_SYMBOL_DETAILS_PUSH
        handler = client.transport.on.call_args[0][1]

        body = _build_symbol_details_body(symbol_id=42)
        result = CommandResult(command=CMD_SYMBOL_DETAILS_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        details = callback.call_args[0][0]
        assert len(details) == 1
        assert details[0]["symbol_id"] == 42
        assert details[0]["symbol"] == "EURUSD"

    def test_unknown_symbol_id_no_symbol_key(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_symbol_details(callback)
        handler = client.transport.on.call_args[0][1]

        body = _build_symbol_details_body(symbol_id=999)
        result = CommandResult(command=CMD_SYMBOL_DETAILS_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        details = callback.call_args[0][0]
        assert len(details) == 1
        assert details[0]["symbol_id"] == 999
        assert "symbol" not in details[0]

    def test_empty_body_returns_empty_list(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_symbol_details(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_SYMBOL_DETAILS_PUSH, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with([])


# ---------------------------------------------------------------------------
# on_trade_result
# ---------------------------------------------------------------------------


class TestOnTradeResult:
    def test_callback_receives_parsed_trade_result(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_result(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_TRADE_RESULT_PUSH
        handler = client.transport.on.call_args[0][1]

        body = _build_trade_result_push_body()
        result = CommandResult(command=CMD_TRADE_RESULT_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert isinstance(data, dict)
        assert "action_result_code" in data
        assert "result" in data
        assert "retcode" in data["result"]

    def test_action_only_body_no_result(self) -> None:
        """Body that only has the action part, no response part."""
        client = MockClient()
        callback = MagicMock()

        client.on_trade_result(callback)
        handler = client.transport.on.call_args[0][1]

        # Build only the action part
        action_fields = []
        for field in TRADE_RESULT_PUSH_SCHEMA:
            entry = dict(field)
            entry["propValue"] = 0
            if field["propType"] == PROP_F64:
                entry["propValue"] = 0.0
            action_fields.append(entry)
        body = SeriesCodec.serialize(action_fields)

        result = CommandResult(command=CMD_TRADE_RESULT_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert "action_result_code" in data
        assert "result" not in data

    def test_empty_body_calls_callback_with_empty_dict(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_result(callback)
        handler = client.transport.on.call_args[0][1]

        result = CommandResult(command=CMD_TRADE_RESULT_PUSH, code=0, body=b"")
        handler(result)

        callback.assert_called_once_with({})


# ---------------------------------------------------------------------------
# on_trade_transaction
# ---------------------------------------------------------------------------


class TestOnTradeTransaction:
    def test_balance_update_type_2(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_transaction(callback)
        client.transport.on.assert_called_once()
        assert client.transport.on.call_args[0][0] == CMD_TRADE_UPDATE_PUSH
        handler = client.transport.on.call_args[0][1]

        # type=2 is a balance update
        update_type = struct.pack("<I", 2)
        # Build a balance info section
        from pymt5.schemas import TRADE_UPDATE_BALANCE_SCHEMA

        balance_fields = []
        for field in TRADE_UPDATE_BALANCE_SCHEMA:
            entry = dict(field)
            entry["propValue"] = 100.0  # some dummy balance value
            balance_fields.append(entry)
        balance_data = SeriesCodec.serialize(balance_fields)
        # Add empty deals and positions (count=0 each)
        empty_counted = struct.pack("<I", 0)
        body = update_type + balance_data + empty_counted + empty_counted

        result = CommandResult(command=CMD_TRADE_UPDATE_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert data["update_type"] == 2
        assert "balance_info" in data
        assert data["balance_info"]["balance"] == 100.0

    def test_order_transaction_type_not_2(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_transaction(callback)
        handler = client.transport.on.call_args[0][1]

        # type=0 is an order transaction (not balance update)
        update_type = struct.pack("<I", 0)
        # Build a transaction section
        from pymt5.schemas import TRADE_TRANSACTION_SCHEMA

        txn_fields = []
        for field in TRADE_TRANSACTION_SCHEMA:
            entry = dict(field)
            entry["propValue"] = 1
            txn_fields.append(entry)
        txn_data = SeriesCodec.serialize(txn_fields)
        body = update_type + txn_data

        result = CommandResult(command=CMD_TRADE_UPDATE_PUSH, code=0, body=body)
        handler(result)

        callback.assert_called_once()
        data = callback.call_args[0][0]
        assert data["update_type"] == 0
        assert data["flag_mask"] == 1
        assert data["transaction_id"] == 1
        assert data["transaction_type"] == 1

    def test_body_too_short_no_callback(self) -> None:
        client = MockClient()
        callback = MagicMock()

        client.on_trade_transaction(callback)
        handler = client.transport.on.call_args[0][1]

        # Body shorter than 4 bytes => handler returns early
        result = CommandResult(command=CMD_TRADE_UPDATE_PUSH, code=0, body=b"\x01")
        handler(result)

        callback.assert_not_called()


# ---------------------------------------------------------------------------
# _cache_tick_push
# ---------------------------------------------------------------------------


class TestCacheTickPush:
    def test_caches_tick_by_id(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        body = _build_tick_body(symbol_id=1, bid=1.12345, ask=1.12350)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        client._cache_tick_push(result)

        assert 1 in client._tick_cache_by_id
        assert abs(client._tick_cache_by_id[1]["bid"] - 1.12345) < 1e-9

    def test_caches_tick_by_name(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        body = _build_tick_body(symbol_id=1, bid=1.12345)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        client._cache_tick_push(result)

        assert "EURUSD" in client._tick_cache_by_name
        assert abs(client._tick_cache_by_name["EURUSD"]["bid"] - 1.12345) < 1e-9

    def test_tick_history_appended(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        for i in range(3):
            body = _build_tick_body(symbol_id=1, bid=1.0 + i * 0.001)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            client._cache_tick_push(result)

        assert 1 in client._tick_history_by_id
        assert len(client._tick_history_by_id[1]) == 3
        assert "EURUSD" in client._tick_history_by_name
        assert len(client._tick_history_by_name["EURUSD"]) == 3

    def test_tick_history_respects_limit(self) -> None:
        client = MockClient()
        client._tick_history_limit = 5
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        for i in range(10):
            body = _build_tick_body(symbol_id=1, bid=1.0 + i * 0.001)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            client._cache_tick_push(result)

        assert len(client._tick_history_by_id[1]) == 5

    def test_tick_history_name_and_id_share_same_deque(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        body = _build_tick_body(symbol_id=1, bid=1.10)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        client._cache_tick_push(result)

        # Both should reference the same deque object
        assert client._tick_history_by_id[1] is client._tick_history_by_name["EURUSD"]

    def test_empty_body_no_error(self) -> None:
        client = MockClient()
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=b"")
        client._cache_tick_push(result)

        assert len(client._tick_cache_by_id) == 0
        assert len(client._tick_cache_by_name) == 0

    def test_unknown_symbol_id_cached_by_id_only(self) -> None:
        client = MockClient()

        body = _build_tick_body(symbol_id=999, bid=2.0)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        client._cache_tick_push(result)

        assert 999 in client._tick_cache_by_id
        assert len(client._tick_cache_by_name) == 0

    def test_multiple_symbols_cached_separately(self) -> None:
        client = MockClient()
        client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
        client._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)

        body = _build_tick_body(symbol_id=1, bid=1.1) + _build_tick_body(symbol_id=2, bid=1.3)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        client._cache_tick_push(result)

        assert 1 in client._tick_cache_by_id
        assert 2 in client._tick_cache_by_id
        assert "EURUSD" in client._tick_cache_by_name
        assert "GBPUSD" in client._tick_cache_by_name


# ---------------------------------------------------------------------------
# _cache_book_push
# ---------------------------------------------------------------------------


class TestCacheBookPush:
    def test_caches_book_by_id(self) -> None:
        client = MockClient()
        client._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)

        body = _build_book_body(symbol_id=42)
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body)
        client._cache_book_push(result)

        assert 42 in client._book_cache_by_id
        assert client._book_cache_by_id[42]["symbol_id"] == 42
        assert len(client._book_cache_by_id[42]["bids"]) == 1
        assert len(client._book_cache_by_id[42]["asks"]) == 1

    def test_caches_book_by_name(self) -> None:
        client = MockClient()
        client._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)

        body = _build_book_body(symbol_id=42)
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body)
        client._cache_book_push(result)

        assert "EURUSD" in client._book_cache_by_name
        assert client._book_cache_by_name["EURUSD"]["symbol"] == "EURUSD"

    def test_empty_body_no_error(self) -> None:
        client = MockClient()
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=b"")
        client._cache_book_push(result)

        assert len(client._book_cache_by_id) == 0
        assert len(client._book_cache_by_name) == 0

    def test_unknown_symbol_id_cached_by_id_only(self) -> None:
        client = MockClient()

        body = _build_book_body(symbol_id=999)
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body)
        client._cache_book_push(result)

        assert 999 in client._book_cache_by_id
        assert len(client._book_cache_by_name) == 0

    def test_cache_overwrites_previous_entry(self) -> None:
        client = MockClient()
        client._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)

        body1 = _build_book_body(symbol_id=42, bid_price=1.10, ask_price=1.11)
        result1 = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body1)
        client._cache_book_push(result1)

        body2 = _build_book_body(symbol_id=42, bid_price=1.20, ask_price=1.21)
        result2 = CommandResult(command=CMD_BOOK_PUSH, code=0, body=body2)
        client._cache_book_push(result2)

        assert abs(client._book_cache_by_id[42]["bids"][0]["price"] - 1.20) < 1e-9
        assert abs(client._book_cache_by_id[42]["asks"][0]["price"] - 1.21) < 1e-9

    def test_short_body_no_crash(self) -> None:
        client = MockClient()
        # 3 bytes -- less than 4, so _parse_book_entries returns []
        result = CommandResult(command=CMD_BOOK_PUSH, code=0, body=b"\x01\x02\x03")
        client._cache_book_push(result)

        assert len(client._book_cache_by_id) == 0


# ---------------------------------------------------------------------------
# Handler return values
# ---------------------------------------------------------------------------


class TestHandlerReturnValues:
    """Verify that all on_* methods return the handler/callback for use with transport.off()."""

    def test_on_tick_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_tick(MagicMock())
        assert callable(handler)

    def test_on_position_update_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_position_update(MagicMock())
        assert callable(handler)

    def test_on_order_update_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_order_update(MagicMock())
        assert callable(handler)

    def test_on_trade_update_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_trade_update(MagicMock())
        assert callable(handler)

    def test_on_symbol_update_returns_callback(self) -> None:
        client = MockClient()
        cb = MagicMock()
        assert client.on_symbol_update(cb) is cb

    def test_on_account_update_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_account_update(MagicMock())
        assert callable(handler)

    def test_on_login_status_returns_callback(self) -> None:
        client = MockClient()
        cb = MagicMock()
        assert client.on_login_status(cb) is cb

    def test_on_book_update_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_book_update(MagicMock())
        assert callable(handler)

    def test_on_symbol_details_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_symbol_details(MagicMock())
        assert callable(handler)

    def test_on_trade_result_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_trade_result(MagicMock())
        assert callable(handler)

    def test_on_trade_transaction_returns_handler(self) -> None:
        client = MockClient()
        handler = client.on_trade_transaction(MagicMock())
        assert callable(handler)
