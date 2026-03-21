"""Tests for new modules added in Phase 2-5.

Covers: events, _rate_limiter, _subscription, _dataframe, _metrics, _logging.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5._logging import get_logger
from pymt5._metrics import MetricsCollector
from pymt5._rate_limiter import TokenBucketRateLimiter
from pymt5._subscription import SubscriptionHandle
from pymt5.events import AccountEvent, BookEvent, TickEvent, TradeResultEvent

# ---------------------------------------------------------------------------
# events.py
# ---------------------------------------------------------------------------


class TestTickEvent:
    def test_creation(self):
        raw = {"bid": 1.1, "ask": 1.2}
        ev = TickEvent(
            symbol_id=1,
            symbol="EURUSD",
            bid=1.1,
            ask=1.2,
            last=1.15,
            volume=100.0,
            timestamp=1234567890.0,
            raw=raw,
        )
        assert ev.symbol_id == 1
        assert ev.symbol == "EURUSD"
        assert ev.bid == 1.1
        assert ev.ask == 1.2
        assert ev.last == 1.15
        assert ev.volume == 100.0
        assert ev.timestamp == 1234567890.0
        assert ev.raw is raw

    def test_frozen(self):
        ev = TickEvent(
            symbol_id=1,
            symbol="EURUSD",
            bid=1.1,
            ask=1.2,
            last=0.0,
            volume=0.0,
            timestamp=0.0,
            raw={},
        )
        with pytest.raises(FrozenInstanceError):
            ev.bid = 2.0  # type: ignore[misc]

    def test_equality(self):
        kwargs = dict(
            symbol_id=1,
            symbol="EURUSD",
            bid=1.1,
            ask=1.2,
            last=0.0,
            volume=0.0,
            timestamp=0.0,
            raw={},
        )
        assert TickEvent(**kwargs) == TickEvent(**kwargs)

    def test_repr(self):
        ev = TickEvent(
            symbol_id=1,
            symbol="X",
            bid=0.0,
            ask=0.0,
            last=0.0,
            volume=0.0,
            timestamp=0.0,
            raw={},
        )
        assert "TickEvent" in repr(ev)
        assert "symbol='X'" in repr(ev)


class TestBookEvent:
    def test_creation(self):
        entries = [{"price": 1.1, "volume": 10}]
        raw = {"entries": entries}
        ev = BookEvent(symbol_id=2, symbol="GBPUSD", entries=entries, raw=raw)
        assert ev.symbol_id == 2
        assert ev.symbol == "GBPUSD"
        assert ev.entries == entries
        assert ev.raw is raw

    def test_frozen(self):
        ev = BookEvent(symbol_id=1, symbol="X", entries=[], raw={})
        with pytest.raises(FrozenInstanceError):
            ev.symbol = "Y"  # type: ignore[misc]


class TestTradeResultEvent:
    def test_creation(self):
        raw = {"retcode": 10009}
        ev = TradeResultEvent(
            retcode=10009,
            order=123,
            deal=456,
            volume=0.01,
            price=1.1,
            comment="ok",
            raw=raw,
        )
        assert ev.retcode == 10009
        assert ev.order == 123
        assert ev.deal == 456
        assert ev.volume == 0.01
        assert ev.price == 1.1
        assert ev.comment == "ok"

    def test_frozen(self):
        ev = TradeResultEvent(
            retcode=0,
            order=0,
            deal=0,
            volume=0.0,
            price=0.0,
            comment="",
            raw={},
        )
        with pytest.raises(FrozenInstanceError):
            ev.retcode = 1  # type: ignore[misc]


class TestAccountEvent:
    def test_creation(self):
        raw = {"balance": 1000.0}
        ev = AccountEvent(
            balance=1000.0,
            equity=1100.0,
            margin=50.0,
            margin_free=1050.0,
            raw=raw,
        )
        assert ev.balance == 1000.0
        assert ev.equity == 1100.0
        assert ev.margin == 50.0
        assert ev.margin_free == 1050.0
        assert ev.raw is raw

    def test_frozen(self):
        ev = AccountEvent(
            balance=0.0,
            equity=0.0,
            margin=0.0,
            margin_free=0.0,
            raw={},
        )
        with pytest.raises(FrozenInstanceError):
            ev.balance = 999.0  # type: ignore[misc]

    def test_equality(self):
        kwargs = dict(
            balance=100.0,
            equity=100.0,
            margin=10.0,
            margin_free=90.0,
            raw={},
        )
        assert AccountEvent(**kwargs) == AccountEvent(**kwargs)


# ---------------------------------------------------------------------------
# _rate_limiter.py
# ---------------------------------------------------------------------------


class TestTokenBucketRateLimiter:
    async def test_disabled_rate_passes_immediately(self):
        limiter = TokenBucketRateLimiter(rate=0, burst=10)
        # Should return immediately without blocking
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

    async def test_negative_rate_passes_immediately(self):
        limiter = TokenBucketRateLimiter(rate=-5.0, burst=10)
        await limiter.acquire()

    async def test_burst_allows_initial_calls(self):
        limiter = TokenBucketRateLimiter(rate=100.0, burst=5)
        # Should be able to acquire burst tokens without waiting
        for _ in range(5):
            await limiter.acquire()
        assert limiter._tokens < 1.0

    async def test_refill_restores_tokens(self):
        limiter = TokenBucketRateLimiter(rate=1000.0, burst=10)
        # Drain all tokens
        for _ in range(10):
            await limiter.acquire()
        # Manually advance time via refill
        limiter._last_refill = time.monotonic() - 1.0  # 1 second ago
        limiter._refill()
        assert limiter._tokens >= 1.0

    async def test_refill_caps_at_burst(self):
        limiter = TokenBucketRateLimiter(rate=1000.0, burst=5)
        # Set last refill far in the past
        limiter._last_refill = time.monotonic() - 100.0
        limiter._refill()
        assert limiter._tokens == 5.0  # capped at burst

    async def test_acquire_waits_when_empty(self):
        limiter = TokenBucketRateLimiter(rate=1000.0, burst=1)
        # Use the one token
        await limiter.acquire()
        # Next call should still complete (high rate = fast refill)
        await asyncio.wait_for(limiter.acquire(), timeout=1.0)

    async def test_initial_state(self):
        limiter = TokenBucketRateLimiter(rate=10.0, burst=20)
        assert limiter.rate == 10.0
        assert limiter.burst == 20
        assert limiter._tokens == 20.0

    async def test_concurrent_acquire_is_safe(self):
        """Verify that concurrent acquire() calls don't corrupt state."""
        limiter = TokenBucketRateLimiter(rate=10000.0, burst=50)
        acquired = 0

        async def worker():
            nonlocal acquired
            for _ in range(10):
                await limiter.acquire()
                acquired += 1

        # Launch 5 concurrent workers, each acquiring 10 tokens
        await asyncio.gather(*(worker() for _ in range(5)))
        assert acquired == 50

    async def test_has_lock_attribute(self):
        limiter = TokenBucketRateLimiter(rate=10.0, burst=20)
        assert hasattr(limiter, "_lock")
        assert isinstance(limiter._lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# _subscription.py
# ---------------------------------------------------------------------------


class TestSubscriptionHandle:
    async def test_ids_returns_copy(self):
        unsub = AsyncMock()
        handle = SubscriptionHandle(ids=[1, 2, 3], unsubscribe_fn=unsub)
        ids = handle.ids
        assert ids == [1, 2, 3]
        ids.append(4)
        assert handle.ids == [1, 2, 3]  # original unmodified

    async def test_active_initially_true(self):
        handle = SubscriptionHandle(ids=[1], unsubscribe_fn=AsyncMock())
        assert handle.active is True

    async def test_unsubscribe_calls_fn(self):
        unsub = AsyncMock()
        handle = SubscriptionHandle(ids=[10, 20], unsubscribe_fn=unsub)
        await handle.unsubscribe()
        unsub.assert_awaited_once_with([10, 20])
        assert handle.active is False

    async def test_unsubscribe_idempotent(self):
        unsub = AsyncMock()
        handle = SubscriptionHandle(ids=[1], unsubscribe_fn=unsub)
        await handle.unsubscribe()
        await handle.unsubscribe()
        await handle.unsubscribe()
        unsub.assert_awaited_once()

    async def test_context_manager_calls_unsubscribe(self):
        unsub = AsyncMock()
        handle = SubscriptionHandle(ids=[5], unsubscribe_fn=unsub)
        async with handle as h:
            assert h is handle
            assert h.active is True
        assert handle.active is False
        unsub.assert_awaited_once_with([5])

    async def test_context_manager_unsubscribes_on_exception(self):
        unsub = AsyncMock()
        handle = SubscriptionHandle(ids=[7], unsubscribe_fn=unsub)
        with pytest.raises(ValueError):
            async with handle:
                raise ValueError("test")
        assert handle.active is False
        unsub.assert_awaited_once()

    async def test_ids_stored_as_copy(self):
        original = [1, 2]
        handle = SubscriptionHandle(ids=original, unsubscribe_fn=AsyncMock())
        original.append(3)
        assert handle.ids == [1, 2]  # not affected by mutation


# ---------------------------------------------------------------------------
# _dataframe.py
# ---------------------------------------------------------------------------


class TestToDataFrame:
    def test_converts_records_to_dataframe(self):
        pd = pytest.importorskip("pandas")
        from pymt5._dataframe import to_dataframe

        records = [
            {"time": 1000, "open": 1.1, "close": 1.2},
            {"time": 2000, "open": 1.2, "close": 1.3},
        ]
        df = to_dataframe(records)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == ["time", "open", "close"]

    def test_empty_records(self):
        pd = pytest.importorskip("pandas")
        from pymt5._dataframe import to_dataframe

        df = to_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_raises_import_error_when_pandas_missing(self):
        with patch.dict("sys.modules", {"pandas": None}):
            # Force reimport
            import importlib

            from pymt5 import _dataframe

            importlib.reload(_dataframe)
            with pytest.raises(ImportError, match="pandas is required"):
                _dataframe.to_dataframe([{"a": 1}])
            # Restore
            importlib.reload(_dataframe)


# ---------------------------------------------------------------------------
# _metrics.py
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    def test_protocol_is_runtime_checkable(self):
        assert isinstance(MetricsCollector, type)

    def test_concrete_impl_satisfies_protocol(self):
        class MyMetrics:
            def on_command_sent(self, command: int) -> None:
                pass

            def on_command_received(self, command: int, code: int) -> None:
                pass

            def on_connect(self) -> None:
                pass

            def on_disconnect(self, reason: str) -> None:
                pass

            def on_reconnect_attempt(self, attempt: int) -> None:
                pass

            def on_reconnect_success(self, attempt: int) -> None:
                pass

        assert isinstance(MyMetrics(), MetricsCollector)

    def test_incomplete_impl_does_not_satisfy(self):
        class Incomplete:
            def on_connect(self) -> None:
                pass

        assert not isinstance(Incomplete(), MetricsCollector)

    def test_mock_satisfies_protocol(self):
        mock = MagicMock(spec=MetricsCollector)
        mock.on_command_sent(42)
        mock.on_command_sent.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# _logging.py
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_stdlib_logger_without_structlog(self):
        import logging

        with patch.dict("sys.modules", {"structlog": None}):
            import importlib

            from pymt5 import _logging

            importlib.reload(_logging)
            logger = _logging.get_logger("test.name")
            assert isinstance(logger, logging.Logger)
            assert logger.name == "test.name"
            importlib.reload(_logging)

    def test_returns_logger_with_valid_name(self):
        logger = get_logger("pymt5.test")
        assert logger is not None

    def test_log_level_env_var(self):
        import importlib
        import logging

        with patch.dict("sys.modules", {"structlog": None}), patch.dict("os.environ", {"PYMT5_LOG_LEVEL": "DEBUG"}):
            from pymt5 import _logging

            importlib.reload(_logging)
            logger = _logging.get_logger("test.loglevel")
            assert isinstance(logger, logging.Logger)
            assert logger.level == logging.DEBUG
            importlib.reload(_logging)

    def test_log_level_env_var_invalid(self):
        import importlib
        import logging

        with (
            patch.dict("sys.modules", {"structlog": None}),
            patch.dict("os.environ", {"PYMT5_LOG_LEVEL": "NOTAVALIDLEVEL"}),
        ):
            from pymt5 import _logging

            importlib.reload(_logging)
            logger = _logging.get_logger("test.invalid")
            assert isinstance(logger, logging.Logger)
            # Level should not change for invalid values
            importlib.reload(_logging)


# ---------------------------------------------------------------------------
# __init__.py exports
# ---------------------------------------------------------------------------


class TestExports:
    def test_event_classes_exported(self):
        import pymt5

        assert hasattr(pymt5, "TickEvent")
        assert hasattr(pymt5, "BookEvent")
        assert hasattr(pymt5, "TradeResultEvent")
        assert hasattr(pymt5, "AccountEvent")

    def test_subscription_handle_exported(self):
        import pymt5

        assert hasattr(pymt5, "SubscriptionHandle")

    def test_to_dataframe_exported(self):
        import pymt5

        assert hasattr(pymt5, "to_dataframe")

    def test_transport_state_exported(self):
        import pymt5

        assert hasattr(pymt5, "TransportState")
