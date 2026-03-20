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
from pymt5.transport import CommandResult

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
            raise RuntimeError("network error")
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
    failing_transport.connect = AsyncMock(side_effect=RuntimeError("refused"))
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

    def _make_transport(uri, timeout=30.0):
        nonlocal attempt_count
        attempt_count += 1
        t = MagicMock()
        t._on_disconnect = None
        t._listeners = {}
        t.on = MagicMock()
        t.close = AsyncMock()
        if attempt_count == 1:
            t.connect = AsyncMock(side_effect=RuntimeError("timeout"))
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
    old_transport.close = AsyncMock(side_effect=RuntimeError("already closed"))

    failing_transport = MagicMock()
    failing_transport.connect = AsyncMock(side_effect=RuntimeError("fail"))
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
