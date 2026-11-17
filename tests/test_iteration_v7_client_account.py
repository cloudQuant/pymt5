"""Iteration 07 regression tests for client lifecycle, connection pool, and account contracts."""

from __future__ import annotations

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.http11 import Response

from pymt5._pool import MT5ConnectionPool, PoolAccount
from pymt5.client import MT5WebClient
from pymt5.constants import (
    CMD_CHANGE_PASSWORD,
    CMD_INIT,
    CMD_LOGIN,
    CMD_OPEN_DEMO,
    CMD_OPEN_REAL,
    CMD_OTP_SETUP,
    CMD_SEND_VERIFY_CODES,
    CMD_VERIFY_CODE,
    PROP_FIXED_STRING,
    PROP_I64,
    PROP_U8,
    PROP_U32,
)
from pymt5.exceptions import MT5ConnectionError, MT5TimeoutError, ProtocolError, SessionError, ValidationError
from pymt5.protocol import SeriesCodec
from pymt5.transport import CommandResult, TransportState
from pymt5.types import AccountOpeningRequest, DemoAccountRequest, RealAccountRequest


def _make_login_body() -> bytes:
    return bytes(160) + (42).to_bytes(8, "little", signed=False)


def _open_account_body(code: int = 0, login: int = 123456) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U32, code),
            (PROP_I64, login),
            (PROP_FIXED_STRING, "demo-pass", 32),
            (PROP_FIXED_STRING, "inv-pass", 32),
        ]
    )


def _verification_body(email: bool = True, phone: bool = False) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U8, int(email)),
            (PROP_U8, int(phone)),
        ]
    )


# ---------------------------------------------------------------------------
# 26.1 support: _book_subscription_lock initialized on MT5WebClient
# ---------------------------------------------------------------------------


def test_client_initializes_book_subscription_lock() -> None:
    client = MT5WebClient()
    assert isinstance(client._book_subscription_lock, asyncio.Lock)
    assert client._book_subscription_lock is not client._tick_subscription_lock


# ---------------------------------------------------------------------------
# 25.6 (S2-09): Metrics hook isolation in MT5WebClient._reconnect_loop
# ---------------------------------------------------------------------------


async def test_reconnect_attempt_metrics_error_does_not_abort_reconnect(caplog) -> None:
    metrics = MagicMock()
    metrics.on_reconnect_attempt.side_effect = RuntimeError("metrics attempt exploded")
    metrics.on_reconnect_success.return_value = None

    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=1,
        reconnect_delay=0.0,
        metrics=metrics,
    )
    client._login_kwargs = {"login": 123, "password": "pw"}
    client.transport.close = AsyncMock()

    new_transport = MagicMock()
    new_transport._listeners = {}
    new_transport._callback_error_handlers = []
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
    )

    caplog.set_level("WARNING", logger="pymt5.client")
    try:
        with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
            await client._reconnect_loop()
    finally:
        client._stop_heartbeat()

    assert client.is_connected is True
    assert client._reconnect_count == 1
    metrics.on_reconnect_attempt.assert_called_once_with(1)
    metrics.on_reconnect_success.assert_called_once_with(1)
    assert "metrics hook on_reconnect_attempt failed" in caplog.text


async def test_reconnect_success_metrics_error_does_not_close_restored_session(caplog) -> None:
    metrics = MagicMock()
    metrics.on_reconnect_attempt.return_value = None
    metrics.on_reconnect_success.side_effect = OSError("metrics backend down")

    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=1,
        reconnect_delay=0.0,
        metrics=metrics,
    )
    client._login_kwargs = {"login": 123, "password": "pw"}
    client.transport.close = AsyncMock()

    new_transport = MagicMock()
    new_transport._listeners = {}
    new_transport._callback_error_handlers = []
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
    )

    caplog.set_level("WARNING", logger="pymt5.client")
    try:
        with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
            await client._reconnect_loop()
    finally:
        client._stop_heartbeat()

    assert client.is_connected is True
    assert client.transport is new_transport
    assert client._reconnect_count == 1
    new_transport.close.assert_not_awaited()
    assert "metrics hook on_reconnect_success failed" in caplog.text


