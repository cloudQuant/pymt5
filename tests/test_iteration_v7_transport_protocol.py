"""Iteration 07 regressions for Group 1: transport, protocol, and rate limiter."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions

from pymt5._rate_limiter import TokenBucketRateLimiter
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_BOOTSTRAP,
    CMD_GET_ACCOUNT,
    CMD_GET_POSITIONS_ORDERS,
    CMD_LOGIN_STATUS_PUSH,
    CMD_PING,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    OUTER_PROTOCOL_VERSION,
    PROP_BYTES,
    PROP_FIXED_STRING,
    PROP_STRING,
    PROP_U32,
)
from pymt5.crypto import initial_cipher
from pymt5.exceptions import MT5ConnectionError, MT5TimeoutError, ProtocolError, ValidationError
from pymt5.protocol import ResponseFrame, SeriesCodec, get_series_size, pack_outer, unpack_outer
from pymt5.schemas import ORDER_SCHEMA, POSITION_SCHEMA, SYMBOL_DETAILS_6320_SCHEMA, SYMBOL_DETAILS_SCHEMA
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState


class _QueueWebSocket:
    """Async iterable mock WebSocket fed via an asyncio.Queue."""

    def __init__(self) -> None:
        self.messages: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
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


def _encrypted_frame(cipher, command: int, code: int = 0, body: bytes = b"", version: int = OUTER_PROTOCOL_VERSION):
    inner = b"\x00\x00" + struct.pack("<H", command) + bytes([code]) + body
    encrypted = cipher.encrypt(inner)
    return struct.pack("<II", len(encrypted), version) + encrypted


# ---------------------------------------------------------------------------
# 25.1 (S1-01): Async listener dispatch isolation
# ---------------------------------------------------------------------------


async def test_async_listener_can_send_command_without_deadlock() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1.0)
    ws = _QueueWebSocket()
    cipher = initial_cipher()
    transport.cipher = cipher
    transport.ws = ws
    transport.is_ready = True

    account_reply = _encrypted_frame(cipher, CMD_GET_ACCOUNT, code=0, body=b"acc-ok")

    async def on_send(_raw: bytes) -> None:
        ws.messages.put_nowait(account_reply)

    ws.send.side_effect = on_send

    results: list[CommandResult] = []
    done = asyncio.Event()

    async def async_tick_listener(_res: CommandResult) -> None:
        resp = await transport.send_command(CMD_GET_ACCOUNT)
        results.append(resp)
        done.set()

    transport.on(CMD_TICK_PUSH, async_tick_listener)
    recv_task = asyncio.create_task(transport._recv_loop(ws, transport._generation))
    transport._recv_task = recv_task
    try:
        await asyncio.wait_for(ws.reading.wait(), timeout=1.0)
        ws.messages.put_nowait(_encrypted_frame(cipher, CMD_TICK_PUSH, code=0, body=b"tick"))
        await asyncio.wait_for(done.wait(), timeout=1.0)
        assert len(results) == 1
        assert results[0].body == b"acc-ok"
    finally:
        await transport.close()


async def test_async_listeners_preserve_delivery_order_and_route_errors() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1.0)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True

    order: list[str] = []
    errors: list[tuple[Exception, object]] = []
    release_first = asyncio.Event()
    finished = asyncio.Event()

    transport._callback_error_handlers.append(lambda exc, cb: errors.append((exc, cb)))

    async def first_async(res: CommandResult) -> None:
        order.append(f"first-start:{res.body.decode()}")
        if res.body == b"f1":
            await release_first.wait()
        order.append(f"first-end:{res.body.decode()}")

    def second_sync(res: CommandResult) -> None:
        order.append(f"second:{res.body.decode()}")
        if res.body == b"f1":
            raise RuntimeError("sync boom")

    async def third_async(res: CommandResult) -> None:
        order.append(f"third:{res.body.decode()}")
        if res.body == b"f2":
            finished.set()
            return
        raise ValueError("async boom")

    transport._listeners[CMD_TICK_PUSH] = [first_async, second_sync, third_async]  # type: ignore[assignment]

    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"f1"))
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"f2"))
    await asyncio.sleep(0)
    assert order == ["first-start:f1"]
    release_first.set()
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    if transport._listener_task is not None:
        await transport._listener_task

    assert order == [
        "first-start:f1",
        "first-end:f1",
        "second:f1",
        "third:f1",
        "first-start:f2",
        "first-end:f2",
        "second:f2",
        "third:f2",
    ]
    assert len(errors) == 2
    assert isinstance(errors[0][0], RuntimeError)
    assert errors[0][1] is second_sync
    assert isinstance(errors[1][0], ValueError)
    assert errors[1][1] is third_async


async def test_close_from_inside_active_listener_does_not_deadlock() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True
    closed_inside = asyncio.Event()

    async def self_closing_listener(_res: CommandResult) -> None:
        await transport.close()
        closed_inside.set()

    transport.on(CMD_TICK_PUSH, self_closing_listener)
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"))
    await asyncio.wait_for(closed_inside.wait(), timeout=1.0)
    if transport._listener_task is not None:
        await transport._listener_task
    assert transport.state is TransportState.DISCONNECTED


async def test_close_and_isolation_cancel_queued_async_listeners_without_warnings() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.1)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True
    started = asyncio.Event()
    release = asyncio.Event()
    ran: list[bytes] = []

    async def slow_listener(res: CommandResult) -> None:
        if res.body == b"1":
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        ran.append(res.body)

    transport.on(CMD_TICK_PUSH, slow_listener)
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"2"))

    await asyncio.wait_for(transport.close(), timeout=0.5)
    assert transport.state is TransportState.DISCONNECTED
    assert ran == []
    release.set()
    await asyncio.sleep(0.05)


async def test_listener_stale_generation_closes_unawaited_coroutines() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True
    generation = transport._generation

    async def inner_coro(_res: CommandResult) -> None:
        raise AssertionError("should not execute")

    def sync_returning_coro_after_invalidation(res: CommandResult):
        transport._generation_invalidated.set()
        return inner_coro(res)

    transport.on(CMD_TICK_PUSH, sync_returning_coro_after_invalidation)
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"x"), source_ws=ws, source_generation=generation)
    assert transport._listener_task is None


async def test_queued_listener_with_invalidated_generation_closes_awaitable() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True
    generation = transport._generation
    ran: list[str] = []

    async def first(_res: CommandResult) -> None:
        ran.append("first")

    async def second(_res: CommandResult) -> None:
        ran.append("second")

    transport.on(CMD_TICK_PUSH, first)
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"), source_ws=ws, source_generation=generation)
    assert transport._listener_task is not None
    # Queue a second item with a stale generation while _listener_task is pending.
    transport._listener_queue.append(
        (
            second(ResponseFrame(CMD_TICK_PUSH, 0, b"2")),
            second,
            CommandResult(CMD_TICK_PUSH, 0, b"2"),
            (),
            ws,
            generation + 1,
        )
    )
    await transport._listener_task
    assert ran == ["first"]


async def test_connect_detaches_stale_listener_task() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.2)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def bg(_res: CommandResult) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    transport.on(CMD_TICK_PUSH, bg)
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"x"))
    await started.wait()

    ws = _QueueWebSocket()
    bootstrap = CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 66 + b"a" * 16)
    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)),
        patch.object(transport, "_send_raw", new=AsyncMock(return_value=bootstrap)),
    ):
        await transport.connect()
    assert cancelled.is_set()
    await transport.close()


# ---------------------------------------------------------------------------
# 25.2 (S1-02): Listener CancelledError isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async_direct", "async_subtask"])
async def test_internal_listener_cancelled_error_does_not_kill_recv_loop(mode: str) -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1.0)
    ws = _QueueWebSocket()
    cipher = initial_cipher()
    transport.cipher = cipher
    transport.ws = ws
    transport.is_ready = True

    errors: list[tuple[BaseException, object]] = []
    later_received: list[bytes] = []
    later_event = asyncio.Event()
    transport._callback_error_handlers.append(lambda exc, cb: errors.append((exc, cb)))

    def sync_cancel(_res: CommandResult) -> None:
        raise asyncio.CancelledError("internal sync cancel")

    async def async_cancel(_res: CommandResult) -> None:
        raise asyncio.CancelledError("internal async cancel")

    async def async_subtask_cancel(_res: CommandResult) -> None:
        sub = asyncio.create_task(asyncio.Event().wait())
        sub.cancel()
        await sub

    bad_cb = {"sync": sync_cancel, "async_direct": async_cancel, "async_subtask": async_subtask_cancel}[mode]

    def handle_tick(res: CommandResult) -> None:
        if res.body == b"first":
            return bad_cb(res)
        later_received.append(res.body)
        later_event.set()
        return None

    transport.on(CMD_TICK_PUSH, handle_tick)
    recv_task = asyncio.create_task(transport._recv_loop(ws, transport._generation))
    transport._recv_task = recv_task
    try:
        await ws.reading.wait()
        ws.messages.put_nowait(_encrypted_frame(cipher, CMD_TICK_PUSH, code=0, body=b"first"))
        ws.messages.put_nowait(_encrypted_frame(cipher, CMD_TICK_PUSH, code=0, body=b"second"))
        await asyncio.wait_for(later_event.wait(), timeout=1.0)
        assert later_received == [b"second"]
        assert transport.is_ready is True
        assert not recv_task.done()
        assert len(errors) == 1
        assert isinstance(errors[0][0], asyncio.CancelledError)
    finally:
        await transport.close()


# ---------------------------------------------------------------------------
# 25.3 (S1-03): Bounded close() cleanup
# ---------------------------------------------------------------------------


async def test_close_is_bounded_when_recv_and_ws_ignore_cancellation() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.1)
    release = asyncio.Event()
    recv_cancelled = asyncio.Event()
    ws_cancelled = asyncio.Event()

    async def uncooperative_recv() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            recv_cancelled.set()
            await release.wait()
            raise RuntimeError("late recv error") from None

    async def uncooperative_close() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            ws_cancelled.set()
            await release.wait()
            raise RuntimeError("late ws error") from None

    ws = MagicMock()
    ws.close = uncooperative_close
    transport.ws = ws
    transport.is_ready = True
    transport._recv_task = asyncio.create_task(uncooperative_recv())
    await asyncio.sleep(0)

    await asyncio.wait_for(transport.close(), timeout=0.6)
    assert transport.state is TransportState.DISCONNECTED
    assert transport.ws is None
    assert transport._recv_task is None
    await asyncio.wait_for(recv_cancelled.wait(), timeout=0.5)
    await asyncio.wait_for(ws_cancelled.wait(), timeout=0.5)

    release.set()
    await asyncio.sleep(0.05)


async def test_close_preserves_first_error_when_ws_close_also_fails_or_hangs() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.1)
    started = asyncio.Event()

    async def failing_recv() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ValueError("recv first failure") from None

    ws = MagicMock()
    ws.close = AsyncMock(side_effect=OSError("ws second failure"))
    transport.ws = ws
    transport._recv_task = asyncio.create_task(failing_recv())
    await started.wait()

    with pytest.raises(ValueError, match="recv first failure"):
        await transport.close()
    assert transport.state is TransportState.DISCONNECTED


async def test_close_propagates_ws_close_error_when_recv_is_none() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.1)
    ws = MagicMock()
    ws.close = AsyncMock(side_effect=OSError("ws close error"))
    transport.ws = ws

    with pytest.raises(OSError, match="ws close error"):
        await transport.close()
    assert transport.state is TransportState.DISCONNECTED


async def test_close_handles_internal_cancelled_error_from_ws_close() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.1)
    ws = MagicMock()
    ws.close = AsyncMock(side_effect=asyncio.CancelledError())
    transport.ws = ws

    await transport.close()
    assert transport.state is TransportState.DISCONNECTED


# ---------------------------------------------------------------------------
# 25.4 (S1-04): Ambiguous frame completion policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "push_cmd",
    [
        CMD_TICK_PUSH,
        CMD_BOOK_PUSH,
        CMD_TRADE_UPDATE_PUSH,
        CMD_TRADE_RESULT_PUSH,
        CMD_SYMBOL_UPDATE_PUSH,
        CMD_ACCOUNT_UPDATE_PUSH,
        CMD_LOGIN_STATUS_PUSH,
    ],
)
async def test_unsolicited_push_commands_never_complete_pending_futures(push_cmd: int) -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1.0)
    future = asyncio.get_running_loop().create_future()
    transport._pending[push_cmd].append(future)
    delivered: list[CommandResult] = []
    transport.on(push_cmd, delivered.append)

    await transport._dispatch(ResponseFrame(push_cmd, 0, b"push-payload"))
    assert not future.done()
    assert len(delivered) == 1
    assert delivered[0].body == b"push-payload"


async def test_send_command_response_predicate_filters_mismatched_frames() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True

    listener_seen: list[bytes] = []
    transport.on(CMD_GET_ACCOUNT, lambda r: listener_seen.append(r.body))

    task = asyncio.create_task(
        transport.send_command(
            CMD_GET_ACCOUNT,
            response_predicate=lambda res: res.body.startswith(b"VALID:"),
        )
    )
    while not transport._pending.get(CMD_GET_ACCOUNT):
        await asyncio.sleep(0)

    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"WRONG:1"))
    assert not task.done()
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"VALID:2"))
    res = await asyncio.wait_for(task, timeout=1.0)
    assert res.body == b"VALID:2"
    assert listener_seen == [b"WRONG:1", b"VALID:2"]


async def test_send_command_predicate_exception_fails_future_safely() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True

    def bad_predicate(_res: CommandResult) -> bool:
        raise ValueError("predicate crashed")

    task = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT, response_predicate=bad_predicate))
    while not transport._pending.get(CMD_GET_ACCOUNT):
        await asyncio.sleep(0)

    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"abc"))
    with pytest.raises(ValueError, match="predicate crashed"):
        await task


async def test_shared_command_cmd4_rejects_malformed_frames_and_accepts_valid() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True

    pos_size = get_series_size(POSITION_SCHEMA)
    order_size = get_series_size(ORDER_SCHEMA)

    # Malformed bodies for code=0: too short, count exceeds length, order count exceeds length
    malformed_bodies = [
        b"",
        b"\x01\x02\x03",
        struct.pack("<II", 5, 0),
        struct.pack("<II", 0, 2),
        struct.pack("<I", 1) + b"\x00" * pos_size,  # missing order_count
    ]
    task = asyncio.create_task(transport.send_command(CMD_GET_POSITIONS_ORDERS))
    while not transport._pending.get(CMD_GET_POSITIONS_ORDERS):
        await asyncio.sleep(0)

    for bad_body in malformed_bodies:
        await transport._dispatch(ResponseFrame(CMD_GET_POSITIONS_ORDERS, 0, bad_body))
        assert not task.done()

    valid_body = struct.pack("<I", 1) + (b"\x00" * pos_size) + struct.pack("<I", 1) + (b"\x00" * order_size)
    await transport._dispatch(ResponseFrame(CMD_GET_POSITIONS_ORDERS, 0, valid_body))
    res = await asyncio.wait_for(task, timeout=1.0)
    assert res.body == valid_body


async def test_shared_command_cmd17_rejects_unaligned_and_accepts_error_or_aligned() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True
    transport._server_build = 6320
    detail_size = get_series_size(SYMBOL_DETAILS_6320_SCHEMA)

    task = asyncio.create_task(transport.send_command(CMD_SYMBOL_DETAILS_PUSH))
    while not transport._pending.get(CMD_SYMBOL_DETAILS_PUSH):
        await asyncio.sleep(0)

    await transport._dispatch(ResponseFrame(CMD_SYMBOL_DETAILS_PUSH, 0, b"\x01\x02"))
    assert not task.done()

    await transport._dispatch(ResponseFrame(CMD_SYMBOL_DETAILS_PUSH, 0, b"\x00" * detail_size))
    res = await asyncio.wait_for(task, timeout=1.0)
    assert len(res.body) == detail_size

    # Legacy build + nonzero error code
    transport._server_build = 0
    legacy_size = get_series_size(SYMBOL_DETAILS_SCHEMA)
    assert legacy_size > 0
    task2 = asyncio.create_task(transport.send_command(CMD_SYMBOL_DETAILS_PUSH))
    while not transport._pending.get(CMD_SYMBOL_DETAILS_PUSH):
        await asyncio.sleep(0)
    await transport._dispatch(ResponseFrame(CMD_SYMBOL_DETAILS_PUSH, 5, b"err"))
    res2 = await asyncio.wait_for(task2, timeout=1.0)
    assert res2.code == 5


# ---------------------------------------------------------------------------
# 25.5 (S1-05): Bootstrap key length validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body_len", [66, 67, 81, 83, 97])
async def test_bootstrap_invalid_key_lengths_raise_mt5_connection_error(body_len: int) -> None:
    transport = MT5WebSocketTransport("wss://broker.invalid/ws")
    ws = _QueueWebSocket()
    transport._send_raw = AsyncMock(return_value=CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * body_len))  # type: ignore[method-assign]

    with (
        patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)),
        pytest.raises(MT5ConnectionError, match="AES key") as exc_info,
    ):
        await transport.connect()

    assert exc_info.value.server_uri == "wss://broker.invalid/ws"
    ws.close.assert_awaited_once()
    assert transport.ws is None
    assert transport._recv_task is None
    assert transport.state is TransportState.DISCONNECTED


# ---------------------------------------------------------------------------
# 25.6 (S1-06, S1-13): Metrics & _on_disconnect exception isolation
# ---------------------------------------------------------------------------


async def test_all_transport_metrics_hooks_and_on_disconnect_are_isolated(caplog) -> None:
    class BrokenMetrics:
        def on_connect(self) -> None:
            raise RuntimeError("on_connect boom")

        def on_command_sent(self, command: int) -> None:
            raise RuntimeError(f"on_command_sent boom {command}")

        def on_command_received(self, command: int, code: int) -> None:
            raise RuntimeError(f"on_command_received boom {command} {code}")

        def on_disconnect(self, reason: str) -> None:
            raise RuntimeError(f"on_disconnect boom {reason}")

    caplog.set_level("WARNING", logger="pymt5.transport")
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1.0, metrics=BrokenMetrics())
    ws = _QueueWebSocket()
    cipher = initial_cipher()
    bootstrap_body = b"\x00" * 66 + b"k" * 16
    ws.messages.put_nowait(_encrypted_frame(initial_cipher(), CMD_BOOTSTRAP, code=0, body=bootstrap_body))

    async def send_side_effect(raw_frame: bytes) -> None:
        # After bootstrap, respond to PING
        if ws.send.await_count == 2:
            ws.messages.put_nowait(_encrypted_frame(cipher, CMD_PING, code=0, body=b"pong"))

    # Use same key for bootstrap response so after key exchange decrypt works
    ws.messages = asyncio.Queue()
    ws.messages.put_nowait(_encrypted_frame(initial_cipher(), CMD_BOOTSTRAP, code=0, body=bootstrap_body))
    from pymt5.crypto import AESCipher

    session_cipher = AESCipher(b"k" * 16)
    ws.send.side_effect = lambda _data: (
        ws.messages.put_nowait(_encrypted_frame(session_cipher, CMD_PING, code=0, body=b"pong"))
        if ws.send.await_count >= 2
        else None
    )

    with patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=ws)):
        await transport.connect()

    assert transport.is_ready
    res = await transport.send_command(CMD_PING)
    assert res.body == b"pong"
    assert transport.is_ready

    # Trigger _on_disconnect exception at end of _recv_loop
    transport._on_disconnect = lambda: (_ for _ in ()).throw(RuntimeError("disconnect cb boom"))
    ws.messages.put_nowait(None)  # EOF
    assert transport._recv_task is not None
    await asyncio.wait_for(transport._recv_task, timeout=1.0)
    assert transport.state is TransportState.ERROR
    assert "disconnect callback failed: RuntimeError" in caplog.text
    assert "metrics on_connect failed" in caplog.text
    assert "metrics on_command_sent failed" in caplog.text
    assert "metrics on_command_received failed" in caplog.text
    assert "metrics on_disconnect failed" in caplog.text


# ---------------------------------------------------------------------------
# 25.7 (S1-12): Preserve external CancelledError during _send_raw cleanup
# ---------------------------------------------------------------------------


async def test_send_raw_timeout_then_external_cancel_during_cleanup_propagates_cancelled() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.02)
    ws = _QueueWebSocket()
    close_entered = asyncio.Event()
    release_close = asyncio.Event()

    async def slow_close() -> None:
        close_entered.set()
        await release_close.wait()

    ws.close.side_effect = slow_close
    transport.ws = ws
    transport.is_ready = True

    task = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(close_entered.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    release_close.set()


async def test_send_raw_external_cancel_observed_even_if_cleanup_returns() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.02)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True

    task: asyncio.Task[CommandResult]

    async def cancel_self_and_return(_ws, _gen) -> None:
        task.cancel()

    transport._invalidate_uncertain_send = cancel_self_and_return  # type: ignore[method-assign]
    task = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)


async def test_send_raw_internal_cleanup_cancel_without_task_cancel_logs_and_times_out(caplog) -> None:
    caplog.set_level("WARNING", logger="pymt5.transport")
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=0.02)
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True

    transport._invalidate_uncertain_send = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
    with pytest.raises(MT5TimeoutError):
        await transport.send_command(CMD_GET_ACCOUNT)
    assert "failed to finish uncertain-send cleanup: CancelledError" in caplog.text


# ---------------------------------------------------------------------------
# WebSocketException wrapping in transport
# ---------------------------------------------------------------------------


async def test_transport_wraps_websocket_exceptions_as_mt5_connection_error() -> None:
    transport = MT5WebSocketTransport("wss://broker.invalid/ws", timeout=1.0)
    with (
        patch(
            "pymt5.transport.websockets.connect",
            new=AsyncMock(side_effect=websockets.exceptions.InvalidHandshake("bad handshake")),
        ),
        pytest.raises(MT5ConnectionError, match="websocket connection failed") as exc_info,
    ):
        await transport.connect()
    assert exc_info.value.server_uri == "wss://broker.invalid/ws"

    transport_send = MT5WebSocketTransport("wss://broker.invalid/ws", timeout=1.0)
    ws = _QueueWebSocket()
    ws.send.side_effect = websockets.exceptions.ConnectionClosedError(None, None)
    transport_send.ws = ws
    transport_send.is_ready = True
    with pytest.raises(MT5ConnectionError, match="websocket send failed"):
        await transport_send.send_command(CMD_GET_ACCOUNT)

    ws2 = _QueueWebSocket()
    ws2.messages.put_nowait(websockets.exceptions.ConnectionClosedError(None, None))
    transport2 = MT5WebSocketTransport("wss://broker.invalid/ws")
    transport2.ws = ws2
    transport2.is_ready = True
    fut = asyncio.get_running_loop().create_future()
    transport2._pending[CMD_GET_ACCOUNT].append(fut)
    await transport2._recv_loop()
    with pytest.raises(MT5ConnectionError, match="websocket connection failed"):
        fut.result()


# ---------------------------------------------------------------------------
# 25.8 (S1-10): TokenBucketRateLimiter validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "burst"),
    [
        (-1.0, 10),
        (-0.001, 1),
        (float("nan"), 10),
        (float("inf"), 10),
        (float("-inf"), 10),
        (True, 10),
        (10.0, 0),
        (10.0, -1),
        (10.0, float("nan")),
        (10.0, float("inf")),
        (10.0, False),
        (10.0, True),
    ],
)
def test_rate_limiter_rejects_invalid_parameters(rate, burst) -> None:
    with pytest.raises(ValidationError):
        TokenBucketRateLimiter(rate=rate, burst=burst)
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate=rate, burst=burst)


# ---------------------------------------------------------------------------
# 26.4 (S1-07): Reject negative and bool propLength
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_len", [-1, -100, True, False, "bad"])
def test_protocol_rejects_negative_and_bool_prop_length(bad_len) -> None:
    for schema in (
        [{"propType": PROP_FIXED_STRING, "propLength": bad_len}],
        [(PROP_FIXED_STRING, "abc", bad_len)],
        [{"propType": PROP_BYTES, "propLength": bad_len}],
        [{"propType": PROP_STRING, "propLength": bad_len}],
    ):
        with pytest.raises(ProtocolError):
            get_series_size(schema)
        with pytest.raises(ProtocolError):
            SeriesCodec.serialize(schema)
        with pytest.raises(ProtocolError):
            SeriesCodec.parse(b"\x00" * 16, schema)


# ---------------------------------------------------------------------------
# 26.5 (S1-08): SeriesCodec.parse offset validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_offset", [-1, -50, 11, 100, True, False])
def test_series_codec_parse_rejects_out_of_range_or_bool_offset(bad_offset) -> None:
    buf = b"\x00" * 10
    with pytest.raises(ProtocolError):
        SeriesCodec.parse(buf, [], offset=bad_offset)
    with pytest.raises(ProtocolError):
        SeriesCodec.parse(buf, [{"propType": PROP_U32}], offset=bad_offset)


def test_series_codec_parse_offset_at_end_of_buffer() -> None:
    buf = b"\x00" * 4
    assert SeriesCodec.parse(buf, [], offset=4) == []
    with pytest.raises(ProtocolError, match="buffer too short"):
        SeriesCodec.parse(buf, [{"propType": PROP_U32}], offset=4)


# ---------------------------------------------------------------------------
# 26.8 (S1-09): Outer protocol version validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("good_version", [0, OUTER_PROTOCOL_VERSION])
def test_unpack_outer_accepts_supported_versions(good_version: int) -> None:
    payload = b"\x01\x02\x03\x04"
    frame = struct.pack("<II", len(payload), good_version) + payload
    body_len, version, body = unpack_outer(frame)
    assert body_len == len(payload)
    assert version == good_version
    assert body == payload


@pytest.mark.parametrize("bad_version", [2, 99, 0xFFFFFFFF])
def test_unpack_outer_rejects_unsupported_version(bad_version: int) -> None:
    payload = b"\x01\x02\x03\x04"
    frame = struct.pack("<II", len(payload), bad_version) + payload
    with pytest.raises(ProtocolError, match=f"unsupported protocol version: {bad_version}"):
        unpack_outer(frame)


async def test_recv_loop_rejects_unsupported_outer_version_and_continues(caplog) -> None:
    caplog.set_level("ERROR", logger="pymt5.transport")
    transport = MT5WebSocketTransport("ws://offline.invalid")
    cipher = initial_cipher()
    transport.cipher = cipher
    ws = _QueueWebSocket()
    transport.ws = ws

    received: list[CommandResult] = []
    transport.on(CMD_TICK_PUSH, received.append)

    bad_ver_frame = _encrypted_frame(cipher, CMD_TICK_PUSH, code=0, body=b"badver", version=99)
    good_frame = pack_outer(cipher.encrypt(b"\x00\x00" + struct.pack("<H", CMD_TICK_PUSH) + b"\x00good"))
    ws.messages.put_nowait(bad_ver_frame)
    ws.messages.put_nowait(good_frame)
    ws.messages.put_nowait(None)

    await transport._recv_loop()
    assert len(received) == 1
    assert received[0].body == b"good"
    assert "unsupported protocol version: 99" in caplog.text


# ---------------------------------------------------------------------------
# Additional coverage and edge-case tests for transport.py
# ---------------------------------------------------------------------------


async def test_close_and_isolation_preserve_external_cancel_during_listener_stop() -> None:
    # Cover close() external cancellation while stopping _listener_task (line 474)
    t1 = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws1 = _QueueWebSocket()
    t1.ws = ws1
    t1.is_ready = True
    started1 = asyncio.Event()
    cancelled1 = asyncio.Event()
    release1 = asyncio.Event()

    async def listener1(_res: CommandResult) -> None:
        started1.set()
        try:
            await release1.wait()
        except asyncio.CancelledError:
            cancelled1.set()
            await release1.wait()

    t1.on(CMD_TICK_PUSH, listener1)
    await t1._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"))
    await started1.wait()
    close_task = asyncio.create_task(t1.close())
    await cancelled1.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    release1.set()
    with pytest.raises(asyncio.CancelledError):
        await close_task
    ws1.close.assert_awaited_once()

    # Cover _invalidate_uncertain_send() external cancellation while stopping _listener_task (line 760)
    t2 = MT5WebSocketTransport("ws://offline.invalid", timeout=0.5)
    ws2 = _QueueWebSocket()
    t2.ws = ws2
    t2.is_ready = True
    started2 = asyncio.Event()
    cancelled2 = asyncio.Event()
    release2 = asyncio.Event()

    async def listener2(_res: CommandResult) -> None:
        started2.set()
        try:
            await release2.wait()
        except asyncio.CancelledError:
            cancelled2.set()
            await release2.wait()

    t2.on(CMD_TICK_PUSH, listener2)
    await t2._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"2"))
    await started2.wait()
    iso_task = asyncio.create_task(t2._invalidate_uncertain_send(ws2, t2._generation))
    await cancelled2.wait()
    iso_task.cancel()
    await asyncio.sleep(0)
    release2.set()
    with pytest.raises(asyncio.CancelledError):
        await iso_task
    ws2.close.assert_awaited_once()


async def test_recv_loop_stops_when_sync_listener_replaces_connection() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    cipher = initial_cipher()
    transport.cipher = cipher
    ws = _QueueWebSocket()
    transport.ws = ws
    transport.is_ready = True
    seen: list[bytes] = []

    def switch_generation(res: CommandResult) -> None:
        seen.append(res.body)
        transport._generation += 1
        transport.ws = _QueueWebSocket()

    transport.on(CMD_TICK_PUSH, switch_generation)
    ws.messages.put_nowait(_encrypted_frame(cipher, CMD_TICK_PUSH, code=0, body=b"one"))
    ws.messages.put_nowait(_encrypted_frame(cipher, CMD_TICK_PUSH, code=0, body=b"two"))
    await transport._recv_loop(ws, 0)
    assert seen == [b"one"]


async def test_callback_error_handler_raising_does_not_stop_later_handlers(caplog) -> None:
    caplog.set_level("WARNING", logger="pymt5.transport")
    transport = MT5WebSocketTransport("ws://offline.invalid")
    seen: list[str] = []

    def bad_err_handler(_exc, _cb) -> None:
        raise RuntimeError("handler crash")

    def good_err_handler(exc, _cb) -> None:
        seen.append(str(exc))

    transport._callback_error_handlers = [bad_err_handler, good_err_handler]
    transport.on(CMD_TICK_PUSH, lambda _r: (_ for _ in ()).throw(ValueError("cb fail")))
    await transport._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b""))
    assert seen == ["cb fail"]
    assert "itself raised" in caplog.text


async def test_resolve_pending_future_skips_when_only_done_futures_present() -> None:
    transport = MT5WebSocketTransport("ws://offline.invalid")
    done_fut = asyncio.get_running_loop().create_future()
    done_fut.set_result(CommandResult(CMD_GET_ACCOUNT, 0, b"old"))
    transport._pending[CMD_GET_ACCOUNT].append(done_fut)
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"new"))
    assert not transport._pending[CMD_GET_ACCOUNT]


async def test_listener_queue_edge_cases_for_invalidation_and_external_cancel() -> None:
    # 1. Sync listener raises CancelledError during shutdown -> propagates out of _dispatch
    t_sync = MT5WebSocketTransport("ws://offline.invalid")

    def shutdown_and_cancel(_res: CommandResult) -> None:
        t_sync._shutdown_event.set()
        raise asyncio.CancelledError("shutting down")

    t_sync.on(CMD_TICK_PUSH, shutdown_and_cancel)
    with pytest.raises(asyncio.CancelledError):
        await t_sync._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b""))

    # 2. First async invalidates -> skips remaining; second queued frame skipped at _invoke_listener_step
    t2 = MT5WebSocketTransport("ws://offline.invalid")
    ws2 = _QueueWebSocket()
    t2.ws = ws2
    t2.is_ready = True
    gen2 = t2._generation
    ran2: list[str] = []

    async def first_invalidates(_res: CommandResult) -> None:
        await asyncio.sleep(0)
        t2._generation_invalidated.set()
        ran2.append("first")

    def skipped_sync(_res: CommandResult) -> None:
        ran2.append("skipped")

    t2._listeners[CMD_TICK_PUSH] = [first_invalidates, skipped_sync]  # type: ignore[assignment]
    await t2._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"), source_ws=ws2, source_generation=gen2)
    await t2._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"2"), source_ws=ws2, source_generation=gen2)
    if t2._listener_task is not None:
        await t2._listener_task
    assert ran2 == ["first"]

    # 3. Remaining loop: sync invalidates (break line 1073) & second async invalidates (break line 1081)
    t3 = MT5WebSocketTransport("ws://offline.invalid")
    ws3 = _QueueWebSocket()
    t3.ws = ws3
    t3.is_ready = True
    gen3 = t3._generation
    ran3: list[str] = []

    async def a1(_res: CommandResult) -> None:
        await asyncio.sleep(0)
        ran3.append("a1")

    def s_inv(_res: CommandResult) -> None:
        ran3.append("s_inv")
        t3._generation_invalidated.set()

    def s_after(_res: CommandResult) -> None:
        ran3.append("s_after")

    t3._listeners[CMD_TICK_PUSH] = [a1, s_inv, s_after]  # type: ignore[assignment]
    await t3._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"), source_ws=ws3, source_generation=gen3)
    if t3._listener_task is not None:
        await t3._listener_task
    assert ran3 == ["a1", "s_inv"]

    t4 = MT5WebSocketTransport("ws://offline.invalid")
    ws4 = _QueueWebSocket()
    t4.ws = ws4
    t4.is_ready = True
    gen4 = t4._generation
    ran4: list[str] = []

    async def b1(_res: CommandResult) -> None:
        await asyncio.sleep(0)
        ran4.append("b1")

    async def b2_inv(_res: CommandResult) -> None:
        await asyncio.sleep(0)
        ran4.append("b2_inv")
        t4._generation_invalidated.set()

    def b3_after(_res: CommandResult) -> None:
        ran4.append("b3_after")

    t4._listeners[CMD_TICK_PUSH] = [b1, b2_inv, b3_after]  # type: ignore[assignment]
    await t4._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"), source_ws=ws4, source_generation=gen4)
    if t4._listener_task is not None:
        await t4._listener_task
    assert ran4 == ["b1", "b2_inv"]

    # 4. Direct cancellation of _listener_task drains queued items in finally (lines 1084-1085)
    t5 = MT5WebSocketTransport("ws://offline.invalid")
    entered5 = asyncio.Event()

    async def slow5(_res: CommandResult) -> None:
        entered5.set()
        await asyncio.Event().wait()

    t5.on(CMD_TICK_PUSH, slow5)
    await t5._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"1"))
    await entered5.wait()
    await t5._dispatch(ResponseFrame(CMD_TICK_PUSH, 0, b"2"))
    assert t5._listener_task is not None
    t5._listener_task.cancel()
    await asyncio.gather(t5._listener_task, return_exceptions=True)
    assert not t5._listener_queue


async def test_connect_propagates_cancellation_during_listener_detach_stop() -> None:
    """NEW-02: cancelling connect() during _stop_task_bounded listener cleanup must raise CancelledError."""
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=1.0)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_listener() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    transport._listener_task = asyncio.create_task(slow_listener())
    await entered.wait()

    with patch("pymt5.transport.websockets.connect", new=AsyncMock()) as mock_connect:
        connect_task = asyncio.create_task(transport.connect())
        await asyncio.sleep(0)
        connect_task.cancel()
        await asyncio.sleep(0)
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await connect_task
    mock_connect.assert_not_called()
    assert transport.state is TransportState.DISCONNECTED
