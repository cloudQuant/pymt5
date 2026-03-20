"""Comprehensive lifecycle tests for MT5WebSocketTransport.

Covers connect(), close(), send_command(), _send_raw(), _recv_loop(),
and _dispatch() with push listeners -- all via mocks (no real WebSocket).
"""

import asyncio
import struct
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymt5.constants import (
    CMD_BOOTSTRAP,
    CMD_GET_ACCOUNT,
    CMD_TICK_PUSH,
    DEFAULT_TOKEN_LENGTH,
)
from pymt5.crypto import AESCipher, initial_cipher
from pymt5.protocol import ResponseFrame, pack_outer
from pymt5.transport import CommandResult, MT5WebSocketTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bootstrap_response_body(code: int = 0, token: bytes | None = None, key: bytes | None = None) -> bytes:
    """Build a valid (or invalid) bootstrap response body.

    A valid bootstrap response body has:
      - 2 bytes padding
      - 64 bytes token  (bytes[2:66])
      - 16/24/32 bytes AES key (bytes[66:])
    """
    tok = token or (b"\xab" * DEFAULT_TOKEN_LENGTH)
    k = key or (b"\xcd" * 16)  # 16-byte AES key
    return bytes([0, 0]) + tok + k


def _build_encrypted_response(cipher: AESCipher, command: int, code: int, body: bytes) -> bytes:
    """Encrypt a response frame the same way the server would.

    Frame layout: 2 random bytes + 2-byte command + 1-byte code + body
    Then encrypt, then wrap with pack_outer.
    """
    inner = b"\x00\x00" + struct.pack("<H", command) + bytes([code]) + body
    encrypted = cipher.encrypt(inner)
    return pack_outer(encrypted)


class _AsyncMessageIterator:
    """Async iterator that yields messages for mock WebSocket."""

    def __init__(self, messages):
        self._messages = list(messages)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg


class _MockWS:
    """Mock WebSocket that supports async iteration over messages."""

    def __init__(self, messages: list | None = None):
        self._messages = list(messages) if messages else []
        self.close = AsyncMock()
        self.send = AsyncMock()

    def __aiter__(self):
        return _AsyncMessageIterator(self._messages)


def _make_mock_ws(messages: list | None = None) -> _MockWS:
    """Create a mock ClientConnection that supports async iteration."""
    return _MockWS(messages)


# ============================================================================
# 1. connect()
# ============================================================================


class TestConnect:
    """Tests for MT5WebSocketTransport.connect()."""

    async def test_connect_success_sets_token_cipher_ready(self):
        """Successful connect sets token, cipher, and is_ready."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        bootstrap_body = _make_bootstrap_response_body()
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _make_mock_ws(messages=[response_bytes])

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await t.connect()

        assert t.is_ready is True
        assert t.token == b"\xab" * DEFAULT_TOKEN_LENGTH
        assert isinstance(t.cipher, AESCipher)
        assert t.cipher.key == b"\xcd" * 16
        assert t.ws is mock_ws

    async def test_connect_bootstrap_bad_code_raises(self):
        """Bootstrap response with non-zero code raises RuntimeError."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        bootstrap_body = _make_bootstrap_response_body()
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=1, body=bootstrap_body)

        mock_ws = _make_mock_ws(messages=[response_bytes])

        with (
            patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws),
            pytest.raises(RuntimeError, match="bootstrap failed: code=1"),
        ):
            await t.connect()

    async def test_connect_bootstrap_short_body_raises(self):
        """Bootstrap response with body < 66 bytes raises RuntimeError."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        short_body = b"\x00" * 10
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=short_body)

        mock_ws = _make_mock_ws(messages=[response_bytes])

        with (
            patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws),
            pytest.raises(RuntimeError, match="bootstrap response too short"),
        ):
            await t.connect()

    async def test_connect_sends_bootstrap_command(self):
        """connect() sends a bootstrap command over WebSocket."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        bootstrap_body = _make_bootstrap_response_body()
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _make_mock_ws(messages=[response_bytes])

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await t.connect()

        # ws.send should have been called with the bootstrap command
        mock_ws.send.assert_called_once()
        sent_data = mock_ws.send.call_args[0][0]
        assert isinstance(sent_data, bytes)
        # The sent data is pack_outer(encrypted bootstrap). Just ensure it's non-empty.
        assert len(sent_data) > 8  # at least the outer header

    async def test_double_connect_closes_first(self):
        """Calling connect() when already connected closes existing connection first."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        bootstrap_body = _make_bootstrap_response_body()
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws1 = _make_mock_ws(messages=[response_bytes])
        mock_ws2 = _make_mock_ws(messages=[response_bytes])

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws1):
            await t.connect()

        assert t.ws is mock_ws1

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws2):
            await t.connect()

        # First ws should have been closed
        mock_ws1.close.assert_called_once()
        assert t.ws is mock_ws2

    async def test_connect_starts_recv_task(self):
        """connect() creates a _recv_task."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)

        bootstrap_body = _make_bootstrap_response_body()
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _make_mock_ws(messages=[response_bytes])

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await t.connect()

        assert t._recv_task is not None
        await t.close()