# ---------------------------------------------------------------------------
# 27.1 (S2-01): init_session() serialization and generation revalidation
# ---------------------------------------------------------------------------


async def test_init_session_waits_for_session_lock_and_rejects_stale_generation() -> None:
    client = MT5WebClient()
    client.transport.is_ready = True
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_INIT, code=0, body=b""))

    await client._session_lock.acquire()
    init_task = asyncio.create_task(client.init_session())
    await asyncio.sleep(0)
    client._session_generation += 1
    client._session_lock.release()

    with pytest.raises(SessionError, match="init_session invalidated by close"):
        await init_task
    client.transport.send_command.assert_not_awaited()


async def test_init_session_rejects_when_transport_not_ready() -> None:
    client = MT5WebClient()
    client.transport.is_ready = False
    client._bootstrap_pristine = True
    client.transport.send_command = AsyncMock()

    with pytest.raises(SessionError, match="transport not ready"):
        await client.init_session()
    assert client._bootstrap_pristine is True
    client.transport.send_command.assert_not_awaited()


async def test_init_session_rejects_invalidated_session_after_send() -> None:
    client = MT5WebClient()
    client.transport.is_ready = True
    client.transport.close = AsyncMock()
    sent = asyncio.Event()
    release = asyncio.Event()

    async def slow_send(command, payload=b""):
        sent.set()
        await release.wait()
        return CommandResult(command=command, code=0, body=b"")

    client.transport.send_command = AsyncMock(side_effect=slow_send)
    init_task = asyncio.create_task(client.init_session())
    close_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(sent.wait(), timeout=1.0)
        close_task = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        assert not close_task.done()
    finally:
        release.set()
        if close_task is not None:
            await close_task

    with pytest.raises(SessionError, match="init_session invalidated by close"):
        await init_task


async def test_init_session_rejects_when_close_in_progress() -> None:
    client = MT5WebClient()
    client.transport.is_ready = True
    client._closing = True
    client.transport.send_command = AsyncMock()

    with pytest.raises(SessionError, match="close cleanup is still in progress"):
        await client.init_session()
    client.transport.send_command.assert_not_awaited()


async def test_init_session_success_under_lock() -> None:
    client = MT5WebClient()
    client.transport.is_ready = True
    client._bootstrap_pristine = True
    expected = CommandResult(command=CMD_INIT, code=0, body=b"ok")
    client.transport.send_command = AsyncMock(return_value=expected)

    result = await client.init_session()
    assert result is expected
    assert client._bootstrap_pristine is False


# ---------------------------------------------------------------------------
# 27.2 (S2-02): Foreground connect()/login() vs active _reconnect_task guard
# ---------------------------------------------------------------------------


async def test_foreground_connect_cancels_active_reconnect_task() -> None:
    client = MT5WebClient(auto_reconnect=True, reconnect_delay=10.0)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def active_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    reconnect_task = asyncio.create_task(active_reconnect())
    client._reconnect_task = reconnect_task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.connect = AsyncMock()

        await client.connect()
    finally:
        if not reconnect_task.done():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)

    assert cancelled.is_set()
    assert client._reconnect_task is None
    client.transport.connect.assert_awaited_once()


async def test_foreground_login_cancels_active_reconnect_task() -> None:
    client = MT5WebClient(auto_reconnect=True, reconnect_delay=10.0)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def active_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    reconnect_task = asyncio.create_task(active_reconnect())
    client._reconnect_task = reconnect_task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
        )

        token, session_id = await client.login(login=123, password="pw", auto_heartbeat=False)
    finally:
        if not reconnect_task.done():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)

    assert cancelled.is_set()
    assert client._reconnect_task is None
    assert session_id == 42
    assert len(token) == 320


async def test_reconnect_loop_own_login_does_not_cancel_itself() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0.0)
    client._login_kwargs = {"login": 123, "password": "pw"}
    client.transport.close = AsyncMock()

    new_transport = MagicMock()
    new_transport._listeners = {}
    new_transport._callback_error_handlers = []
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
    )

    try:
        with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
            task = asyncio.create_task(client._reconnect_loop())
            client._reconnect_task = task
            await task
    finally:
        client._stop_heartbeat()

    assert client.is_connected is True
    assert client._reconnect_count == 1


