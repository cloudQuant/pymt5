"""Tests for Phase 15.3, 16.1, 16.2, and 16.3 features.

- Phase 15.3: Symbol Cache TTL
- Phase 16.1: Typed Push Handler Callbacks
- Phase 16.2: Callback Error Isolation
- Phase 16.3: Connection Health Monitoring
"""

from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_RESULT_PUSH,
    PROP_F64,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
)
from pymt5.events import AccountEvent, BookEvent, HealthStatus, TickEvent, TradeResultEvent
from pymt5.protocol import ResponseFrame, SeriesCodec
from pymt5.schemas import (
    ACCOUNT_WEB_MAIN_SCHEMA,
    TRADE_RESULT_PUSH_SCHEMA,
    TRADE_RESULT_RESPONSE_SCHEMA,
)
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState
from pymt5.types import SymbolInfo

# =====================================================================
# Helpers
# =====================================================================


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
    count_header = struct.pack("<I", 1)
    header_fields = [
        {"propType": PROP_U32, "propValue": symbol_id},
        {"propType": PROP_I32, "propValue": 0},
        {"propType": PROP_I32, "propValue": 0},
        {"propType": PROP_U32, "propValue": bid_count},
        {"propType": PROP_U32, "propValue": ask_count},
        {"propType": PROP_U16, "propValue": 0},
    ]
    header_data = SeriesCodec.serialize(header_fields)
    bid_level = SeriesCodec.serialize([
        {"propType": PROP_F64, "propValue": bid_price},
        {"propType": PROP_I64, "propValue": bid_volume},
    ])
    ask_level = SeriesCodec.serialize([
        {"propType": PROP_F64, "propValue": ask_price},
        {"propType": PROP_I64, "propValue": ask_volume},
    ])
    return count_header + header_data + bid_level + ask_level


def _build_account_body() -> bytes:
    """Build a minimal valid account response body."""
    schema_with_values = []
    for field in ACCOUNT_WEB_MAIN_SCHEMA:
        entry = dict(field)
        entry["propValue"] = 0.0 if field["propType"] == PROP_F64 else 0
        schema_with_values.append(entry)
    return SeriesCodec.serialize(schema_with_values)


def _build_trade_result_push_body() -> bytes:
    """Build a trade result push body (action + response)."""
    action_fields = []
    for field in TRADE_RESULT_PUSH_SCHEMA:
        entry = dict(field)
        entry["propValue"] = 0.0 if field["propType"] == PROP_F64 else 0
        action_fields.append(entry)
    action_data = SeriesCodec.serialize(action_fields)

    resp_fields = []
    for field in TRADE_RESULT_RESPONSE_SCHEMA:
        entry = dict(field)
        entry["propValue"] = 0.0 if field["propType"] == PROP_F64 else 0
        resp_fields.append(entry)
    resp_data = SeriesCodec.serialize(resp_fields)
    return action_data + resp_data


def _create_push_handlers_mixin():
    """Create a _PushHandlersMixin instance with all required attributes."""
    from pymt5._push_handlers import _PushHandlersMixin

    obj = object.__new__(_PushHandlersMixin)
    obj.transport = MT5WebSocketTransport(uri="wss://example.com")
    obj._symbols_by_id = {}
    obj._tick_cache_by_id = {}
    obj._tick_cache_by_name = {}
    obj._tick_history_limit = 100
    obj._tick_history_by_id = {}
    obj._tick_history_by_name = {}
    obj._book_cache_by_id = {}
    obj._book_cache_by_name = {}
    obj._typed_tick_handlers = []
    obj._typed_book_handlers = []
    obj._typed_trade_result_handlers = []
    obj._typed_account_handlers = []
    obj._callback_error_handlers = []
    return obj


