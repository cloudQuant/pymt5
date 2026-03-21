"""Tests to cover the remaining uncovered lines across pymt5 modules.

Targets 39 specific uncovered lines across transport.py, exceptions.py,
_market_data.py, protocol.py, _parsers.py, _push_handlers.py, client.py,
_trading.py, _logging.py, and __init__.py.
"""

from __future__ import annotations

import asyncio
import importlib
import struct
import sys
from collections import deque
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5.constants import (
    CMD_BOOTSTRAP,
    CMD_GET_FULL_SYMBOLS,
    CMD_SUBSCRIBE_BOOK,
    CMD_SUBSCRIBE_TICKS,
    CMD_TICK_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    PROP_BYTES,
    PROP_FIXED_STRING,
    PROP_STRING,
    TRADE_ACTION_PENDING,
    TRADE_RETCODE_INVALID_PRICE,
)
from pymt5.exceptions import ProtocolError, TradeError
from pymt5.protocol import ResponseFrame, SeriesCodec, get_series_size
from pymt5.schemas import (
    ORDER_SCHEMA,
    SYMBOL_DETAILS_SCHEMA,
)
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState

# =====================================================================
# Helper: simple MetricsCollector implementation for testing
# =====================================================================


class FakeMetrics:
    """Concrete MetricsCollector that records all calls."""

    def __init__(self) -> None:
        self.connect_count = 0
        self.disconnect_reasons: list[str] = []
        self.commands_sent: list[int] = []
        self.commands_received: list[tuple[int, int]] = []
        self.reconnect_attempts: list[int] = []
        self.reconnect_successes: list[int] = []

    def on_connect(self) -> None:
        self.connect_count += 1

    def on_disconnect(self, reason: str) -> None:
        self.disconnect_reasons.append(reason)

    def on_command_sent(self, command: int) -> None:
        self.commands_sent.append(command)

    def on_command_received(self, command: int, code: int) -> None:
        self.commands_received.append((command, code))

    def on_reconnect_attempt(self, attempt: int) -> None:
        self.reconnect_attempts.append(attempt)

    def on_reconnect_success(self, attempt: int) -> None:
        self.reconnect_successes.append(attempt)


# =====================================================================
# transport.py — Line 72: state property getter
# =====================================================================


class TestTransportStateProperty:
    def test_state_property_returns_transport_state(self):
        """Cover line 72: return self._state"""
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert t.state == TransportState.DISCONNECTED
        t._state = TransportState.READY
        assert t.state == TransportState.READY
        t._state = TransportState.ERROR
        assert t.state == TransportState.ERROR


# =====================================================================
# transport.py — Lines 119, 166, 202, 209: metrics callbacks
# =====================================================================


