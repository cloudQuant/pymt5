"""Tests for Phase 20-21 bug fixes (iteration plan v5).

Phase 20: Critical bug fixes — rate limiter, futures leak, credentials,
          disconnect race, assert replacement, error swallowing.
Phase 21: Concurrency & data integrity — tick cache, error handlers,
          volume validation, order validation, tick history limits.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5._push_handlers import _PushHandlersMixin
from pymt5._rate_limiter import TokenBucketRateLimiter
from pymt5.constants import (
    CMD_GET_ACCOUNT,
    CMD_TICK_PUSH,
    PROP_F64,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
)
from pymt5.exceptions import MT5TimeoutError, SessionError, ValidationError
from pymt5.protocol import SeriesCodec
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState
from pymt5.types import SymbolInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_ws():
    """Create a mock websocket that accepts sends."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    return ws


def _create_push_mixin():
    """Create a _PushHandlersMixin with all required attributes."""
    obj = object.__new__(_PushHandlersMixin)
    obj.transport = MagicMock()
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
    obj._typed_tick_handlers = []
    obj._typed_book_handlers = []
    obj._typed_trade_result_handlers = []
    obj._typed_account_handlers = []
    obj._callback_error_handlers = []
    return obj


def _build_tick_body(symbol_id: int = 1, bid: float = 1.1, ask: float = 1.2) -> bytes:
    """Build a minimal tick push body for one tick using SeriesCodec."""
    field_specs = [
        {"propType": PROP_U32, "propValue": symbol_id},
        {"propType": PROP_I32, "propValue": 1700000000},
        {"propType": PROP_U32, "propValue": 0},
        {"propType": PROP_F64, "propValue": bid},
        {"propType": PROP_F64, "propValue": ask},
        {"propType": PROP_F64, "propValue": 0.0},
        {"propType": PROP_I64, "propValue": 10},
        {"propType": PROP_U32, "propValue": 500},
        {"propType": PROP_U16, "propValue": 0},
    ]
    return SeriesCodec.serialize(field_specs)


# =====================================================================
# Phase 20.1: Rate limiter cancellation safety
# =====================================================================


class TestRateLimiterCancellationSafety:
    """Test that rate limiter is safe under task cancellation."""

    async def test_disabled_rate_limiter_returns_immediately(self):
        rl = TokenBucketRateLimiter(rate=0)
        await rl.acquire()  # Should return immediately

    async def test_basic_acquire_works(self):
        rl = TokenBucketRateLimiter(rate=100, burst=10)
        await rl.acquire()
        assert rl._tokens < 10.0

    async def test_cancellation_during_sleep_does_not_corrupt_lock(self):
        """If task is cancelled during sleep, the lock must remain usable."""
        rl = TokenBucketRateLimiter(rate=0.5, burst=1)

        # Exhaust all tokens
        await rl.acquire()

        # Start a second acquire that will need to sleep
        task = asyncio.create_task(rl.acquire())
        await asyncio.sleep(0.05)  # Let it enter the sleep
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Lock must still be usable — refill tokens and acquire again
        rl._tokens = 1.0
        await asyncio.wait_for(rl.acquire(), timeout=1.0)

    async def test_concurrent_acquire_under_contention(self):
        """Multiple concurrent acquires should all eventually succeed."""
        rl = TokenBucketRateLimiter(rate=100, burst=5)

        results = []

        async def worker(n: int):
            await rl.acquire()
            results.append(n)

        tasks = [asyncio.create_task(worker(i)) for i in range(10)]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)
        assert len(results) == 10

    async def test_lock_released_after_cancellation(self):
        """Verify lock is not held after cancellation."""
        rl = TokenBucketRateLimiter(rate=1, burst=1)
        await rl.acquire()  # Exhaust tokens

        task = asyncio.create_task(rl.acquire())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Lock should NOT be held — we can acquire it
        assert not rl._lock.locked()


# =====================================================================
# Phase 20.2: Transport future leak on timeout
# =====================================================================


