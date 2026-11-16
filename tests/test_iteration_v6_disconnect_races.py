"""Controlled EOF, disconnect and generation races for the transport."""

import asyncio
import struct
from unittest.mock import AsyncMock, Mock, patch

import pytest

from pymt5.constants import CMD_BOOTSTRAP, CMD_GET_ACCOUNT, CMD_TICK_PUSH
from pymt5.exceptions import MT5ConnectionError, SessionError
from pymt5.protocol import ResponseFrame, pack_outer
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState


class _Socket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.reading = asyncio.Event()
        self.send = AsyncMock()
        self.close = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.reading.set()
        item = await self.messages.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item


def _ready_transport():
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1)
    socket = _Socket()
    transport.ws = socket
    transport.is_ready = True
    return transport, socket


@pytest.mark.parametrize("termination", [None, OSError("network failure")])
async def test_close_wins_before_eof_or_error_claims_disconnect(termination):
    transport, socket = _ready_transport()
    notifications = []
    transport._on_disconnect = lambda: notifications.append(True)
    future = asyncio.get_running_loop().create_future()
    transport._pending[CMD_GET_ACCOUNT].append(future)

    await transport._disconnect_lock.acquire()
    close = asyncio.create_task(transport.close())
    await asyncio.sleep(0)
    receive = asyncio.create_task(transport._recv_loop(socket, transport._generation))
    await socket.reading.wait()
    socket.messages.put_nowait(termination)
    await asyncio.sleep(0)
    transport._disconnect_lock.release()
    await asyncio.gather(close, receive)

    with pytest.raises(SessionError, match="transport closed"):
        future.result()
    assert notifications == []
    assert transport.state is TransportState.DISCONNECTED
    socket.close.assert_awaited_once()


@pytest.mark.parametrize("termination", [None, OSError("network failure")])
async def test_disconnect_claims_once_and_callback_can_schedule_close(termination):
    transport, socket = _ready_transport()
    close_tasks = []
    transport._on_disconnect = lambda: close_tasks.append(asyncio.create_task(transport.close()))
    receive = asyncio.create_task(transport._recv_loop(socket, transport._generation))
    await socket.reading.wait()
    socket.messages.put_nowait(termination)
    await receive
    assert len(close_tasks) == 1
    await asyncio.wait_for(close_tasks[0], 1)
    assert transport.state is TransportState.DISCONNECTED


async def test_duplicate_eof_notifies_and_records_metrics_only_once():
    transport, socket = _ready_transport()
    notifications = []
    transport._on_disconnect = lambda: notifications.append(True)
    transport._metrics = Mock()
    first = asyncio.create_task(transport._recv_loop(socket, transport._generation))
    second = asyncio.create_task(transport._recv_loop(socket, transport._generation))
    socket.messages.put_nowait(None)
    socket.messages.put_nowait(None)
    await asyncio.gather(first, second)
    assert notifications == [True]
    transport._metrics.on_disconnect.assert_called_once()
    assert transport.state is TransportState.ERROR
    await transport.close()


async def test_metrics_failure_does_not_suppress_pending_failure_or_notification():
    transport, socket = _ready_transport()
    notifications = []
    transport._on_disconnect = lambda: notifications.append(True)
    transport._metrics = Mock()
    transport._metrics.on_disconnect.side_effect = RuntimeError("metrics failure")
    future = asyncio.get_running_loop().create_future()
    transport._pending[CMD_GET_ACCOUNT].append(future)
    receive = asyncio.create_task(transport._recv_loop(socket, transport._generation))
    socket.messages.put_nowait(OSError("socket failure"))
    await receive
    with pytest.raises(OSError, match="socket failure"):
        future.result()
    assert notifications == [True]
    assert transport.state is TransportState.ERROR
    await transport.close()


