"""Tests for MT5WebSocketTransport: init, on/off, _dispatch, _fail_all,
timeout cleanup, invalid command validation."""

import asyncio

import pytest

from pymt5.constants import CMD_BOOTSTRAP, CMD_TICK_PUSH, VALID_COMMANDS
from pymt5.transport import CommandResult, MT5WebSocketTransport


# ---- Init ----

def test_transport_init_defaults():
    t = MT5WebSocketTransport(uri="wss://example.com")
    assert t.uri == "wss://example.com"
    assert t.timeout == 30.0
    assert t.ws is None
    assert t.is_ready is False
    assert t.token == bytes(64)
    assert t._recv_task is None
    assert t._on_disconnect is None


def test_transport_init_custom_timeout():
    t = MT5WebSocketTransport(uri="wss://example.com", timeout=10.0)
    assert t.timeout == 10.0


# ---- on / off listener management ----

def test_on_registers_listener():
    t = MT5WebSocketTransport(uri="wss://x")
    cb = lambda r: None
    t.on(CMD_TICK_PUSH, cb)
    assert cb in t._listeners[CMD_TICK_PUSH]


def test_off_removes_specific_listener():
    t = MT5WebSocketTransport(uri="wss://x")
    cb1 = lambda r: None
    cb2 = lambda r: None
    t.on(CMD_TICK_PUSH, cb1)
    t.on(CMD_TICK_PUSH, cb2)
    assert len(t._listeners[CMD_TICK_PUSH]) == 2
    t.off(CMD_TICK_PUSH, cb1)
    assert cb1 not in t._listeners[CMD_TICK_PUSH]
    assert cb2 in t._listeners[CMD_TICK_PUSH]


def test_off_clears_all_listeners():
    t = MT5WebSocketTransport(uri="wss://x")
    t.on(CMD_TICK_PUSH, lambda r: None)
    t.on(CMD_TICK_PUSH, lambda r: None)
    assert len(t._listeners[CMD_TICK_PUSH]) == 2
    t.off(CMD_TICK_PUSH)
    assert len(t._listeners[CMD_TICK_PUSH]) == 0


def test_off_nonexistent_callback_no_error():
    t = MT5WebSocketTransport(uri="wss://x")
    cb = lambda r: None
    t.off(CMD_TICK_PUSH, cb)  # should not raise


# ---- _dispatch ----

async def test_dispatch_resolves_pending_future():
    t = MT5WebSocketTransport(uri="wss://x")
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    t._pending[CMD_TICK_PUSH].append(future)

    from pymt5.protocol import ResponseFrame
    frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"test")
    await t._dispatch(frame)

    assert future.done()
    result = future.result()
    assert result.command == CMD_TICK_PUSH
    assert result.body == b"test"


async def test_dispatch_calls_listeners():
    t = MT5WebSocketTransport(uri="wss://x")
    received = []
    t.on(CMD_TICK_PUSH, lambda r: received.append(r))

    from pymt5.protocol import ResponseFrame
    frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"data")
    await t._dispatch(frame)

    assert len(received) == 1
    assert received[0].body == b"data"


async def test_dispatch_calls_async_listener():
    t = MT5WebSocketTransport(uri="wss://x")
    received = []

    async def async_handler(r):
        received.append(r)

    t.on(CMD_TICK_PUSH, async_handler)

    from pymt5.protocol import ResponseFrame
    frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"async")
    await t._dispatch(frame)

    assert len(received) == 1
    assert received[0].body == b"async"


async def test_dispatch_skips_done_futures():
    t = MT5WebSocketTransport(uri="wss://x")
    loop = asyncio.get_running_loop()
    done_future = loop.create_future()
    done_future.set_result(None)
    pending_future = loop.create_future()
    t._pending[CMD_TICK_PUSH].append(done_future)
    t._pending[CMD_TICK_PUSH].append(pending_future)

    from pymt5.protocol import ResponseFrame
    frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"x")
    await t._dispatch(frame)

    assert pending_future.done()
    assert pending_future.result().body == b"x"


# ---- _fail_all ----

def test_fail_all_sets_exceptions():
    t = MT5WebSocketTransport(uri="wss://x")
    loop = asyncio.new_event_loop()
    f1 = loop.create_future()
    f2 = loop.create_future()
    t._pending[CMD_TICK_PUSH].append(f1)
    t._pending[CMD_BOOTSTRAP].append(f2)

    exc = RuntimeError("disconnected")
    t._fail_all(exc)

    assert f1.done()
    assert f2.done()
    with pytest.raises(RuntimeError, match="disconnected"):
        f1.result()
    with pytest.raises(RuntimeError, match="disconnected"):
        f2.result()
    loop.close()


def test_fail_all_skips_done_futures():
    t = MT5WebSocketTransport(uri="wss://x")
    loop = asyncio.new_event_loop()
    done = loop.create_future()
    done.set_result(None)
    pending = loop.create_future()
    t._pending[CMD_TICK_PUSH].append(done)
    t._pending[CMD_TICK_PUSH].append(pending)

    t._fail_all(RuntimeError("test"))

    with pytest.raises(RuntimeError):
        pending.result()
    # done future should still have its original result
    assert done.result() is None
    loop.close()


# ---- Invalid command validation ----

async def test_send_raw_invalid_command_raises():
    t = MT5WebSocketTransport(uri="wss://x")
    with pytest.raises(ValueError, match="unsupported command"):
        await t.send_command(99999)


async def test_send_command_not_ready_raises():
    t = MT5WebSocketTransport(uri="wss://x")
    t.is_ready = False
    with pytest.raises(RuntimeError, match="transport not ready"):
        await t.send_command(CMD_TICK_PUSH)


async def test_send_command_no_ws_raises():
    t = MT5WebSocketTransport(uri="wss://x")
    t.is_ready = True
    with pytest.raises(RuntimeError, match="websocket not connected"):
        await t.send_command(CMD_TICK_PUSH)


# ---- CommandResult ----

def test_command_result_dataclass():
    cr = CommandResult(command=8, code=0, body=b"hello")
    assert cr.command == 8
    assert cr.code == 0
    assert cr.body == b"hello"


# ---- Module-level proxy detection cache ----

def test_ws_connect_has_proxy_is_bool():
    from pymt5.transport import _WS_CONNECT_HAS_PROXY
    assert isinstance(_WS_CONNECT_HAS_PROXY, bool)