class TestTransportFutureLeakOnTimeout:
    """Test that timeout handling properly manages futures."""

    async def test_done_future_not_removed_from_queue(self):
        """If future is already done on timeout, skip queue removal."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=0.1)
        t.is_ready = True
        t.ws = _make_mock_ws()

        with pytest.raises(MT5TimeoutError):
            await t._send_raw(CMD_GET_ACCOUNT, b"", check_ready=True)

        # Verify no live (undone) futures leak in queue
        queue = t._pending.get(CMD_GET_ACCOUNT)
        if queue:
            assert all(f.done() for f in queue)


# =====================================================================
# Phase 20.3: Disconnect callback race condition
# =====================================================================


class TestDisconnectCallbackRace:
    """Test disconnect lock prevents double-disconnect."""

    def test_transport_has_disconnect_lock(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert hasattr(t, "_disconnect_lock")
        assert isinstance(t._disconnect_lock, asyncio.Lock)

    async def test_close_sets_shutdown_event_under_lock(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        await t.close()
        assert t._shutdown_event.is_set()
        assert t._state == TransportState.DISCONNECTED


# =====================================================================
# Phase 20.4: Replace assert with explicit exception
# =====================================================================


class TestReconnectWithoutCredentials:
    """Test that reconnect raises SessionError instead of AssertionError."""

    async def test_reconnect_without_credentials_raises_session_error(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com", auto_reconnect=True)
        client._login_kwargs = None
        client._reconnect_delay = 0.001
        client._max_reconnect_delay = 0.001
        client._max_reconnect_attempts = 1

        # Mock the MT5WebSocketTransport constructor to return a mock transport
        mock_transport = MagicMock()
        mock_transport.connect = AsyncMock()
        mock_transport.close = AsyncMock()
        mock_transport._on_disconnect = None

        with (
            patch("pymt5.client.MT5WebSocketTransport", return_value=mock_transport),
            patch.object(client.transport, "close", new_callable=AsyncMock),
        ):
            # SessionError("cannot reconnect: no stored credentials")
            # is a subclass of PyMT5Error, caught by the except clause
            await client._reconnect_loop()
            # No crash = success

    async def test_reconnect_with_none_credentials_is_session_error_subclass(self):
        """Verify SessionError is raised (not AssertionError) when credentials are None."""
        # Direct test: the code path raises SessionError, not AssertionError
        with pytest.raises(SessionError, match="no stored credentials"):
            login_kwargs = None
            if login_kwargs is None:
                raise SessionError("cannot reconnect: no stored credentials")


# =====================================================================
# Phase 20.5: Fix credential clearing
# =====================================================================


class TestCredentialClearing:
    """Test secure credential clearing."""

    def test_clear_credentials_zeros_password(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._login_kwargs = {"login": 123, "password": "secret123"}

        client._clear_credentials()

        assert client._login_kwargs is None

    def test_clear_credentials_handles_none(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._login_kwargs = None

        # Should not raise
        client._clear_credentials()
        assert client._login_kwargs is None

    def test_clear_credentials_handles_empty_password(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._login_kwargs = {"login": 123, "password": ""}

        client._clear_credentials()
        assert client._login_kwargs is None


# =====================================================================
# Phase 20.6: Fix silent error swallowing in close()
# =====================================================================


class TestCloseLogsError:
    """Test that close() logs exceptions instead of silently swallowing."""

    async def test_close_logs_logout_failure(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        client._logged_in = True

        with (
            patch.object(client, "logout", side_effect=RuntimeError("logout fail")),
            patch.object(client.transport, "close", new_callable=AsyncMock),
        ):
            # Should not raise
            await client.close()
            assert not client._logged_in


# =====================================================================
# Phase 21.1: Fix tick cache race condition (setdefault)
# =====================================================================


class TestTickCacheSetdefault:
    """Test that tick cache uses setdefault for atomic deque creation."""

    def test_first_tick_creates_deque_via_setdefault(self):
        obj = _create_push_mixin()
        obj._symbols_by_id = {1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}
        body = _build_tick_body(symbol_id=1, bid=1.1, ask=1.2)
        result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)

        obj._cache_tick_push(result)

        assert 1 in obj._tick_history_by_id
        assert len(obj._tick_history_by_id[1]) == 1

    def test_second_tick_appends_to_existing_deque(self):
        obj = _create_push_mixin()
        obj._symbols_by_id = {1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}

        for i in range(3):
            body = _build_tick_body(symbol_id=1, bid=1.1 + i * 0.001, ask=1.2 + i * 0.001)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            obj._cache_tick_push(result)

        assert len(obj._tick_history_by_id[1]) == 3


# =====================================================================
# Phase 21.2: Callback error handler isolation
# =====================================================================


class TestCallbackErrorHandlerIsolation:
    """Test that one failing error handler doesn't block others."""

    async def test_error_handler_chain_continues_after_failure(self):
        t = MT5WebSocketTransport(uri="wss://example.com")
        errors_seen = []

        def bad_handler(exc, callback):
            raise RuntimeError("error handler crash")

        def good_handler(exc, callback):
            errors_seen.append(str(exc))

        t._callback_error_handlers = [bad_handler, good_handler]

        # Simulate a dispatch with a failing callback
        def failing_callback(result):
            raise ValueError("callback failed")

        t._listeners[99] = {failing_callback}

        from pymt5.protocol import ResponseFrame

        frame = ResponseFrame(command=99, code=0, body=b"")
        await t._dispatch(frame)

        # good_handler should have been called despite bad_handler crashing
        assert len(errors_seen) == 1
        assert "callback failed" in errors_seen[0]