@pytest.mark.parametrize("late", ["eof", "error", "frame"])
async def test_old_generation_cannot_consume_or_fail_new_pending(late):
    transport, old_socket = _ready_transport()
    receive = asyncio.create_task(transport._recv_loop(old_socket, transport._generation))
    await old_socket.reading.wait()
    new_socket = _Socket()
    transport._generation += 1
    transport._generation_invalidated = asyncio.Event()
    transport.ws = new_socket
    transport.is_ready = True
    pending = asyncio.get_running_loop().create_future()
    transport._pending[CMD_GET_ACCOUNT].append(pending)
    if late == "frame":
        inner = b"\x00\x00" + struct.pack("<H", CMD_GET_ACCOUNT) + b"\x00old"
        old_socket.messages.put_nowait(pack_outer(transport.cipher.encrypt(inner)))
    else:
        old_socket.messages.put_nowait(None if late == "eof" else OSError("old failure"))
    await receive
    assert not pending.done()
    assert transport.state is TransportState.READY
    assert transport.ws is new_socket
    await transport.close()
    with pytest.raises(SessionError):
        pending.result()


async def test_delayed_old_close_cannot_clear_new_connection_or_pending():
    transport, old_socket = _ready_transport()
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_old():
        close_started.set()
        await release_close.wait()

    old_socket.close.side_effect = close_old
    old_close = asyncio.create_task(transport.close())
    await close_started.wait()
    new_socket = _Socket()
    bootstrap = CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 66 + b"a" * 16)
    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=new_socket)),
        patch.object(transport, "_send_raw", new=AsyncMock(return_value=bootstrap)),
    ):
        await transport.connect()
    pending = asyncio.get_running_loop().create_future()
    transport._pending[CMD_GET_ACCOUNT].append(pending)
    release_close.set()
    await old_close
    assert transport.is_ready
    assert transport.ws is new_socket
    assert not pending.done()
    await transport.close()
    with pytest.raises(SessionError):
        pending.result()


async def test_superseded_connection_open_cannot_replace_or_close_new_socket():
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1)
    old_socket = _Socket()
    started = asyncio.Event()
    release = asyncio.Event()

    async def open_old(*args, **kwargs):
        started.set()
        await release.wait()
        return old_socket

    with patch("pymt5.transport.websockets.connect", new=open_old):
        old_connect = asyncio.create_task(transport.connect())
        await started.wait()
        await transport.close()
        new_socket = _Socket()
        transport._generation += 1
        transport._generation_invalidated = asyncio.Event()
        transport._shutdown_event.clear()
        transport.ws = new_socket
        transport.is_ready = True
        release.set()
        with pytest.raises(SessionError, match="superseded"):
            await old_connect
    old_socket.close.assert_awaited_once()
    new_socket.close.assert_not_awaited()
    assert transport.ws is new_socket
    assert transport.state is TransportState.READY
    await transport.close()


async def test_bootstrap_response_followed_immediately_by_eof_is_not_ready():
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1)
    socket = _Socket()
    bootstrap = CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 66 + b"a" * 16)

    async def bootstrap_then_eof(*args, **kwargs):
        socket.messages.put_nowait(None)
        while transport.state is not TransportState.ERROR:
            await asyncio.sleep(0)
        return bootstrap

    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=socket)),
        patch.object(transport, "_send_raw", new=bootstrap_then_eof),
        pytest.raises(SessionError, match="superseded"),
    ):
        await transport.connect()
    assert not transport.is_ready
    socket.close.assert_awaited_once()