async def test_foreground_connect_clears_already_failed_reconnect_task(caplog) -> None:
    client = MT5WebClient()

    async def failed_reconnect() -> None:
        raise RuntimeError("stale reconnect boom")

    task = asyncio.create_task(failed_reconnect())
    await asyncio.sleep(0)
    assert task.done()
    client._reconnect_task = task
    client.transport.connect = AsyncMock()

    caplog.set_level("WARNING", logger="pymt5.client")
    await client.connect()

    assert client._reconnect_task is None
    assert "completed reconnect task failed" in caplog.text
    client.transport.connect.assert_awaited_once()


async def test_foreground_connect_clears_already_cancelled_reconnect_task() -> None:
    client = MT5WebClient()

    async def cancelled_reconnect() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_reconnect())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.cancelled()
    client._reconnect_task = task
    client.transport.connect = AsyncMock()

    await client.connect()

    assert client._reconnect_task is None
    client.transport.connect.assert_awaited_once()


async def test_foreground_connect_handles_reconnect_task_raising_on_cancel(caplog) -> None:
    client = MT5WebClient(timeout=0.5)
    entered = asyncio.Event()

    async def error_on_cancel() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("reconnect error during cancel") from exc

    reconnect_task = asyncio.create_task(error_on_cancel())
    client._reconnect_task = reconnect_task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.connect = AsyncMock()

        caplog.set_level("WARNING", logger="pymt5.client")
        await client.connect()
    finally:
        if not reconnect_task.done():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)

    assert client._reconnect_task is None
    assert "active reconnect task failed during cancellation" in caplog.text
    client.transport.connect.assert_awaited_once()


async def test_foreground_connect_times_out_waiting_for_uncooperative_reconnect() -> None:
    client = MT5WebClient(timeout=0.02)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def uncooperative_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(uncooperative_reconnect())
    client._reconnect_task = task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.connect = AsyncMock()

        with pytest.raises(SessionError, match="reconnect cleanup is still in progress"):
            await client.connect()

        assert client._reconnect_task is None
        client.transport.connect.assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_foreground_connect_propagates_caller_cancellation_waiting_reconnect() -> None:
    client = MT5WebClient(timeout=1.0)
    entered = asyncio.Event()
    cancel_seen = asyncio.Event()
    release = asyncio.Event()

    async def slow_cancel_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await release.wait()
            raise

    reconnect_task = asyncio.create_task(slow_cancel_reconnect())
    client._reconnect_task = reconnect_task
    connect_task: asyncio.Task[MT5WebClient] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        connect_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(cancel_seen.wait(), timeout=1.0)
        connect_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await connect_task
    finally:
        release.set()
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)
        if not reconnect_task.done():
            reconnect_task.cancel()
        await asyncio.gather(reconnect_task, return_exceptions=True)