class TestTransportMetricsCallbacks:
    async def test_connect_metrics_on_connect(self):
        """Cover line 119: self._metrics.on_connect()"""
        metrics = FakeMetrics()
        t = MT5WebSocketTransport(uri="wss://example.com", metrics=metrics)

        # Simulate a successful connect by mocking websockets.connect and
        # the bootstrap exchange.
        mock_ws = AsyncMock()
        # Bootstrap response: code=0, body with 66+ bytes for token + cipher key
        bootstrap_body = bytes(66) + bytes(16)  # token(64+2) + cipher key

        # mock ws.send to do nothing, mock recv via __aiter__
        mock_ws.send = AsyncMock()
        mock_ws.__aiter__ = AsyncMock(return_value=iter([]))
        mock_ws.close = AsyncMock()

        with patch("pymt5.transport.websockets.connect", return_value=_async_return(mock_ws)):
            # We need the recv_loop to process the bootstrap response.
            # Instead of running the full connect(), we manually orchestrate:
            # 1. Set up the transport state
            # 2. Call connect which needs to exchange bootstrap
            # Let's mock _send_raw to return our bootstrap result.
            bootstrap_result = CommandResult(command=CMD_BOOTSTRAP, code=0, body=bootstrap_body)
            with (
                patch.object(t, "_send_raw", return_value=bootstrap_result),
                patch.object(t, "_recv_loop", return_value=None),
                patch("asyncio.create_task", return_value=MagicMock()),
            ):
                await t.connect()

        assert metrics.connect_count == 1
        assert t.state == TransportState.READY

    async def test_dispatch_metrics_on_command_received(self):
        """Cover line 209: self._metrics.on_command_received(frame.command, frame.code)"""
        metrics = FakeMetrics()
        t = MT5WebSocketTransport(uri="wss://example.com", metrics=metrics)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"")
        await t._dispatch(frame)

        assert len(metrics.commands_received) == 1
        assert metrics.commands_received[0] == (CMD_TICK_PUSH, 0)

    async def test_send_raw_metrics_on_command_sent(self):
        """Cover line 166: self._metrics.on_command_sent(command)"""
        metrics = FakeMetrics()
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=1.0, metrics=metrics)
        t._state = TransportState.READY

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        t.ws = mock_ws

        # Set up a future that will be resolved by _dispatch

        async def resolve_after_send():
            """Resolve the pending future after a small delay."""
            await asyncio.sleep(0.01)
            frame = ResponseFrame(command=CMD_BOOTSTRAP, code=0, body=b"data")
            await t._dispatch(frame)

        task = asyncio.create_task(resolve_after_send())
        result = await t.send_command(CMD_BOOTSTRAP)
        await task

        assert CMD_BOOTSTRAP in metrics.commands_sent
        assert result.body == b"data"

    async def test_recv_loop_disconnect_metrics(self):
        """Cover line 202: self._metrics.on_disconnect(str(exc))"""
        metrics = FakeMetrics()
        t = MT5WebSocketTransport(uri="wss://example.com", metrics=metrics)

        mock_ws = AsyncMock()
        # Simulate a WebSocket disconnect by raising ConnectionClosedError
        import websockets.exceptions

        mock_ws.__aiter__ = MagicMock(side_effect=websockets.exceptions.ConnectionClosedError(None, None))
        t.ws = mock_ws
        t._on_disconnect = None

        await t._recv_loop()

        assert t.state == TransportState.ERROR
        assert len(metrics.disconnect_reasons) == 1


# =====================================================================
# transport.py — Lines 175-176: timeout cleanup (future already removed)
# =====================================================================


class TestTransportTimeoutCleanup:
    async def test_timeout_cleanup_future_already_removed(self):
        """Cover lines 175-176: queue.remove(future) raises ValueError / pass.

        We need the future to time out AND the future to no longer be in the
        pending queue when the cleanup runs. We achieve this by replacing the
        pending deque with a custom subclass that raises ValueError on remove.
        """
        from pymt5.exceptions import MT5TimeoutError

        class RemoveFailsDeque(deque):
            """Deque subclass where remove() always raises ValueError."""

            def remove(self, value):
                raise ValueError("simulated: future already consumed")

        metrics = FakeMetrics()
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=0.01, metrics=metrics)
        t._state = TransportState.READY

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        t.ws = mock_ws

        # Replace the defaultdict factory to produce our custom deque
        class FailingPendingDict(dict):
            """dict subclass that returns RemoveFailsDeque on get."""

            def __missing__(self, key):
                q = RemoveFailsDeque()
                self[key] = q
                return q

            def get(self, key, default=None):
                if key in self:
                    return self[key]
                return default

        t._pending = FailingPendingDict()

        with pytest.raises(MT5TimeoutError):
            await t.send_command(CMD_BOOTSTRAP)


# =====================================================================
# exceptions.py — Lines 47-50: TradeError.__init__ with kwargs
# =====================================================================


class TestTradeErrorInit:
    def test_trade_error_with_keyword_args(self):
        """Cover lines 47-50: TradeError.__init__ with retcode, symbol, action"""
        err = TradeError(
            "order failed",
            retcode=10013,
            symbol="EURUSD",
            action=1,
        )
        assert str(err) == "order failed"
        assert err.retcode == 10013
        assert err.symbol == "EURUSD"
        assert err.action == 1

    def test_trade_error_default_kwargs(self):
        err = TradeError("simple error")
        assert err.retcode == 0
        assert err.symbol == ""
        assert err.action == 0

    def test_trade_error_inherits_from_value_error(self):
        err = TradeError("test", retcode=99)
        assert isinstance(err, ValueError)


# =====================================================================
# protocol.py — Lines 246, 251, 256, 260: parse() error branches
# =====================================================================


