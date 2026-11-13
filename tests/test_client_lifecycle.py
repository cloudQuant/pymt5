"""Tests for client lifecycle methods: connect, close, shutdown, heartbeat,
reconnect, async context manager, bootstrap commands, credential handling,
and payload building for OTP setup.

Covers previously untested lines in pymt5/client.py:
  149-152, 163-165, 168-184, 188-192, 196, 203-204, 207, 212-214,
  218-219, 222-231, 247, 251-278, 307, 374, 378, 381-385, 403, 486
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_LOGIN, CMD_LOGOUT, CMD_PING
from pymt5.exceptions import AuthenticationError, MT5ConnectionError, ProtocolError, SessionError
from pymt5.transport import CommandResult, MT5WebSocketTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_login_body() -> bytes:
    """Build a minimal valid login response body (160 token + 8 session)."""
    token_bytes = bytes(160)
    session_id = (42).to_bytes(8, "little", signed=False)
    return token_bytes + session_id


def _ok_result(command: int = 0) -> CommandResult:
    return CommandResult(command=command, code=0, body=b"")


class _StatefulReconnectTransport(MT5WebSocketTransport):
    """Use the real ready/close/send guards with a controllable login response."""

    def __init__(self, *, fail_close: bool = False) -> None:
        super().__init__(uri="wss://example.com")
        self.fail_close = fail_close
        self.socket = MagicMock()
        self.socket.close = AsyncMock()

    async def connect(self) -> None:
        self.ws = self.socket
        self.is_ready = True

    async def close(self) -> None:
        if self._on_disconnect:
            self._on_disconnect()
        if self.fail_close:
            raise RuntimeError("close failed")
        await super().close()

    async def send_command(self, command: int, payload: bytes | None = None) -> CommandResult:
        if command == CMD_LOGIN and self.is_ready:
            return CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body())
        return await super().send_command(command, payload)


# ---------------------------------------------------------------------------
# connect() — lines 149-152
# ---------------------------------------------------------------------------


async def test_connect_sets_bootstrap_pristine():
    """connect() awaits transport.connect and sets _bootstrap_pristine."""
    client = MT5WebClient()
    client.transport.connect = AsyncMock()
    await client.connect()
    assert client._bootstrap_pristine is True
    client.transport.connect.assert_awaited_once()


async def test_connect_returns_self():
    """connect() returns the client instance for chaining."""
    client = MT5WebClient()
    client.transport.connect = AsyncMock()
    result = await client.connect()
    assert result is client


# ---------------------------------------------------------------------------
# initialize() — lines 163-165
# ---------------------------------------------------------------------------


async def test_initialize_connects_when_transport_not_ready():
    """initialize() calls connect() if transport.is_ready is False."""
    client = MT5WebClient()
    client.transport.is_ready = False
    client.transport.connect = AsyncMock()

    # After connect, transport.is_ready should be True for init_session
    async def _fake_connect():
        client.transport.is_ready = True

    client.transport.connect.side_effect = _fake_connect
    client.transport.send_command = AsyncMock(return_value=_ok_result())
    await client.initialize()
    client.transport.connect.assert_awaited_once()


async def test_initialize_skips_connect_when_ready():
    """initialize() does not call connect() when transport is already ready."""
    client = MT5WebClient()
    client.transport.is_ready = True
    client.transport.connect = AsyncMock()
    client.transport.send_command = AsyncMock(return_value=_ok_result())
    await client.initialize()
    client.transport.connect.assert_not_awaited()


# ---------------------------------------------------------------------------
# close() — lines 168-184
# ---------------------------------------------------------------------------


async def test_close_when_logged_in():
    """close() sends logout, stops heartbeat, and clears credentials."""
    client = MT5WebClient()
    client._logged_in = True
    client._login_kwargs = {"login": 123, "password": "test"}
    client._bootstrap_pristine = True
    client.transport.send_command = AsyncMock(return_value=_ok_result())
    client.transport.close = AsyncMock()

    await client.close()

    assert client._logged_in is False
    assert client._bootstrap_pristine is False
    assert client._login_kwargs is None
    assert client._closing is False
    client.transport.close.assert_awaited_once()


async def test_close_cancels_reconnect_task():
    """close() cancels any running reconnect task."""
    client = MT5WebClient()
    client._logged_in = False
    client.transport.close = AsyncMock()

    # Create a fake reconnect task
    async def _never_ending():
        await asyncio.sleep(999)

    client._reconnect_task = asyncio.create_task(_never_ending())
    await client.close()
    assert client._reconnect_task is None


async def test_close_stops_heartbeat():
    """close() cancels a running heartbeat task."""
    client = MT5WebClient()
    client._logged_in = False
    client.transport.close = AsyncMock()

    # Start a heartbeat
    client.transport.send_command = AsyncMock(return_value=_ok_result())
    client._heartbeat_interval = 999  # won't actually fire
    client._start_heartbeat()
    assert client._heartbeat_task is not None

    await client.close()
    assert client._heartbeat_task is None


async def test_close_swallows_logout_error():
    """close() catches errors from logout() and proceeds gracefully."""
    client = MT5WebClient()
    client._logged_in = True
    client.transport.send_command = AsyncMock(side_effect=RuntimeError("gone"))
    client.transport.close = AsyncMock()

    await client.close()
    assert client._logged_in is False
    client.transport.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _clear_credentials() — lines 188-192
# ---------------------------------------------------------------------------


def test_clear_credentials_wipes_password():
    """_clear_credentials() overwrites the password then clears kwargs."""
    client = MT5WebClient()
    client._login_kwargs = {"login": 1, "password": "secret123"}
    client._clear_credentials()
    assert client._login_kwargs is None


def test_clear_credentials_noop_when_none():
    """_clear_credentials() is safe to call when _login_kwargs is None."""
    client = MT5WebClient()
    assert client._login_kwargs is None
    client._clear_credentials()
    assert client._login_kwargs is None


def test_clear_credentials_handles_missing_password_key():
    """_clear_credentials() works even if 'password' key is absent."""
    client = MT5WebClient()
    client._login_kwargs = {"login": 1}
    client._clear_credentials()
    assert client._login_kwargs is None


# ---------------------------------------------------------------------------
# shutdown() — line 196
# ---------------------------------------------------------------------------


async def test_shutdown_delegates_to_close():
    """shutdown() is an alias that calls close()."""
    client = MT5WebClient()
    client._logged_in = False
    client.transport.close = AsyncMock()
    await client.shutdown()
    client.transport.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# __aenter__ / __aexit__ — lines 203-204, 207
# ---------------------------------------------------------------------------


async def test_async_context_manager_enter_exit():
    """async with MT5WebClient() calls connect on enter and close on exit."""
    client = MT5WebClient()
    client.transport.connect = AsyncMock()
    client.transport.close = AsyncMock()
    client.transport.send_command = AsyncMock(return_value=_ok_result())

    async with client as ctx:
        assert ctx is client
        assert client._bootstrap_pristine is True
        client.transport.connect.assert_awaited_once()

    client.transport.close.assert_awaited_once()


async def test_async_context_manager_close_on_exception():
    """__aexit__ is called even when an exception occurs inside the block."""
    client = MT5WebClient()
    client.transport.connect = AsyncMock()
    client.transport.close = AsyncMock()

    with pytest.raises(ValueError, match="boom"):
        async with client:
            raise ValueError("boom")

    client.transport.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _start_heartbeat / _stop_heartbeat — lines 212-214, 218-219
# ---------------------------------------------------------------------------


async def test_start_heartbeat_creates_task():
    """_start_heartbeat() creates a background heartbeat task."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=_ok_result())
    client._heartbeat_interval = 999

    client._start_heartbeat()
    assert client._heartbeat_task is not None
    assert not client._heartbeat_task.done()

    # Cleanup
    client._stop_heartbeat()