async def test_foreground_connect_rejects_concurrent_close_during_reconnect_cancel() -> None:
    client = MT5WebClient(timeout=1.0)
    entered = asyncio.Event()
    cancel_seen = asyncio.Event()
    release = asyncio.Event()

    async def slow_cancel_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await release.wait()
            raise

    reconnect_task = asyncio.create_task(slow_cancel_reconnect())
    client._reconnect_task = reconnect_task
    connect_task: asyncio.Task[MT5WebClient] | None = None
    close_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.connect = AsyncMock()
        client.transport.close = AsyncMock()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(cancel_seen.wait(), timeout=1.0)
        close_task = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        release.set()
        await close_task

        with pytest.raises(SessionError, match="close cleanup is still in progress"):
            await connect_task
        client.transport.connect.assert_not_awaited()
    finally:
        release.set()
        for t in (connect_task, close_task, reconnect_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.gather(
            *(t for t in (connect_task, close_task, reconnect_task) if t is not None),
            return_exceptions=True,
        )


async def test_foreground_connect_rejects_generation_bump_during_reconnect_cancel() -> None:
    client = MT5WebClient(timeout=1.0)
    entered = asyncio.Event()

    async def bump_generation_on_cancel() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            client._session_generation += 1
            raise

    reconnect_task = asyncio.create_task(bump_generation_on_cancel())
    client._reconnect_task = reconnect_task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.connect = AsyncMock()

        with pytest.raises(SessionError, match="connection invalidated by close"):
            await client.connect()
        client.transport.connect.assert_not_awaited()
    finally:
        if not reconnect_task.done():
            reconnect_task.cancel()
        await asyncio.gather(reconnect_task, return_exceptions=True)


async def test_foreground_login_rejects_concurrent_close_during_reconnect_cancel() -> None:
    client = MT5WebClient(timeout=1.0)
    entered = asyncio.Event()
    cancel_seen = asyncio.Event()
    release = asyncio.Event()

    async def slow_cancel_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await release.wait()
            raise

    reconnect_task = asyncio.create_task(slow_cancel_reconnect())
    client._reconnect_task = reconnect_task
    login_task: asyncio.Task[tuple[str, int]] | None = None
    close_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        client.transport.send_command = AsyncMock()
        client.transport.close = AsyncMock()

        login_task = asyncio.create_task(client.login(123, "pw", auto_heartbeat=False))
        await asyncio.wait_for(cancel_seen.wait(), timeout=1.0)
        close_task = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        release.set()
        await close_task

        with pytest.raises(SessionError, match="close cleanup is still in progress"):
            await login_task
        client.transport.send_command.assert_not_awaited()
    finally:
        release.set()
        for t in (login_task, close_task, reconnect_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.gather(
            *(t for t in (login_task, close_task, reconnect_task) if t is not None),
            return_exceptions=True,
        )


# ---------------------------------------------------------------------------
# 27.3 (S2-03): Per-callback health degraded thresholds
# ---------------------------------------------------------------------------


async def test_on_health_degraded_independent_per_callback_thresholds() -> None:
    for reverse_order in (False, True):
        client = MT5WebClient()
        client._connected_at = 100.0
        client.transport._state = TransportState.READY
        client.transport._last_message_at = 100.0

        fast_calls = []
        slow_calls = []

        if reverse_order:
            client.on_health_degraded(slow_calls.append, threshold_ms=50.0)
            client.on_health_degraded(fast_calls.append, threshold_ms=10.0)
        else:
            client.on_health_degraded(fast_calls.append, threshold_ms=10.0)
            client.on_health_degraded(slow_calls.append, threshold_ms=50.0)

        # Latency = 25ms -> triggers 10ms callback only
        clock_25 = iter([100.0, 100.025, 100.025])
        with (
            patch.object(client, "ping", new_callable=AsyncMock),
            patch("pymt5.client.time.monotonic", side_effect=clock_25),
        ):
            await client.health_check()

        assert len(fast_calls) == 1
        assert len(slow_calls) == 0

        # Latency = 100ms -> triggers both callbacks
        clock_100 = iter([200.0, 200.1, 200.1])
        with (
            patch.object(client, "ping", new_callable=AsyncMock),
            patch("pymt5.client.time.monotonic", side_effect=clock_100),
        ):
            await client.health_check()

        assert len(fast_calls) == 2
        assert len(slow_calls) == 1


@pytest.mark.parametrize("threshold_ms", [0, 0.0, -5.0, float("nan"), float("inf"), float("-inf"), True, False, "10"])
def test_on_health_degraded_validates_threshold_ms(threshold_ms) -> None:
    client = MT5WebClient()
    with pytest.raises(ValidationError, match="threshold_ms"):
        client.on_health_degraded(lambda _s: None, threshold_ms=threshold_ms)


# ---------------------------------------------------------------------------
# 27.4 (S2-04): Clear subscription tracking on logout() and close()
# ---------------------------------------------------------------------------


async def test_logout_clears_subscriptions_and_caches_without_cross_session_resubscribe() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0.0)
    client._logged_in = True
    client._login_kwargs = {"login": 1, "password": "old"}
    client._subscribed_ids = [10, 20]
    client._subscribed_book_ids = [30]
    client._tick_cache_by_id[10] = {"bid": 1.1}
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.1}
    client._book_cache_by_id[30] = {"symbol": "EURUSD"}
    client._book_cache_by_name["EURUSD"] = {"symbol": "EURUSD"}

    client.transport.is_ready = True
    client.transport.send_command = AsyncMock(
        side_effect=[
            CommandResult(command=10, code=0, body=b""),  # CMD_LOGOUT
            CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
        ]
    )

    await client.logout()
    assert client._subscribed_ids == []
    assert client._subscribed_book_ids == []
    assert client._tick_cache_by_id == {}
    assert client._tick_cache_by_name == {}
    assert client._book_cache_by_id == {}
    assert client._book_cache_by_name == {}

    await client.login(login=2, password="new", auto_heartbeat=False)

    new_transport = MagicMock()
    new_transport._listeners = {}
    new_transport._callback_error_handlers = []
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
    )
    client.transport.close = AsyncMock()
    client.subscribe_ticks = AsyncMock()
    client.subscribe_book = AsyncMock()

    try:
        with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
            await client._reconnect_loop()
    finally:
        client._stop_heartbeat()

    client.subscribe_ticks.assert_not_awaited()
    client.subscribe_book.assert_not_awaited()