class TestProtocolParseErrors:
    def test_parse_fixed_string_no_prop_length_raises(self):
        """Cover line 246: ProtocolError for PROP_FIXED_STRING with no propLength"""
        schema = [{"propType": PROP_FIXED_STRING}]  # missing propLength
        buffer = bytes(64)
        # get_series_size is called first and raises its own error.
        # We must bypass it to reach the parse-specific error on line 246.
        with (
            patch("pymt5.protocol.get_series_size", return_value=0),
            pytest.raises(ProtocolError, match="fixed string requires propLength"),
        ):
            SeriesCodec.parse(buffer, schema)

    def test_parse_bytes_no_prop_length_raises(self):
        """Cover line 251: ProtocolError for PROP_BYTES with no propLength"""
        schema = [{"propType": PROP_BYTES}]  # missing propLength
        buffer = bytes(64)
        with (
            patch("pymt5.protocol.get_series_size", return_value=0),
            pytest.raises(ProtocolError, match="bytes requires propLength"),
        ):
            SeriesCodec.parse(buffer, schema)

    def test_parse_string_no_prop_length_raises(self):
        """Cover line 256: ProtocolError for PROP_STRING with no propLength"""
        schema = [{"propType": PROP_STRING}]  # missing propLength
        buffer = bytes(64)
        with (
            patch("pymt5.protocol.get_series_size", return_value=0),
            pytest.raises(ProtocolError, match="string requires propLength"),
        ):
            SeriesCodec.parse(buffer, schema)

    def test_parse_unsupported_prop_type_raises(self):
        """Cover line 260: NotImplementedError for unsupported prop_type"""
        schema = [{"propType": 999}]  # unsupported type
        buffer = bytes(64)
        with (
            patch("pymt5.protocol.get_series_size", return_value=0),
            pytest.raises(NotImplementedError, match="unsupported propType=999"),
        ):
            SeriesCodec.parse(buffer, schema)


# =====================================================================
# _parsers.py — Line 82: _coerce_timestamp(datetime_value)
# =====================================================================


class TestCoerceTimestamp:
    def test_coerce_timestamp_with_datetime(self):
        """Cover line 82: when passed a datetime, converts to int(timestamp)"""
        from pymt5._parsers import _coerce_timestamp

        dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = _coerce_timestamp(dt)
        assert isinstance(result, int)
        assert result == int(dt.timestamp())

    def test_coerce_timestamp_with_int(self):
        from pymt5._parsers import _coerce_timestamp

        result = _coerce_timestamp(1700000000)
        assert result == 1700000000

    def test_coerce_timestamp_with_float(self):
        from pymt5._parsers import _coerce_timestamp

        result = _coerce_timestamp(1700000000.5)
        assert result == 1700000000


# =====================================================================
# _parsers.py — Lines 455-457: _parse_account_response catch block
# =====================================================================


class TestParseAccountResponseCatchBlock:
    def test_parse_account_response_returns_empty_on_parse_failure(self):
        """Cover lines 455-457: catch block returning {}"""
        from pymt5._parsers import _parse_account_response
        from pymt5.schemas import ACCOUNT_WEB_MAIN_SCHEMA

        # Create a body that is long enough to pass the initial size check
        # but contains garbage data that will cause a parse error
        min_size = get_series_size(ACCOUNT_WEB_MAIN_SCHEMA)
        # Make it large enough but with intentionally corrupted data
        # that will cause an error during the complex parsing logic
        # after initial parse succeeds.
        # We can trigger this by providing a body that's exactly min_size
        # but with data that causes issues in the post-parse processing.
        # The easiest way: patch SeriesCodec.parse to raise
        with patch(
            "pymt5._parsers.SeriesCodec.parse",
            side_effect=ValueError("mocked parse failure"),
        ):
            result = _parse_account_response(bytes(min_size))

        assert result == {}


# =====================================================================
# _parsers.py — Line 494: _parse_rate_bars break
# =====================================================================


