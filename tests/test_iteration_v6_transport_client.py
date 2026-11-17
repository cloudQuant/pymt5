"""Behavioral regressions for iteration v6 transport and client lifecycle fixes."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, patch

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import (
    CMD_BOOK_PUSH,
    CMD_BOOTSTRAP,
    CMD_GET_ACCOUNT,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    PROP_F64,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
)
from pymt5.exceptions import MT5ConnectionError, MT5TimeoutError, SessionError
from pymt5.protocol import ResponseFrame, SeriesCodec
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState
from pymt5.types import SymbolInfo


class _WebSocket:
    def __init__(self) -> None:
        self.close = AsyncMock()
        self.send = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()
        raise StopAsyncIteration


class _EmptyWebSocket(_WebSocket):
    async def __anext__(self):
        raise StopAsyncIteration


def _tick_body(*, symbol_id: int = 1, bid: float = 1.1, ask: float = 1.2) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U32, symbol_id),
            (PROP_I32, 1_700_000_000),
            (PROP_U32, 0),
            (PROP_F64, bid),
            (PROP_F64, ask),
            (PROP_F64, 0.0),
            (PROP_I64, 10),
            (PROP_U32, 500),
            (PROP_U16, 0),
        ]
    )


def _book_body(symbol_id: int = 1) -> bytes:
    return b"\x01\x00\x00\x00" + SeriesCodec.serialize(
        [
            (PROP_U32, symbol_id),
            (PROP_I32, 0),
            (PROP_I32, 0),
            (PROP_U32, 1),
            (PROP_U32, 0),
            (PROP_U16, 0),
            (PROP_F64, 1.1),
            (PROP_I64, 100),
        ]
    )


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (CommandResult(CMD_BOOTSTRAP, 1, b""), "bootstrap failed"),
        (CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 10), "response too short"),
        (CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 81), "AES key"),
    ],
)
async def test_bootstrap_failures_release_socket_and_recv_task(result: CommandResult, match: str) -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    ws = _WebSocket()
    transport._send_raw = AsyncMock(return_value=result)  # type: ignore[method-assign]

    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)),
        pytest.raises(Exception, match=match),
    ):
        await transport.connect()

    ws.close.assert_awaited_once()
    assert transport.ws is None
    assert transport._recv_task is None
    assert transport.state is TransportState.DISCONNECTED
    assert not transport._pending


async def test_websocket_open_failure_resets_state() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(side_effect=OSError("refused"))),
        pytest.raises(OSError, match="refused"),
    ):
        await transport.connect()
    assert transport.state is TransportState.DISCONNECTED
    assert transport.ws is None
    assert transport._recv_task is None


async def test_cancellation_during_bootstrap_releases_resources() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    ws = _WebSocket()
    bootstrap_started = asyncio.Event()

    async def wait_for_bootstrap(*args, **kwargs):
        bootstrap_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    transport._send_raw = wait_for_bootstrap  # type: ignore[method-assign]
    with patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)):
        task = asyncio.create_task(transport.connect())
        await bootstrap_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    ws.close.assert_awaited_once()
    assert transport.ws is None
    assert transport._recv_task is None
    assert transport.state is TransportState.DISCONNECTED


async def test_connect_cleanup_error_does_not_mask_bootstrap_error() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    ws = _WebSocket()
    ws.close.side_effect = RuntimeError("close failed")
    transport._send_raw = AsyncMock(  # type: ignore[method-assign]
        return_value=CommandResult(CMD_BOOTSTRAP, 1, b"")
    )

    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)),
        pytest.raises(MT5ConnectionError, match="bootstrap failed"),
    ):
        await transport.connect()

    assert transport.ws is None
    assert transport._recv_task is None
    assert transport.state is TransportState.DISCONNECTED


async def test_timeout_removes_exact_pending_future() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.001)
    transport.ws = _WebSocket()
    transport.is_ready = True

    with pytest.raises(MT5TimeoutError):
        await transport.send_command(CMD_GET_ACCOUNT)

    assert not transport._pending.get(CMD_GET_ACCOUNT)


async def test_caller_cancellation_removes_pending_future() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    ws = _WebSocket()
    transport.ws = ws
    transport.is_ready = True

    task = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    while not transport._pending.get(CMD_GET_ACCOUNT):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not transport._pending.get(CMD_GET_ACCOUNT)
    ws.send.assert_awaited_once()


async def test_send_failure_removes_pending_future() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    ws = _WebSocket()
    ws.send.side_effect = OSError("send failed")
    transport.ws = ws
    transport.is_ready = True

    with pytest.raises(OSError, match="send failed"):
        await transport.send_command(CMD_GET_ACCOUNT)

    assert not transport._pending.get(CMD_GET_ACCOUNT)


async def test_normal_eof_fails_pending_and_notifies_disconnect_once() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    transport.ws = _EmptyWebSocket()
    transport.is_ready = True
    notifications: list[bool] = []
    transport._on_disconnect = lambda: notifications.append(True)
    pending = asyncio.get_running_loop().create_future()
    transport._pending[CMD_GET_ACCOUNT].append(pending)

    await transport._recv_loop()

    assert transport.state is TransportState.ERROR
    assert notifications == [True]
    with pytest.raises(MT5ConnectionError, match="closed"):
        pending.result()
    assert not transport._pending.get(CMD_GET_ACCOUNT)


async def test_normal_eof_during_intentional_shutdown_does_not_notify() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    transport.ws = _EmptyWebSocket()
    transport._shutdown_event.set()
    transport._on_disconnect = lambda: pytest.fail("intentional close must not reconnect")

    await transport._recv_loop()


async def _make_reconnect_transport(*args, **kwargs) -> MT5WebSocketTransport:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    transport.connect = AsyncMock()  # type: ignore[method-assign]
    transport.close = AsyncMock()  # type: ignore[method-assign]
    return transport


async def test_reconnect_restores_callbacks_cache_and_error_handlers_without_duplicates() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client.login = AsyncMock(return_value=("token", 1))  # type: ignore[method-assign]
    client.transport.close = AsyncMock()  # type: ignore[method-assign]

    ticks = []
    client.on_tick(ticks.extend)
    callback_errors = []
    client.on_callback_error(lambda exc, callback: callback_errors.append((exc, callback)))

    def failing_symbol_callback(result: CommandResult) -> None:
        raise ValueError("callback failed")

    client.on_symbol_update(failing_symbol_callback)
    removed_callback = client.on_symbol_update(lambda result: pytest.fail("removed listener resurrected"))
    client.transport.off(CMD_SYMBOL_UPDATE_PUSH, removed_callback)

    first = await _make_reconnect_transport()
    with patch("pymt5.client.MT5WebSocketTransport", return_value=first):
        await client._reconnect_loop()

    assert len(first._listeners[CMD_TICK_PUSH]) == 2
    assert first._callback_error_handlers == client._callback_error_handlers
    await first._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, _tick_body()))
    assert len(ticks) == 1
    assert client._tick_cache_by_id[1]["bid"] == pytest.approx(1.1)

    await first._dispatch(ResponseFrame(CMD_SYMBOL_UPDATE_PUSH, 0, b""))
    assert len(callback_errors) == 1

    first.close = AsyncMock()  # type: ignore[method-assign]
    second = await _make_reconnect_transport()
    with patch("pymt5.client.MT5WebSocketTransport", return_value=second):
        await client._reconnect_loop()

    assert len(second._listeners[CMD_TICK_PUSH]) == 2
    await second._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, _tick_body(bid=1.2)))
    assert len(ticks) == 2
    assert client._tick_cache_by_id[1]["bid"] == pytest.approx(1.2)

    await second._dispatch(ResponseFrame(CMD_SYMBOL_UPDATE_PUSH, 0, b""))
    assert len(callback_errors) == 2


async def test_reconnect_preserves_raw_typed_direct_book_and_command_unregistration() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    client._symbols_by_id[1] = SymbolInfo(name="EURUSD", symbol_id=1, digits=5)
    client.login = AsyncMock(return_value=("token", 1))  # type: ignore[method-assign]
    client.transport.close = AsyncMock()  # type: ignore[method-assign]
    raw_ticks = []
    typed_ticks = []
    direct_ticks = []
    typed_books = []
    client.on_tick(raw_ticks.extend)
    client.on_tick_event(typed_ticks.append)
    client.on_book_event(typed_books.append)
    client.transport.on(CMD_TICK_PUSH, direct_ticks.append)
    client.on_symbol_update(lambda result: pytest.fail("off(command) listener resurrected"))
    client.transport.off(CMD_SYMBOL_UPDATE_PUSH)

    for bid in (1.1, 1.2):
        replacement = await _make_reconnect_transport()
        with patch("pymt5.client.MT5WebSocketTransport", return_value=replacement):
            await client._reconnect_loop()
        await replacement._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, _tick_body(bid=bid)))
        await replacement._dispatch(ResponseFrame(CMD_BOOK_PUSH, 0, _book_body()))
        await replacement._dispatch(ResponseFrame(CMD_SYMBOL_UPDATE_PUSH, 0, b""))

    assert len(raw_ticks) == 2
    assert len(typed_ticks) == 2
    assert len(direct_ticks) == 2
    assert len(typed_books) == 2
    assert client._tick_cache_by_id[1]["bid"] == pytest.approx(1.2)
    assert client._book_cache_by_id[1]["bids"][0]["price"] == pytest.approx(1.1)


async def test_reconnect_retries_without_losing_listeners() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=2, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    client.login = AsyncMock(return_value=("token", 1))  # type: ignore[method-assign]
    client.transport.close = AsyncMock()  # type: ignore[method-assign]
    ticks = []
    client.on_tick(ticks.extend)
    failed = await _make_reconnect_transport()
    failed.connect = AsyncMock(side_effect=MT5ConnectionError("offline"))  # type: ignore[method-assign]
    succeeded = await _make_reconnect_transport()

    with patch("pymt5.client.MT5WebSocketTransport", side_effect=[failed, succeeded]):
        await client._reconnect_loop()

    assert failed.connect.await_count == 1
    assert succeeded.connect.await_count == 1
    await succeeded._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, _tick_body()))
    assert len(ticks) == 1


async def test_listener_changes_during_reconnect_close_are_migrated() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    client.login = AsyncMock(return_value=("token", 1))  # type: ignore[method-assign]
    close_started = asyncio.Event()
    finish_close = asyncio.Event()
    removed = client.on_symbol_update(lambda result: None)
    added_results = []

    async def controlled_close() -> None:
        close_started.set()
        await finish_close.wait()

    client.transport.close = controlled_close  # type: ignore[method-assign]
    replacement = await _make_reconnect_transport()
    with patch("pymt5.client.MT5WebSocketTransport", return_value=replacement):
        reconnect = asyncio.create_task(client._reconnect_loop())
        await close_started.wait()
        client.transport.off(CMD_SYMBOL_UPDATE_PUSH, removed)
        client.transport.on(CMD_SYMBOL_UPDATE_PUSH, added_results.append)
        finish_close.set()
        await reconnect

    assert removed not in replacement._listeners[CMD_SYMBOL_UPDATE_PUSH]
    await replacement._dispatch(ResponseFrame(CMD_SYMBOL_UPDATE_PUSH, 0, b"payload"))
    assert len(added_results) == 1
    assert added_results[0].body == b"payload"


async def test_close_failure_resets_closing_and_allows_later_reconnect() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    client.transport.close = AsyncMock(side_effect=RuntimeError("close failed"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="close failed"):
        await client.close()

    assert client._closing is False
    assert client._login_kwargs is None
    client._login_kwargs = {"login": 1, "password": "new"}  # New explicit login credentials.
    client._handle_disconnect()
    assert client._reconnect_task is not None
    client._reconnect_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await client._reconnect_task


async def test_connect_while_logged_in_does_not_replace_transport() -> None:
    client = MT5WebClient()
    client._logged_in = True
    client.transport.connect = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(SessionError, match="already logged in"):
        await client.connect()
    client.transport.connect.assert_not_awaited()


async def test_command_build_failure_removes_pending_future() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    transport.ws = _WebSocket()
    transport.is_ready = True

    with (
        patch("pymt5.transport.build_command", side_effect=ValueError("bad command")),
        pytest.raises(ValueError, match="bad command"),
    ):
        await transport.send_command(CMD_GET_ACCOUNT)

    assert CMD_GET_ACCOUNT not in transport._pending


async def test_encryption_failure_removes_pending_future() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    transport.ws = _WebSocket()
    transport.is_ready = True

    with (
        patch.object(transport.cipher, "encrypt", side_effect=ValueError("bad cipher")),
        pytest.raises(ValueError, match="bad cipher"),
    ):
        await transport.send_command(CMD_GET_ACCOUNT)

    assert CMD_GET_ACCOUNT not in transport._pending


async def test_metrics_failure_after_send_removes_pending_future() -> None:
    class FailingMetrics:
        def on_command_sent(self, command: int) -> None:
            raise RuntimeError("metrics failed")

    transport = MT5WebSocketTransport("ws://offline.invalid", metrics=FailingMetrics())
    transport.ws = _WebSocket()
    transport.is_ready = True

    task = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    while not transport._pending.get(CMD_GET_ACCOUNT):
        await asyncio.sleep(0)
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"ok"))
    result = await task

    assert result.body == b"ok"
    assert transport.is_ready
    assert CMD_GET_ACCOUNT not in transport._pending


async def test_close_cancellation_cleans_flags_and_does_not_reconnect() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    old_reconnect = asyncio.create_task(asyncio.Event().wait())
    client._reconnect_task = old_reconnect
    transport_close_started = asyncio.Event()

    async def hanging_transport_close() -> None:
        client._handle_disconnect()
        transport_close_started.set()
        await asyncio.Event().wait()

    client.transport.close = hanging_transport_close  # type: ignore[method-assign]
    close_task = asyncio.create_task(client.close())
    await transport_close_started.wait()
    assert client._closing is True
    assert client._reconnect_task is None
    assert old_reconnect.cancelled()

    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert client._closing is False
    assert client._reconnect_task is None
    assert client._login_kwargs is None


async def test_normal_eof_drives_client_auto_reconnect() -> None:
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 1, "password": "secret"}
    client.login = AsyncMock(return_value=("token", 1))  # type: ignore[method-assign]
    client.transport.ws = _EmptyWebSocket()
    client.transport.is_ready = True
    replacement = await _make_reconnect_transport()

    with patch("pymt5.client.MT5WebSocketTransport", return_value=replacement):
        await client.transport._recv_loop()
        assert client._reconnect_task is not None
        await client._reconnect_task

    replacement.connect.assert_awaited_once()
    client.login.assert_awaited_once()
    assert client.transport is replacement
    assert client._closing is False


async def test_connect_defaults_server_build_when_prefix_decode_fails() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    ws = _WebSocket()
    bootstrap = CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 66 + b"a" * 16)
    transport._send_raw = AsyncMock(return_value=bootstrap)  # type: ignore[method-assign]

    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)),
        patch("pymt5.transport.struct.unpack_from", side_effect=struct.error("invalid prefix")),
    ):
        await transport.connect()

    assert transport.server_build == 0
    assert transport.state is TransportState.READY
    await transport.close()