async def test_old_frame_stops_dispatch_after_noncooperative_listener_and_reconnect():
    transport, old_socket = _ready_transport()
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    delivered = []

    async def first(_result):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    def second(_result):
        delivered.append("stale")

    # Deterministic listener order exercises the yield between two callbacks;
    # production's set does not promise any particular order.
    transport._listeners[CMD_TICK_PUSH] = [first, second]  # type: ignore[assignment]
    frame = b"\x00\x00" + struct.pack("<H", CMD_TICK_PUSH) + b"\x00old"
    old_socket.messages.put_nowait(pack_outer(transport.cipher.encrypt(frame)))
    receive = asyncio.create_task(transport._recv_loop(old_socket, transport._generation))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        transport._generation_invalidated.set()
        transport._generation += 1
        transport._generation_invalidated = asyncio.Event()
        transport.ws = _Socket()
        transport.is_ready = True
        receive.cancel()
        await asyncio.wait_for(cancelled.wait(), 1)
        release.set()
        old_socket.messages.put_nowait(None)
        await asyncio.wait_for(receive, 1)
        assert delivered == []
        assert transport.is_ready
    finally:
        release.set()
        if not receive.done():
            receive.cancel()
        await asyncio.gather(receive, return_exceptions=True)
        await transport.close()


async def test_close_preserves_external_cancellation_while_recv_stops():
    transport, socket = _ready_transport()
    started = asyncio.Event()

    async def receive():
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_task.cancel()
            raise

    transport._recv_task = asyncio.create_task(receive())
    await started.wait()
    close_task = asyncio.create_task(transport.close())

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(close_task, timeout=1)
    assert transport.state is TransportState.DISCONNECTED
    socket.close.assert_awaited_once()


async def test_close_logs_socket_failure_after_receive_failure(caplog):
    transport, socket = _ready_transport()
    started = asyncio.Event()
    socket.close.side_effect = OSError("socket second")

    async def receive():
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("recv first") from None

    transport._recv_task = asyncio.create_task(receive())
    await started.wait()
    caplog.set_level("WARNING", logger="pymt5.transport")

    with pytest.raises(RuntimeError, match="recv first"):
        await transport.close()
    assert "websocket close failed during transport cleanup" in caplog.text
    socket.close.assert_awaited_once()


async def test_recv_loop_without_socket_records_disconnect():
    transport = MT5WebSocketTransport("ws://offline.invalid")
    notifications = []
    transport._on_disconnect = lambda: notifications.append(True)
    pending = asyncio.get_running_loop().create_future()
    transport._pending[CMD_GET_ACCOUNT].append(pending)

    await transport._recv_loop()

    with pytest.raises(MT5ConnectionError, match="websocket not connected"):
        pending.result()
    assert notifications == [True]
    assert transport.state is TransportState.ERROR


@pytest.mark.parametrize("stage", ["initial", "metrics", "pending", "callback", "error_handler"])
async def test_dispatch_stops_when_source_is_invalidated(stage):
    transport, socket = _ready_transport()
    generation = transport._generation
    listener_calls = []
    error_calls = []

    def invalidate(*_args):
        transport._generation_invalidated.set()

    if stage == "initial":
        invalidate()
    if stage == "metrics":
        transport._metrics = Mock()
        transport._metrics.on_command_received.side_effect = invalidate

    future = None
    if stage == "pending":
        future = Mock()
        future.done.return_value = False
        future.set_result.side_effect = invalidate
        transport._pending[CMD_GET_ACCOUNT].append(future)

    def first_listener(_result):
        listener_calls.append("first")
        if stage == "callback":
            invalidate()
            raise ValueError("callback failure")
        if stage == "error_handler":
            raise ValueError("callback failure")

    def second_listener(_result):
        listener_calls.append("second")

    def first_error_handler(_exc, _callback):
        error_calls.append("first")
        if stage == "error_handler":
            invalidate()

    def second_error_handler(_exc, _callback):
        error_calls.append("second")

    transport._listeners[CMD_GET_ACCOUNT] = [first_listener, second_listener]  # type: ignore[assignment]
    transport._callback_error_handlers = [first_error_handler, second_error_handler]

    await transport._dispatch(
        ResponseFrame(CMD_GET_ACCOUNT, 0, b""),
        source_ws=socket,
        source_generation=generation,
    )

    expected_listeners = ["first"] if stage in {"callback", "error_handler"} else []
    assert listener_calls == expected_listeners
    assert error_calls == (["first"] if stage == "error_handler" else [])
    if future is not None:
        future.set_result.assert_called_once()
