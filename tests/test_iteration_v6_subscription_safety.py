"""Tick subscription acknowledgements and handle lifecycle regressions."""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5._subscription import SubscriptionHandle
from pymt5.client import MT5WebClient
from pymt5.constants import CMD_SUBSCRIBE_TICKS
from pymt5.exceptions import ProtocolError
from pymt5.transport import CommandResult


def response(code: int) -> CommandResult:
    return CommandResult(command=CMD_SUBSCRIBE_TICKS, code=code, body=b"")


async def test_first_subscription_rejection_preserves_empty_set():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=response(1))

    with pytest.raises(ProtocolError, match=r"command 7 failed: code=1"):
        await client.subscribe_ticks([42])

    assert client._subscribed_ids == []
    client.transport.send_command.assert_awaited_once_with(CMD_SUBSCRIBE_TICKS, struct.pack("<II", 1, 42))


async def test_addition_rejection_preserves_previous_confirmed_set():
    client = MT5WebClient()
    client._subscribed_ids = [1]
    client.transport.send_command = AsyncMock(return_value=response(1))

    with pytest.raises(ProtocolError):
        await client.subscribe_ticks([2])
    assert client._subscribed_ids == [1]
    client.transport.send_command.assert_awaited_once_with(CMD_SUBSCRIBE_TICKS, struct.pack("<III", 2, 1, 2))


@pytest.mark.parametrize("removed", [[2], [1, 2]])
async def test_unsubscribe_rejection_preserves_last_confirmed_set(removed):
    client = MT5WebClient()
    client._subscribed_ids = [1, 2]
    client.transport.send_command = AsyncMock(return_value=response(1))

    with pytest.raises(ProtocolError, match=r"command 7 failed: code=1"):
        await client.unsubscribe_ticks(removed)

    assert client._subscribed_ids == [1, 2]
    payload = client.transport.send_command.await_args.args[1]
    assert payload == (struct.pack("<II", 1, 1) if removed == [2] else struct.pack("<I", 0))


async def test_successes_commit_full_set_including_empty():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=response(0))

    await client.subscribe_ticks([2, 1])
    assert client._subscribed_ids == [1, 2]
    await client.unsubscribe_ticks([2])
    assert client._subscribed_ids == [1]
    await client.unsubscribe_ticks([1])
    assert client._subscribed_ids == []
    assert client.transport.send_command.await_args_list[-1].args[1] == struct.pack("<I", 0)


async def test_concurrent_additions_serialize_read_send_commit():
    client = MT5WebClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def send(command, payload):
        sent.append((command, payload))
        if len(sent) == 1:
            entered.set()
            await release.wait()
        return response(0)

    client.transport.send_command = AsyncMock(side_effect=send)
    first = asyncio.create_task(client.subscribe_ticks([1]))
    await entered.wait()
    second = asyncio.create_task(client.subscribe_ticks([2]))
    await asyncio.sleep(0)
    assert len(sent) == 1
    release.set()
    await asyncio.gather(first, second)

    assert sent == [
        (CMD_SUBSCRIBE_TICKS, struct.pack("<II", 1, 1)),
        (CMD_SUBSCRIBE_TICKS, struct.pack("<III", 2, 1, 2)),
    ]
    assert client._subscribed_ids == [1, 2]


async def test_cancelled_subscription_keeps_confirmed_set_and_releases_lock():
    client = MT5WebClient()
    client._subscribed_ids = [1]
    entered = asyncio.Event()

    async def blocked_send(*_args):
        entered.set()
        await asyncio.Event().wait()

    client.transport.send_command = AsyncMock(side_effect=blocked_send)
    task = asyncio.create_task(client.subscribe_ticks([2]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client._subscribed_ids == [1]

    # A fresh, usable transport is represented by a successful send mock.
    client.transport.send_command = AsyncMock(return_value=response(0))
    await asyncio.wait_for(client.subscribe_ticks([3]), timeout=1)
    assert client._subscribed_ids == [1, 3]


async def test_failed_unsubscribe_retains_intent_for_reconnect_retry():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=2, reconnect_delay=0)
    client._login_kwargs = {"login": 123, "password": "test"}
    client._subscribed_ids = [1, 2]
    client.transport.send_command = AsyncMock(return_value=response(1))
    with pytest.raises(ProtocolError):
        await client.unsubscribe_ticks([2])
    client.transport.close = AsyncMock()
    client.login = AsyncMock()

    def mock_transport(code):
        transport = MagicMock()
        transport.connect = AsyncMock()
        transport.close = AsyncMock()
        transport.send_command = AsyncMock(return_value=response(code))
        transport._listeners = {}
        transport._callback_error_handlers = []
        return transport

    rejected, accepted = mock_transport(1), mock_transport(0)
    with (
        patch("pymt5.client.MT5WebSocketTransport", side_effect=[rejected, accepted]),
        patch("pymt5.client.random.uniform", return_value=0),
    ):
        await client._reconnect_loop()

    expected = (CMD_SUBSCRIBE_TICKS, struct.pack("<III", 2, 1, 2))
    rejected.send_command.assert_awaited_once_with(*expected)
    accepted.send_command.assert_awaited_once_with(*expected)
    rejected.close.assert_awaited()
    assert client._subscribed_ids == [1, 2]
    assert client._reconnect_count == 1


async def test_handle_can_retry_after_failure():
    unsubscribe = AsyncMock(side_effect=[ProtocolError("rejected"), None])
    handle = SubscriptionHandle([1], unsubscribe)

    with pytest.raises(ProtocolError):
        await handle.unsubscribe()
    assert handle.active
    await handle.unsubscribe()
    assert not handle.active
    assert unsubscribe.await_count == 2


async def test_handle_cancelled_unsubscribe_remains_active_and_retryable():
    entered = asyncio.Event()

    async def blocked(_ids):
        entered.set()
        await asyncio.Event().wait()

    unsubscribe = AsyncMock(side_effect=blocked)
    handle = SubscriptionHandle([1], unsubscribe)
    task = asyncio.create_task(handle.unsubscribe())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handle.active
    unsubscribe.side_effect = None
    await asyncio.wait_for(handle.unsubscribe(), timeout=1)
    assert not handle.active


async def test_concurrent_handle_unsubscribe_sends_once_after_success():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_ids):
        entered.set()
        await release.wait()

    unsubscribe = AsyncMock(side_effect=blocked)
    handle = SubscriptionHandle([1], unsubscribe)
    first = asyncio.create_task(handle.unsubscribe())
    await entered.wait()
    second = asyncio.create_task(handle.unsubscribe())
    await asyncio.sleep(0)
    assert unsubscribe.await_count == 1
    release.set()
    await asyncio.gather(first, second)
    assert not handle.active
    unsubscribe.assert_awaited_once_with([1])