class TestParseRateBarsBreak:
    def test_parse_rate_bars_with_exact_body(self):
        """Test _parse_rate_bars with exactly 1 complete bar.

        Note: Line 494 (defensive break guard) is mathematically unreachable
        because count = len(body) // bar_size guarantees
        offset + bar_size <= len(body) for all iterations.
        """
        from pymt5._parsers import _parse_rate_bars
        from pymt5.schemas import RATE_BAR_SCHEMA

        bar_size = get_series_size(RATE_BAR_SCHEMA)
        body = bytes(bar_size)
        result = _parse_rate_bars(body)
        assert len(result) == 1
        assert isinstance(result[0], dict)


# =====================================================================
# _push_handlers.py — Line 200: symbol details break
# =====================================================================


class TestSymbolDetailsBreak:
    async def test_symbol_details_handler_with_one_record(self):
        """Test on_symbol_details handler parses exactly 1 record.

        Note: Line 200 (defensive break guard) is mathematically unreachable
        because count = len(body) // detail_size guarantees
        offset + detail_size <= len(body) for all iterations.
        """
        detail_size = get_series_size(SYMBOL_DETAILS_SCHEMA)
        mixin = _create_push_handlers_mixin()

        received = []

        def callback(details):
            received.extend(details)

        handler = mixin.on_symbol_details(callback)

        body = bytes(detail_size)
        result = CommandResult(command=17, code=0, body=body)
        handler(result)

        assert len(received) == 1


# =====================================================================
# _push_handlers.py — Lines 283-284: trade update push handler
# =====================================================================


class TestTradeUpdatePushHandler:
    async def test_trade_transaction_handler_order_parsing(self):
        """Cover lines 283-284: order data parsing from body"""
        mixin = _create_push_handlers_mixin()

        received = []

        def callback(data):
            received.append(data)

        handler = mixin.on_trade_transaction(callback)

        # Build a body for update_type != 2 (transaction path)
        # The body structure is:
        # [4 bytes: update_type] [TRADE_TRANSACTION_SCHEMA] [ORDER_SCHEMA]
        order_size = get_series_size(ORDER_SCHEMA)

        # update_type = 1 (not balance update)
        update_type = struct.pack("<I", 1)
        # Transaction data: flag_mask=0, transaction_id=42, transaction_type=0
        txn_data = struct.pack("<III", 0, 42, 0)
        # Order data: full order record (all zeros)
        order_data = bytes(order_size)

        body = update_type + txn_data + order_data
        result = CommandResult(command=CMD_TRADE_UPDATE_PUSH, code=0, body=body)
        handler(result)

        assert len(received) == 1
        data = received[0]
        assert data["update_type"] == 1
        assert "order" in data
        assert data["transaction_id"] == 42


# =====================================================================
# _market_data.py — Lines 200-202: get_full_symbol_info catch block
# =====================================================================


class TestGetFullSymbolInfoCatchBlock:
    async def test_get_full_symbol_info_catch_block(self):
        """Cover lines 200-202: catch block when parsing raises error.

        We mock _parse_counted_records to raise a ValueError, which triggers
        the except block on line 200.
        """
        mixin = _create_market_data_mixin()

        # Return a non-empty body so the code enters the try path
        mock_result = CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=b"\x00" * 100)
        mixin.transport.send_command = AsyncMock(return_value=mock_result)

        # Patch _parse_counted_records to raise ValueError inside the try block
        with patch(
            "pymt5._market_data._parse_counted_records",
            side_effect=ValueError("mocked parse error"),
        ):
            result = await mixin.get_full_symbol_info("EURUSD")

        assert result is None


# =====================================================================
# _market_data.py — Lines 398-399, 406-407: subscribe managed
# =====================================================================


class TestSubscribeManaged:
    async def test_subscribe_ticks_managed_returns_handle(self):
        """Cover lines 398-399: subscribe_ticks_managed()"""
        mixin = _create_market_data_mixin()
        mixin.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b"")
        )

        handle = await mixin.subscribe_ticks_managed([100, 200])
        assert handle.active is True
        assert handle.ids == [100, 200]

    async def test_subscribe_book_managed_returns_handle(self):
        """Cover lines 406-407: subscribe_book_managed()"""
        mixin = _create_market_data_mixin()
        mixin.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b"")
        )

        handle = await mixin.subscribe_book_managed([300, 400])
        assert handle.active is True
        assert handle.ids == [300, 400]


# =====================================================================
# client.py — Lines 272, 301: metrics hooks during reconnection
# =====================================================================