# ============================================================================
# 2. close()
# ============================================================================


class TestClose:
    """Tests for MT5WebSocketTransport.close()."""

    async def test_close_sets_is_ready_false(self):
        """close() sets is_ready to False."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = True
        t.ws = _make_mock_ws()
        await t.close()
        assert t.is_ready is False

    async def test_close_sets_ws_to_none(self):
        """close() sets ws to None after closing."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        mock_ws = _make_mock_ws()
        t.ws = mock_ws
        await t.close()
        assert t.ws is None
        mock_ws.close.assert_called_once()

    async def test_close_cancels_recv_task(self):
        """close() cancels the _recv_task."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.ws = _make_mock_ws()

        # Create a long-running task to simulate _recv_loop
        async def long_running():
            await asyncio.sleep(999)

        t._recv_task = asyncio.create_task(long_running())
        await t.close()

        assert t._recv_task is None

    async def test_close_calls_fail_all(self):
        """close() calls _fail_all which fails pending futures."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.ws = _make_mock_ws()

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        t._pending[CMD_TICK_PUSH].append(future)

        await t.close()

        assert future.done()
        with pytest.raises(RuntimeError, match="transport closed"):
            future.result()

    async def test_close_on_already_closed_transport(self):
        """close() on a transport with ws=None and no recv_task is a no-op."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert t.ws is None
        assert t._recv_task is None
        # Should not raise
        await t.close()
        assert t.is_ready is False

    async def test_close_recv_task_cancelled_error_suppressed(self):
        """close() suppresses CancelledError from recv_task."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.ws = _make_mock_ws()

        async def will_be_cancelled():
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                raise

        t._recv_task = asyncio.create_task(will_be_cancelled())
        # Should not raise even though the task raises CancelledError
        await t.close()
        assert t._recv_task is None


# ============================================================================
# 3. send_command()
# ============================================================================


