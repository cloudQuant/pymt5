import asyncio
import contextlib
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable

import websockets

from pymt5.constants import CMD_BOOTSTRAP, DEFAULT_COMMAND_TIMEOUT, DEFAULT_TOKEN_LENGTH, VALID_COMMANDS
from pymt5.crypto import AESCipher, initial_cipher
from pymt5.protocol import ResponseFrame, build_command, pack_outer, parse_response_frame, unpack_outer

logger = logging.getLogger("pymt5.transport")


@dataclass(slots=True)
class CommandResult:
    command: int
    code: int
    body: bytes


class MT5WebSocketTransport:
    def __init__(self, uri: str, timeout: float = DEFAULT_COMMAND_TIMEOUT):
        self.uri = uri
        self.timeout = timeout
        self.ws = None
        self.is_ready = False
        self.token = bytes(DEFAULT_TOKEN_LENGTH)
        self.cipher: AESCipher = initial_cipher()
        self._recv_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._pending: dict[int, deque[asyncio.Future]] = defaultdict(deque)
        self._listeners: dict[int, set[Callable[[CommandResult], Awaitable[None] | None]]] = defaultdict(set)
        self._on_disconnect: Callable[[], None] | None = None

    async def connect(self) -> None:
        self.is_ready = False
        self.cipher = initial_cipher()
        logger.info("connecting to %s", self.uri)
        self.ws = await asyncio.wait_for(
            websockets.connect(
                self.uri,
                ping_interval=None,
                max_size=None,
                open_timeout=self.timeout,
                additional_headers={
                    "Origin": "https://web.metatrader.app",
                },
            ),
            timeout=self.timeout,
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.debug("websocket open, sending bootstrap")
        bootstrap = await self._send_raw(CMD_BOOTSTRAP, self.token, check_ready=False)
        if bootstrap.code != 0:
            raise RuntimeError(f"bootstrap failed: code={bootstrap.code}")
        if len(bootstrap.body) < 66:
            raise RuntimeError(f"bootstrap response too short: {len(bootstrap.body)}")
        self.token = bootstrap.body[2:66]
        self.cipher = AESCipher(bootstrap.body[66:])
        self.is_ready = True
        logger.info("transport ready (key exchanged)")

    async def close(self) -> None:
        logger.info("closing transport")
        self.is_ready = False
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
        self._fail_all(RuntimeError("transport closed"))
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
            raise ValueError(f"unsupported command: {command}")
        if check_ready and not self.is_ready:
            raise RuntimeError(f"transport not ready for command {command}")
        if self.ws is None:
            raise RuntimeError("websocket not connected")
        async with self._lock:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[command].append(future)
            inner = build_command(command, payload)
            encrypted = self.cipher.encrypt(inner)
            logger.debug("send cmd=%d payload=%d bytes", command, len(payload))
            await self.ws.send(pack_outer(encrypted))
        return await asyncio.wait_for(future, timeout=self.timeout)

    async def _recv_loop(self) -> None:
        try:
            assert self.ws is not None
            async for message in self.ws:
                if isinstance(message, str):
                    continue
                try:
                    _, _, encrypted = unpack_outer(bytes(message))
                    decrypted = self.cipher.decrypt(encrypted)
                    frame = parse_response_frame(decrypted)
                    logger.debug("recv cmd=%d code=%d body=%d bytes", frame.command, frame.code, len(frame.body))
                    await self._dispatch(frame)
                except Exception as exc:
                    logger.error("recv_loop parse error: %s", exc)
                    self._fail_all(exc)
                    raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("recv_loop disconnected: %s", exc)
            self._fail_all(exc)
            self.is_ready = False
            if self._on_disconnect:
                self._on_disconnect()

    async def _dispatch(self, frame: ResponseFrame) -> None:
        result = CommandResult(command=frame.command, code=frame.code, body=frame.body)
        queue = self._pending.get(frame.command)
        if queue:
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_result(result)
                    break
        for callback in list(self._listeners.get(frame.command, set())):
            maybe = callback(result)
            if asyncio.iscoroutine(maybe):
                await maybe

    def _fail_all(self, exc: Exception) -> None:
        for queue in self._pending.values():
            while queue:
                future = queue.popleft()
                if not future.done():
                    future.set_exception(exc)