async def test_start_heartbeat_is_idempotent():
    """Calling _start_heartbeat() twice does not replace the existing task."""
    client = MT5WebClient()
    client._heartbeat_interval = 999
    client._start_heartbeat()
    first_task = client._heartbeat_task

    client._start_heartbeat()
    assert client._heartbeat_task is first_task

    # Cleanup
    client._stop_heartbeat()


async def test_stop_heartbeat_cancels_and_clears():
    """_stop_heartbeat() cancels the task and sets it to None."""
    client = MT5WebClient()
    client._heartbeat_interval = 999
    client._start_heartbeat()
    task = client._heartbeat_task
    assert task is not None

    client._stop_heartbeat()
    assert client._heartbeat_task is None
    # Give the event loop a chance to process the cancellation
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


def test_stop_heartbeat_noop_when_no_task():
    """_stop_heartbeat() is safe when no heartbeat is running."""
    client = MT5WebClient()
    assert client._heartbeat_task is None
    client._stop_heartbeat()
    assert client._heartbeat_task is None


# ---------------------------------------------------------------------------
# _heartbeat_loop() — lines 222-231
# ---------------------------------------------------------------------------


async def test_heartbeat_loop_calls_ping():
    """_heartbeat_loop sends ping, then can be cancelled."""
    client = MT5WebClient()
    client._heartbeat_interval = 0.01  # very fast
    ping_count = 0

    async def _count_ping(cmd, payload=b""):
        nonlocal ping_count
        ping_count += 1
        if ping_count >= 2:
            raise asyncio.CancelledError
        return _ok_result(CMD_PING)

    client.transport.send_command = AsyncMock(side_effect=_count_ping)
    # Run the heartbeat briefly
    client._start_heartbeat()
    await asyncio.sleep(0.05)
    client._stop_heartbeat()
    assert ping_count >= 1


async def test_heartbeat_loop_handles_ping_failure():
    """Ping failure inside the heartbeat loop is caught, loop continues."""
    client = MT5WebClient()
    client._heartbeat_interval = 0.01
    call_count = 0

    async def _failing_ping(cmd, payload=b""):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise MT5ConnectionError("network error")
        return _ok_result(CMD_PING)

    client.transport.send_command = AsyncMock(side_effect=_failing_ping)
    client._start_heartbeat()
    await asyncio.sleep(0.08)
    client._stop_heartbeat()
    # Should have been called multiple times despite failures
    assert call_count >= 2


