"""Old reconnect generations must not mutate a newer client session."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_LOGIN
from pymt5.exceptions import SessionError
from pymt5.transport import CommandResult


async def test_stale_reconnect_after_close_cannot_replace_new_session():
    client = MT5WebClient(auto_reconnect=True, reconnect_delay=0, max_reconnect_attempts=1)
    client._login_kwargs = {"login": 1, "password": "old"}
    old_transport = client.transport
    old_transport.close = AsyncMock()
    old_transport.connect = AsyncMock()
    old_transport.send_command = AsyncMock(return_value=CommandResult(CMD_LOGIN, 0, bytes(168)))
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def paused_cleanup(transport=None):
        assert transport is old_transport
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    # Deliberately unregistered to model a delayed old task escaping cancellation.
    with patch.object(client, "_close_reconnect_transport", side_effect=paused_cleanup):
        old_reconnect = asyncio.create_task(client._reconnect_loop())
        await entered.wait()
        old_reconnect.cancel()
        await cancelled.wait()
        await client.close()
        await client.connect()
        await client.login(2, "new", auto_heartbeat=False)
        new_generation = client._session_generation
        release.set()
        await old_reconnect

    assert client.transport is old_transport
    assert client._session_generation == new_generation
    assert client._logged_in
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 2
    assert client._login_kwargs["password"] == "new"
    old_transport.connect.assert_awaited_once()
    old_transport.close.assert_awaited_once()
    old_transport.send_command.assert_awaited_once()


async def test_reconnect_cleanup_operates_on_captured_transport():
    client = MT5WebClient(timeout=1)
    previous = client.transport
    replacement = MagicMock()
    replacement.close = AsyncMock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_close():
        entered.set()
        await release.wait()

    previous.close = AsyncMock(side_effect=controlled_close)
    cleanup = asyncio.create_task(client._close_reconnect_transport(previous))
    await entered.wait()
    client.transport = replacement
    release.set()
    await cleanup

    previous.close.assert_awaited_once()
    replacement.close.assert_not_awaited()


async def test_lingering_reconnect_cleanup_blocks_new_sessions_until_finished():
    client = MT5WebClient(timeout=0.02)
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def delayed_close():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    client.transport.close = AsyncMock(side_effect=delayed_close)
    client.transport.connect = AsyncMock()
    cleanup = asyncio.create_task(client._close_reconnect_transport(client.transport))
    await entered.wait()
    await cancelled.wait()
    await cleanup
    with pytest.raises(SessionError, match="reconnect cleanup is still in progress"):
        await client.connect()
    with pytest.raises(SessionError, match="reconnect cleanup is still in progress"):
        await client.login(1, "test")
    client.transport.connect.assert_not_awaited()

    assert len(client._reconnect_close_tasks) == 1
    pending_close = next(iter(client._reconnect_close_tasks))
    release.set()
    await asyncio.wait_for(pending_close, timeout=1)
    await client.connect()
    client.transport.connect.assert_awaited_once()


async def test_consume_reconnect_close_result_logs_late_failure(caplog):
    client = MT5WebClient()

    async def fail_close():
        raise RuntimeError("late close")

    task = asyncio.create_task(fail_close())
    await asyncio.sleep(0)
    caplog.set_level("WARNING", logger="pymt5.client")

    client._consume_reconnect_close_result(task)

    assert "failed to close reconnect transport" in caplog.text
    assert "late close" in caplog.text