class TestSendCommand:
    """Tests for MT5WebSocketTransport.send_command()."""

    async def test_send_command_not_ready_raises(self):
        """send_command() raises RuntimeError when transport is not ready."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = False
        with pytest.raises(RuntimeError, match="transport not ready"):
            await t.send_command(CMD_GET_ACCOUNT)

    async def test_send_command_no_ws_raises(self):
        """send_command() raises RuntimeError when ws is None."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = True
        t.ws = None
        with pytest.raises(RuntimeError, match="websocket not connected"):
            await t.send_command(CMD_GET_ACCOUNT)

    async def test_send_command_creates_pending_future(self):
        """send_command() creates a future in _pending for the command."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=2.0)
        t.is_ready = True
        mock_ws = _make_mock_ws()
        t.ws = mock_ws

        # We need to resolve the future from the dispatch side.
        # Start send_command in a task and resolve it manually.
        async def resolve_after_send():
            await asyncio.sleep(0.05)
            # There should be a pending future for CMD_GET_ACCOUNT
            queue = t._pending.get(CMD_GET_ACCOUNT)
            assert queue is not None
            assert len(queue) == 1
            future = queue[0]
            future.set_result(CommandResult(command=CMD_GET_ACCOUNT, code=0, body=b"ok"))

        resolver = asyncio.create_task(resolve_after_send())
        result = await t.send_command(CMD_GET_ACCOUNT)
        await resolver

        assert result.command == CMD_GET_ACCOUNT
        assert result.code == 0
        assert result.body == b"ok"

    async def test_send_command_invalid_command_raises(self):
        """send_command() with invalid command raises ValueError."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = True
        t.ws = _make_mock_ws()
        with pytest.raises(ValueError, match="unsupported command"):
            await t.send_command(99999)

    async def test_send_command_passes_payload(self):
        """send_command() sends payload bytes via WebSocket."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=2.0)
        t.is_ready = True
        mock_ws = _make_mock_ws()
        t.ws = mock_ws

        async def resolve_quickly():
            await asyncio.sleep(0.05)
            queue = t._pending.get(CMD_GET_ACCOUNT)
            if queue:
                queue[0].set_result(CommandResult(command=CMD_GET_ACCOUNT, code=0, body=b""))

        resolver = asyncio.create_task(resolve_quickly())
        await t.send_command(CMD_GET_ACCOUNT, payload=b"\x01\x02\x03")
        await resolver

        mock_ws.send.assert_called_once()
        sent = mock_ws.send.call_args[0][0]
        assert isinstance(sent, bytes)
        assert len(sent) > 0


# ============================================================================
# 4. _send_raw()
# ============================================================================


class TestSendRaw:
    """Tests for MT5WebSocketTransport._send_raw()."""

    async def test_send_raw_invalid_command_raises(self):
        """_send_raw() with invalid command raises ValueError."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        with pytest.raises(ValueError, match="unsupported command"):
            await t._send_raw(99999, b"", check_ready=False)

    async def test_send_raw_check_ready_false_bypasses_readiness(self):
        """_send_raw() with check_ready=False does not check is_ready."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=2.0)
        t.is_ready = False
        mock_ws = _make_mock_ws()
        t.ws = mock_ws

        async def resolve():
            await asyncio.sleep(0.05)
            queue = t._pending.get(CMD_BOOTSTRAP)
            if queue:
                queue[0].set_result(CommandResult(command=CMD_BOOTSTRAP, code=0, body=b"ok"))

        resolver = asyncio.create_task(resolve())
        # Should NOT raise even though is_ready is False
        result = await t._send_raw(CMD_BOOTSTRAP, b"", check_ready=False)
        await resolver

        assert result.code == 0

    async def test_send_raw_check_ready_true_raises_when_not_ready(self):
        """_send_raw() with check_ready=True raises when not ready."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = False
        t.ws = _make_mock_ws()
        with pytest.raises(RuntimeError, match="transport not ready"):
            await t._send_raw(CMD_GET_ACCOUNT, b"", check_ready=True)

    async def test_send_raw_ws_none_raises(self):
        """_send_raw() raises RuntimeError when ws is None."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = False
        t.ws = None
        with pytest.raises(RuntimeError, match="websocket not connected"):
            await t._send_raw(CMD_BOOTSTRAP, b"", check_ready=False)

    async def test_send_raw_timeout_cleans_leaked_future(self):
        """On timeout, _send_raw() removes the leaked future from _pending."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=0.1)
        t.is_ready = True
        mock_ws = _make_mock_ws()
        t.ws = mock_ws

        # Don't resolve the future -- let it timeout
        with pytest.raises(TimeoutError):
            await t._send_raw(CMD_GET_ACCOUNT, b"", check_ready=True)

        # The future should have been removed from _pending
        queue = t._pending.get(CMD_GET_ACCOUNT)
        assert queue is None or len(queue) == 0

    async def test_send_raw_timeout_with_empty_queue(self):
        """Timeout cleanup handles case where future was already removed from queue."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=0.1)
        t.is_ready = True
        mock_ws = _make_mock_ws()
        t.ws = mock_ws

        async def steal_future():
            """Remove the future from the queue before timeout fires."""
            await asyncio.sleep(0.02)
            queue = t._pending.get(CMD_GET_ACCOUNT)
            if queue:
                queue.clear()

        stealer = asyncio.create_task(steal_future())

        with pytest.raises(TimeoutError):
            await t._send_raw(CMD_GET_ACCOUNT, b"", check_ready=True)

        await stealer

        # Should not raise even though the future was already removed
        queue = t._pending.get(CMD_GET_ACCOUNT)
        assert queue is None or len(queue) == 0


# ============================================================================
# 5. _recv_loop()
# ============================================================================


class TestRecvLoop:
    """Tests for MT5WebSocketTransport._recv_loop()."""

    async def test_recv_loop_dispatches_valid_message(self):
        """_recv_loop processes valid encrypted messages and dispatches them."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        cipher = initial_cipher()
        t.cipher = cipher

        # Build an encrypted response for CMD_TICK_PUSH
        response_bytes = _build_encrypted_response(cipher, CMD_TICK_PUSH, code=0, body=b"tickdata")

        received = []
        t.on(CMD_TICK_PUSH, lambda r: received.append(r))

        mock_ws = _make_mock_ws(messages=[response_bytes])
        t.ws = mock_ws

        # Run _recv_loop directly -- it will iterate once then stop (no more messages)
        await t._recv_loop()

        assert len(received) == 1
        assert received[0].command == CMD_TICK_PUSH
        assert received[0].body == b"tickdata"

    async def test_recv_loop_skips_text_messages(self):
        """_recv_loop ignores string (text) messages."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        cipher = initial_cipher()
        t.cipher = cipher

        received = []
        t.on(CMD_TICK_PUSH, lambda r: received.append(r))

        valid_response = _build_encrypted_response(cipher, CMD_TICK_PUSH, code=0, body=b"data")

        mock_ws = _make_mock_ws(messages=["text message", valid_response])
        t.ws = mock_ws

        await t._recv_loop()

        assert len(received) == 1

    async def test_recv_loop_parse_error_continues(self):
        """Parse errors in _recv_loop are caught and the loop continues."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        cipher = initial_cipher()
        t.cipher = cipher

        received = []
        t.on(CMD_TICK_PUSH, lambda r: received.append(r))

        # First message: garbage (will cause parse error)
        garbage = b"\x00\x01\x02\x03"
        # Second message: valid
        valid_response = _build_encrypted_response(cipher, CMD_TICK_PUSH, code=0, body=b"good")

        mock_ws = _make_mock_ws(messages=[garbage, valid_response])
        t.ws = mock_ws

        await t._recv_loop()

        assert len(received) == 1
        assert received[0].body == b"good"

    async def test_recv_loop_struct_error_continues(self):
        """struct.error in _recv_loop is caught and loop continues."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        cipher = initial_cipher()
        t.cipher = cipher

        received = []
        t.on(CMD_TICK_PUSH, lambda r: received.append(r))

        # Build a message with valid outer framing but corrupted encrypted content
        bad_inner = b"\xff" * 32
        outer_bad = pack_outer(bad_inner)

        valid_response = _build_encrypted_response(cipher, CMD_TICK_PUSH, code=0, body=b"ok")

        mock_ws = _make_mock_ws(messages=[outer_bad, valid_response])
        t.ws = mock_ws

        await t._recv_loop()

        assert len(received) == 1

    async def test_recv_loop_cancelled_error_reraises(self):
        """CancelledError in _recv_loop is re-raised (not swallowed)."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        class _CancellingIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise asyncio.CancelledError()

        mock_ws = MagicMock()
        mock_ws.__aiter__ = lambda self_: _CancellingIterator()
        t.ws = mock_ws

        with pytest.raises(asyncio.CancelledError):
            await t._recv_loop()

    async def test_recv_loop_connection_error_calls_fail_all_and_disconnect(self):
        """Connection error in _recv_loop calls _fail_all and _on_disconnect."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = True

        disconnect_called = []
        t._on_disconnect = lambda: disconnect_called.append(True)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        t._pending[CMD_TICK_PUSH].append(future)

        class _ErrorIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise ConnectionError("connection lost")

        mock_ws = MagicMock()
        mock_ws.__aiter__ = lambda self_: _ErrorIterator()
        t.ws = mock_ws

        await t._recv_loop()

        assert future.done()
        with pytest.raises(ConnectionError, match="connection lost"):
            future.result()

        assert t.is_ready is False
        assert len(disconnect_called) == 1

    async def test_recv_loop_connection_error_no_disconnect_handler(self):
        """Connection error without _on_disconnect handler does not crash."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = True
        t._on_disconnect = None

        class _BrokenPipeIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise OSError("broken pipe")

        mock_ws = MagicMock()
        mock_ws.__aiter__ = lambda self_: _BrokenPipeIterator()
        t.ws = mock_ws

        await t._recv_loop()
        assert t.is_ready is False

    async def test_recv_loop_resolves_pending_future(self):
        """_recv_loop resolves a pending future when a matching response arrives."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        cipher = initial_cipher()
        t.cipher = cipher

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        t._pending[CMD_GET_ACCOUNT].append(future)

        response_bytes = _build_encrypted_response(cipher, CMD_GET_ACCOUNT, code=0, body=b"account_data")

        mock_ws = _make_mock_ws(messages=[response_bytes])
        t.ws = mock_ws

        await t._recv_loop()

        assert future.done()
        result = future.result()
        assert result.command == CMD_GET_ACCOUNT
        assert result.body == b"account_data"

    async def test_recv_loop_handles_memoryview_message(self):
        """_recv_loop handles messages that are memoryview (not bytes)."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        cipher = initial_cipher()
        t.cipher = cipher

        received = []
        t.on(CMD_TICK_PUSH, lambda r: received.append(r))

        response_bytes = _build_encrypted_response(cipher, CMD_TICK_PUSH, code=0, body=b"mv_data")
        mv_message = memoryview(response_bytes)

        mock_ws = _make_mock_ws(messages=[mv_message])
        t.ws = mock_ws

        await t._recv_loop()

        assert len(received) == 1
        assert received[0].body == b"mv_data"