async def test_heartbeat_loop_stops_on_cancel():
    """_heartbeat_loop exits cleanly on CancelledError (it catches it)."""
    client = MT5WebClient()
    client._heartbeat_interval = 0.01
    client.transport.send_command = AsyncMock(return_value=_ok_result(CMD_PING))

    task = asyncio.create_task(client._heartbeat_loop())
    await asyncio.sleep(0.03)
    task.cancel()
    # The heartbeat loop catches CancelledError and returns cleanly
    await task
    assert task.done()


# ---------------------------------------------------------------------------
# _handle_disconnect() — line 247
# ---------------------------------------------------------------------------


async def test_handle_disconnect_triggers_reconnect():
    """_handle_disconnect creates a reconnect task when auto_reconnect is on."""
    client = MT5WebClient(auto_reconnect=True)
    client._login_kwargs = {"login": 123, "password": "test"}
    client._logged_in = True

    client._handle_disconnect()

    assert client._logged_in is False
    assert client._bootstrap_pristine is False
    assert client._reconnect_task is not None

    # Cleanup
    client._reconnect_task.cancel()
    try:
        await client._reconnect_task
    except (asyncio.CancelledError, Exception):
        pass


def test_handle_disconnect_no_reconnect_when_closing():
    """_handle_disconnect does not reconnect when _closing is True."""
    client = MT5WebClient(auto_reconnect=True)
    client._login_kwargs = {"login": 123, "password": "test"}
    client._closing = True

    client._handle_disconnect()

    assert client._reconnect_task is None


def test_handle_disconnect_no_reconnect_without_credentials():
    """_handle_disconnect does not reconnect if _login_kwargs is None."""
    client = MT5WebClient(auto_reconnect=True)
    client._login_kwargs = None

    client._handle_disconnect()

    assert client._reconnect_task is None


def test_handle_disconnect_invokes_user_callback():
    """_handle_disconnect calls the registered on_disconnect callback."""
    client = MT5WebClient()
    events = []
    client.on_disconnect(lambda: events.append("disconnected"))

    client._handle_disconnect()

    assert events == ["disconnected"]


# ---------------------------------------------------------------------------
# _reconnect_loop() — lines 251-278
# ---------------------------------------------------------------------------


async def test_reconnect_loop_success():
    """_reconnect_loop reconnects, re-logs-in, and re-subscribes."""
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=2,
        reconnect_delay=0.001,
    )
    client._login_kwargs = {"login": 123, "password": "test"}
    client._subscribed_ids = [1, 2]
    client._subscribed_book_ids = [3]

    login_body = _make_login_body()

    # Create a mock transport that the constructor will return
    new_transport = MagicMock()
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body),
    )
    new_transport._listeners = {}
    new_transport._on_disconnect = None
    new_transport.on = MagicMock()

    # Also mock subscribe_ticks and subscribe_book on the client
    client.subscribe_ticks = AsyncMock()
    client.subscribe_book = AsyncMock()
    client.login = AsyncMock(return_value=("token_hex", 42))

    # Mock close on old transport
    client.transport.close = AsyncMock()

    with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
        await client._reconnect_loop()

    client.login.assert_awaited_once()
    # Verify auto_heartbeat was passed
    call_kwargs = client.login.call_args[1]
    assert call_kwargs["auto_heartbeat"] is True
    assert call_kwargs["login"] == 123
    assert call_kwargs["password"] == "test"

    # Re-subscriptions should have been called
    client.subscribe_ticks.assert_awaited_once_with([1, 2])
    client.subscribe_book.assert_awaited_once_with([3])


async def test_reconnect_loop_all_attempts_exhausted():
    """_reconnect_loop logs error after all attempts fail."""
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=2,
        reconnect_delay=0.001,
    )
    client._login_kwargs = {"login": 123, "password": "test"}
    client.transport.close = AsyncMock()

    # Make every reconnect attempt fail
    failing_transport = MagicMock()
    failing_transport.connect = AsyncMock(side_effect=MT5ConnectionError("refused"))
    failing_transport.close = AsyncMock()
    failing_transport._on_disconnect = None
    failing_transport._listeners = {}
    failing_transport.on = MagicMock()

    with patch("pymt5.client.MT5WebSocketTransport", return_value=failing_transport):
        await client._reconnect_loop()

    # Should have tried max_reconnect_attempts times
    assert failing_transport.connect.await_count == 2