async def test_close_clears_subscriptions_and_caches_while_disconnect_preserves_them() -> None:
    client = MT5WebClient(auto_reconnect=False)
    client._logged_in = True
    client._login_kwargs = {"login": 1, "password": "pw"}
    client._subscribed_ids = [1, 2]
    client._subscribed_book_ids = [3]
    client._tick_cache_by_id[1] = {"bid": 1.0}
    client._book_cache_by_id[3] = {"symbol": "USDJPY"}

    # Unexpected disconnect preserves subscription IDs for reconnect
    client._handle_disconnect()
    assert client._subscribed_ids == [1, 2]
    assert client._subscribed_book_ids == [3]

    # Explicit close clears subscription IDs and caches
    client.transport.close = AsyncMock()
    await client.close()
    assert client._subscribed_ids == []
    assert client._subscribed_book_ids == []
    assert client._tick_cache_by_id == {}
    assert client._book_cache_by_id == {}


# ---------------------------------------------------------------------------
# 27.5 (S2-05): Validate heartbeat_interval in MT5WebClient.__init__
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "interval",
    [0, 0.0, -1, -0.1, float("nan"), float("inf"), float("-inf"), True, False, "30"],
)
def test_init_rejects_invalid_heartbeat_interval(interval) -> None:
    with pytest.raises(ValidationError, match="heartbeat_interval"):
        MT5WebClient(heartbeat_interval=interval)


# ---------------------------------------------------------------------------
# 27.8 (S2-07): Catch WebSocketException in heartbeat, reconnect, health_check
# ---------------------------------------------------------------------------


async def test_heartbeat_loop_catches_websocket_exception(caplog) -> None:
    client = MT5WebClient(heartbeat_interval=0.005)
    calls = 0

    async def ping_raises(_cmd, _payload=b""):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionClosedError(None, None)
        raise asyncio.CancelledError

    client.transport.send_command = AsyncMock(side_effect=ping_raises)
    caplog.set_level("WARNING", logger="pymt5.client")
    await client._heartbeat_loop()

    assert calls == 2
    assert "heartbeat ping failed" in caplog.text


async def test_reconnect_loop_catches_websocket_exception_and_cleans_transport() -> None:
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=2,
        reconnect_delay=0.0,
    )
    client._login_kwargs = {"login": 123, "password": "pw"}
    client.transport.close = AsyncMock()

    bad_transport = MagicMock()
    bad_transport._listeners = {}
    bad_transport._callback_error_handlers = []
    bad_transport.connect = AsyncMock(
        side_effect=InvalidStatus(Response(502, "Bad Gateway", Headers())),
    )
    bad_transport.close = AsyncMock()

    good_transport = MagicMock()
    good_transport._listeners = {}
    good_transport._callback_error_handlers = []
    good_transport.connect = AsyncMock()
    good_transport.close = AsyncMock()
    good_transport.is_ready = True
    good_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
    )

    try:
        with patch("pymt5.client.MT5WebSocketTransport", side_effect=[bad_transport, good_transport]):
            await client._reconnect_loop()
    finally:
        client._stop_heartbeat()

    assert bad_transport.close.await_count >= 1
    assert client.transport is good_transport
    assert client.is_connected is True
    assert client._reconnect_count == 1


