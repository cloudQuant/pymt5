"""Regressions for isolating connections after uncertain command sends."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import pymt5.transport as transport_module
from pymt5.client import MT5WebClient
from pymt5.constants import CMD_BOOTSTRAP, CMD_GET_ACCOUNT, CMD_PING
from pymt5.exceptions import MT5TimeoutError, SessionError
from pymt5.protocol import ResponseFrame
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState


def _transport(timeout: float = 1.0):
    transport = MT5WebSocketTransport("ws://offline.invalid", timeout=timeout)
    socket = AsyncMock()
    transport.ws = socket
    transport.is_ready = True
    return transport, socket


async def _wait_for_pending(transport, command):
    async with asyncio.timeout(1):
        while not transport._pending.get(command):
            await asyncio.sleep(0)


async def _wait_for_sends(socket, count):
    async with asyncio.timeout(1):
        while socket.send.await_count < count:
            await asyncio.sleep(0)


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
async def test_uncertain_request_isolates_connection_and_fails_other_senders(failure):
    transport, socket = _transport(timeout=0.1 if failure == "timeout" else 1)
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    notifications = []

    async def close():
        close_started.set()
        await release_close.wait()

    socket.close.side_effect = close
    transport._on_disconnect = lambda: notifications.append(True)
    first_sent = asyncio.Event()
    original_send = transport._send_with_deadline

    async def observed_send(*args):
        await original_send(*args)
        first_sent.set()

    transport._send_with_deadline = observed_send
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(first_sent.wait(), 1)
    # Give the unrelated command a longer deadline than the request under test.
    transport.timeout = 1
    queued = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    other = asyncio.create_task(transport.send_command(CMD_PING))
    await _wait_for_pending(transport, CMD_PING)
    await _wait_for_sends(socket, 2)
    if failure == "cancel":
        first.cancel()
    await asyncio.wait_for(close_started.wait(), 1)

    assert transport.state is TransportState.ERROR
    assert transport.ws is None
    assert socket.send.await_count == 2
    with pytest.raises(SessionError, match="outcome may be unknown"):
        await other
    with pytest.raises(SessionError):
        await transport.send_command(CMD_GET_ACCOUNT)
    # A late response cannot complete the still-queued second request.
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"late"))
    assert not queued.done()
    release_close.set()
    with pytest.raises(MT5TimeoutError if failure == "timeout" else asyncio.CancelledError):
        await first
    with pytest.raises(SessionError):
        await queued
    assert notifications == [True]
    assert socket.send.await_count == 2
    socket.close.assert_awaited_once()


async def test_same_command_serializes_until_response():
    transport, socket = _transport()
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    await _wait_for_sends(socket, 1)
    second = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.sleep(0)
    assert socket.send.await_count == 1
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"first"))
    assert (await first).body == b"first"
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    await _wait_for_sends(socket, 2)
    assert socket.send.await_count == 2
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"second"))
    assert (await second).body == b"second"


async def test_different_commands_can_be_in_flight_together():
    transport, socket = _transport()
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    second = asyncio.create_task(transport.send_command(CMD_PING))
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    await _wait_for_pending(transport, CMD_PING)
    await _wait_for_sends(socket, 2)
    assert socket.send.await_count == 2
    await transport._dispatch(ResponseFrame(CMD_PING, 0, b"ping"))
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"account"))
    assert (await first).body == b"account"
    assert (await second).body == b"ping"


async def test_build_failure_does_not_disconnect():
    transport, socket = _transport()
    with (
        patch("pymt5.transport.build_command", side_effect=ValueError("build")),
        pytest.raises(ValueError, match="build"),
    ):
        await transport.send_command(CMD_GET_ACCOUNT)
    assert transport.is_ready
    socket.send.assert_not_awaited()
    socket.close.assert_not_awaited()


@pytest.mark.parametrize("failure", ["send", "metrics", "send_cancel"])
async def test_send_or_metrics_failure_invalidates(failure):
    transport, socket = _transport()
    notifications = []
    transport._on_disconnect = lambda: notifications.append(True)
    if failure == "send":
        socket.send.side_effect = OSError("send failure")
    elif failure == "metrics":
        transport._metrics = AsyncMock()
        transport._metrics.on_command_sent = lambda command: (_ for _ in ()).throw(ValueError("metrics"))
        transport._metrics.on_disconnect = lambda message: None
    else:
        started = asyncio.Event()

        async def send(_):
            started.set()
            await asyncio.Event().wait()

        socket.send.side_effect = send
    task = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    if failure == "send_cancel":
        await started.wait()
        task.cancel()
    with pytest.raises({"send": OSError, "metrics": ValueError, "send_cancel": asyncio.CancelledError}[failure]):
        await task
    assert transport.state is TransportState.ERROR
    assert transport.ws is None
    socket.close.assert_awaited_once()
    assert notifications == [True]


async def test_explicit_close_during_isolation_suppresses_disconnect_notification():
    transport, socket = _transport(timeout=0.02)
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    notifications = []

    async def close():
        close_started.set()
        await release_close.wait()

    socket.close.side_effect = close
    transport._on_disconnect = lambda: notifications.append(True)
    request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(close_started.wait(), 1)
    await transport.close()
    release_close.set()
    with pytest.raises(MT5TimeoutError):
        await request
    assert notifications == []
    assert transport.state is TransportState.DISCONNECTED


async def test_replacement_connection_is_not_closed_or_notified_by_old_cleanup():
    transport, old_socket = _transport(timeout=0.02)
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    notifications = []

    async def close():
        close_started.set()
        await release_close.wait()

    old_socket.close.side_effect = close
    transport._on_disconnect = lambda: notifications.append(True)
    old_request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(close_started.wait(), 1)
    replacement = AsyncMock()
    transport._generation += 1
    transport._generation_invalidated = asyncio.Event()
    transport.ws = replacement
    transport.is_ready = True
    new_request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    release_close.set()
    with pytest.raises(MT5TimeoutError):
        await old_request
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"new"))
    assert (await new_request).body == b"new"
    assert transport.ws is replacement
    assert transport.is_ready
    replacement.close.assert_not_awaited()
    assert notifications == []


async def test_isolation_notifies_client_to_schedule_reconnect():
    client = MT5WebClient(auto_reconnect=True, timeout=0.02)
    socket = AsyncMock()
    client.transport.ws = socket
    client.transport.is_ready = True
    client._logged_in = True
    client._login_kwargs = {"login": 1, "password": "test"}
    reconnect_started = asyncio.Event()

    async def reconnect():
        reconnect_started.set()

    client._reconnect_loop = reconnect
    with pytest.raises(MT5TimeoutError):
        await client.transport.send_command(CMD_GET_ACCOUNT)
    await asyncio.wait_for(reconnect_started.wait(), 1)
    assert not client.is_connected
    await client.close()


async def test_isolation_wakes_rate_limited_sender_without_leaking_waiters():
    transport, socket = _transport(timeout=0.05)
    before = asyncio.all_tasks()
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    transport.timeout = 1
    limiter_started = asyncio.Event()
    limiter_finished = asyncio.Event()

    async def blocked_acquire():
        limiter_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            limiter_finished.set()

    transport._rate_limiter.acquire = blocked_acquire
    transport._rate_limiter.rate = 1
    throttled = asyncio.create_task(transport.send_command(CMD_PING))
    await asyncio.wait_for(limiter_started.wait(), 1)
    with pytest.raises(MT5TimeoutError):
        await first
    with pytest.raises(SessionError):
        await asyncio.wait_for(throttled, 0.5)
    assert limiter_finished.is_set()
    assert socket.send.await_count == 1
    await asyncio.sleep(0)
    assert not (asyncio.all_tasks() - before)


async def test_socket_cleanup_failure_does_not_mask_send_error():
    transport, socket = _transport()
    socket.send.side_effect = OSError("send failed")
    socket.close.side_effect = RuntimeError("close failed")
    notifications = []
    transport._on_disconnect = lambda: notifications.append(True)
    with pytest.raises(OSError, match="send failed"):
        await transport.send_command(CMD_GET_ACCOUNT)
    assert transport.state is TransportState.ERROR
    assert notifications == [True]


async def test_second_cancellation_during_cleanup_preserves_isolation_and_notifies():
    transport, socket = _transport()
    close_started = asyncio.Event()
    notifications = []

    async def close():
        close_started.set()
        await asyncio.Event().wait()

    socket.close.side_effect = close
    transport._on_disconnect = lambda: notifications.append(True)
    request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    request.cancel()
    await asyncio.wait_for(close_started.wait(), 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert transport.state is TransportState.ERROR
    assert transport.ws is None
    assert notifications == [True]


async def test_uncooperative_socket_close_is_bounded_and_cannot_affect_replacement(monkeypatch):
    transport, socket = _transport(timeout=0.02)
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()
    release_close = asyncio.Event()
    close_tasks = []
    notifications = []
    close_observed = asyncio.Event()
    original_observer = transport_module._observe_isolation_task

    def observe(task):
        original_observer(task)
        close_observed.set()

    monkeypatch.setattr(transport_module, "_observe_isolation_task", observe)

    async def close():
        close_tasks.append(asyncio.current_task())
        close_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_cancelled.set()
            await release_close.wait()
        raise RuntimeError("late old socket close failure")

    socket.close.side_effect = close
    transport._on_disconnect = lambda: notifications.append(True)
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await _wait_for_pending(transport, CMD_GET_ACCOUNT)
    queued = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    try:
        await asyncio.wait_for(close_started.wait(), 1)
        with pytest.raises(MT5TimeoutError):
            await asyncio.wait_for(first, 0.8)
        await asyncio.wait_for(close_cancelled.wait(), 0.5)
        with pytest.raises(SessionError):
            await asyncio.wait_for(queued, 0.5)
        assert notifications == [True]
        assert transport.state is TransportState.ERROR

        replacement = AsyncMock()
        transport._generation += 1
        transport._generation_invalidated = asyncio.Event()
        transport.ws = replacement
        transport.is_ready = True
        transport.timeout = 1
        new_request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
        await _wait_for_pending(transport, CMD_GET_ACCOUNT)
        release_close.set()
        await asyncio.wait_for(close_observed.wait(), 1)
        assert close_tasks[0].done()
        assert transport.ws is replacement
        assert transport.is_ready
        assert not new_request.done()
        replacement.close.assert_not_awaited()
        await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"new"))
        assert (await new_request).body == b"new"
        assert notifications == [True]
    finally:
        release_close.set()
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)


async def test_uncooperative_receive_task_eventual_error_is_observed(monkeypatch):
    transport, _socket = _transport(timeout=0.02)
    receive_started = asyncio.Event()
    receive_cancelled = asyncio.Event()
    release_receive = asyncio.Event()
    receive_observed = asyncio.Event()
    original_observer = transport_module._observe_isolation_task

    async def receive():
        receive_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            receive_cancelled.set()
            await release_receive.wait()
        raise RuntimeError("late old receive failure")

    recv_task = asyncio.create_task(receive())

    def observe(task):
        original_observer(task)
        if task is recv_task:
            receive_observed.set()

    monkeypatch.setattr(transport_module, "_observe_isolation_task", observe)
    transport._recv_task = recv_task
    await receive_started.wait()
    try:
        with pytest.raises(MT5TimeoutError):
            await asyncio.wait_for(transport.send_command(CMD_GET_ACCOUNT), 0.8)
        await asyncio.wait_for(receive_cancelled.wait(), 0.5)
        assert transport.state is TransportState.ERROR
        release_receive.set()
        await asyncio.wait_for(receive_observed.wait(), 1)
        assert recv_task.done()
    finally:
        release_receive.set()
        await asyncio.gather(recv_task, return_exceptions=True)


async def test_uncooperative_send_is_bounded_and_fails_other_pending(monkeypatch):
    transport, socket = _transport(timeout=0.05)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    send_tasks = []
    notifications = []
    send_observed = asyncio.Event()
    original_observer = transport_module._observe_isolation_task

    def observe(task):
        original_observer(task)
        if task in send_tasks:
            send_observed.set()

    monkeypatch.setattr(transport_module, "_observe_isolation_task", observe)

    async def send(_frame):
        send_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        raise RuntimeError("late old send failure")

    socket.send.side_effect = send
    transport._on_disconnect = lambda: notifications.append(True)
    pending = asyncio.get_running_loop().create_future()
    transport._pending[CMD_PING].append(pending)
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(started.wait(), 1)
    queued = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    other_queued = asyncio.create_task(transport.send_command(CMD_PING))
    try:
        # The assertion is deliberately made without cancelling first, so a
        # send that ignores cancellation cannot hang the pre-fix test.
        await asyncio.sleep(0.2)
        assert first.done(), "send stage exceeded its deadline"
        with pytest.raises(MT5TimeoutError):
            await first
        with pytest.raises(SessionError):
            await queued
        with pytest.raises(SessionError):
            await other_queued
        with pytest.raises(SessionError, match="outcome may be unknown"):
            pending.result()
        await asyncio.wait_for(cancelled.wait(), 0.5)
        assert transport.state is TransportState.ERROR
        assert notifications == [True]
        release.set()
        await asyncio.wait_for(send_observed.wait(), 0.5)
    finally:
        release.set()
        for task in (first, queued, other_queued):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, queued, other_queued, *send_tasks, return_exceptions=True)


async def test_cancelling_uncooperative_send_preserves_cancellation_and_isolates():
    transport, socket = _transport(timeout=1)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    send_tasks = []

    async def send(_frame):
        send_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    socket.send.side_effect = send
    first = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(started.wait(), 1)
    queued = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    try:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 0.5)
        await asyncio.wait_for(cancelled.wait(), 0.5)
        with pytest.raises(SessionError):
            await asyncio.wait_for(queued, 0.5)
        assert transport.state is TransportState.ERROR
        assert transport.ws is None
    finally:
        release.set()
        for task in (first, queued):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, queued, *send_tasks, return_exceptions=True)


async def test_close_and_reconnect_do_not_inherit_old_stuck_send_locks():
    transport, old_socket = _transport(timeout=2)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    send_tasks = []

    async def old_send(_frame):
        send_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        raise RuntimeError("late old send failure")

    class OpenSocket:
        def __init__(self):
            self.send = AsyncMock()
            self.close = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()
            raise StopAsyncIteration

    old_socket.send.side_effect = old_send
    old_request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
    await asyncio.wait_for(started.wait(), 1)
    new_request = None
    try:
        await transport.close()
        replacement = OpenSocket()
        bootstrap = CommandResult(CMD_BOOTSTRAP, 0, b"\x00" * 66 + b"a" * 16)
        with (
            patch("pymt5.transport.websockets.connect", new=AsyncMock(return_value=replacement)),
            patch.object(transport, "_send_raw", new=AsyncMock(return_value=bootstrap)),
        ):
            await transport.connect()
        new_request = asyncio.create_task(transport.send_command(CMD_GET_ACCOUNT))
        async with asyncio.timeout(0.3):
            await _wait_for_pending(transport, CMD_GET_ACCOUNT)
        await transport._dispatch(ResponseFrame(CMD_GET_ACCOUNT, 0, b"new"))
        assert (await new_request).body == b"new"
        await asyncio.wait_for(cancelled.wait(), 0.5)
        with pytest.raises(SessionError):
            await old_request
        assert transport.is_ready
        assert transport.ws is replacement
        replacement.close.assert_not_awaited()
    finally:
        release.set()
        for task in (old_request, new_request):
            if task is not None and not task.done():
                task.cancel()
        tasks = [task for task in (old_request, new_request, *send_tasks) if task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)
        await transport.close()