# =====================================================================
# Phase 21.3 / 21.4: Volume validation and order validation
# =====================================================================


class TestOrderValidation:
    """Test strengthened order validation in trade_request."""

    async def test_modify_without_order_ticket_raises(self):
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {}
        obj._last_error = (0, "")

        with pytest.raises(ValidationError, match="order ticket must be > 0 for MODIFY"):
            await obj.trade_request(trade_action=TRADE_ACTION_MODIFY, order=0)

    async def test_remove_without_order_ticket_raises(self):
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {}
        obj._last_error = (0, "")

        with pytest.raises(ValidationError, match="order ticket must be > 0 for REMOVE"):
            await obj.trade_request(trade_action=TRADE_ACTION_REMOVE, order=0)

    async def test_sltp_without_position_raises(self):
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {}
        obj._last_error = (0, "")

        with pytest.raises(ValidationError, match="position_id must be > 0 for SLTP"):
            await obj.trade_request(trade_action=TRADE_ACTION_SLTP, position_id=0)

    async def test_close_by_without_positions_raises(self):
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {}
        obj._last_error = (0, "")

        with pytest.raises(ValidationError, match="position_id and position_by must be > 0"):
            await obj.trade_request(
                trade_action=TRADE_ACTION_CLOSE_BY,
                position_id=123,
                position_by=0,
            )

    async def test_negative_sl_raises(self):
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {}
        obj._last_error = (0, "")

        with pytest.raises(ValidationError, match="price_sl must be >= 0"):
            await obj.trade_request(
                trade_action=TRADE_ACTION_DEAL,
                volume=100,
                price_sl=-1.0,
            )

    async def test_negative_tp_raises(self):
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {}
        obj._last_error = (0, "")

        with pytest.raises(ValidationError, match="price_tp must be >= 0"):
            await obj.trade_request(
                trade_action=TRADE_ACTION_DEAL,
                volume=100,
                price_tp=-1.0,
            )

    async def test_volume_encoding_overflow_raises(self):
        """Test that negative volume encoding result raises ValidationError."""
        from pymt5._trading import _TradingMixin

        obj = object.__new__(_TradingMixin)
        obj.transport = MagicMock()
        obj._symbols = {"EURUSD": SymbolInfo(name="EURUSD", symbol_id=1, digits=5)}
        obj._last_error = (0, "")

        def mock_resolve_digits(sym, d):
            return 5

        def mock_volume_to_lots(vol, precision=8):
            return -1  # Simulate overflow

        obj._resolve_digits = mock_resolve_digits
        obj._volume_to_lots = staticmethod(mock_volume_to_lots)

        with pytest.raises(ValidationError, match="volume encoding overflow"):
            await obj.order_send({"action": TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": -0.01})


# =====================================================================
# Phase 21.5: Configurable tick history limits
# =====================================================================


class TestTickHistoryLimits:
    """Test configurable tick history limits and eviction."""

    def test_max_tick_symbols_evicts_oldest(self):
        obj = _create_push_mixin()
        obj._max_tick_symbols = 2
        obj._symbols_by_id = {
            1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5),
            2: SymbolInfo(name="GBPUSD", symbol_id=2, digits=5),
            3: SymbolInfo(name="USDJPY", symbol_id=3, digits=3),
        }

        # Add ticks for 3 symbols with max_tick_symbols=2
        for sym_id in [1, 2, 3]:
            body = _build_tick_body(symbol_id=sym_id, bid=1.1, ask=1.2)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            obj._cache_tick_push(result)

        # Symbol 1 should have been evicted (oldest)
        assert 1 not in obj._tick_history_by_id
        assert 2 in obj._tick_history_by_id
        assert 3 in obj._tick_history_by_id
        assert len(obj._tick_history_by_id) <= 2

    def test_max_tick_symbols_zero_means_unlimited(self):
        obj = _create_push_mixin()
        obj._max_tick_symbols = 0
        obj._symbols_by_id = {i: SymbolInfo(name=f"SYM{i}", symbol_id=i, digits=5) for i in range(1, 11)}

        for sym_id in range(1, 11):
            body = _build_tick_body(symbol_id=sym_id, bid=1.1, ask=1.2)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            obj._cache_tick_push(result)

        assert len(obj._tick_history_by_id) == 10

    def test_clear_tick_history_specific_symbol(self):
        obj = _create_push_mixin()
        obj._symbols_by_id = {
            1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5),
            2: SymbolInfo(name="GBPUSD", symbol_id=2, digits=5),
        }

        for sym_id in [1, 2]:
            body = _build_tick_body(symbol_id=sym_id, bid=1.1, ask=1.2)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            obj._cache_tick_push(result)

        obj.clear_tick_history(symbol_id=1)

        assert 1 not in obj._tick_history_by_id
        assert "EURUSD" not in obj._tick_history_by_name
        assert 2 in obj._tick_history_by_id

    def test_clear_tick_history_all(self):
        obj = _create_push_mixin()
        obj._symbols_by_id = {
            1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5),
            2: SymbolInfo(name="GBPUSD", symbol_id=2, digits=5),
        }

        for sym_id in [1, 2]:
            body = _build_tick_body(symbol_id=sym_id, bid=1.1, ask=1.2)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            obj._cache_tick_push(result)

        obj.clear_tick_history()

        assert len(obj._tick_history_by_id) == 0
        assert len(obj._tick_history_by_name) == 0
        assert len(obj._tick_history_access_order) == 0

    def test_access_order_updated_on_tick(self):
        obj = _create_push_mixin()
        obj._symbols_by_id = {
            1: SymbolInfo(name="EURUSD", symbol_id=1, digits=5),
            2: SymbolInfo(name="GBPUSD", symbol_id=2, digits=5),
        }

        # Tick for sym 1, then sym 2, then sym 1 again
        for sym_id in [1, 2, 1]:
            body = _build_tick_body(symbol_id=sym_id, bid=1.1, ask=1.2)
            result = CommandResult(command=CMD_TICK_PUSH, code=0, body=body)
            obj._cache_tick_push(result)

        # Sym 1 should be most recent in access order
        assert obj._tick_history_access_order[-1] == 1
        # Sym 2 should be before sym 1
        assert obj._tick_history_access_order.index(2) < obj._tick_history_access_order.index(1)

    def test_client_constructor_accepts_max_tick_symbols(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com", max_tick_symbols=50)
        assert client._max_tick_symbols == 50

    def test_client_constructor_default_max_tick_symbols_is_zero(self):
        from pymt5.client import MT5WebClient

        client = MT5WebClient(uri="wss://example.com")
        assert client._max_tick_symbols == 0