def _create_market_data_mixin():
    """Create a _MarketDataMixin instance with all required attributes."""
    from pymt5._market_data import _MarketDataMixin

    obj = object.__new__(_MarketDataMixin)
    obj.transport = MagicMock()
    obj._symbols = {}
    obj._symbols_by_id = {}
    obj._full_symbols = {}
    obj._tick_cache_by_id = {}
    obj._tick_cache_by_name = {}
    obj._tick_history_limit = 100
    obj._tick_history_by_id = {}
    obj._tick_history_by_name = {}
    obj._book_cache_by_id = {}
    obj._book_cache_by_name = {}
    obj._subscribed_ids = []
    obj._subscribed_book_ids = []
    obj._symbol_cache_ttl = 0.0
    obj._symbols_loaded_at = 0.0
    return obj


# =====================================================================
# Phase 15.3: Symbol Cache TTL
# =====================================================================


class TestSymbolCacheTTL:
    """Tests for symbol cache TTL feature."""

    def test_client_init_default_ttl(self):
        """symbol_cache_ttl defaults to 0 (no expiry / no cache hit)."""
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._symbol_cache_ttl == 0.0
        assert client._symbols_loaded_at == 0.0

    def test_client_init_custom_ttl(self):
        """Custom TTL value is stored correctly."""
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com", symbol_cache_ttl=300.0)
        assert client._symbol_cache_ttl == 300.0

    def test_client_init_negative_ttl_clamped_to_zero(self):
        """Negative TTL is clamped to 0."""
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com", symbol_cache_ttl=-10.0)
        assert client._symbol_cache_ttl == 0.0

    def test_is_symbol_cache_valid_no_ttl(self):
        """With TTL=0, cache is never valid."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 0
        assert mixin._is_symbol_cache_valid() is False

    def test_is_symbol_cache_valid_not_loaded(self):
        """With TTL>0 but not loaded yet, cache is not valid."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 300.0
        mixin._symbols_loaded_at = 0.0
        assert mixin._is_symbol_cache_valid() is False

    def test_is_symbol_cache_valid_fresh(self):
        """With TTL>0 and recently loaded, cache is valid."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 300.0
        mixin._symbols_loaded_at = time.monotonic()
        assert mixin._is_symbol_cache_valid() is True

    def test_is_symbol_cache_valid_expired(self):
        """With TTL>0 but loaded long ago, cache is expired."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 1.0
        mixin._symbols_loaded_at = time.monotonic() - 5.0
        assert mixin._is_symbol_cache_valid() is False

    def test_invalidate_symbol_cache(self):
        """invalidate_symbol_cache resets _symbols_loaded_at."""
        mixin = _create_market_data_mixin()
        mixin._symbols_loaded_at = time.monotonic()
        mixin.invalidate_symbol_cache()
        assert mixin._symbols_loaded_at == 0.0

    async def test_load_symbols_returns_cached_when_valid(self):
        """When cache is valid, load_symbols returns cached data without fetching."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 300.0
        mixin._symbols_loaded_at = time.monotonic()
        mixin._symbols = {"EURUSD": SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}

        result = await mixin.load_symbols()
        assert "EURUSD" in result
        # get_symbols should NOT have been called
        mixin.transport.send_command.assert_not_called()

    async def test_load_symbols_fetches_when_cache_invalid(self):
        """When cache is expired, load_symbols re-fetches."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 1.0
        mixin._symbols_loaded_at = time.monotonic() - 5.0
        mixin._symbols = {"OLD": SymbolInfo(name="OLD", symbol_id=99, digits=2)}

        # Mock get_symbols to return new data
        mock_result = CommandResult(command=7, code=0, body=b"")
        mixin.transport.send_command = AsyncMock(return_value=mock_result)

        with patch("pymt5._market_data._parse_counted_records", return_value=[]):
            result = await mixin.load_symbols()

        assert result == {}
        assert mixin._symbols_loaded_at > 0

    async def test_load_symbols_fetches_when_no_ttl(self):
        """With TTL=0, load_symbols always fetches."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 0
        mixin._symbols = {"EURUSD": SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}

        mock_result = CommandResult(command=7, code=0, body=b"")
        mixin.transport.send_command = AsyncMock(return_value=mock_result)

        with patch("pymt5._market_data._parse_counted_records", return_value=[]):
            await mixin.load_symbols()

        # Should have fetched (cache was bypassed)
        mixin.transport.send_command.assert_called_once()

    async def test_load_symbols_sets_loaded_at(self):
        """load_symbols sets _symbols_loaded_at after a successful fetch."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 300.0
        assert mixin._symbols_loaded_at == 0.0

        mock_result = CommandResult(command=7, code=0, body=b"")
        mixin.transport.send_command = AsyncMock(return_value=mock_result)

        with patch("pymt5._market_data._parse_counted_records", return_value=[]):
            await mixin.load_symbols()

        assert mixin._symbols_loaded_at > 0

    async def test_load_symbols_fetches_after_invalidation(self):
        """After invalidation, load_symbols fetches even with TTL set."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 300.0
        mixin._symbols_loaded_at = time.monotonic()
        mixin._symbols = {"EURUSD": SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}

        # First call should use cache
        result = await mixin.load_symbols()
        assert "EURUSD" in result

        # Invalidate
        mixin.invalidate_symbol_cache()

        # Now it should fetch
        mock_result = CommandResult(command=7, code=0, body=b"")
        mixin.transport.send_command = AsyncMock(return_value=mock_result)
        with patch("pymt5._market_data._parse_counted_records", return_value=[]):
            result = await mixin.load_symbols()

        mixin.transport.send_command.assert_called_once()

    async def test_load_symbols_fetches_when_cache_empty(self):
        """Even with valid TTL, if cache is empty, fetch is triggered."""
        mixin = _create_market_data_mixin()
        mixin._symbol_cache_ttl = 300.0
        mixin._symbols_loaded_at = time.monotonic()
        mixin._symbols = {}  # empty cache

        mock_result = CommandResult(command=7, code=0, body=b"")
        mixin.transport.send_command = AsyncMock(return_value=mock_result)

        with patch("pymt5._market_data._parse_counted_records", return_value=[]):
            await mixin.load_symbols()

        mixin.transport.send_command.assert_called_once()


# =====================================================================
# Phase 16.1: Typed Push Handler Callbacks
# =====================================================================


class TestTypedTickHandler:
    """Tests for on_tick_event()."""

    def test_on_tick_event_receives_tick_event(self):
        mixin = _create_push_handlers_mixin()
        mixin._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        received = []

        def callback(event: TickEvent):
            received.append(event)

        handler = mixin.on_tick_event(callback)

        body = _build_tick_body(symbol_id=1, bid=1.12345, ask=1.12350)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
        handler(result)

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, TickEvent)
        assert event.symbol_id == 1
        assert event.symbol == "EURUSD"
        assert abs(event.bid - 1.12345) < 1e-9
        assert abs(event.ask - 1.12350) < 1e-9

    def test_on_tick_event_frozen(self):
        mixin = _create_push_handlers_mixin()
        mixin._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)

        received = []
        handler = mixin.on_tick_event(received.append)

        body = _build_tick_body(symbol_id=1, bid=1.1)
        handler(CommandResult(command=CMD_TICK_PUSH, code=0, body=body))

        with pytest.raises(FrozenInstanceError):
            received[0].bid = 999.0  # type: ignore[misc]

    def test_on_tick_event_multiple_ticks(self):
        mixin = _create_push_handlers_mixin()
        mixin._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
        mixin._symbols_by_id[2] = SymbolInfo(name="GBPUSD", symbol_id=2, digits=5)

        received = []
        handler = mixin.on_tick_event(received.append)

        body = _build_tick_body(symbol_id=1, bid=1.1) + _build_tick_body(symbol_id=2, bid=1.3)
        handler(CommandResult(command=CMD_TICK_PUSH, code=0, body=body))

        assert len(received) == 2
        assert received[0].symbol == "EURUSD"
        assert received[1].symbol == "GBPUSD"

    def test_on_tick_event_stored_in_handler_list(self):
        mixin = _create_push_handlers_mixin()
        cb = MagicMock()
        mixin.on_tick_event(cb)
        assert cb in mixin._typed_tick_handlers

    def test_on_tick_event_returns_handler(self):
        mixin = _create_push_handlers_mixin()
        handler = mixin.on_tick_event(MagicMock())
        assert callable(handler)

    def test_on_tick_event_empty_body(self):
        """Empty body should not crash."""
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_tick_event(received.append)
        handler(CommandResult(command=CMD_TICK_PUSH, code=0, body=b""))
        assert received == []

    def test_on_tick_event_malformed_body_no_crash(self):
        """Malformed body logs error but does not crash."""
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_tick_event(received.append)
        # Very short body
        handler(CommandResult(command=CMD_TICK_PUSH, code=0, body=b"\x01\x02"))
        # No events dispatched, but no crash
        assert received == []


class TestTypedBookHandler:
    """Tests for on_book_event()."""

    def test_on_book_event_receives_book_event(self):
        mixin = _create_push_handlers_mixin()
        mixin._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)

        received = []
        handler = mixin.on_book_event(received.append)

        body = _build_book_body(symbol_id=42, bid_price=1.10, ask_price=1.11)
        handler(CommandResult(command=CMD_BOOK_PUSH, code=0, body=body))

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, BookEvent)
        assert event.symbol_id == 42
        assert event.symbol == "EURUSD"
        assert len(event.entries) == 2  # 1 bid + 1 ask

    def test_on_book_event_frozen(self):
        mixin = _create_push_handlers_mixin()
        mixin._symbols_by_id[42] = SymbolInfo(name="EURUSD", symbol_id=42, digits=5)

        received = []
        handler = mixin.on_book_event(received.append)

        body = _build_book_body(symbol_id=42)
        handler(CommandResult(command=CMD_BOOK_PUSH, code=0, body=body))

        with pytest.raises(FrozenInstanceError):
            received[0].symbol = "X"  # type: ignore[misc]

    def test_on_book_event_stored_in_handler_list(self):
        mixin = _create_push_handlers_mixin()
        cb = MagicMock()
        mixin.on_book_event(cb)
        assert cb in mixin._typed_book_handlers

    def test_on_book_event_empty_body(self):
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_book_event(received.append)
        handler(CommandResult(command=CMD_BOOK_PUSH, code=0, body=b""))
        assert received == []


class TestTypedTradeResultHandler:
    """Tests for on_trade_result_event()."""

    def test_on_trade_result_event_receives_event(self):
        mixin = _create_push_handlers_mixin()

        received = []
        handler = mixin.on_trade_result_event(received.append)

        body = _build_trade_result_push_body()
        handler(CommandResult(command=CMD_TRADE_RESULT_PUSH, code=0, body=body))

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, TradeResultEvent)
        assert event.retcode == 0
        assert isinstance(event.raw, dict)

    def test_on_trade_result_event_frozen(self):
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_trade_result_event(received.append)

        body = _build_trade_result_push_body()
        handler(CommandResult(command=CMD_TRADE_RESULT_PUSH, code=0, body=body))

        with pytest.raises(FrozenInstanceError):
            received[0].retcode = 1  # type: ignore[misc]

    def test_on_trade_result_event_stored_in_handler_list(self):
        mixin = _create_push_handlers_mixin()
        cb = MagicMock()
        mixin.on_trade_result_event(cb)
        assert cb in mixin._typed_trade_result_handlers

    def test_on_trade_result_event_empty_body(self):
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_trade_result_event(received.append)
        handler(CommandResult(command=CMD_TRADE_RESULT_PUSH, code=0, body=b""))
        assert len(received) == 1
        # Empty body should produce a TradeResultEvent with defaults
        assert received[0].retcode == 0


class TestTypedAccountHandler:
    """Tests for on_account_event()."""

    def test_on_account_event_receives_event(self):
        mixin = _create_push_handlers_mixin()

        received = []
        handler = mixin.on_account_event(received.append)

        body = _build_account_body()
        handler(CommandResult(command=CMD_ACCOUNT_UPDATE_PUSH, code=0, body=body))

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, AccountEvent)
        assert isinstance(event.balance, float)
        assert isinstance(event.raw, dict)

    def test_on_account_event_frozen(self):
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_account_event(received.append)

        body = _build_account_body()
        handler(CommandResult(command=CMD_ACCOUNT_UPDATE_PUSH, code=0, body=body))

        with pytest.raises(FrozenInstanceError):
            received[0].balance = 999.0  # type: ignore[misc]

    def test_on_account_event_stored_in_handler_list(self):
        mixin = _create_push_handlers_mixin()
        cb = MagicMock()
        mixin.on_account_event(cb)
        assert cb in mixin._typed_account_handlers

    def test_on_account_event_empty_body_no_dispatch(self):
        """Empty body produces empty parse result, no event dispatched."""
        mixin = _create_push_handlers_mixin()
        received = []
        handler = mixin.on_account_event(received.append)
        handler(CommandResult(command=CMD_ACCOUNT_UPDATE_PUSH, code=0, body=b""))
        assert received == []


# =====================================================================
# Phase 16.2: Callback Error Isolation
# =====================================================================


class TestCallbackErrorIsolation:
    """Tests for callback error isolation in transport._dispatch()."""

    async def test_bad_callback_does_not_kill_dispatch(self):
        """A callback that raises should not prevent other callbacks from running."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        received = []

        def good_callback(result: CommandResult) -> None:
            received.append("good")

        def bad_callback(result: CommandResult) -> None:
            raise RuntimeError("I am broken")

        t.on(CMD_TICK_PUSH, bad_callback)
        t.on(CMD_TICK_PUSH, good_callback)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"")
        await t._dispatch(frame)

        # good_callback should have been called even though bad_callback raised
        assert "good" in received

    async def test_callback_error_handler_is_called(self):
        """Error handlers should be notified when a callback fails."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        errors = []

        def error_handler(exc, callback):
            errors.append((exc, callback))

        t._callback_error_handlers.append(error_handler)

        def bad_callback(result: CommandResult) -> None:
            raise ValueError("boom")

        t.on(CMD_TICK_PUSH, bad_callback)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"")
        await t._dispatch(frame)

        assert len(errors) == 1
        assert isinstance(errors[0][0], ValueError)
        assert errors[0][1] is bad_callback

    async def test_error_handler_itself_raising_does_not_crash(self):
        """An error handler that raises should not crash the dispatch."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        def bad_error_handler(exc, callback):
            raise RuntimeError("error handler is also broken")

        t._callback_error_handlers.append(bad_error_handler)

        def bad_callback(result: CommandResult) -> None:
            raise ValueError("original error")

        t.on(CMD_TICK_PUSH, bad_callback)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"")
        # Should not crash
        await t._dispatch(frame)

    async def test_multiple_callbacks_all_run_despite_errors(self):
        """When multiple callbacks are registered, all are attempted."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        call_order = []

        def cb1(result):
            call_order.append(1)

        def cb2(result):
            call_order.append(2)
            raise RuntimeError("cb2 fails")

        def cb3(result):
            call_order.append(3)

        t.on(CMD_TICK_PUSH, cb1)
        t.on(CMD_TICK_PUSH, cb2)
        t.on(CMD_TICK_PUSH, cb3)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"")
        await t._dispatch(frame)

        assert 1 in call_order
        assert 2 in call_order
        assert 3 in call_order

    async def test_pending_future_resolved_despite_callback_error(self):
        """Pending futures should still be resolved even if a callback fails."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        def bad_callback(result):
            raise RuntimeError("broken")

        t.on(CMD_TICK_PUSH, bad_callback)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        t._pending[CMD_TICK_PUSH].append(future)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"")
        await t._dispatch(frame)

        # The future should be resolved
        assert future.done()
        assert future.result().command == CMD_TICK_PUSH

    def test_on_callback_error_registration(self):
        """on_callback_error registers handler on both mixin and transport."""
        mixin = _create_push_handlers_mixin()

        def error_handler(exc, cb):
            pass

        mixin.on_callback_error(error_handler)

        assert error_handler in mixin._callback_error_handlers
        assert error_handler in mixin.transport._callback_error_handlers