async def test_reconnect_loop_succeeds_on_second_attempt():
    """_reconnect_loop succeeds after first attempt fails."""
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=3,
        reconnect_delay=0.001,
    )
    client._login_kwargs = {"login": 456, "password": "pw"}
    client._subscribed_ids = []
    client._subscribed_book_ids = []
    client.transport.close = AsyncMock()

    attempt_count = 0

    login_body = _make_login_body()

    def _make_transport(uri, timeout=30.0, **_kwargs):
        nonlocal attempt_count
        attempt_count += 1
        t = MagicMock()
        t._on_disconnect = None
        t._listeners = {}
        t.on = MagicMock()
        t.close = AsyncMock()
        if attempt_count == 1:
            t.connect = AsyncMock(side_effect=MT5ConnectionError("timeout"))
        else:
            t.connect = AsyncMock()
            t.is_ready = True
            t.send_command = AsyncMock(
                return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body),
            )
        return t

    client.login = AsyncMock(return_value=("token", 42))

    with patch("pymt5.client.MT5WebSocketTransport", side_effect=_make_transport):
        await client._reconnect_loop()

    # First failed, second succeeded
    assert attempt_count == 2
    client.login.assert_awaited_once()


@pytest.mark.parametrize("failure", ["login", "subscribe"])
async def test_reconnect_real_login_retries_after_failure(failure: str):
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=2, reconnect_delay=0)
    client._login_kwargs = {"login": 456, "password": "pw"}
    client.transport.close = AsyncMock()
    if failure == "subscribe":
        client._subscribed_ids = [1]
        client.subscribe_ticks = AsyncMock(side_effect=[SessionError("subscription failed"), None])

    transports = []
    for index in range(2):
        transport = MagicMock()
        transport.connect = AsyncMock()
        transport.close = AsyncMock()
        code = 3 if failure == "login" and index == 0 else 0
        transport.send_command = AsyncMock(
            return_value=CommandResult(command=CMD_LOGIN, code=code, body=_make_login_body())
        )
        transports.append(transport)

    with patch("pymt5.client.MT5WebSocketTransport", side_effect=transports):
        await client._reconnect_loop()

    for transport in transports:
        assert transport.send_command.await_count == 1
        assert transport.send_command.await_args.args[0] == CMD_LOGIN
    assert client._logged_in is True
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 456
    assert client._login_kwargs["password"] == "pw"
    assert client._reconnect_count == 1
    client._stop_heartbeat()


async def test_reconnect_exhausted_after_subscribe_failure_clears_login_state():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 456, "password": "pw"}
    client._subscribed_ids = [1]
    client.subscribe_ticks = AsyncMock(side_effect=SessionError("subscription failed"))
    client.transport.close = AsyncMock()
    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.close = AsyncMock()
    transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body())
    )
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        await client._reconnect_loop()
    assert client._logged_in is False
    assert client._heartbeat_task is None
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 456


@pytest.mark.parametrize("fail_close", [False, True])
async def test_reconnect_exhausted_disables_authenticated_transport(fail_close: bool):
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 456, "password": "pw"}
    client._subscribed_ids = [1]
    client.subscribe_ticks = AsyncMock(side_effect=SessionError("subscription failed"))
    transport = _StatefulReconnectTransport(fail_close=fail_close)
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        await client._reconnect_loop()
    assert not transport.is_ready
    assert transport._on_disconnect is None
    assert client._reconnect_task is None
    assert client._login_kwargs is not None
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)
    if not fail_close:
        transport.socket.close.assert_awaited_once()


async def test_cancelled_reconnect_disables_authenticated_transport():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 456, "password": "pw"}
    client._subscribed_ids = [1]
    started = asyncio.Event()

    async def wait_for_subscription(_ids):
        started.set()
        await asyncio.Event().wait()

    client.subscribe_ticks = AsyncMock(side_effect=wait_for_subscription)
    transport = _StatefulReconnectTransport()
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        task = asyncio.create_task(client._reconnect_loop())
        client._reconnect_task = task
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not transport.is_ready
    assert transport._on_disconnect is None
    assert client._login_kwargs is not None
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)
    transport.socket.close.assert_awaited_once()


async def test_cancellation_during_failure_cleanup_waits_for_transport_close():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 456, "password": "pw"}
    client._subscribed_ids = [1]
    client.subscribe_ticks = AsyncMock(side_effect=SessionError("subscription failed"))
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_cancelled = False
    transport = _StatefulReconnectTransport()

    async def delayed_close():
        nonlocal close_cancelled
        close_started.set()
        try:
            await allow_close.wait()
        except asyncio.CancelledError:
            close_cancelled = True
            raise

    transport.socket.close = AsyncMock(side_effect=delayed_close)
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        task = asyncio.create_task(client._reconnect_loop())
        client._reconnect_task = task
        await close_started.wait()
        assert not transport.is_ready
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not close_cancelled
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    transport.socket.close.assert_awaited_once()
    assert not close_cancelled
    assert transport.ws is None
    assert not transport.is_ready
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)


async def test_reconnect_close_timeout_remains_nonready():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0, timeout=0.05)
    client._login_kwargs = {"login": 456, "password": "pw"}
    client._subscribed_ids = [1]
    client.subscribe_ticks = AsyncMock(side_effect=SessionError("subscription failed"))
    close_cancelled = asyncio.Event()
    transport = _StatefulReconnectTransport()

    async def never_close():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_cancelled.set()
            raise

    transport.socket.close = AsyncMock(side_effect=never_close)
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        await asyncio.wait_for(client._reconnect_loop(), timeout=1)
        await asyncio.wait_for(close_cancelled.wait(), timeout=1)
    assert not transport.is_ready
    assert transport._on_disconnect is None
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)