async def test_health_check_catches_websocket_exception() -> None:
    client = MT5WebClient()
    client.transport._state = TransportState.READY
    client._connected_at = time.monotonic() - 5.0

    with patch.object(
        client,
        "ping",
        new_callable=AsyncMock,
        side_effect=ConnectionClosedError(None, None),
    ):
        status = await client.health_check()

    assert status.ping_latency_ms is None
    assert status.state == TransportState.READY


# ---------------------------------------------------------------------------
# 27.9 (S2-08): Close leaked transport in connect() on generation invalidation
# ---------------------------------------------------------------------------


async def test_connect_closes_transport_when_invalidated_during_connect() -> None:
    client = MT5WebClient()
    transport = client.transport
    ws_mock = MagicMock()
    ws_mock.close = AsyncMock()
    transport.close = AsyncMock()
    original_handler = transport._on_disconnect

    async def connect_then_close() -> None:
        transport.ws = ws_mock
        transport.is_ready = True
        client._session_generation += 1

    transport.connect = AsyncMock(side_effect=connect_then_close)

    with pytest.raises(SessionError, match="connection invalidated by close"):
        await client.connect()

    transport.close.assert_awaited_once()
    assert not transport.is_ready
    assert transport._on_disconnect is original_handler


async def test_connect_closes_replaced_transport_when_swapped_during_connect() -> None:
    client = MT5WebClient()
    old_transport = client.transport
    old_transport.close = AsyncMock()
    replacement = MagicMock()

    async def connect_then_swap() -> None:
        old_transport.is_ready = True
        client.transport = replacement

    old_transport.connect = AsyncMock(side_effect=connect_then_swap)

    with pytest.raises(SessionError, match="connection invalidated by close"):
        await client.connect()

    old_transport.close.assert_awaited_once()
    assert old_transport._on_disconnect is None
    assert client.transport is replacement


# ---------------------------------------------------------------------------
# 27.12 (S2-12): Reset _connected_at on disconnect/close and check readiness
# ---------------------------------------------------------------------------


async def test_handle_disconnect_and_close_reset_connected_at_and_uptime() -> None:
    client = MT5WebClient(auto_reconnect=False)
    client.transport.connect = AsyncMock()
    client.transport.close = AsyncMock()

    clock = iter([100.0, 101.0, 101.05, 105.0])
    with (
        patch("pymt5.client.time.monotonic", side_effect=lambda: next(clock, 110.0)),
        patch.object(client, "ping", new_callable=AsyncMock),
    ):
        await client.connect()
        client.transport._state = TransportState.READY
        assert client._connected_at == 100.0

        connected_status = await client.health_check()
    assert connected_status.uptime_seconds == pytest.approx(5.0)

    client._handle_disconnect()
    assert client._connected_at == 0.0
    client.transport._state = TransportState.ERROR
    disconnected_status = await client.health_check()
    assert disconnected_status.uptime_seconds == 0.0

    # Even if _connected_at was set, non-ready transport must report 0.0 uptime
    client._connected_at = time.monotonic() - 10.0
    assert (await client.health_check()).uptime_seconds == 0.0

    await client.close()
    assert client._connected_at == 0.0
    assert (await client.health_check()).uptime_seconds == 0.0


# ---------------------------------------------------------------------------
# 27.10 (S2-10, S2-11) & 29.1 (S2-06): Connection pool hardening
# ---------------------------------------------------------------------------


def test_pool_account_repr_redacts_password() -> None:
    acct = PoolAccount(
        server="wss://broker.example.com/ws",
        login=123456,
        password="top-secret-password",
        label="primary",
    )
    rendered = repr(acct)
    assert "top-secret-password" not in rendered
    assert "123456" in rendered
    assert "primary" in rendered