class TestClientReconnectMetrics:
    async def test_reconnect_loop_metrics_attempt_and_success(self):
        """Cover lines 272 and 301: metrics.on_reconnect_attempt/success"""
        from pymt5.client import MT5WebClient

        metrics = FakeMetrics()
        client = MT5WebClient(
            uri="wss://example.com",
            auto_reconnect=True,
            max_reconnect_attempts=3,
            reconnect_delay=0.01,
            max_reconnect_delay=0.05,
            metrics=metrics,
        )

        # Store login kwargs to enable reconnect
        client._login_kwargs = {
            "login": 12345,
            "password": "test",
            "url": "",
            "session": 0,
            "otp": "",
            "version": 0,
            "cid": None,
            "lead_cookie_id": 0,
            "lead_affiliate_site": "",
            "utm_campaign": "",
            "utm_source": "",
        }
        client._subscribed_ids = []
        client._subscribed_book_ids = []

        # Mock transport.close, transport.connect, and login
        with (
            patch.object(client.transport, "close", new_callable=AsyncMock),
            patch.object(client, "login", new_callable=AsyncMock, return_value=("token", 1)),
        ):
            # Patch MT5WebSocketTransport constructor to return a mock transport
            mock_transport = MagicMock()
            mock_transport.connect = AsyncMock()
            mock_transport._on_disconnect = None

            with patch("pymt5.client.MT5WebSocketTransport", return_value=mock_transport):
                await client._reconnect_loop()

        # Should have recorded attempt 1 and success 1
        assert 1 in metrics.reconnect_attempts
        assert 1 in metrics.reconnect_successes


# =====================================================================
# _trading.py — Line 599: pending order with price_order <= 0
# =====================================================================


class TestValidateOrderCheckPendingPrice:
    async def test_pending_order_invalid_price_returns_retcode(self):
        """Cover line 599: returns TRADE_RETCODE_INVALID_PRICE for pending + price_order <= 0.

        To reach line 599, _resolve_order_check_price must return a positive
        price (so line 596 doesn't trigger first), but price_order must be <= 0.
        This is possible with BUY_STOP_LIMIT (type=6) where the price comes
        from price_trigger, not price_order.
        """
        mixin = _create_trading_mixin()

        # Symbol info with trade_mode=4 (full), valid volume range
        symbol_info = {
            "trade_mode": 4,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "filling_mode": 0,
            "point": 0.00001,
            "trade_stops_level": 0,
        }
        mixin.symbol_info = AsyncMock(return_value=symbol_info)
        mixin.symbol_info_tick = MagicMock(return_value={"bid": 1.1000, "ask": 1.1001})

        # Request: PENDING action with BUY_STOP_LIMIT (type=6).
        # price_trigger=1.09 makes _resolve_order_check_price return 1.09
        # but price_order=0.0 triggers line 599.
        request = {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": 0.1,
            "type": 6,  # ORDER_TYPE_BUY_STOP_LIMIT
            "price": 1.0900,
            "price_order": 0.0,  # This triggers line 599
            "price_trigger": 1.0900,  # This makes price_check positive
            "sl": 0.0,
            "tp": 0.0,
            "type_filling": 0,
            "type_time": 0,
            "expiration": 0,
            "position": 0,
            "position_by": 0,
            "order": 0,
            "deviation": 0,
            "comment": "",
        }

        result = await mixin._validate_deal_or_pending(request, symbol_info)
        assert result is not None
        retcode, _ = result
        assert retcode == TRADE_RETCODE_INVALID_PRICE


# =====================================================================
# _trading.py — Lines 694-696: trade_request parse failure catch block
# =====================================================================


class TestTradeResponseParseFailure:
    def test_parse_trade_response_extended_parse_failure(self):
        """Cover lines 694-696: catch block for extended trade response parse failure"""
        from pymt5.types import TRADE_RESPONSE_SCHEMA

        mixin = _create_trading_mixin()

        # Build a body that has a valid retcode but garbage extended data
        resp_schema_size = get_series_size(TRADE_RESPONSE_SCHEMA)
        # retcode = 10009 (DONE) followed by garbage
        body = struct.pack("<I", 10009) + bytes(resp_schema_size - 4)

        # Patch SeriesCodec.parse to raise on the extended parse
        with patch(
            "pymt5._trading.SeriesCodec.parse",
            side_effect=struct.error("mocked parse failure"),
        ):
            result = mixin._parse_trade_response(body, "EURUSD", 1, 1000)

        assert result.retcode == 10009
        assert result.success is True
        # Should fallback to basic TradeResult without extended fields