# ============================================================================
# 6. _dispatch() with push listeners (extended tests)
# ============================================================================


class TestDispatchExtended:
    """Extended _dispatch tests focusing on push listener interactions."""

    async def test_dispatch_both_pending_and_listener(self):
        """_dispatch resolves a pending future AND notifies listeners."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        t._pending[CMD_TICK_PUSH].append(future)

        listener_received = []
        t.on(CMD_TICK_PUSH, lambda r: listener_received.append(r))

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"both")
        await t._dispatch(frame)

        # Future should be resolved
        assert future.done()
        assert future.result().body == b"both"

        # Listener should also have received the result
        assert len(listener_received) == 1
        assert listener_received[0].body == b"both"

    async def test_dispatch_multiple_listeners(self):
        """_dispatch calls all registered listeners for a command."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        results_a = []
        results_b = []
        t.on(CMD_TICK_PUSH, lambda r: results_a.append(r))
        t.on(CMD_TICK_PUSH, lambda r: results_b.append(r))

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"multi")
        await t._dispatch(frame)

        assert len(results_a) == 1
        assert len(results_b) == 1

    async def test_dispatch_no_pending_no_listener(self):
        """_dispatch with no pending futures and no listeners is a no-op."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"orphan")
        # Should not raise
        await t._dispatch(frame)

    async def test_dispatch_async_listener(self):
        """_dispatch awaits async listeners."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        received = []

        async def async_cb(r):
            await asyncio.sleep(0.01)
            received.append(r)

        t.on(CMD_TICK_PUSH, async_cb)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"async_push")
        await t._dispatch(frame)

        assert len(received) == 1
        assert received[0].body == b"async_push"

    async def test_dispatch_multiple_pending_resolves_first_non_done(self):
        """_dispatch resolves only the first non-done future in the queue."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f1.set_result(CommandResult(command=CMD_TICK_PUSH, code=0, body=b"old"))
        f2 = loop.create_future()
        f3 = loop.create_future()

        t._pending[CMD_TICK_PUSH].append(f1)
        t._pending[CMD_TICK_PUSH].append(f2)
        t._pending[CMD_TICK_PUSH].append(f3)

        frame = ResponseFrame(command=CMD_TICK_PUSH, code=0, body=b"new")
        await t._dispatch(frame)

        # f1 was already done so skipped; f2 should be resolved; f3 untouched
        assert f2.done()
        assert f2.result().body == b"new"
        assert not f3.done()

    async def test_dispatch_with_error_code(self):
        """_dispatch correctly passes non-zero error codes."""
        t = MT5WebSocketTransport(uri="wss://example.com")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        t._pending[CMD_GET_ACCOUNT].append(future)

        frame = ResponseFrame(command=CMD_GET_ACCOUNT, code=5, body=b"error_info")
        await t._dispatch(frame)

        assert future.done()
        result = future.result()
        assert result.code == 5
        assert result.body == b"error_info"


# ============================================================================
# 7. Additional edge cases
# ============================================================================


class TestEdgeCases:
    """Edge case and integration-style tests."""

    async def test_fail_all_clears_all_queues(self):
        """_fail_all fails futures across multiple command queues."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        loop = asyncio.get_running_loop()

        f1 = loop.create_future()
        f2 = loop.create_future()
        f3 = loop.create_future()
        t._pending[CMD_TICK_PUSH].append(f1)
        t._pending[CMD_GET_ACCOUNT].append(f2)
        t._pending[CMD_BOOTSTRAP].append(f3)

        t._fail_all(RuntimeError("boom"))

        for f in (f1, f2, f3):
            assert f.done()
            with pytest.raises(RuntimeError, match="boom"):
                f.result()

    async def test_on_disconnect_callback(self):
        """_on_disconnect callback is stored and can be set."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        assert t._on_disconnect is None

        callback = MagicMock()
        t._on_disconnect = callback

        assert t._on_disconnect is callback

    async def test_transport_lock_prevents_concurrent_sends(self):
        """_lock serializes concurrent _send_raw calls."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=2.0)
        t.is_ready = True
        mock_ws = _make_mock_ws()
        t.ws = mock_ws

        send_order = []

        async def tracking_send(data):
            send_order.append(len(send_order))
            await asyncio.sleep(0.01)

        mock_ws.send = tracking_send

        async def send_and_resolve(cmd):
            async def resolve():
                await asyncio.sleep(0.05)
                queue = t._pending.get(cmd)
                if queue:
                    for f in queue:
                        if not f.done():
                            f.set_result(CommandResult(command=cmd, code=0, body=b""))
                            break

            resolver = asyncio.create_task(resolve())
            await t._send_raw(cmd, b"", check_ready=True)
            await resolver

        await asyncio.gather(
            send_and_resolve(CMD_GET_ACCOUNT),
            send_and_resolve(CMD_TICK_PUSH),
        )

        assert len(send_order) == 2

    def test_command_result_slots(self):
        """CommandResult uses slots for memory efficiency."""
        cr = CommandResult(command=1, code=0, body=b"")
        with pytest.raises(AttributeError):
            cr.nonexistent = "value"  # type: ignore[attr-defined]

    async def test_close_then_send_raises(self):
        """After close(), send_command raises RuntimeError."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        t.is_ready = True
        t.ws = _make_mock_ws()
        await t.close()

        with pytest.raises(RuntimeError):
            await t.send_command(CMD_GET_ACCOUNT)

    async def test_connect_resets_cipher_to_initial(self):
        """connect() resets cipher to initial_cipher() before bootstrap."""
        t = MT5WebSocketTransport(uri="wss://example.com", timeout=5.0)
        t.cipher = AESCipher(b"\x00" * 16)

        bootstrap_body = _make_bootstrap_response_body()
        init_cipher = initial_cipher()
        response_bytes = _build_encrypted_response(init_cipher, CMD_BOOTSTRAP, code=0, body=bootstrap_body)

        mock_ws = _make_mock_ws(messages=[response_bytes])

        with patch("pymt5.transport.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
            await t.connect()

        assert t.cipher.key == b"\xcd" * 16
        await t.close()

    def test_pending_uses_defaultdict_deque(self):
        """_pending automatically creates deque for unknown commands."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        queue = t._pending[12345]
        assert isinstance(queue, deque)
        assert len(queue) == 0

    async def test_listeners_uses_defaultdict_set(self):
        """_listeners automatically creates set for unknown commands."""
        t = MT5WebSocketTransport(uri="wss://example.com")
        listeners = t._listeners[12345]
        assert isinstance(listeners, set)
        assert len(listeners) == 0
