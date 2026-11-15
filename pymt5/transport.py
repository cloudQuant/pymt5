"""Encrypted WebSocket transport for the MT5 Web Terminal binary protocol.

The transport owns socket lifecycle, bootstrap key exchange, command framing,
FIFO response correlation, push-callback dispatch, and optional rate limiting.
It does not authenticate accounts or automatically reconnect. Failures after a
request may have reached the server invalidate the connection so late responses
cannot be mistaken for replies to later commands.
"""

import asyncio
import enum
import inspect as _inspect
import struct
import time
import traceback
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from pymt5._logging import get_logger
from pymt5._metrics import MetricsCollector
from pymt5._rate_limiter import TokenBucketRateLimiter
from pymt5.constants import CMD_BOOTSTRAP, DEFAULT_COMMAND_TIMEOUT, DEFAULT_TOKEN_LENGTH, VALID_COMMANDS
from pymt5.crypto import AESCipher, initial_cipher
from pymt5.exceptions import MT5ConnectionError, MT5TimeoutError, ProtocolError, SessionError
from pymt5.protocol import ResponseFrame, build_command, pack_outer, parse_response_frame, unpack_outer

logger = get_logger("pymt5.transport")

# Cache inspect.signature result at module level (Phase 3.5)
_WS_CONNECT_HAS_PROXY = "proxy" in _inspect.signature(websockets.connect).parameters
_WEB_TERMINAL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"
)


def _observe_isolation_task(task: asyncio.Task[Any]) -> None:
    """Retrieve eventual cleanup errors without retaining the transport."""
    if task.cancelled():
        return
    try:
        error = task.exception()
    except BaseException as exc:
        logger.warning("connection isolation task inspection failed: %s", type(exc).__name__)
        return
    if error is not None:
        logger.warning("connection isolation task failed: %s", type(error).__name__)


