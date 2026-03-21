import asyncio
import contextlib
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


class TransportState(enum.Enum):
    """Connection lifecycle states for the WebSocket transport."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    CLOSING = "closing"
    ERROR = "error"


@dataclass(slots=True)
class CommandResult:
    command: int
    code: int
    body: bytes


class MT5WebSocketTransport:
    def __init__(
        self,
        uri: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
        rate_limit: float = 0,
        rate_burst: int = 20,
        metrics: MetricsCollector | None = None,
    ):
        self.uri = uri
        self.timeout = timeout
        self.ws: ClientConnection | None = None
        self._state = TransportState.DISCONNECTED
        self.token = bytes(DEFAULT_TOKEN_LENGTH)
        self.cipher: AESCipher = initial_cipher()
        self._recv_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._rate_limiter = TokenBucketRateLimiter(rate=rate_limit, burst=rate_burst)
        self._pending: dict[int, deque[asyncio.Future[CommandResult]]] = defaultdict(deque)
        self._listeners: dict[int, set[Callable[[CommandResult], Awaitable[None] | None]]] = defaultdict(set)
        self._on_disconnect: Callable[[], None] | None = None
        self._shutdown_event = asyncio.Event()
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
        # Guard against double-connect (Phase 2.4)
        if self.ws is not None:
            await self.close()
        self._state = TransportState.CONNECTING
        self._shutdown_event.clear()
        self.cipher = initial_cipher()
        logger.info("connecting to %s", self.uri)
        connect_kwargs: dict[str, Any] = {
            "ping_interval": None,
            "max_size": None,
            "open_timeout": self.timeout,
            "additional_headers": {
                "Origin": "https://web.metatrader.app",
            },
        }
        # websockets >=15 auto-detects system proxy (proxy=True default)
        # which breaks the MT5 binary protocol; bypass it explicitly.
        if _WS_CONNECT_HAS_PROXY:
            connect_kwargs["proxy"] = None
        self.ws = await asyncio.wait_for(
            websockets.connect(self.uri, **connect_kwargs),
            timeout=self.timeout,
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.debug("websocket open, sending bootstrap")
        bootstrap = await self._send_raw(CMD_BOOTSTRAP, self.token, check_ready=False)
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

    async def close(self) -> None:
        logger.info("closing transport")
        self._state = TransportState.CLOSING
        self._shutdown_event.set()
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
        self._fail_all(SessionError("transport closed"))
        self._state = TransportState.DISCONNECTED
        logger.debug("transport closed")

    def on(self, command: int, callback: Callable[[CommandResult], Awaitable[None] | None]) -> None:
        self._listeners[command].add(callback)

    def off(self, command: int, callback: Callable[[CommandResult], Awaitable[None] | None] | None = None) -> None:
        if callback is None:
            self._listeners[command].clear()
            return
        self._listeners[command].discard(callback)

    async def send_command(self, command: int, payload: bytes | None = None) -> CommandResult:
        return await self._send_raw(command, payload or b"", check_ready=True)

    async def _send_raw(self, command: int, payload: bytes, check_ready: bool) -> CommandResult:
        if command not in VALID_COMMANDS:
            raise ProtocolError(f"unsupported command: {command}")
        if check_ready and not self.is_ready:
            raise SessionError(f"transport not ready for command {command}")
        if self.ws is None:
            raise MT5ConnectionError("websocket not connected")
        await self._rate_limiter.acquire()
        async with self._lock:
            future: asyncio.Future[CommandResult] = asyncio.get_running_loop().create_future()
            self._pending[command].append(future)
            inner = build_command(command, payload)
            encrypted = self.cipher.encrypt(inner)
            logger.debug("send cmd=%d payload=%d bytes", command, len(payload))
            await self.ws.send(pack_outer(encrypted))
            if self._metrics:
                self._metrics.on_command_sent(command)
        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError:
            # Remove leaked future from _pending on timeout (Phase 2.1)
            queue = self._pending.get(command)
            if queue:
                try:
                    queue.remove(future)
                except ValueError:
                    pass
            raise MT5TimeoutError(f"command {command} timed out after {self.timeout}s") from None

    async def _recv_loop(self) -> None:
        try:
            assert self.ws is not None
            async for message in self.ws:
                if isinstance(message, str):
                    continue
                try:
                    raw = message if isinstance(message, bytes) else bytes(message)
                    _, _, encrypted = unpack_outer(raw)
                    decrypted = self.cipher.decrypt(encrypted)
                    frame = parse_response_frame(decrypted)
                    self._last_message_at = time.monotonic()
                    logger.debug("recv cmd=%d code=%d body=%d bytes", frame.command, frame.code, len(frame.body))
                    await self._dispatch(frame)
                except (struct.error, ValueError, TypeError, IndexError, ProtocolError) as exc:
                    logger.error("recv_loop parse error: %s", exc)
                    continue
        except asyncio.CancelledError:
            raise
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            logger.error("recv_loop disconnected: %s", exc)
            self._fail_all(exc)
            self._state = TransportState.ERROR
            if self._metrics:
                self._metrics.on_disconnect(str(exc))
            if self._on_disconnect and not self._shutdown_event.is_set():
                self._on_disconnect()

    async def _dispatch(self, frame: ResponseFrame) -> None:
        result = CommandResult(command=frame.command, code=frame.code, body=frame.body)
        if self._metrics:
            self._metrics.on_command_received(frame.command, frame.code)
        queue = self._pending.get(frame.command)
        if queue:
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_result(result)
                    break
        for callback in tuple(self._listeners.get(frame.command, ())):
            try:
                maybe = callback(result)
                if _inspect.isawaitable(maybe):
                    await maybe
            except Exception as exc:
                logger.error(
                    "callback %s raised %s:\n%s",
                    getattr(callback, "__name__", repr(callback)),
                    exc,
                    traceback.format_exc(),
                )
                for error_handler in self._callback_error_handlers:
                    try:
                        error_handler(exc, callback)
                    except Exception:
                        logger.error("callback error handler itself raised: %s", traceback.format_exc())

    def _fail_all(self, exc: Exception) -> None:
        for queue in self._pending.values():
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_exception(exc)
