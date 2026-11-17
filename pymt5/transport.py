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
from websockets.exceptions import WebSocketException

from pymt5._logging import get_logger
from pymt5._metrics import MetricsCollector
from pymt5._rate_limiter import TokenBucketRateLimiter
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_BOOTSTRAP,
    CMD_GET_POSITIONS_ORDERS,
    CMD_LOGIN_STATUS_PUSH,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_TOKEN_LENGTH,
    VALID_COMMANDS,
)
from pymt5.crypto import AESCipher, initial_cipher
from pymt5.exceptions import MT5ConnectionError, MT5TimeoutError, ProtocolError, SessionError
from pymt5.protocol import (
    ResponseFrame,
    build_command,
    get_series_size,
    pack_outer,
    parse_response_frame,
    unpack_outer,
)
from pymt5.schemas import ORDER_SCHEMA, POSITION_SCHEMA, SYMBOL_DETAILS_6320_SCHEMA, SYMBOL_DETAILS_SCHEMA

logger = get_logger("pymt5.transport")

# Cache inspect.signature result at module level (Phase 3.5)
_WS_CONNECT_HAS_PROXY = "proxy" in _inspect.signature(websockets.connect).parameters
_WEB_TERMINAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"
_ABORT_CLEANUP_TIMEOUT = 10.0
_UNSOLICITED_PUSH_COMMANDS = frozenset(
    {
        CMD_TICK_PUSH,
        CMD_TRADE_UPDATE_PUSH,
        CMD_SYMBOL_UPDATE_PUSH,
        CMD_ACCOUNT_UPDATE_PUSH,
        CMD_LOGIN_STATUS_PUSH,
        CMD_TRADE_RESULT_PUSH,
        CMD_BOOK_PUSH,
    }
)
_POSITION_RECORD_SIZE = get_series_size(POSITION_SCHEMA)
_ORDER_RECORD_SIZE = get_series_size(ORDER_SCHEMA)
_SYMBOL_DETAILS_RECORD_SIZE = get_series_size(SYMBOL_DETAILS_SCHEMA)
_SYMBOL_DETAILS_6320_RECORD_SIZE = get_series_size(SYMBOL_DETAILS_6320_SCHEMA)

_ListenerCallback = Callable[["CommandResult"], Awaitable[None] | None]
_ListenerWorkItem = tuple[
    Awaitable[Any] | None,
    _ListenerCallback | None,
    "CommandResult",
    tuple[_ListenerCallback, ...],
    ClientConnection | None,
    int | None,
]


def _close_unawaited(awaitable: Any) -> None:
    """Close an unawaited coroutine or awaitable so discarded callbacks emit no RuntimeWarning."""
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


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


async def _stop_task_bounded(
    task: asyncio.Task[Any] | None,
    timeout: float,
    *,
    label: str,
    context: str,
    capture_error: bool,
    defer_cancel: bool = True,
) -> BaseException | None:
    """Cancel a background task and wait up to *timeout* seconds for completion."""
    if task is None or task is asyncio.current_task():
        return None
    task.add_done_callback(_observe_isolation_task)
    task.cancel()
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError as exc:
        if defer_cancel:
            return exc
        raise
    if not done:
        logger.warning("%s did not stop during %s", label, context)
        return None
    if capture_error and not task.cancelled():
        return task.exception()
    return None


async def _close_socket_bounded(
    ws: ClientConnection,
    timeout: float,
    *,
    context: str,
) -> BaseException | None:
    """Close a WebSocket within *timeout* seconds without hanging on uncooperative sockets."""
    close_task: asyncio.Task[Any] | None = None
    try:

        async def _close_ws() -> None:
            await ws.close()

        close_task = asyncio.create_task(_close_ws())
        close_task.add_done_callback(_observe_isolation_task)
        done, _ = await asyncio.wait({close_task}, timeout=timeout)
        if not done:
            close_task.cancel()
            logger.warning("socket close did not finish during %s", context)
            return None
        if not close_task.cancelled():
            return close_task.exception()
        return None
    except BaseException as exc:
        if close_task is not None and not close_task.done():
            close_task.cancel()
        return exc