async def test_reconnect_loop_no_resubscribe_without_subscriptions():
    """_reconnect_loop skips re-subscribe when lists are empty."""
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=1,
        reconnect_delay=0.001,
    )
    client._login_kwargs = {"login": 1, "password": "x"}
    client._subscribed_ids = []
    client._subscribed_book_ids = []
    client.transport.close = AsyncMock()

    login_body = _make_login_body()
    new_transport = MagicMock()
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport._on_disconnect = None
    new_transport._listeners = {}
    new_transport.on = MagicMock()
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body),
    )

    client.subscribe_ticks = AsyncMock()
    client.subscribe_book = AsyncMock()
    client.login = AsyncMock(return_value=("tok", 1))

    with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
        await client._reconnect_loop()

    client.subscribe_ticks.assert_not_awaited()
    client.subscribe_book.assert_not_awaited()


async def test_reconnect_loop_closes_old_transport_even_on_error():
    """_reconnect_loop tries to close old transport before each attempt."""
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=1,
        reconnect_delay=0.001,
    )
    client._login_kwargs = {"login": 1, "password": "x"}
    # Save reference to the old transport before it gets replaced
    old_transport = client.transport
    old_transport.close = AsyncMock(side_effect=SessionError("already closed"))

    failing_transport = MagicMock()
    failing_transport.connect = AsyncMock(side_effect=MT5ConnectionError("fail"))
    failing_transport.close = AsyncMock()
    failing_transport._on_disconnect = None
    failing_transport._listeners = {}
    failing_transport.on = MagicMock()

    with patch("pymt5.client.MT5WebSocketTransport", return_value=failing_transport):
        await client._reconnect_loop()

    # Old transport close was attempted despite it raising
    old_transport.close.assert_awaited()


# ---------------------------------------------------------------------------
# send_bootstrap_command_52() — line 307
# ---------------------------------------------------------------------------


async def test_bootstrap_command_52_raises_when_not_ready():
    """send_bootstrap_command_52() raises RuntimeError if transport not ready."""
    client = MT5WebClient()
    client.transport.is_ready = False

    with pytest.raises(RuntimeError, match="transport not ready"):
        await client.send_bootstrap_command_52()


async def test_bootstrap_command_52_raises_after_login():
    """send_bootstrap_command_52() raises when session is not pristine."""
    client = MT5WebClient()
    client.transport.is_ready = True
    client._logged_in = True
    client._bootstrap_pristine = True

    with pytest.raises(RuntimeError, match="only safe on a fresh bootstrap"):
        await client.send_bootstrap_command_52()


# ---------------------------------------------------------------------------
# login() auto_heartbeat — line 374
# ---------------------------------------------------------------------------


async def test_login_starts_heartbeat_when_auto():
    """login() with auto_heartbeat=True starts the heartbeat task."""
    client = MT5WebClient()
    login_body = _make_login_body()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body),
    )
    client.transport.is_ready = True

    token, session = await client.login(login=12345, password="pw", auto_heartbeat=True)

    assert client._heartbeat_task is not None
    assert client._logged_in is True
    assert session == 42

    # Cleanup
    client._stop_heartbeat()


async def test_login_stores_credentials():
    """login() stores _login_kwargs for potential reconnect."""
    client = MT5WebClient()
    login_body = _make_login_body()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body),
    )

    await client.login(
        login=999,
        password="pw123",
        url="https://example.com",
        auto_heartbeat=False,
    )

    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 999
    assert client._login_kwargs["password"] == "pw123"
    assert client._login_kwargs["url"] == "https://example.com"


@pytest.mark.parametrize("body", [b"", _make_login_body()])
async def test_login_rejection_reports_response_code_without_authenticating(body: bytes):
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=3, body=body),
    )
    client._start_heartbeat = MagicMock()
    with pytest.raises(AuthenticationError, match="response code 3"):
        await client.login(login=999, password="invalid")
    assert client._logged_in is False
    assert client._login_kwargs is None
    client._start_heartbeat.assert_not_called()


@pytest.mark.parametrize("body", [b"", _make_login_body()[:-1]])
async def test_login_success_with_truncated_body_is_protocol_error(body: bytes):
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=body),
    )
    client._start_heartbeat = MagicMock()
    with pytest.raises(ProtocolError, match="buffer too short"):
        await client.login(login=999, password="pw")
    assert client._logged_in is False
    assert client._login_kwargs is None
    client._start_heartbeat.assert_not_called()


async def test_login_while_logged_in_requires_logout_without_sending():
    client = MT5WebClient()
    client._logged_in = True
    client._login_kwargs = {"login": 123, "password": "old"}
    client.transport.send_command = AsyncMock()
    with pytest.raises(SessionError, match="logout"):
        await client.login(login=456, password="new")
    client.transport.send_command.assert_not_awaited()
    assert client._logged_in is True
    assert client._login_kwargs == {"login": 123, "password": "old"}