# =====================================================================
# Phase 16.3: Connection Health Monitoring
# =====================================================================


class TestHealthStatus:
    """Tests for the HealthStatus dataclass."""

    def test_health_status_creation(self):
        status = HealthStatus(
            state=TransportState.READY,
            ping_latency_ms=15.5,
            last_message_at=12345.0,
            uptime_seconds=3600.0,
            reconnect_count=2,
        )
        assert status.state == TransportState.READY
        assert status.ping_latency_ms == 15.5
        assert status.last_message_at == 12345.0
        assert status.uptime_seconds == 3600.0
        assert status.reconnect_count == 2

    def test_health_status_frozen(self):
        status = HealthStatus(
            state=TransportState.READY,
            ping_latency_ms=10.0,
            last_message_at=None,
            uptime_seconds=0.0,
            reconnect_count=0,
        )
        with pytest.raises(FrozenInstanceError):
            status.ping_latency_ms = 20.0  # type: ignore[misc]

    def test_health_status_none_values(self):
        status = HealthStatus(
            state=TransportState.DISCONNECTED,
            ping_latency_ms=None,
            last_message_at=None,
            uptime_seconds=0.0,
            reconnect_count=0,
        )
        assert status.ping_latency_ms is None
        assert status.last_message_at is None

    def test_health_status_exported(self):
        import pymt5

        assert hasattr(pymt5, "HealthStatus")