async def test_pool_connect_all_cancellation_closes_already_connected_clients() -> None:
    accounts = [
        PoolAccount(server="wss://fast", login=1, password="p1", label="fast"),
        PoolAccount(server="wss://slow", login=2, password="p2", label="slow"),
    ]
    pool = MT5ConnectionPool(accounts)

    fast_client = MagicMock()
    fast_client.connect = AsyncMock()
    fast_client.login = AsyncMock()
    fast_client.close = AsyncMock()

    slow_started = asyncio.Event()
    slow_client = MagicMock()

    async def slow_connect() -> None:
        slow_started.set()
        await asyncio.Event().wait()

    slow_client.connect = AsyncMock(side_effect=slow_connect)
    slow_client.login = AsyncMock()
    slow_client.close = AsyncMock()

    def client_factory(*, uri: str, **_kwargs):
        return fast_client if uri == "wss://fast" else slow_client

    with patch("pymt5._pool.MT5WebClient", side_effect=client_factory):
        task = asyncio.create_task(pool.connect_all())
        try:
            await asyncio.wait_for(slow_started.wait(), timeout=1.0)
            await asyncio.sleep(0)
            assert pool.get_client(1) is fast_client
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    fast_client.close.assert_awaited_once()
    slow_client.close.assert_awaited_once()
    assert pool.clients == []


async def test_pool_broadcast_methods_log_warnings_on_errors(caplog) -> None:
    accounts = [
        PoolAccount(server="wss://a", login=101, password="p1", label="alpha"),
        PoolAccount(server="wss://b", login=202, password="p2"),
    ]
    pool = MT5ConnectionPool(accounts)

    c1 = MagicMock()
    c1.subscribe_ticks = AsyncMock(side_effect=MT5TimeoutError("tick timeout"))
    c1.load_symbols = AsyncMock(side_effect=OSError("symbol io error"))

    c2 = MagicMock()
    c2.subscribe_ticks = AsyncMock(side_effect=RuntimeError("bad tick state"))
    c2.load_symbols = AsyncMock(return_value=[])

    pool._clients = {101: c1, 202: c2}
    caplog.set_level("WARNING", logger="pymt5.pool")

    await pool.broadcast_subscribe_ticks([1, 2])
    await pool.broadcast_load_symbols()

    assert "alpha (login=101)" in caplog.text
    assert "tick timeout" in caplog.text
    assert "symbol io error" in caplog.text
    assert "202" in caplog.text
    assert "bad tick state" in caplog.text


# ---------------------------------------------------------------------------
# 27.6 (S5-01, S5-04), 27.7 (S5-02), 27.11 (S5-05): Account contract fixes
# ---------------------------------------------------------------------------


async def test_disable_otp_raises_protocol_error_on_nonzero_code() -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_OTP_SETUP, code=4, body=b""),
    )

    with pytest.raises(ProtocolError, match="disable_otp failed"):
        await client.disable_otp(login=123, password="pw", otp="123456")


async def test_change_password_validates_code_and_body_length() -> None:
    client = MT5WebClient()

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_CHANGE_PASSWORD, code=2, body=struct.pack("<i", 0)),
    )
    with pytest.raises(ProtocolError, match="change_password failed"):
        await client.change_password("new", "old")

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_CHANGE_PASSWORD, code=0, body=b"\x01\x02"),
    )
    with pytest.raises(ProtocolError, match="change_password response too short"):
        await client.change_password("new", "old")

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_CHANGE_PASSWORD, code=0, body=struct.pack("<i", -5)),
    )
    assert await client.change_password("new", "old") == -5


async def test_opening_commands_raise_protocol_error_on_nonzero_code() -> None:
    client = MT5WebClient()
    client.transport.is_ready = True
    demo_req = DemoAccountRequest(first_name="Ada", second_name="Lovelace")
    real_req = RealAccountRequest(first_name="Ada", second_name="Lovelace")
    base_req = AccountOpeningRequest(first_name="Ada", second_name="Lovelace")

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_OPEN_DEMO, code=5, body=_open_account_body(code=0)),
    )
    with pytest.raises(ProtocolError, match="open_demo_account failed"):
        await client.open_demo_account(demo_req, initialize=False)

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_OPEN_REAL, code=6, body=_open_account_body(code=0)),
    )
    with pytest.raises(ProtocolError, match="open_real_account failed"):
        await client.open_real_account(real_req, initialize=False)

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_VERIFY_CODE, code=7, body=b""),
    )
    with pytest.raises(ProtocolError, match="request_opening_verification failed"):
        await client.request_opening_verification(base_req, initialize=False)

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SEND_VERIFY_CODES, code=8, body=b""),
    )
    with pytest.raises(ProtocolError, match="submit_opening_verification failed"):
        await client.submit_opening_verification(base_req, initialize=False)


