"""Client close retains the first error while completing local cleanup."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_LOGIN, CMD_LOGOUT
from pymt5.exceptions import SessionError
from pymt5.transport import CommandResult


def configured_client() -> MT5WebClient:
    client = MT5WebClient(auto_reconnect=True)
    client._logged_in = True
    client._bootstrap_pristine = True
    client._login_kwargs = {"login": 123, "password": "test"}
    return client


def assert_cleared(client: MT5WebClient) -> None:
    assert not client._logged_in
    assert not client._bootstrap_pristine
    assert client._login_kwargs is None
    assert client._reconnect_task is None
    assert client._heartbeat_task is None
    assert not client._closing


async def test_first_logout_error_survives_transport_close_error():
    client = configured_client()
    client._logout_unlocked = AsyncMock(side_effect=ValueError("logout first"))
    client.transport.close = AsyncMock(side_effect=RuntimeError("transport second"))

    with pytest.raises(ValueError, match="logout first"):
        await client.close()
    client.transport.close.assert_awaited_once()
    assert_cleared(client)


async def test_transport_error_clears_state_and_allows_repeat_close():
    client = configured_client()
    client._logout_unlocked = AsyncMock()
    client.transport.close = AsyncMock(side_effect=[RuntimeError("transport failure"), None])

    with pytest.raises(RuntimeError, match="transport failure"):
        await client.close()
    assert_cleared(client)
    await client.close()
    assert_cleared(client)
    assert client.transport.close.await_count == 2


async def test_cancellation_during_logout_still_closes_transport():
    client = configured_client()
    entered = asyncio.Event()

    async def wait_in_logout():
        entered.set()
        await asyncio.Event().wait()

    client._logout_unlocked = AsyncMock(side_effect=wait_in_logout)
    client.transport.close = AsyncMock()
    task = asyncio.create_task(client.close())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    client.transport.close.assert_awaited_once()
    assert_cleared(client)


async def test_repeated_cancellation_waits_for_transport_cleanup():
    client = configured_client()
    client._logout_unlocked = AsyncMock()
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    release = asyncio.Event()
    cancellations = 0

    async def slow_close():
        nonlocal cancellations
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            interrupted.set()
            await release.wait()

    client.transport.close = AsyncMock(side_effect=slow_close)
    task = asyncio.create_task(client.close())
    await entered.wait()
    task.cancel()
    await interrupted.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert cancellations == 1
    assert_cleared(client)


async def test_logout_error_precedes_later_external_cancellation():
    client = configured_client()
    client._logout_unlocked = AsyncMock(side_effect=ValueError("logout first"))
    entered = asyncio.Event()

    async def wait_in_close():
        entered.set()
        await asyncio.Event().wait()

    client.transport.close = AsyncMock(side_effect=wait_in_close)
    task = asyncio.create_task(client.close())
    await entered.wait()
    task.cancel()
    with pytest.raises(ValueError, match="logout first"):
        await asyncio.wait_for(task, timeout=1)
    assert_cleared(client)


async def test_cancel_while_login_holds_session_lock_waits_for_release():
    client = configured_client()
    client._logout_unlocked = AsyncMock()
    client.transport.close = AsyncMock()
    await client._session_lock.acquire()
    task = asyncio.create_task(client.close())
    while not client._closing:
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    client._session_lock.release()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    client.transport.close.assert_awaited_once()
    assert_cleared(client)


async def test_close_timeout_cancels_cooperative_transport_cleanup():
    client = configured_client()
    client.timeout = 0.02
    client._logout_unlocked = AsyncMock()
    finished = asyncio.Event()

    async def hanging_close():
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    client.transport.close = AsyncMock(side_effect=hanging_close)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.close(), timeout=1)
    assert finished.is_set()
    assert_cleared(client)


async def test_disconnect_during_close_does_not_schedule_reconnect():
    client = configured_client()
    client._logout_unlocked = AsyncMock()

    async def close_with_disconnect():
        client._handle_disconnect()

    client.transport.close = AsyncMock(side_effect=close_with_disconnect)
    await client.close()
    assert_cleared(client)


async def test_cancelled_logout_coroutine_does_not_skip_cleanup():
    client = configured_client()
    client._logout_unlocked = AsyncMock(side_effect=asyncio.CancelledError())
    client.transport.close = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await client.close()
    client.transport.close.assert_awaited_once()
    assert_cleared(client)


async def test_late_login_after_close_timeout_cannot_revive_session():
    client = MT5WebClient(timeout=0.02)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def late_login(_command, _payload):
        entered.set()
        await release.wait()
        return CommandResult(command=CMD_LOGIN, code=0, body=bytes(168))

    client.transport.send_command = AsyncMock(side_effect=late_login)
    client.transport.close = AsyncMock()
    login_task = asyncio.create_task(client.login(123, "test"))
    await entered.wait()
    with pytest.raises(TimeoutError):
        await client.close()
    assert_cleared(client)
    client.transport.close.assert_awaited_once()

    release.set()
    with pytest.raises(SessionError, match="invalidated by close"):
        await login_task
    assert_cleared(client)
    assert client.transport.send_command.await_count == 1


async def test_late_logout_after_close_timeout_cannot_change_new_session():
    client = configured_client()
    client.timeout = 0.02
    entered = asyncio.Event()
    release = asyncio.Event()

    async def late_logout(command, _payload=None):
        assert command == CMD_LOGOUT
        entered.set()
        await release.wait()
        return CommandResult(command=CMD_LOGOUT, code=0, body=b"")

    client.transport.send_command = AsyncMock(side_effect=late_logout)
    client.transport.close = AsyncMock()
    logout_task = asyncio.create_task(client.logout())
    await entered.wait()
    with pytest.raises(TimeoutError):
        await client.close()
    assert_cleared(client)

    client.transport.connect = AsyncMock()
    await client.connect()
    release.set()
    with pytest.raises(SessionError, match="logout invalidated by close"):
        await logout_task
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=bytes(168))
    )
    await client.login(456, "new", auto_heartbeat=False)
    assert client._logged_in
    assert client._login_kwargs is not None
    assert client._login_kwargs["password"] == "new"


async def test_queued_old_logout_does_not_logout_new_session():
    client = MT5WebClient(timeout=0.02)
    entered = asyncio.Event()
    release = asyncio.Event()
    sent: list[int] = []

    async def send(command, _payload=None):
        sent.append(command)
        if command == CMD_LOGIN:
            entered.set()
            await release.wait()
            return CommandResult(command=CMD_LOGIN, code=0, body=bytes(168))
        return CommandResult(command=command, code=0, body=b"")

    client.transport.send_command = AsyncMock(side_effect=send)
    client.transport.close = AsyncMock()
    client.transport.connect = AsyncMock()
    old_login = asyncio.create_task(client.login(123, "old", auto_heartbeat=False))
    await entered.wait()
    old_logout = asyncio.create_task(client.logout())
    await asyncio.sleep(0)
    assert not old_logout.done()

    with pytest.raises(TimeoutError):
        await client.close()
    await client.connect()
    release.set()
    with pytest.raises(SessionError, match="login invalidated by close"):
        await old_login
    with pytest.raises(SessionError, match="logout invalidated by close"):
        await old_logout

    await client.login(456, "new", auto_heartbeat=False)
    assert sent == [CMD_LOGIN, CMD_LOGIN]
    assert client._logged_in
    assert client._login_kwargs is not None
    assert client._login_kwargs["password"] == "new"


async def test_unfinished_close_blocks_sessions_until_cleanup_finishes():
    client = MT5WebClient(timeout=0.02)
    close_entered = asyncio.Event()
    cancel_received = asyncio.Event()
    release_close = asyncio.Event()

    async def uncooperative_close():
        close_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_received.set()
            await release_close.wait()

    client.transport.close = AsyncMock(side_effect=uncooperative_close)
    client.transport.connect = AsyncMock()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=bytes(168))
    )
    close_task = asyncio.create_task(client.close())
    await close_entered.wait()
    await cancel_received.wait()
    with pytest.raises(TimeoutError):
        await close_task

    with pytest.raises(SessionError, match="close cleanup is still in progress"):
        await client.connect()
    with pytest.raises(SessionError, match="close cleanup is still in progress"):
        await client.login(123, "test")
    with pytest.raises(SessionError, match="close cleanup is still in progress"):
        await client.close()
    client.transport.connect.assert_not_awaited()
    client.transport.send_command.assert_not_awaited()
    client.transport.close.assert_awaited_once()

    release_close.set()
    assert client._unfinished_close_task is not None
    await asyncio.wait_for(client._unfinished_close_task, timeout=1)
    await client.connect()
    await client.login(123, "new", auto_heartbeat=False)
    assert client._logged_in
    assert client._login_kwargs is not None
    assert client._login_kwargs["password"] == "new"
    await asyncio.sleep(0)
    assert client._logged_in
    client.transport.close.assert_awaited_once()
    client.transport.send_command.assert_awaited_once()