class TestHealthCheck:
    """Tests for the health_check() method."""

    async def test_health_check_when_connected(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._connected_at = time.monotonic() - 10.0
        client._reconnect_count = 3

        # Mock transport state and ping
        client.transport._state = TransportState.READY
        client.transport._last_message_at = time.monotonic() - 1.0

        with patch.object(client, "ping", new_callable=AsyncMock):
            status = await client.health_check()

        assert status.state == TransportState.READY
        assert status.ping_latency_ms is not None
        assert status.ping_latency_ms >= 0
        assert status.last_message_at is not None
        assert status.uptime_seconds >= 10.0
        assert status.reconnect_count == 3

    async def test_health_check_when_disconnected(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._connected_at = 0.0
        client._reconnect_count = 0

        # Transport not ready
        client.transport._state = TransportState.DISCONNECTED
        client.transport._last_message_at = 0.0

        status = await client.health_check()

        assert status.state == TransportState.DISCONNECTED
        assert status.ping_latency_ms is None
        assert status.last_message_at is None
        assert status.uptime_seconds == 0.0
        assert status.reconnect_count == 0

    async def test_health_check_ping_failure(self):
        from pymt5.client import MT5WebClient
        from pymt5.exceptions import PyMT5Error

        client = MT5WebClient(uri="wss://example.com")
        client._connected_at = time.monotonic()
        client.transport._state = TransportState.READY

        with patch.object(client, "ping", new_callable=AsyncMock, side_effect=PyMT5Error("ping fail")):
            status = await client.health_check()

        assert status.state == TransportState.READY
        assert status.ping_latency_ms is None

    async def test_health_check_fires_degraded_callback(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._connected_at = time.monotonic()
        client.transport._state = TransportState.READY
        client.transport._last_message_at = time.monotonic()

        degraded_events = []

        def on_degraded(status):
            degraded_events.append(status)

        client.on_health_degraded(on_degraded, threshold_ms=0.001)

        # Mock ping with a tiny sleep to ensure latency > threshold
        async def slow_ping():
            await asyncio.sleep(0.01)

        with patch.object(client, "ping", new_callable=AsyncMock, side_effect=slow_ping):
            status = await client.health_check()

        assert status.ping_latency_ms is not None
        assert status.ping_latency_ms > 0.001
        assert len(degraded_events) == 1
        assert degraded_events[0] is status

    async def test_health_check_no_degraded_callback_when_fast(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._connected_at = time.monotonic()
        client.transport._state = TransportState.READY
        client.transport._last_message_at = time.monotonic()

        degraded_events = []
        client.on_health_degraded(degraded_events.append, threshold_ms=999999.0)

        with patch.object(client, "ping", new_callable=AsyncMock):
            await client.health_check()

        assert degraded_events == []

    async def test_health_degraded_callback_error_handled(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._connected_at = time.monotonic()
        client.transport._state = TransportState.READY
        client.transport._last_message_at = time.monotonic()

        def broken_callback(status):
            raise RuntimeError("broken")

        client.on_health_degraded(broken_callback, threshold_ms=0.001)

        async def slow_ping():
            await asyncio.sleep(0.01)

        with patch.object(client, "ping", new_callable=AsyncMock, side_effect=slow_ping):
            # Should not crash
            status = await client.health_check()

        assert status is not None


class TestTransportLastMessageAt:
    """Tests for _last_message_at tracking in transport recv loop."""

    def test_initial_last_message_at_is_zero(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert t._last_message_at == 0.0

    def test_initial_connected_at_is_zero(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert t._connected_at == 0.0


class TestReconnectCount:
    """Tests for _reconnect_count tracking in client."""

    def test_initial_reconnect_count_is_zero(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._reconnect_count == 0

    async def test_reconnect_count_incremented(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(
            uri="wss://example.com",
            auto_reconnect=True,
            max_reconnect_attempts=3,
            reconnect_delay=0.01,
            max_reconnect_delay=0.05,
        )
        client._login_kwargs = {
            "login": 12345, "password": "test", "url": "",
            "session": 0, "otp": "", "version": 0, "cid": None,
            "lead_cookie_id": 0, "lead_affiliate_site": "",
            "utm_campaign": "", "utm_source": "",
        }
        client._subscribed_ids = []
        client._subscribed_book_ids = []

        with (
            patch.object(client.transport, "close", new_callable=AsyncMock),
            patch.object(client, "login", new_callable=AsyncMock, return_value=("token", 1)),
        ):
            mock_transport = MagicMock()
            mock_transport.connect = AsyncMock()
            mock_transport._on_disconnect = None
            with patch("pymt5.client.MT5WebSocketTransport", return_value=mock_transport):
                await client._reconnect_loop()

        assert client._reconnect_count == 1


class TestConnectedAt:
    """Tests for _connected_at tracking."""

    async def test_connect_sets_connected_at(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._connected_at == 0.0

        with patch.object(client.transport, "connect", new_callable=AsyncMock):
            before = time.monotonic()
            await client.connect()
            after = time.monotonic()

        assert before <= client._connected_at <= after


class TestClientInitNewAttributes:
    """Verify new Phase 16 attributes are initialized in MT5WebClient."""

    def test_typed_handler_lists_initialized(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._typed_tick_handlers == []
        assert client._typed_book_handlers == []
        assert client._typed_trade_result_handlers == []
        assert client._typed_account_handlers == []

    def test_callback_error_handlers_initialized(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._callback_error_handlers == []

    def test_health_degraded_callbacks_initialized(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._health_degraded_callbacks == []
        assert client._health_degraded_threshold_ms == 5000.0