async def test_verification_commands_reject_short_or_empty_body() -> None:
    client = MT5WebClient()
    base_req = AccountOpeningRequest(first_name="Ada", second_name="Lovelace")

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_VERIFY_CODE, code=0, body=b""),
    )
    with pytest.raises(ProtocolError, match="request_opening_verification response too short"):
        await client.request_opening_verification(base_req, initialize=False)

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SEND_VERIFY_CODES, code=0, body=b"\x01"),
    )
    with pytest.raises(ProtocolError, match="submit_opening_verification response too short"):
        await client.submit_opening_verification(base_req, initialize=False)

    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_VERIFY_CODE, code=0, body=_verification_body(True, False)),
    )
    status = await client.request_opening_verification(base_req, initialize=False)
    assert status.email is True and status.phone is False


def test_split_password_blob_non_hex_and_whitespace_320_chars_fall_back() -> None:
    client = MT5WebClient()

    non_hex = "z" * 320
    prefix, blob = client._split_password_blob(non_hex)
    assert prefix == non_hex[:32]
    assert blob is None

    spaced_hex = "ab  " * 80
    assert len(spaced_hex) == 320
    prefix2, blob2 = client._split_password_blob(spaced_hex)
    assert prefix2 == spaced_hex[:32]
    assert blob2 is None

    valid_hex = "cd" * 160
    prefix3, blob3 = client._split_password_blob(valid_hex)
    assert prefix3 == ""
    assert blob3 == bytes.fromhex(valid_hex)


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: MT5TimeoutError("timed out"),
        lambda: MT5ConnectionError("connection lost"),
        lambda: OSError("socket down"),
    ],
)
async def test_version_and_get_account_summary_handle_pymt5_and_os_errors(exc_factory) -> None:
    client = MT5WebClient()
    client.get_positions_and_orders = AsyncMock(
        return_value={
            "positions": [{"profit": 25.0, "commission": -2.0, "storage": -0.5}],
            "orders": [{"trade_order": 10}],
        }
    )
    client.get_account = AsyncMock(side_effect=exc_factory())

    assert await client.version() is None
    assert client.last_error()[0] == -99
    assert "version() failed" in client.last_error()[1]

    summary = await client.get_account_summary()
    assert summary.profit == pytest.approx(22.5)
    assert summary.positions_count == 1
    assert summary.orders_count == 1
    assert summary.balance == 0.0


async def test_foreground_connect_restores_on_disconnect_after_reconnect_cancellation() -> None:
    """NEW-01: foreground connect() must restore _on_disconnect after reconnect cleanup."""
    client = MT5WebClient(auto_reconnect=True, reconnect_delay=10.0)
    disconnected = []
    client.on_disconnect(lambda: disconnected.append(True))

    client.transport.close = AsyncMock()
    client.transport.connect = AsyncMock()
    client._logged_in = True
    client._login_kwargs = {"login": 1, "password": "pw"}

    client._handle_disconnect()
    await asyncio.sleep(0)
    assert disconnected == [True]
    assert client._reconnect_task is not None

    await client.connect()
    assert client.transport._on_disconnect == client._handle_disconnect

    client.transport._on_disconnect()
    assert disconnected == [True, True]
    await client.close()


async def test_foreground_connect_tracks_timed_out_reconnect_task_in_quiescence() -> None:
    """NEW-03: timed-out reconnect task stays tracked in _reconnect_close_tasks until done."""
    client = MT5WebClient(timeout=0.02)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stuck_reconnect() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    reconnect_task = asyncio.create_task(stuck_reconnect())
    client._reconnect_task = reconnect_task
    await entered.wait()
    client.transport.connect = AsyncMock()

    with pytest.raises(SessionError, match="reconnect cleanup is still in progress"):
        await client.connect()

    assert reconnect_task in client._reconnect_close_tasks
    with pytest.raises(SessionError, match="reconnect cleanup is still in progress"):
        await client.connect()

    release.set()
    await asyncio.wait_for(reconnect_task, timeout=1.0)
    await asyncio.sleep(0)
    assert reconnect_task not in client._reconnect_close_tasks
    await client.connect()
    client.transport.connect.assert_awaited_once()
