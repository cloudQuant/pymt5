import asyncio
import os
import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any, TypeVar

# Import mixins
from pymt5._account import _AccountMixin
from pymt5._logging import get_logger
from pymt5._market_data import _MarketDataMixin
from pymt5._metrics import MetricsCollector
from pymt5._order_helpers import _OrderHelpersMixin

# Import parser functions (with re-exports for backward compatibility)
from pymt5._parsers import (  # noqa: F401  — re-exported for backward compatibility
    BUY_ORDER_TYPES,
    PERIOD_MINUTES_MAP,
    SELL_ORDER_TYPES,
    _coerce_optional_timestamp,
    _coerce_timestamp,
    _coerce_timestamp_ms,
    _coerce_timestamp_ms_end,
    _currencies_equal,
    _history_lookback_seconds,
    _matches_group_mask,
    _normalize_full_symbol_record,
    _normalize_timeframe_minutes,
    _order_side,
    _parse_account_response,
    _parse_book_entries,
    _parse_counted_records,
    _parse_open_account_result,
    _parse_rate_bars,
    _parse_tick_batch,
    _parse_verification_status,
    _tick_matches_copy_flags,
    _to_copy_tick_record,
    _validate_requested_stops,
    _validate_requested_volume,
)
from pymt5._push_handlers import _PushHandlersMixin
from pymt5._trading import _TradingMixin
from pymt5.constants import (
    CMD_BOOK_PUSH,
    CMD_INIT,
    CMD_LOGIN,
    CMD_LOGOUT,
    CMD_PING,
    CMD_TICK_PUSH,
    DEFAULT_WS_URI,
    PROP_BYTES,
    PROP_FIXED_STRING,
    PROP_U32,
    PROP_U64,
)
from pymt5.events import HealthStatus
from pymt5.exceptions import PyMT5Error, SessionError, ValidationError
from pymt5.helpers import build_client_id, bytes_to_hex
from pymt5.protocol import SeriesCodec
from pymt5.transport import CommandResult, MT5WebSocketTransport

# Import types (with re-exports for backward compatibility)
from pymt5.types import (  # noqa: F401  — re-exported for backward compatibility
    LOGIN_RESPONSE_SCHEMA,
    OPEN_ACCOUNT_RESPONSE_SCHEMA,
    REAL_ACCOUNT_RESERVED_PAYLOAD,
    TRADE_RESPONSE_SCHEMA,
    TRADER_PARAMS_SCHEMA,
    VERIFICATION_STATUS_SCHEMA,
    AccountDocument,
    AccountInfo,
    AccountOpeningRequest,
    DemoAccountRequest,
    OpenAccountResult,
    RealAccountRequest,
    Record,
    RecordList,
    SymbolInfo,
    TradeResult,
    VerificationStatus,
)

logger = get_logger("pymt5.client")

_T = TypeVar("_T")
MT5_TERMINAL_VERSION = 500
OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES = {
    5687: "15 Mar 2026",
}