async def test_logout_waits_for_inflight_login_then_invalidates_session():
    client = MT5WebClient()
    login_sent = asyncio.Event()
    release_login = asyncio.Event()
    logout_started = asyncio.Event()
    sent = []

    async def send(command, payload=None):
        sent.append(command)
        if command == CMD_LOGIN:
            login_sent.set()
            await release_login.wait()
            return CommandResult(command=command, code=0, body=_make_login_body())
        return _ok_result(command)

    async def logout():
        logout_started.set()
        await client.logout()

    client.transport.send_command = AsyncMock(side_effect=send)
    login_task = asyncio.create_task(client.login(login=123, password="old", auto_heartbeat=False))
    await login_sent.wait()
    logout_task = asyncio.create_task(logout())
    await logout_started.wait()
    assert sent == [CMD_LOGIN]
    assert not logout_task.done()
    release_login.set()
    await asyncio.gather(login_task, logout_task)
    assert sent == [CMD_LOGIN, CMD_LOGOUT]
    assert client._logged_in is False
    assert client._login_kwargs is None
    await client.login(login=456, password="new", auto_heartbeat=False)
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 456


async def test_close_waits_for_inflight_login_then_closes_session():
    client = MT5WebClient()
    transport = _StatefulReconnectTransport()
    await transport.connect()
    client.transport = transport
    login_sent = asyncio.Event()
    release_login = asyncio.Event()
    close_started = asyncio.Event()
    normal_send = transport.send_command
    sent = []

    async def send(command, payload=None):
        sent.append(command)
        if command == CMD_LOGIN:
            login_sent.set()
            await release_login.wait()
            return await normal_send(command, payload)
        if command == CMD_LOGOUT:
            return _ok_result(CMD_LOGOUT)
        return await normal_send(command, payload)

    async def close():
        close_started.set()
        await client.close()

    transport.send_command = AsyncMock(side_effect=send)
    login_task = asyncio.create_task(client.login(login=123, password="old", auto_heartbeat=False))
    await login_sent.wait()
    close_task = asyncio.create_task(close())
    await close_started.wait()
    assert not close_task.done()
    assert sent == [CMD_LOGIN]
    release_login.set()
    await asyncio.gather(login_task, close_task)
    assert sent == [CMD_LOGIN, CMD_LOGOUT]
    assert client._logged_in is False
    assert client._login_kwargs is None
    assert not transport.is_ready
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)


async def test_close_stops_late_heartbeat_if_logout_fails():
    client = MT5WebClient()
    transport = _StatefulReconnectTransport()
    await transport.connect()
    client.transport = transport
    login_sent = asyncio.Event()
    release_login = asyncio.Event()
    close_started = asyncio.Event()
    normal_send = transport.send_command
    heartbeats = []
    start_heartbeat = client._start_heartbeat

    def record_heartbeat():
        start_heartbeat()
        heartbeats.append(client._heartbeat_task)

    async def send(command, payload=None):
        if command == CMD_LOGIN:
            login_sent.set()
            await release_login.wait()
            return await normal_send(command, payload)
        if command == CMD_LOGOUT:
            raise SessionError("logout failed")
        return await normal_send(command, payload)

    async def close():
        close_started.set()
        await client.close()

    client._start_heartbeat = record_heartbeat
    transport.send_command = AsyncMock(side_effect=send)
    login_task = asyncio.create_task(client.login(login=123, password="old", auto_heartbeat=True))
    await login_sent.wait()
    close_task = asyncio.create_task(close())
    await close_started.wait()
    assert not close_task.done()
    release_login.set()
    await asyncio.gather(login_task, close_task)
    await asyncio.sleep(0)
    assert len(heartbeats) == 1
    assert heartbeats[0] is not None and heartbeats[0].done()
    assert client._heartbeat_task is None
    assert client._logged_in is False
    assert client._login_kwargs is None
    assert not transport.is_ready


async def test_logout_cancels_inflight_reconnect_without_reauthenticating_old_account():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 123, "password": "old"}
    client._subscribed_ids = [1]
    started = asyncio.Event()

    async def wait_for_subscription(_ids):
        started.set()
        await asyncio.Event().wait()

    client.subscribe_ticks = AsyncMock(side_effect=wait_for_subscription)
    transport = _StatefulReconnectTransport()
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        reconnect = asyncio.create_task(client._reconnect_loop())
        client._reconnect_task = reconnect
        await started.wait()
        await client.logout()
    assert reconnect.cancelled()
    assert client._reconnect_task is None
    assert client._logged_in is False
    assert client._login_kwargs is None
    assert not transport.is_ready
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)
    # A caller can reconnect explicitly and then log in with new credentials.
    await transport.connect()
    await client.login(login=456, password="new", auto_heartbeat=False)
    assert client._logged_in is True
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 456