# =====================================================================
# _logging.py — Line 32: structlog branch
# =====================================================================


class TestLoggingStructlogBranch:
    def test_get_logger_with_structlog(self):
        """Cover line 32: return structlog.get_logger(name)"""
        mock_structlog = MagicMock()
        mock_logger = MagicMock()
        mock_structlog.get_logger = MagicMock(return_value=mock_logger)

        # Temporarily inject mock structlog into sys.modules
        original = sys.modules.get("structlog")
        sys.modules["structlog"] = mock_structlog
        try:
            # Reload the module to pick up the mocked structlog
            import pymt5._logging

            importlib.reload(pymt5._logging)
            result = pymt5._logging.get_logger("test.logger")
            mock_structlog.get_logger.assert_called_with("test.logger")
            assert result == mock_logger
        finally:
            # Restore original module state
            if original is None:
                sys.modules.pop("structlog", None)
            else:
                sys.modules["structlog"] = original
            importlib.reload(pymt5._logging)


# =====================================================================
# __init__.py — Lines 10-11: ImportError fallback
# =====================================================================


class TestInitImportErrorFallback:
    def test_version_fallback_on_import_error(self):
        """Cover lines 10-11: ImportError fallback for importlib.metadata.

        We reload pymt5.__init__ with importlib.metadata blocked so the
        except ImportError branch executes and sets __version__ = "1.0.0".
        """
        import pymt5

        # Block importlib.metadata by making it raise ImportError on import
        real_metadata = sys.modules.get("importlib.metadata")
        try:
            # Setting a module to None in sys.modules causes ImportError
            sys.modules["importlib.metadata"] = None  # type: ignore[assignment]
            importlib.reload(pymt5)
            assert pymt5.__version__ == "1.0.0"
        finally:
            # Restore
            if real_metadata is not None:
                sys.modules["importlib.metadata"] = real_metadata
            else:
                sys.modules.pop("importlib.metadata", None)
            importlib.reload(pymt5)


# =====================================================================
# Helpers for creating mixin instances with required attributes
# =====================================================================


def _create_push_handlers_mixin():
    """Create a _PushHandlersMixin instance with all required attributes."""
    from pymt5._push_handlers import _PushHandlersMixin

    obj = object.__new__(_PushHandlersMixin)
    obj.transport = MT5WebSocketTransport(uri="wss://example.com")
    obj._symbols_by_id = {}
    obj._tick_cache_by_id = {}
    obj._tick_cache_by_name = {}
    obj._tick_history_limit = 100
    obj._max_tick_symbols = 0
    obj._tick_history_by_id = {}
    obj._tick_history_by_name = {}
    obj._tick_history_access_order = []
    obj._book_cache_by_id = {}
    obj._book_cache_by_name = {}
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
    obj._max_tick_symbols = 0
    obj._tick_history_by_id = {}
    obj._tick_history_by_name = {}
    obj._tick_history_access_order = []
    obj._book_cache_by_id = {}
    obj._book_cache_by_name = {}
    obj._subscribed_ids = []
    obj._subscribed_book_ids = []
    return obj


def _create_trading_mixin():
    """Create a _TradingMixin instance with all required attributes."""
    from pymt5._trading import _TradingMixin

    obj = object.__new__(_TradingMixin)
    obj.transport = MagicMock()
    obj._symbols = {}
    obj._last_error = (0, "")
    obj._clear_last_error = lambda: setattr(obj, "_last_error", (0, ""))
    obj._fail_last_error = lambda code, msg: (setattr(obj, "_last_error", (code, msg)), None)[1]

    return obj


def _make_response_frame(command: int, code: int, body: bytes) -> bytes:
    """Build a raw decrypted response frame for testing."""
    return bytes(2) + struct.pack("<H", command) + bytes([code]) + body


async def _async_return(value):
    """Helper to create a coroutine that returns a value."""
    return value
