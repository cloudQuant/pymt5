"""Disconnect callbacks cannot prevent transport EOF recovery."""

from unittest.mock import AsyncMock, patch

from pymt5.client import MT5WebClient
from pymt5.constants import CMD_LOGIN
from pymt5.transport import CommandResult, MT5WebSocketTransport, TransportState


class EmptyWebSocket:
    def __init__(self):
        self.close = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def raise_from_callback(events):
    events.append("disconnect")
    raise RuntimeError("sensitive-callback-message")


async def test_eof_callback_error_is_reported_and_auto_reconnect_succeeds():
    client = MT5WebClient(auto_reconnect=True, max_reconnect_attempts=1, reconnect_delay=0)
    client._login_kwargs = {"login": 123, "password": "test"}
    client._logged_in = True
    old_transport = client.transport
    old_transport.ws = EmptyWebSocket()
    old_transport.is_ready = True
    events = []
    client.on_disconnect(lambda: raise_from_callback(events))

    replacement = MT5WebSocketTransport("ws://offline.invalid")

    async def connect():
        replacement.is_ready = True

    replacement.connect = AsyncMock(side_effect=connect)
    replacement.send_command = AsyncMock(return_value=CommandResult(CMD_LOGIN, 0, bytes(168)))
    try:
        with (
            patch("pymt5.client.MT5WebSocketTransport", return_value=replacement),
            patch("pymt5.client.logger.error") as log_error,
        ):
            await old_transport._recv_loop()
            task = client._reconnect_task
            assert task is not None
            await old_transport._recv_loop()
            assert client._reconnect_task is task
            await task

        assert events == ["disconnect"]
        log_error.assert_called_once_with("disconnect callback failed: %s", "RuntimeError")
        assert "sensitive-callback-message" not in str(log_error.call_args)
        replacement.connect.assert_awaited_once()
        replacement.send_command.assert_awaited_once()
        assert client.transport is replacement
        assert client.is_connected
        assert client._reconnect_count == 1
    finally:
        client._stop_heartbeat()


async def test_eof_callback_error_does_not_enable_auto_reconnect():
    client = MT5WebClient(auto_reconnect=False)
    client._login_kwargs = {"login": 123, "password": "test"}
    client._logged_in = True
    transport = client.transport
    transport.ws = EmptyWebSocket()
    transport.is_ready = True
    events = []
    client.on_disconnect(lambda: raise_from_callback(events))

    with patch("pymt5.client.logger.error") as log_error:
        await transport._recv_loop()

    assert events == ["disconnect"]
    log_error.assert_called_once_with("disconnect callback failed: %s", "RuntimeError")
    assert client._reconnect_task is None
    assert not client._logged_in
    assert transport.state is TransportState.ERROR