async def test_logout_during_reconnect_login_prevents_late_old_session():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 123, "password": "old"}
    client._subscribed_ids = [1]
    login_sent = asyncio.Event()
    release_login = asyncio.Event()
    logout_started = asyncio.Event()
    transport = _StatefulReconnectTransport()
    normal_send = transport.send_command

    async def delayed_send(command, payload=None):
        if command == CMD_LOGIN:
            login_sent.set()
            await release_login.wait()
        return await normal_send(command, payload)

    async def wait_for_subscription(_ids):
        await asyncio.Event().wait()

    async def logout():
        logout_started.set()
        await client.logout()

    transport.send_command = AsyncMock(side_effect=delayed_send)
    client.subscribe_ticks = AsyncMock(side_effect=wait_for_subscription)
    with patch("pymt5.client.MT5WebSocketTransport", return_value=transport):
        reconnect = asyncio.create_task(client._reconnect_loop())
        client._reconnect_task = reconnect
        await login_sent.wait()
        logout_task = asyncio.create_task(logout())
        await logout_started.wait()
        assert not logout_task.done()
        release_login.set()
        await logout_task
    assert reconnect.cancelled()
    assert not transport.is_ready
    assert client._logged_in is False
    assert client._login_kwargs is None
    with pytest.raises(SessionError, match="transport not ready"):
        await client.trade_request(trade_action=1, volume=1)


async def test_logout_clears_credentials_then_allows_new_login():
    client = MT5WebClient(auto_reconnect=True)
    client._logged_in = True
    client._login_kwargs = {"login": 123, "password": "old"}
    client.transport.send_command = AsyncMock(side_effect=[
        _ok_result(CMD_LOGOUT),
        CommandResult(command=CMD_LOGIN, code=0, body=_make_login_body()),
    ])
    await client.logout()
    assert client._logged_in is False
    assert client._login_kwargs is None
    client._handle_disconnect()
    assert client._reconnect_task is None
    await client.login(login=456, password="new", auto_heartbeat=False)
    assert client._logged_in is True
    assert client._login_kwargs is not None
    assert client._login_kwargs["login"] == 456


async def test_logout_rejection_preserves_session_and_credentials():
    client = MT5WebClient()
    client._logged_in = True
    client._login_kwargs = {"login": 123, "password": "old"}
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_LOGOUT, code=3, body=b""))
    with pytest.raises(SessionError, match="response code 3"):
        await client.logout()
    assert client._logged_in is True
    assert client._login_kwargs == {"login": 123, "password": "old"}


# ---------------------------------------------------------------------------
# ping() — line 378
# ---------------------------------------------------------------------------