async def _cleanup_resources_bounded(
    recv_task: asyncio.Task[Any] | None,
    listener_task: asyncio.Task[Any] | None,
    ws: ClientConnection | None,
    timeout: float,
    *,
    capture_recv_error: bool,
    context: str,
) -> BaseException | None:
    """Stop detached tasks and close *ws* within *timeout*, returning the first error."""
    error: BaseException | None = None
    for task, label, capture in (
        (recv_task, "receive task", capture_recv_error),
        (listener_task, "listener task", False),
    ):
        task_error = await _stop_task_bounded(
            task,
            timeout,
            label=label,
            context=context,
            capture_error=capture,
        )
        if error is None and task_error is not None:
            error = task_error
    if ws is not None:
        ws_error = await _close_socket_bounded(ws, timeout, context=context)
        if ws_error is not None:
            if error is None:
                error = ws_error
            else:
                logger.warning("websocket close failed during %s: %s", context, type(ws_error).__name__)
    return error


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
        self._pending_predicates: dict[asyncio.Future[CommandResult], Callable[[CommandResult], bool]] = {}
        self._listeners: dict[int, set[Callable[[CommandResult], Awaitable[None] | None]]] = defaultdict(set)
        self._listener_queue: deque[_ListenerWorkItem] = deque()
        self._listener_task: asyncio.Task[None] | None = None
        self._listener_generation: int | None = None
        self._on_disconnect: Callable[[], None] | None = None
        self._shutdown_event = asyncio.Event()
        self._disconnect_lock = asyncio.Lock()
        self._metrics = metrics
        self._last_message_at: float = 0.0
        self._connected_at: float = 0.0
        self._callback_error_handlers: list[Callable] = []
        self._server_build: int = 0

    def _cleanup_timeout(self) -> float:
        return max(0.1, min(self.timeout, _ABORT_CLEANUP_TIMEOUT))

    def _call_metrics(self, method_name: str, *args: Any) -> None:
        if self._metrics is None:
            return
        try:
            getattr(self._metrics, method_name)(*args)
        except Exception as exc:
            logger.warning("metrics %s failed: %s", method_name, type(exc).__name__)

    def _detach_listeners(self) -> asyncio.Task[None] | None:
        while self._listener_queue:
            pending_awaitable, *_ = self._listener_queue.popleft()
            _close_unawaited(pending_awaitable)
        listener_task = self._listener_task
        self._listener_task = None
        self._listener_generation = None
        return listener_task

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
            await _stop_task_bounded(
                self._detach_listeners(),
                self._cleanup_timeout(),
                label="listener task",
                context="connect reset",
                capture_error=False,
                defer_cancel=False,
            )
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
            try:
                opened_ws = await asyncio.wait_for(
                    websockets.connect(self.uri, **connect_kwargs),
                    timeout=self.timeout,
                )
            except WebSocketException as exc:
                raise MT5ConnectionError(
                    f"websocket connection failed: {exc}",
                    server_uri=self.uri,
                ) from exc
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
                raise MT5ConnectionError(f"bootstrap failed: code={bootstrap.code}", server_uri=self.uri)
            if len(bootstrap.body) < 66:
                raise MT5ConnectionError(
                    f"bootstrap response too short: {len(bootstrap.body)}",
                    server_uri=self.uri,
                )
            key_material = bootstrap.body[66:]
            if len(key_material) not in (16, 24, 32):
                raise MT5ConnectionError(
                    f"bootstrap response invalid AES key length: {len(key_material)} bytes",
                    server_uri=self.uri,
                )
            self.token = bootstrap.body[2:66]
            self.cipher = AESCipher(key_material)
            # Try to extract server build from the 2-byte prefix (U16 LE)
            try:
                self._server_build = struct.unpack_from("<H", bootstrap.body, 0)[0]
            except struct.error:
                self._server_build = 0
            self._state = TransportState.READY
            self._connected_at = time.monotonic()
            self._call_metrics("on_connect")
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
            listener_task = self._detach_listeners()
            ws = self.ws
            self.ws = None
            self._fail_all(SessionError("transport closed"))

        error = await _cleanup_resources_bounded(
            recv_task,
            listener_task,
            ws,
            self._cleanup_timeout(),
            capture_recv_error=True,
            context="transport cleanup",
        )
        if self._generation == generation:
            self._state = TransportState.DISCONNECTED
        logger.debug("transport closed")
        if error is not None:
            raise error

    def on(self, command: int, callback: Callable[[CommandResult], Awaitable[None] | None]) -> None:
        """Register a listener for responses and push frames for a command.

        Listeners run after any pending command future has been resolved. Both
        synchronous and asynchronous callbacks are accepted; asynchronous
        callbacks are executed sequentially in a per-connection background
        dispatcher so they do not block the receive loop. Registering the same
        callback more than once has no additional effect. Callback exceptions are
        logged and forwarded to configured callback-error handlers without
        stopping other listeners.

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

    async def send_command(
        self,
        command: int,
        payload: bytes | None = None,
        *,
        response_predicate: Callable[[CommandResult], bool] | None = None,
    ) -> CommandResult:
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
            response_predicate: Optional predicate used to verify whether an
                inbound :class:`CommandResult` satisfies this pending request.

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
        return await self._send_raw(
            command,
            payload or b"",
            check_ready=True,
            response_predicate=response_predicate,
        )

    async def _send_raw(
        self,
        command: int,
        payload: bytes,
        check_ready: bool,
        response_predicate: Callable[[CommandResult], bool] | None = None,
    ) -> CommandResult:
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
                    if response_predicate is not None:
                        self._pending_predicates[future] = response_predicate
                    self._pending[command].append(future)
                    logger.debug("send cmd=%d payload=%d bytes", command, len(payload))
                    # Even a failed or cancelled send may have reached the server.
                    send_started = True
                    await self._send_with_deadline(ws, frame, invalidated, command)
                    self._call_metrics("on_command_sent", command)
                return await asyncio.wait_for(future, timeout=self.timeout)
            except BaseException as exc:
                if send_started:
                    if not future.done():
                        future.cancel()
                    current_task = asyncio.current_task()
                    cancelling_before = current_task.cancelling() if current_task is not None else 0
                    try:
                        await self._invalidate_uncertain_send(ws, generation)
                    except BaseException as cleanup_error:
                        if (
                            isinstance(cleanup_error, asyncio.CancelledError)
                            and current_task is not None
                            and current_task.cancelling() > cancelling_before
                        ):
                            raise
                        logger.warning("failed to finish uncertain-send cleanup: %s", type(cleanup_error).__name__)
                    if current_task is not None and current_task.cancelling() > cancelling_before:
                        raise asyncio.CancelledError from None
                if isinstance(exc, TimeoutError):
                    raise MT5TimeoutError(f"command {command} timed out after {self.timeout}s") from None
                raise
            finally:
                self._pending_predicates.pop(future, None)
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

    def _check_send_generation(self, ws: ClientConnection, generation: int, command: int, check_ready: bool) -> None:
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
            try:
                await send_task
            except WebSocketException as exc:
                raise MT5ConnectionError(f"websocket send failed: {exc}", server_uri=self.uri) from exc
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
        if (
            self.ws is not ws
            or self._generation != generation
            or self._state
            in (
                TransportState.ERROR,
                TransportState.CLOSING,
            )
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
        listener_task = self._detach_listeners()
        self.ws = None
        self._fail_all(SessionError("connection invalidated; sent request outcome may be unknown"))

        cleanup_error = await _cleanup_resources_bounded(
            recv_task,
            listener_task,
            ws,
            self._cleanup_timeout(),
            capture_recv_error=False,
            context="connection isolation",
        )

        # An explicit close or a replacement connection supersedes this event.
        if was_ready and self._generation == generation and not self._shutdown_event.is_set():
            self._call_metrics("on_disconnect", "connection invalidated after uncertain send")
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
            if (
                self._listener_task is not None
                and self._listener_generation == generation
                and self._listener_task is not asyncio.current_task()
            ):
                listener_task = self._detach_listeners()
                await _stop_task_bounded(
                    listener_task,
                    self._cleanup_timeout(),
                    label="listener task",
                    context="receive cancellation",
                    capture_error=False,
                )
            raise
        except Exception as exc:
            disconnect_error = (
                MT5ConnectionError(f"websocket connection failed: {exc}", server_uri=self.uri)
                if isinstance(exc, WebSocketException)
                else exc
            )
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
            self._call_metrics("on_disconnect", str(disconnect_error))
        if callback is not None:
            try:
                callback()
            except Exception as exc:
                logger.warning("disconnect callback failed: %s", type(exc).__name__)

    def _source_is_current(
        self,
        source_ws: ClientConnection | None,
        source_generation: int | None,
    ) -> bool:
        return source_generation is None or (
            self.ws is source_ws and self._generation == source_generation and not self._generation_invalidated.is_set()
        )

    def _is_listener_cancellation_external(
        self,
        source_ws: ClientConnection | None,
        source_generation: int | None,
    ) -> bool:
        current_task = asyncio.current_task()
        return (
            self._shutdown_event.is_set()
            or not self._source_is_current(source_ws, source_generation)
            or (current_task is not None and current_task.cancelling() > 0)
        )

    def _notify_callback_error(
        self,
        callback: Callable[..., Any],
        command: int,
        exc: BaseException,
        *,
        source_ws: ClientConnection | None = None,
        source_generation: int | None = None,
    ) -> bool:
        if not self._source_is_current(source_ws, source_generation):
            return False
        logger.error(
            "callback %s for cmd=%d raised %s:\n%s",
            getattr(callback, "__name__", repr(callback)),
            command,
            exc,
            traceback.format_exc(),
        )
        for error_handler in tuple(self._callback_error_handlers):
            if not self._source_is_current(source_ws, source_generation):
                return False
            try:
                error_handler(exc, callback)
            except Exception:
                logger.warning(
                    "callback error handler %s itself raised",
                    getattr(error_handler, "__name__", repr(error_handler)),
                    exc_info=True,
                )
        return True

    def _default_response_predicate(self, command: int, result: CommandResult) -> bool:
        if result.code != 0:
            return True
        body = result.body
        if command == CMD_GET_POSITIONS_ORDERS:
            if len(body) < 8:
                return False
            pos_count = int(struct.unpack_from("<I", body, 0)[0])
            order_offset = 4 + pos_count * _POSITION_RECORD_SIZE
            if order_offset + 4 > len(body):
                return False
            order_count = int(struct.unpack_from("<I", body, order_offset)[0])
            return order_offset + 4 + order_count * _ORDER_RECORD_SIZE <= len(body)
        if command == CMD_SYMBOL_DETAILS_PUSH:
            detail_size = (
                _SYMBOL_DETAILS_6320_RECORD_SIZE if self._server_build == 6320 else _SYMBOL_DETAILS_RECORD_SIZE
            )
            return len(body) % detail_size == 0
        return True

    def _resolve_pending_future(self, command: int, result: CommandResult) -> None:
        if command in _UNSOLICITED_PUSH_COMMANDS:
            return
        queue = self._pending.get(command)
        if not queue:
            return
        while queue and queue[0].done():
            done_future = queue.popleft()
            self._pending_predicates.pop(done_future, None)
        if not queue:
            return
        future = queue[0]
        predicate = self._pending_predicates.get(future)
        try:
            matched = predicate(result) if predicate is not None else self._default_response_predicate(command, result)
        except Exception as exc:
            queue.popleft()
            self._pending_predicates.pop(future, None)
            future.set_exception(exc)
            return
        if not matched:
            logger.warning("ignoring mismatched/unsolicited frame for pending cmd=%d", command)
            return
        queue.popleft()
        self._pending_predicates.pop(future, None)
        future.set_result(result)

    def _invoke_listener_step(
        self,
        callback: _ListenerCallback,
        result: CommandResult,
        source_ws: ClientConnection | None,
        source_generation: int | None,
    ) -> tuple[bool, Awaitable[Any] | None]:
        if not self._source_is_current(source_ws, source_generation):
            return False, None
        try:
            maybe = callback(result)
        except asyncio.CancelledError as exc:
            if self._is_listener_cancellation_external(source_ws, source_generation):
                raise
            ok = self._notify_callback_error(
                callback,
                result.command,
                exc,
                source_ws=source_ws,
                source_generation=source_generation,
            )
            return ok, None
        except Exception as exc:
            ok = self._notify_callback_error(
                callback,
                result.command,
                exc,
                source_ws=source_ws,
                source_generation=source_generation,
            )
            return ok, None
        if _inspect.isawaitable(maybe):
            if not self._source_is_current(source_ws, source_generation):
                _close_unawaited(maybe)
                return False, None
            return True, maybe
        return self._source_is_current(source_ws, source_generation), None

    async def _await_listener_result(
        self,
        awaitable: Awaitable[Any],
        callback: _ListenerCallback,
        command: int,
        *,
        source_ws: ClientConnection | None,
        source_generation: int | None,
    ) -> bool:
        current_task = asyncio.current_task()
        try:
            await awaitable
        except asyncio.CancelledError as exc:
            if self._is_listener_cancellation_external(source_ws, source_generation):
                raise
            return self._notify_callback_error(
                callback,
                command,
                exc,
                source_ws=source_ws,
                source_generation=source_generation,
            )
        except Exception as exc:
            return self._notify_callback_error(
                callback,
                command,
                exc,
                source_ws=source_ws,
                source_generation=source_generation,
            )
        if current_task is not None and current_task.cancelling() > 0:
            raise asyncio.CancelledError
        return self._source_is_current(source_ws, source_generation)

    def _enqueue_listener_work(
        self,
        awaitable: Awaitable[Any] | None,
        callback: _ListenerCallback | None,
        result: CommandResult,
        remaining: tuple[_ListenerCallback, ...],
        *,
        source_ws: ClientConnection | None,
        source_generation: int | None,
    ) -> None:
        self._listener_queue.append((awaitable, callback, result, remaining, source_ws, source_generation))
        if self._listener_task is None or self._listener_task.done():
            self._listener_generation = source_generation
            self._listener_task = asyncio.create_task(self._run_listener_queue())
            self._listener_task.add_done_callback(_observe_isolation_task)

    async def _run_listener_queue(self) -> None:
        current_task = asyncio.current_task()
        try:
            while self._listener_queue:
                awaitable, callback, result, remaining, source_ws, source_generation = self._listener_queue.popleft()
                if awaitable is not None and callback is not None:
                    if not self._source_is_current(source_ws, source_generation):
                        _close_unawaited(awaitable)
                        continue
                    if not await self._await_listener_result(
                        awaitable,
                        callback,
                        result.command,
                        source_ws=source_ws,
                        source_generation=source_generation,
                    ):
                        continue
                for next_cb in remaining:
                    should_continue, maybe = self._invoke_listener_step(next_cb, result, source_ws, source_generation)
                    if not should_continue:
                        break
                    if maybe is not None and not await self._await_listener_result(
                        maybe,
                        next_cb,
                        result.command,
                        source_ws=source_ws,
                        source_generation=source_generation,
                    ):
                        break
        finally:
            while self._listener_queue:
                pending_awaitable, *_ = self._listener_queue.popleft()
                _close_unawaited(pending_awaitable)
            if self._listener_task is current_task:
                self._listener_task = None
                self._listener_generation = None

    async def _dispatch(
        self,
        frame: ResponseFrame,
        *,
        source_ws: ClientConnection | None = None,
        source_generation: int | None = None,
    ) -> None:
        if not self._source_is_current(source_ws, source_generation):
            return
        result = CommandResult(command=frame.command, code=frame.code, body=frame.body)
        self._call_metrics("on_command_received", frame.command, frame.code)
        if not self._source_is_current(source_ws, source_generation):
            return
        self._resolve_pending_future(frame.command, result)
        if not self._source_is_current(source_ws, source_generation):
            return
        listeners = tuple(self._listeners.get(frame.command, ()))
        if not listeners:
            return
        if self._listener_task is not None and not self._listener_task.done():
            self._enqueue_listener_work(
                None,
                None,
                result,
                listeners,
                source_ws=source_ws,
                source_generation=source_generation,
            )
            return
        for idx, callback in enumerate(listeners):
            should_continue, maybe = self._invoke_listener_step(callback, result, source_ws, source_generation)
            if not should_continue:
                return
            if maybe is not None:
                self._enqueue_listener_work(
                    maybe,
                    callback,
                    result,
                    listeners[idx + 1 :],
                    source_ws=source_ws,
                    source_generation=source_generation,
                )
                return

    def _fail_all(self, exc: Exception) -> None:
        for queue in self._pending.values():
            while queue:
                future = queue.popleft()
                self._pending_predicates.pop(future, None)
                if not future.done():
                    future.set_exception(exc)