class MT5WebClient(_PushHandlersMixin, _AccountMixin, _MarketDataMixin, _TradingMixin, _OrderHelpersMixin):
    def __init__(
        self,
        uri: str = DEFAULT_WS_URI,
        timeout: float = 30.0,
        heartbeat_interval: float = 30.0,
        tick_history_limit: int = 10000,
        auto_reconnect: bool = False,
        max_reconnect_attempts: int = 5,
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 60.0,
        rate_limit: float = 0,
        rate_burst: int = 20,
        metrics: MetricsCollector | None = None,
        symbol_cache_ttl: float = 0,
    ):
        self.uri = uri
        self.timeout = timeout
        self._rate_limit = rate_limit
        self._rate_burst = rate_burst
        self._metrics = metrics
        self.transport = MT5WebSocketTransport(
            uri=uri, timeout=timeout,
            rate_limit=rate_limit, rate_burst=rate_burst,
            metrics=metrics,
        )
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: asyncio.Task | None = None
        self._symbols: dict[str, SymbolInfo] = {}
        self._symbols_by_id: dict[int, SymbolInfo] = {}
        self._full_symbols: dict[str, Record] = {}
        self._symbol_cache_ttl: float = max(0.0, float(symbol_cache_ttl))
        self._symbols_loaded_at: float = 0.0
        self._tick_cache_by_id: dict[int, Record] = {}
        self._tick_cache_by_name: dict[str, Record] = {}
        self._tick_history_limit = max(0, int(tick_history_limit))
        self._tick_history_by_id: dict[int, deque[Record]] = {}
        self._tick_history_by_name: dict[str, deque[Record]] = {}
        self._book_cache_by_id: dict[int, Record] = {}
        self._book_cache_by_name: dict[str, Record] = {}
        self._last_error: tuple[int, str] = (0, "")
        self._logged_in = False
        self._bootstrap_pristine = False
        # Reconnect settings
        self._auto_reconnect = auto_reconnect
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._reconnect_task: asyncio.Task | None = None
        self._closing = False
        # Stored credentials for reconnect
        self._login_kwargs: dict | None = None
        self._subscribed_ids: list[int] = []
        self._subscribed_book_ids: list[int] = []
        # User disconnect callback
        self._on_disconnect: Callable[[], None] | None = None
        # Typed push handler callback lists (Phase 16.1)
        self._typed_tick_handlers: list[Callable] = []
        self._typed_book_handlers: list[Callable] = []
        self._typed_trade_result_handlers: list[Callable] = []
        self._typed_account_handlers: list[Callable] = []
        # Callback error handlers (Phase 16.2)
        self._callback_error_handlers: list[Callable] = []
        # Connection health monitoring (Phase 16.3)
        self._reconnect_count: int = 0
        self._connected_at: float = 0.0
        self._health_degraded_callbacks: list[Callable] = []
        self._health_degraded_threshold_ms: float = 5000.0
        self.transport.on(CMD_TICK_PUSH, self._cache_tick_push)
        self.transport.on(CMD_BOOK_PUSH, self._cache_book_push)
        # Wire up transport disconnect handler
        self.transport._on_disconnect = self._handle_disconnect

    @property
    def is_connected(self) -> bool:
        return self.transport.is_ready and self._logged_in

    @property
    def server_build(self) -> int:
        """Server build number extracted from the bootstrap handshake."""
        return self.transport.server_build

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register a callback for disconnect events."""
        self._on_disconnect = callback

    async def connect(self) -> "MT5WebClient":
        await self.transport.connect()
        self._bootstrap_pristine = True
        self._connected_at = time.monotonic()
        if self.transport.server_build:
            logger.info("connected to %s (server_build=%d)", self.uri, self.transport.server_build)
        else:
            logger.info("connected to %s", self.uri)
        return self

    async def initialize(
        self,
        *,
        version: int = 0,
        password: str = "",
        otp: str = "",
        cid: bytes | None = None,
    ) -> CommandResult:
        """Official-style alias for cmd=29 session initialization."""
        if not self.transport.is_ready:
            await self.connect()
        return await self.init_session(version=version, password=password, otp=otp, cid=cid)

    async def close(self) -> None:
        self._closing = True
        self._stop_heartbeat()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._logged_in:
            try:
                await self.logout()
            except Exception:
                pass
            self._logged_in = False
        await self.transport.close()
        self._bootstrap_pristine = False
        self._closing = False
        # Clear stored credentials from memory
        self._clear_credentials()
        logger.info("connection closed")

    def _clear_credentials(self) -> None:
        """Clear stored credentials from memory for security."""
        if self._login_kwargs is not None:
            # Overwrite password value before discarding
            if "password" in self._login_kwargs:
                self._login_kwargs["password"] = ""
            self._login_kwargs = None

    async def shutdown(self) -> None:
        """Official-style alias for closing the websocket session."""
        await self.close()

    def last_error(self) -> tuple[int, str]:
        """Return the latest client-side compatibility-layer error."""
        return self._last_error

    async def __aenter__(self) -> "MT5WebClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    # ---- Heartbeat ----

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task is not None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                try:
                    await self.ping()
                    logger.debug("heartbeat ping ok")
                except (OSError, PyMT5Error) as exc:
                    logger.warning("heartbeat ping failed: %s", exc)
        except asyncio.CancelledError:
            pass

    # ---- Reconnect ----

    def _handle_disconnect(self) -> None:
        """Called by transport when the WebSocket disconnects unexpectedly."""
        self._logged_in = False
        self._bootstrap_pristine = False
        self._stop_heartbeat()
        logger.warning("disconnected from server")
        if self._on_disconnect:
            self._on_disconnect()
        if self._auto_reconnect and not self._closing and self._login_kwargs:
            if self._reconnect_task is not None and not self._reconnect_task.done():
                logger.debug("reconnect already in progress, skipping")
                return
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Try to reconnect and re-login with stored credentials.

        Uses exponential backoff with jitter:
        ``min(base_delay * 2^(attempt-1) + random(0, base_delay), max_delay)``
        """
        for attempt in range(1, self._max_reconnect_attempts + 1):
            delay = min(
                self._reconnect_delay * (2 ** (attempt - 1)) + random.uniform(0, self._reconnect_delay),
                self._max_reconnect_delay,
            )
            logger.info("reconnect attempt %d/%d (delay=%.1fs)", attempt, self._max_reconnect_attempts, delay)
            if self._metrics:
                self._metrics.on_reconnect_attempt(attempt)
            await asyncio.sleep(delay)
            try:
                # Close old transport to release resources
                try:
                    await self.transport.close()
                except Exception:
                    pass
                # Reset transport for fresh connection
                self.transport = MT5WebSocketTransport(
                    uri=self.uri, timeout=self.timeout,
                    rate_limit=self._rate_limit, rate_burst=self._rate_burst,
                    metrics=self._metrics,
                )
                self.transport._on_disconnect = self._handle_disconnect
                await self.transport.connect()
                # Re-login with stored credentials
                assert self._login_kwargs is not None  # guarded by line 238
                kwargs = dict(self._login_kwargs)
                kwargs["auto_heartbeat"] = True
                await self.login(**kwargs)
                # Re-subscribe to ticks if we had subscriptions
                if self._subscribed_ids:
                    await self.subscribe_ticks(self._subscribed_ids)
                # Re-subscribe to order book if we had subscriptions
                if self._subscribed_book_ids:
                    await self.subscribe_book(self._subscribed_book_ids)
                self._reconnect_count += 1
                self._connected_at = time.monotonic()
                logger.info("reconnected successfully on attempt %d", attempt)
                if self._metrics:
                    self._metrics.on_reconnect_success(attempt)
                return
            except (OSError, PyMT5Error) as exc:
                logger.warning("reconnect attempt %d failed: %s", attempt, exc)
        logger.error("all %d reconnect attempts exhausted", self._max_reconnect_attempts)

    # ---- Health monitoring (Phase 16.3) ----

    async def health_check(self) -> HealthStatus:
        """Return a snapshot of the current connection health.

        If the transport is ready, a ping is sent and the round-trip
        latency is measured. Otherwise ``ping_latency_ms`` will be ``None``.
        """
        ping_latency_ms: float | None = None
        if self.transport.is_ready:
            t0 = time.monotonic()
            try:
                await self.ping()
                ping_latency_ms = (time.monotonic() - t0) * 1000.0
            except (OSError, PyMT5Error):
                ping_latency_ms = None

        last_msg = self.transport._last_message_at if self.transport._last_message_at > 0 else None
        uptime = (time.monotonic() - self._connected_at) if self._connected_at > 0 else 0.0

        status = HealthStatus(
            state=self.transport.state,
            ping_latency_ms=ping_latency_ms,
            last_message_at=last_msg,
            uptime_seconds=uptime,
            reconnect_count=self._reconnect_count,
        )

        # Check degraded health thresholds
        if ping_latency_ms is not None and ping_latency_ms > self._health_degraded_threshold_ms:
            for cb in self._health_degraded_callbacks:
                try:
                    cb(status)
                except Exception as exc:
                    logger.error("health_degraded callback error: %s", exc)

        return status

    def on_health_degraded(
        self,
        callback: Callable[[HealthStatus], None],
        threshold_ms: float = 5000.0,
    ) -> None:
        """Register a callback that fires when ping latency exceeds *threshold_ms*.

        The callback receives the :class:`HealthStatus` snapshot.
        """
        self._health_degraded_threshold_ms = threshold_ms
        self._health_degraded_callbacks.append(callback)

    async def send_raw_command(self, command: int, payload: bytes | None = None) -> CommandResult:
        """Send a raw MT5 command.

        This is the escape hatch for reserved or reverse-engineered commands
        that do not have a first-class helper yet.
        """
        if command in {CMD_INIT, CMD_LOGIN, 52}:
            # These commands change the connection state in ways that make the
            # bootstrap-only reserved helper unsafe to reuse on the same socket.
            self._bootstrap_pristine = False
        return await self.transport.send_command(command, payload or b"")

    async def send_bootstrap_command_52(self) -> CommandResult:
        """Send the reserved bootstrap-only ``cmd=52`` helper.

        Observed against the official Web Terminal build 5687 (built on
        2026-03-15):

        - on a fresh bootstrap-only connection, ``cmd=52`` returns ``code=0``
          with an empty body
        - after ``cmd=29`` or ``cmd=28``, the same command causes the server to
          drop the socket

        The numeric ID is kept in the public name intentionally because the
        business meaning is still unknown.
        """
        if not self.transport.is_ready:
            raise SessionError("transport not ready")
        if self._logged_in or not self._bootstrap_pristine:
            raise SessionError(
                "cmd=52 is only safe on a fresh bootstrap-only connection; "
                "create a new client and call it before init_session() or login()",
            )
        self._bootstrap_pristine = False
        return await self.transport.send_command(52)

    async def init_session(
        self,
        version: int = 0,
        password: str = "",
        otp: str = "",
        cid: bytes | None = None,
    ) -> CommandResult:
        self._bootstrap_pristine = False
        payload = self._build_init_payload(
            version=version,
            password=password,
            otp=otp,
            cid=cid,
        )
        return await self.transport.send_command(CMD_INIT, payload)

    async def login(
        self,
        login: int,
        password: str,
        url: str = "",
        session: int = 0,
        otp: str = "",
        version: int = 0,
        cid: bytes | None = None,
        lead_cookie_id: int = 0,
        lead_affiliate_site: str = "",
        utm_campaign: str = "",
        utm_source: str = "",
        auto_heartbeat: bool = True,
    ) -> tuple[str, int]:
        self._bootstrap_pristine = False
        payload = self._build_login_payload(
            login=login,
            password=password,
            url=url,
            session=session,
            otp=otp,
            version=version,
            cid=cid,
            lead_cookie_id=lead_cookie_id,
            lead_affiliate_site=lead_affiliate_site,
            utm_campaign=utm_campaign,
            utm_source=utm_source,
        )
        result = await self.transport.send_command(CMD_LOGIN, payload)
        token_bytes, session_id = SeriesCodec.parse(result.body, LOGIN_RESPONSE_SCHEMA)
        self._logged_in = True
        # Store credentials for potential reconnect
        self._login_kwargs = {
            "login": login,
            "password": password,
            "url": url,
            "session": session,
            "otp": otp,
            "version": version,
            "cid": cid,
            "lead_cookie_id": lead_cookie_id,
            "lead_affiliate_site": lead_affiliate_site,
            "utm_campaign": utm_campaign,
            "utm_source": utm_source,
        }
        logger.info("logged in: login=%d session=%d", login, int(session_id))
        if auto_heartbeat:
            self._start_heartbeat()
        return bytes_to_hex(token_bytes), int(session_id)

    async def ping(self) -> None:
        await self.transport.send_command(CMD_PING)

    async def logout(self) -> None:
        self._stop_heartbeat()
        await self.transport.send_command(CMD_LOGOUT)
        self._logged_in = False
        self._bootstrap_pristine = False
        logger.info("logged out")

    def _clear_last_error(self) -> None:
        self._last_error = (0, "")

    def _fail_last_error(self, code: int, message: str) -> _T | None:
        self._last_error = (int(code), message)
        logger.debug("compat helper error %d: %s", code, message)
        return None

    def _resolve_client_id(self, cid: bytes | None) -> bytes:
        client_id = (
            bytes(cid)
            if cid is not None
            else build_client_id(
                platform=os.name,
                device_pixel_ratio="1",
                language="en-US",
                screen="0x0",
            )
        )
        if len(client_id) != 16:
            raise ValidationError(f"cid must be 16 bytes, got {len(client_id)}")
        return client_id

    def _build_login_payload(
        self,
        *,
        login: int,
        password: str,
        url: str,
        session: int,
        otp: str,
        version: int,
        cid: bytes | None,
        lead_cookie_id: int,
        lead_affiliate_site: str,
        utm_campaign: str,
        utm_source: str,
    ) -> bytes:
        client_id = self._resolve_client_id(cid)
        password_prefix, password_blob = self._split_password_blob(password)
        fields: list[tuple[Any, ...]] = [
            (PROP_U32, version or 0),
            (PROP_FIXED_STRING, password_prefix, 64),
            (PROP_FIXED_STRING, (otp or "")[:64], 128),
            (PROP_BYTES, client_id, 16),
            (PROP_FIXED_STRING, (utm_campaign or "")[:32], 64),
            (PROP_FIXED_STRING, (utm_source or "")[:32], 64),
            (PROP_U64, lead_cookie_id or 0),
            (PROP_FIXED_STRING, (lead_affiliate_site or "")[:64], 128),
            (PROP_U32, min(len(url or ""), 128)),
            (PROP_FIXED_STRING, (url or "")[:128], 256),
            (PROP_U64, int(login)),
            (PROP_BYTES, password_blob or bytes(160), 160),
            (PROP_U64, int(session or 0)),
        ]
        return SeriesCodec.serialize(fields)

    def _build_init_payload(
        self,
        *,
        version: int,
        password: str,
        otp: str,
        cid: bytes | None,
    ) -> bytes:
        client_id = self._resolve_client_id(cid)
        fields: list[tuple[Any, ...]] = [
            (PROP_U32, version or 0),
            (PROP_FIXED_STRING, (password or "")[:32], 64),
            (PROP_FIXED_STRING, (otp or "")[:64], 128),
            (PROP_BYTES, client_id, 16),
            (PROP_FIXED_STRING, "", 64),
            (PROP_FIXED_STRING, "", 64),
            (PROP_U64, 0),
            (PROP_FIXED_STRING, "", 128),
            (PROP_U32, 0),
            (PROP_FIXED_STRING, "", 256),
            (PROP_U64, 0),
        ]
        return SeriesCodec.serialize(fields)

    def _build_otp_setup_payload(
        self,
        *,
        login: int,
        password: str,
        otp: str = "",
        otp_secret: str = "",
        otp_secret_check: str = "",
        cid: bytes | None,
    ) -> bytes:
        client_id = self._resolve_client_id(cid)
        password_prefix, password_blob = self._split_password_blob(password)
        fields: list[tuple[Any, ...]] = [
            (PROP_U32, 5),
            (PROP_U64, int(login)),
            (PROP_FIXED_STRING, password_prefix, 64),
            (PROP_FIXED_STRING, (otp or "")[:64], 128),
            (PROP_FIXED_STRING, (otp_secret or "")[:64], 128),
            (PROP_FIXED_STRING, (otp_secret_check or "")[:64], 128),
            (PROP_BYTES, client_id, 16),
        ]
        if password_blob is not None:
            fields.append((PROP_BYTES, password_blob))
        return SeriesCodec.serialize(fields)