async def test_ping_sends_command():
    """ping() sends CMD_PING via transport."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=_ok_result(CMD_PING))

    await client.ping()

    client.transport.send_command.assert_awaited_once_with(CMD_PING)


# ---------------------------------------------------------------------------
# logout() — lines 381-385
# ---------------------------------------------------------------------------


async def test_logout_sends_command_and_clears_state():
    """logout() stops heartbeat, sends CMD_LOGOUT, clears flags."""
    client = MT5WebClient()
    client._logged_in = True
    client._bootstrap_pristine = True
    client._heartbeat_interval = 999
    client._start_heartbeat()
    assert client._heartbeat_task is not None

    client.transport.send_command = AsyncMock(return_value=_ok_result(CMD_LOGOUT))

    await client.logout()

    assert client._logged_in is False
    assert client._bootstrap_pristine is False
    assert client._heartbeat_task is None
    client.transport.send_command.assert_awaited_once_with(CMD_LOGOUT)


# ---------------------------------------------------------------------------
# _resolve_client_id() — line 403
# ---------------------------------------------------------------------------


def test_resolve_client_id_valid_16_bytes():
    """_resolve_client_id accepts exactly 16 bytes."""
    client = MT5WebClient()
    cid = b"\x01" * 16
    result = client._resolve_client_id(cid)
    assert result == cid


def test_resolve_client_id_wrong_length_raises():
    """_resolve_client_id raises ValueError for non-16-byte input."""
    client = MT5WebClient()
    with pytest.raises(ValueError, match="cid must be 16 bytes"):
        client._resolve_client_id(b"short")


def test_resolve_client_id_too_long_raises():
    """_resolve_client_id raises ValueError for >16-byte input."""
    client = MT5WebClient()
    with pytest.raises(ValueError, match="cid must be 16 bytes"):
        client._resolve_client_id(b"\x00" * 20)


def test_resolve_client_id_none_generates_default():
    """_resolve_client_id(None) generates a valid 16-byte client ID."""
    client = MT5WebClient()
    result = client._resolve_client_id(None)
    assert len(result) == 16
    assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# _build_otp_setup_payload() — line 486
# ---------------------------------------------------------------------------


def test_build_otp_setup_payload_with_short_password():
    """_build_otp_setup_payload with a short password does not append blob."""
    client = MT5WebClient()
    payload = client._build_otp_setup_payload(
        login=12345,
        password="mypassword",
        cid=b"\x00" * 16,
    )
    assert isinstance(payload, bytes)
    assert len(payload) > 0


def test_build_otp_setup_payload_with_hex_password_blob():
    """_build_otp_setup_payload with a 320-char hex password appends blob."""
    client = MT5WebClient()
    hex_password = "ab" * 160  # 320 hex chars = 160 bytes
    payload = client._build_otp_setup_payload(
        login=12345,
        password=hex_password,
        cid=b"\x00" * 16,
    )
    # The payload with blob appended should be longer than without
    payload_short = client._build_otp_setup_payload(
        login=12345,
        password="short",
        cid=b"\x00" * 16,
    )
    assert len(payload) > len(payload_short)


def test_build_otp_setup_payload_includes_otp_fields():
    """_build_otp_setup_payload serializes otp_secret and check fields."""
    client = MT5WebClient()
    payload = client._build_otp_setup_payload(
        login=1,
        password="pw",
        otp="123456",
        otp_secret="JBSWY3DPEHPK3PXP",
        otp_secret_check="654321",
        cid=b"\x00" * 16,
    )
    assert isinstance(payload, bytes)
    assert len(payload) > 0


# ---------------------------------------------------------------------------
# Full lifecycle integration: connect -> login -> logout -> close
# ---------------------------------------------------------------------------


async def test_full_lifecycle():
    """Exercise the full connect -> login -> ping -> logout -> close path."""
    client = MT5WebClient()
    login_body = _make_login_body()

    client.transport.connect = AsyncMock()
    client.transport.close = AsyncMock()

    call_responses = {
        CMD_LOGIN: CommandResult(command=CMD_LOGIN, code=0, body=login_body),
        CMD_PING: _ok_result(CMD_PING),
        CMD_LOGOUT: _ok_result(CMD_LOGOUT),
    }

    async def _route(cmd, payload=b""):
        return call_responses.get(cmd, _ok_result(cmd))

    client.transport.send_command = AsyncMock(side_effect=_route)

    # Connect
    await client.connect()
    assert client._bootstrap_pristine is True

    # Login
    token, session = await client.login(login=1, password="pw", auto_heartbeat=False)
    assert client._logged_in is True
    assert session == 42

    # Ping
    await client.ping()

    # Logout
    await client.logout()
    assert client._logged_in is False

    # Close
    await client.close()
    client.transport.close.assert_awaited()


# ---------------------------------------------------------------------------
# Context manager with login inside
# ---------------------------------------------------------------------------


async def test_context_manager_with_login():
    """Test async context manager with login/logout inside the block."""
    client = MT5WebClient()
    login_body = _make_login_body()

    client.transport.connect = AsyncMock()
    client.transport.close = AsyncMock()

    call_responses = {
        CMD_LOGIN: CommandResult(command=CMD_LOGIN, code=0, body=login_body),
        CMD_LOGOUT: _ok_result(CMD_LOGOUT),
    }

    async def _route(cmd, payload=b""):
        return call_responses.get(cmd, _ok_result(cmd))

    client.transport.send_command = AsyncMock(side_effect=_route)

    async with client:
        token, session = await client.login(
            login=1,
            password="pw",
            auto_heartbeat=False,
        )
        assert client._logged_in is True
        assert client._login_kwargs is not None

    # After exiting, close() should have been called, clearing state
    assert client._login_kwargs is None
    assert client._bootstrap_pristine is False


# ---------------------------------------------------------------------------
# _handle_disconnect with reconnect already in progress
# ---------------------------------------------------------------------------


async def test_handle_disconnect_skips_duplicate_reconnect():
    """_handle_disconnect does not start another reconnect if one runs."""
    client = MT5WebClient(auto_reconnect=True)
    client._login_kwargs = {"login": 1, "password": "x"}

    # Simulate an in-progress reconnect task
    async def _long_running():
        await asyncio.sleep(999)

    client._reconnect_task = asyncio.create_task(_long_running())
    original_task = client._reconnect_task

    # Second disconnect should not replace the task
    client._handle_disconnect()
    assert client._reconnect_task is original_task

    # Cleanup
    client._reconnect_task.cancel()
    try:
        await client._reconnect_task
    except (asyncio.CancelledError, Exception):
        pass


# ---------------------------------------------------------------------------
# Reconnect loop re-wires disconnect handler
# ---------------------------------------------------------------------------


async def test_reconnect_loop_replaces_transport():
    """After reconnect, client.transport is replaced with a new instance."""
    client = MT5WebClient(
        auto_reconnect=True,
        max_reconnect_attempts=1,
        reconnect_delay=0.001,
    )
    client._login_kwargs = {"login": 1, "password": "x"}
    client._subscribed_ids = []
    client._subscribed_book_ids = []
    old_transport = client.transport
    old_transport.close = AsyncMock()

    login_body = _make_login_body()
    new_transport = MagicMock()
    new_transport.connect = AsyncMock()
    new_transport.close = AsyncMock()
    new_transport.is_ready = True
    new_transport._on_disconnect = None
    new_transport._listeners = {}
    new_transport.on = MagicMock()
    new_transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_LOGIN, code=0, body=login_body),
    )

    client.login = AsyncMock(return_value=("tok", 1))

    with patch("pymt5.client.MT5WebSocketTransport", return_value=new_transport):
        await client._reconnect_loop()

    # The client transport should be the new one
    assert client.transport is new_transport
    # connect was called on the new transport
    new_transport.connect.assert_awaited_once()