class TransportState(enum.Enum):
    """Connection lifecycle states for the WebSocket transport."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    CLOSING = "closing"
    ERROR = "error"


@dataclass(slots=True)
class CommandResult:
    """Contain a decoded MT5 response or push frame.

    Attributes:
        command: Numeric MT5 command identifier.
        code: Server-supplied response or push status code.
        body: Decrypted command-specific payload without further parsing.

    """

    command: int
    code: int
    body: bytes


class MT5WebSocketTransport:
    """Manage one encrypted MT5 WebSocket connection and its command lifecycle.

    The bootstrap exchange establishes the session token and cipher. Responses
    do not reliably echo request identifiers, so calls using the same command
    are serialized and matched to responses in FIFO order; different commands
    may proceed concurrently. Response and push frames are also offered to
    registered listeners.

    Closing or losing a connection invalidates its generation and fails pending
    requests. A timeout, send failure, or caller cancellation after transmission
    begins makes the request outcome uncertain, so the transport isolates and
    closes that connection rather than risking ambiguous response correlation.
    Reconnection and reauthentication are coordinated by :class:`MT5WebClient`,
    not by this class.
    """

    def __init__(
        self,
        uri: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
        rate_limit: float = 0,
        rate_burst: int = 20,
        metrics: MetricsCollector | None = None,
    ):
        """Initialize a disconnected transport.

        No socket is opened until :meth:`connect` is called.

        Args:
            uri: MetaTrader 5 Web Terminal WebSocket URI.
            timeout: Timeout in seconds applied independently to socket opening,
                each socket send, and each command-response wait.
            rate_limit: Sustained command rate per second. Zero disables
                command throttling.
            rate_burst: Maximum token-bucket burst size for command throttling.
            metrics: Optional collector for connection and command metrics.

        """
        self.uri = uri
        self.timeout = timeout
        self.ws: ClientConnection | None = None
        self._state = TransportState.DISCONNECTED
        self.token = bytes(DEFAULT_TOKEN_LENGTH)
        self.cipher: AESCipher = initial_cipher()
        self._recv_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._command_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._generation = 0
        self._generation_invalidated = asyncio.Event()
        self._terminated_generation: int | None = None
        self._rate_limiter = TokenBucketRateLimiter(rate=rate_limit, burst=rate_burst)
        self._pending: dict[int, deque[asyncio.Future[CommandResult]]] = defaultdict(deque)
        self._listeners: dict[int, set[Callable[[CommandResult], Awaitable[None] | None]]] = defaultdict(set)
        self._on_disconnect: Callable[[], None] | None = None
        self._shutdown_event = asyncio.Event()
        self._disconnect_lock = asyncio.Lock()
        self._metrics = metrics
        self._last_message_at: float = 0.0
        self._connected_at: float = 0.0
        self._callback_error_handlers: list[Callable] = []
        self._server_build: int = 0

    @property
    def state(self) -> TransportState:
        """Current transport connection state."""
        return self._state

    @property
    def is_ready(self) -> bool:
        """Whether the transport is ready to send commands."""
        return self._state == TransportState.READY

    @is_ready.setter
    def is_ready(self, value: bool) -> None:
        """Backward-compatible setter for is_ready."""
        self._state = TransportState.READY if value else TransportState.DISCONNECTED

    @property
    def server_build(self) -> int:
        """Server build number extracted from the bootstrap response prefix."""
        return self._server_build

    async def connect(self) -> None:
        """Open the WebSocket and perform the MT5 bootstrap key exchange.

        Any existing socket is closed first. A fresh connection generation,
        cipher, and command locks are installed, and the state becomes
        :attr:`TransportState.READY` only after a valid bootstrap response.
        WebSocket-level automatic pings and system proxy detection are disabled
        because they are incompatible with the observed MT5 gateway behavior.

        Returns:
            None.

        Raises:
            MT5ConnectionError: If bootstrap is rejected or its response is too
                short to contain the session token and encryption key.
            SessionError: If close or a replacement connection supersedes this
                attempt.
            TimeoutError: If opening the socket or exchanging bootstrap data
                exceeds the configured timeout.
            asyncio.CancelledError: If the caller cancels the attempt. Partial
                connection resources are cleaned up before cancellation is
                propagated.

        """
        generation: int | None = None
        opened_ws: ClientConnection | None = None
        attached = False
        try:
            # Guard against double-connect (Phase 2.4)
            if self.ws is not None:
                await self.close()
            self._generation_invalidated.set()
            self._generation += 1
            generation = self._generation
            self._generation_invalidated = asyncio.Event()
            # A send on an old socket may ignore cancellation indefinitely.
            # New generations must never inherit its command or write locks.
            self._command_locks = defaultdict(asyncio.Lock)
            self._lock = asyncio.Lock()
            self._state = TransportState.CONNECTING
            self._shutdown_event.clear()
            self.cipher = initial_cipher()
            logger.info("connecting to %s", self.uri)
            connect_kwargs: dict[str, Any] = {
                "ping_interval": None,
                "max_size": None,
                "open_timeout": self.timeout,
                # Some Web Terminal gateways reject the default Python client identity.
                "user_agent_header": _WEB_TERMINAL_USER_AGENT,
                "additional_headers": {
                    "Origin": "https://web.metatrader.app",
                },
            }
            # websockets >=15 auto-detects system proxy (proxy=True default)
            # which breaks the MT5 binary protocol; bypass it explicitly.
            if _WS_CONNECT_HAS_PROXY:
                connect_kwargs["proxy"] = None
            opened_ws = await asyncio.wait_for(
                websockets.connect(self.uri, **connect_kwargs),
                timeout=self.timeout,
            )
            if self._generation != generation or self._shutdown_event.is_set():
                raise SessionError("connection attempt superseded")
            self.ws = opened_ws
            attached = True
            self._recv_task = asyncio.create_task(self._recv_loop(opened_ws, generation))
            logger.debug("websocket open, sending bootstrap")
            bootstrap = await self._send_raw(CMD_BOOTSTRAP, self.token, check_ready=False)
            if (
                self._generation != generation
                or self.ws is not opened_ws
                or self._state is not TransportState.CONNECTING
            ):
                raise SessionError("connection attempt superseded")
            if bootstrap.code != 0:
                raise MT5ConnectionError(f"bootstrap failed: code={bootstrap.code}")
            if len(bootstrap.body) < 66:
                raise MT5ConnectionError(f"bootstrap response too short: {len(bootstrap.body)}")
            self.token = bootstrap.body[2:66]
            self.cipher = AESCipher(bootstrap.body[66:])
            # Try to extract server build from the 2-byte prefix (U16 LE)
            try:
                self._server_build = struct.unpack_from("<H", bootstrap.body, 0)[0]
            except struct.error:
                self._server_build = 0
            self._state = TransportState.READY
            self._connected_at = time.monotonic()
            if self._metrics:
                self._metrics.on_connect()
            logger.info("transport ready (key exchanged)")
        except BaseException:
            # A socket may already exist when bootstrap or task cancellation fails.
            # Cleanup errors must not replace the exception that caused connect to fail.
            try:
                if opened_ws is not None and not attached:
                    await opened_ws.close()
                elif generation is not None and self._generation == generation:
                    await self.close()
            except BaseException:
                logger.warning("failed to clean up after connect failure", exc_info=True)
            raise

    async def close(self) -> None:
        """Close the current connection and invalidate all of its commands.

        Pending requests are failed with :class:`SessionError`, the receive task
        is cancelled, and the WebSocket is closed. The socket and task are
        detached before cleanup awaits, allowing a later connection to use an
        independent generation without being affected by old cleanup. Unless a
        newer connection supersedes it, the final state is
        :attr:`TransportState.DISCONNECTED`.

        Socket cleanup is still attempted after receive-task failure or caller
        cancellation. The first cleanup error is then re-raised.

        Returns:
            None.

        Raises:
            asyncio.CancelledError: If caller cancellation is observed during
                cleanup.
            Exception: If receive-task termination or WebSocket closure fails.

        """
        logger.info("closing transport")
        async with self._disconnect_lock:
            self._generation_invalidated.set()
            self._generation += 1
            generation = self._generation
            self._state = TransportState.CLOSING
            self._shutdown_event.set()
            # Detach and fail this generation's requests before the first await
            # in cleanup. A later connection owns a different pending set.
            recv_task = self._recv_task
            self._recv_task = None
            ws = self.ws
            self.ws = None
            self._fail_all(SessionError("transport closed"))
        error: BaseException | None = None

        if recv_task is not None and recv_task is not asyncio.current_task():
            current_task = asyncio.current_task()
            cancelling_before = current_task.cancelling() if current_task is not None else 0
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError as exc:
                if current_task is not None and current_task.cancelling() > cancelling_before:
                    error = exc
            except BaseException as exc:
                error = exc

        if ws is not None:
            try:
                await ws.close()
            except BaseException as exc:
                if error is None:
                    error = exc
                else:
                    logger.warning("websocket close failed during transport cleanup", exc_info=True)

        if self._generation == generation:
            self._state = TransportState.DISCONNECTED
        logger.debug("transport closed")
        if error is not None:
            raise error

    def on(self, command: int, callback: Callable[[CommandResult], Awaitable[None] | None]) -> None:
        """Register a listener for responses and push frames for a command.

        Listeners run after any pending command future has been resolved. Both
        synchronous and asynchronous callbacks are accepted; asynchronous
        callbacks are awaited in the receive loop. Registering the same callback
        more than once has no additional effect. Callback exceptions are logged
        and forwarded to configured callback-error handlers without stopping
        other listeners.

        Args:
            command: Numeric MT5 command identifier to observe.
            callback: Callable invoked with each matching :class:`CommandResult`.

        Returns:
            None.

        """
        self._listeners[command].add(callback)

    def off(self, command: int, callback: Callable[[CommandResult], Awaitable[None] | None] | None = None) -> None:
        """Unregister one listener or all listeners for a command.

        Removing a callback that is not registered is harmless. Listener
        removal does not cancel an asynchronous callback already being awaited.

        Args:
            command: Numeric MT5 command identifier whose listeners should be
                changed.
            callback: Specific listener to remove. If omitted, all listeners
                for ``command`` are removed.

        Returns:
            None.

        """
        if callback is None:
            self._listeners[command].clear()
            return
        self._listeners[command].discard(callback)

    async def send_command(self, command: int, payload: bytes | None = None) -> CommandResult:
        """Encrypt a command, send it, and await its correlated response.

        Calls for the same command are serialized until a response arrives;
        calls for different commands can proceed independently. The configured
        timeout applies separately to the socket send and the subsequent
        response wait, not to time spent waiting for the rate limiter or another
        request using the same command. Server status codes are returned without
        interpretation.

        Once transmission begins, a send error, timeout, or caller cancellation
        leaves the server-side outcome uncertain. The transport invalidates and
        closes the connection, fails other pending requests, and prevents late
        responses from completing newer calls. In particular, callers must not
        assume that a timed-out or cancelled trade was rejected; reconcile its
        outcome through account or order state before considering any retry.
        Commands are never retried automatically by this method.

        Args:
            command: Numeric MT5 command identifier. It must be present in the
                supported command set.
            payload: Unencrypted command-specific payload. ``None`` sends an
                empty payload.

        Returns:
            The correlated command result containing the command identifier,
            server status code, and decrypted response body.

        Raises:
            ProtocolError: If ``command`` is unsupported.
            SessionError: If the transport is not ready or its connection
                generation is invalidated before a response is received.
            MT5ConnectionError: If no WebSocket is attached.
            MT5TimeoutError: If the socket send or response wait exceeds the
                configured timeout. The remote request outcome may be unknown.
            asyncio.CancelledError: If the caller cancels the operation. When
                transmission has begun, connection isolation is attempted before
                cancellation is propagated and the remote outcome may be unknown.

        """
        return await self._send_raw(command, payload or b"", check_ready=True)

    async def _send_raw(self, command: int, payload: bytes, check_ready: bool) -> CommandResult:
        if command not in VALID_COMMANDS:
            raise ProtocolError(f"unsupported command: {command}")
        if check_ready and not self.is_ready:
            raise SessionError(f"transport not ready for command {command}")
        if self.ws is None:
            raise MT5ConnectionError("websocket not connected")
        ws = self.ws
        generation = self._generation
        invalidated = self._generation_invalidated
        command_lock = self._command_locks[command]
        send_lock = self._lock

        # Responses do not reliably echo a request identifier.  Serialize each
        # command until its response is received, while allowing other commands
        # to proceed independently.  This does not distinguish pushes or duplicate
        # responses from a valid response of the same command.
        async with command_lock:
            self._check_send_generation(ws, generation, command, check_ready)
            await self._acquire_rate_limit(invalidated, command)
            self._check_send_generation(ws, generation, command, check_ready)
            future: asyncio.Future[CommandResult] = asyncio.get_running_loop().create_future()
            send_started = False
            try:
                async with send_lock:
                    self._check_send_generation(ws, generation, command, check_ready)
                    inner = build_command(command, payload)
                    encrypted = self.cipher.encrypt(inner)
                    frame = pack_outer(encrypted)
                    self._pending[command].append(future)
                    logger.debug("send cmd=%d payload=%d bytes", command, len(payload))
                    # Even a failed or cancelled send may have reached the server.
                    send_started = True
                    await self._send_with_deadline(ws, frame, invalidated, command)
                    if self._metrics:
                        self._metrics.on_command_sent(command)
                return await asyncio.wait_for(future, timeout=self.timeout)
            except BaseException as exc:
                if send_started:
                    if not future.done():
                        future.cancel()
                    try:
                        await self._invalidate_uncertain_send(ws, generation)
                    except BaseException as cleanup_error:
                        logger.warning("failed to finish uncertain-send cleanup: %s", type(cleanup_error).__name__)
                if isinstance(exc, TimeoutError):
                    raise MT5TimeoutError(f"command {command} timed out after {self.timeout}s") from None
                raise
            finally:
                queue = self._pending.get(command)
                if queue is not None:
                    try:
                        queue.remove(future)
                    except ValueError:
                        pass
                    if not queue and self._pending.get(command) is queue:
                        self._pending.pop(command, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    # A send can fail after another task has failed this future.
                    future.exception()

    def _check_send_generation(
        self, ws: ClientConnection, generation: int, command: int, check_ready: bool
    ) -> None:
        if self.ws is not ws or self._generation != generation or (check_ready and not self.is_ready):
            raise SessionError(f"transport not ready for command {command}")

    async def _send_with_deadline(
        self, ws: ClientConnection, frame: bytes, invalidated: asyncio.Event, command: int
    ) -> None:
        """Bound a potentially non-cooperative send on the captured socket."""
        send_task = asyncio.create_task(ws.send(frame))
        invalidation_task = asyncio.create_task(invalidated.wait())
        try:
            done, _ = await asyncio.wait(
                {send_task, invalidation_task}, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if invalidated.is_set():
                raise SessionError("connection invalidated; sent request outcome may be unknown")
            if send_task not in done:
                raise TimeoutError
            await send_task
        finally:
            if not send_task.done():
                # A cancelled send can continue running.  It only retains the
                # old socket; do not wait for it or permit a late task error to
                # become an unobserved exception.
                send_task.add_done_callback(_observe_isolation_task)
                send_task.cancel()
            elif not send_task.cancelled():
                send_task.exception()
            if not invalidation_task.done():
                invalidation_task.cancel()
            await asyncio.gather(invalidation_task, return_exceptions=True)

    async def _acquire_rate_limit(self, invalidated: asyncio.Event, command: int) -> None:
        """Wake throttled senders as soon as their connection is invalidated."""
        if self._rate_limiter.rate <= 0:
            if invalidated.is_set():
                raise SessionError(f"transport not ready for command {command}")
            await self._rate_limiter.acquire()
            return
        rate_task = asyncio.create_task(self._rate_limiter.acquire())
        invalidation_task = asyncio.create_task(invalidated.wait())
        try:
            await asyncio.wait({rate_task, invalidation_task}, return_when=asyncio.FIRST_COMPLETED)
            if invalidated.is_set():
                raise SessionError(f"transport not ready for command {command}")
            await rate_task
        finally:
            for task in (rate_task, invalidation_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(rate_task, invalidation_task, return_exceptions=True)

    async def _invalidate_uncertain_send(self, ws: ClientConnection, generation: int) -> None:
        """Isolate a connection whose sent request may still receive a response."""
        if self.ws is not ws or self._generation != generation or self._state in (
            TransportState.ERROR,
            TransportState.CLOSING,
        ):
            return
        was_ready = self.is_ready
        # All of these changes occur before the first await. Waiting senders
        # cannot send on the old socket or attach to a future connection.
        self._state = TransportState.ERROR
        self._terminated_generation = generation
        self._generation_invalidated.set()
        recv_task = self._recv_task
        self._recv_task = None
        self.ws = None
        self._fail_all(SessionError("connection invalidated; sent request outcome may be unknown"))

        cleanup_error: BaseException | None = None
        cleanup_timeout = max(0.1, min(self.timeout, 10.0))
        try:
            if recv_task is not None and recv_task is not asyncio.current_task():
                # A callback in the receive loop may ignore cancellation.  It
                # must not keep isolation blocked or later emit an unobserved
                # exception after the old generation has been detached.
                recv_task.add_done_callback(_observe_isolation_task)
                recv_task.cancel()
                done, _ = await asyncio.wait({recv_task}, timeout=cleanup_timeout)
                if not done:
                    logger.warning("receive task did not stop during connection isolation")
        except BaseException as exc:
            cleanup_error = exc

        close_task: asyncio.Task[Any] | None = None
        try:
            # wait_for()/asyncio.timeout() can hang if the close coroutine
            # suppresses cancellation.  This task owns only the old socket;
            # its callback observes its eventual result without the transport.
            close_task = asyncio.create_task(ws.close())
            close_task.add_done_callback(_observe_isolation_task)
            done, _ = await asyncio.wait({close_task}, timeout=cleanup_timeout)
            if not done:
                close_task.cancel()
                logger.warning("socket close did not finish during connection isolation")
            elif not close_task.cancelled():
                error = close_task.exception()
                if error is not None:
                    raise error
        except BaseException as exc:
            if close_task is not None and not close_task.done():
                close_task.cancel()
            if cleanup_error is None:
                cleanup_error = exc
            logger.warning("socket close failed during connection isolation: %s", type(exc).__name__)

        # An explicit close or a replacement connection supersedes this event.
        if was_ready and self._generation == generation and not self._shutdown_event.is_set():
            if self._metrics:
                try:
                    self._metrics.on_disconnect("connection invalidated after uncertain send")
                except Exception:
                    logger.warning("disconnect metrics failed during connection isolation")
            if self._on_disconnect:
                try:
                    self._on_disconnect()
                except Exception:
                    logger.warning("disconnect callback failed during connection isolation")
        if cleanup_error is not None:
            raise cleanup_error

    async def _recv_loop(self, ws: ClientConnection | None = None, generation: int | None = None) -> None:
        disconnect_error: Exception
        if ws is None:
            ws = self.ws
        if generation is None:
            generation = self._generation
        try:
            if ws is None:
                raise MT5ConnectionError("websocket not connected")
            async for message in ws:
                if self.ws is not ws or self._generation != generation:
                    return
                if isinstance(message, str):
                    continue
                try:
                    raw = message if isinstance(message, bytes) else bytes(message)
                    _, _, encrypted = unpack_outer(raw)
                    decrypted = self.cipher.decrypt(encrypted)
                    frame = parse_response_frame(decrypted)
                    self._last_message_at = time.monotonic()
                    logger.debug("recv cmd=%d code=%d body=%d bytes", frame.command, frame.code, len(frame.body))
                    await self._dispatch(frame, source_ws=ws, source_generation=generation)
                    if self.ws is not ws or self._generation != generation:
                        return
                except (struct.error, ValueError, TypeError, IndexError, ProtocolError) as exc:
                    logger.error("recv_loop parse error: %s", exc)
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            disconnect_error = exc
            logger.error("recv_loop disconnected: %s", exc)
        else:
            # websockets ends async iteration normally for clean close codes.
            # Normal EOF is still a disconnect and must fail requests and notify.
            disconnect_error = MT5ConnectionError("websocket connection closed")
            logger.warning("websocket connection closed by peer")

        # Claim termination, fail pending and choose a callback atomically with
        # close(). The callback runs outside the lock, without an intervening
        # await, so it can schedule cleanup/reconnection without deadlocking.
        callback: Callable[[], None] | None = None
        async with self._disconnect_lock:
            if (
                self.ws is not ws
                or self._generation != generation
                or self._shutdown_event.is_set()
                or self._terminated_generation == generation
            ):
                return
            self._terminated_generation = generation
            self._generation_invalidated.set()
            self._fail_all(disconnect_error)
            self._state = TransportState.ERROR
            callback = self._on_disconnect
            if self._metrics:
                try:
                    self._metrics.on_disconnect(str(disconnect_error))
                except Exception as exc:
                    logger.warning("disconnect metrics failed: %s", type(exc).__name__)
        if callback is not None:
            callback()

    async def _dispatch(
        self,
        frame: ResponseFrame,
        *,
        source_ws: ClientConnection | None = None,
        source_generation: int | None = None,
    ) -> None:
        def source_is_current() -> bool:
            return source_generation is None or (
                self.ws is source_ws
                and self._generation == source_generation
                and not self._generation_invalidated.is_set()
            )

        if not source_is_current():
            return
        result = CommandResult(command=frame.command, code=frame.code, body=frame.body)
        if self._metrics:
            self._metrics.on_command_received(frame.command, frame.code)
        if not source_is_current():
            return
        queue = self._pending.get(frame.command)
        if queue:
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_result(result)
                    break
        for callback in tuple(self._listeners.get(frame.command, ())):
            if not source_is_current():
                return
            try:
                maybe = callback(result)
                if _inspect.isawaitable(maybe):
                    await maybe
            except Exception as exc:
                if not source_is_current():
                    return
                logger.error(
                    "callback %s raised %s:\n%s",
                    getattr(callback, "__name__", repr(callback)),
                    exc,
                    traceback.format_exc(),
                )
                for error_handler in tuple(self._callback_error_handlers):
                    if not source_is_current():
                        return
                    try:
                        error_handler(exc, callback)
                    except Exception:
                        logger.warning(
                            "callback error handler %s itself raised",
                            getattr(error_handler, "__name__", repr(error_handler)),
                            exc_info=True,
                        )
            if not source_is_current():
                return

    def _fail_all(self, exc: Exception) -> None:
        for queue in self._pending.values():
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_exception(exc)
