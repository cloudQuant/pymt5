"""High-level asynchronous client for the MetaTrader 5 Web Terminal.

This module combines encrypted WebSocket transport, session management, account
and market-data operations, trading helpers, and push-notification handling.
:class:`MT5WebClient` supports asynchronous context management and optional
reconnection after unexpected disconnects.
"""

import asyncio
import math
import os
import random
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from websockets.exceptions import WebSocketException

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
    _validate_rate_bar_format,
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
from pymt5.constants import MT5_TERMINAL_VERSION as MT5_TERMINAL_VERSION
from pymt5.constants import OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES as OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES
from pymt5.events import HealthStatus
from pymt5.exceptions import AuthenticationError, PyMT5Error, SessionError, ValidationError
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
    TickPriceMode,
    TradeResult,
    VerificationStatus,
)

logger = get_logger("pymt5.client")

_T = TypeVar("_T")


class MT5WebClient(_PushHandlersMixin, _AccountMixin, _MarketDataMixin, _TradingMixin, _OrderHelpersMixin):
    """Provide an asynchronous MetaTrader 5 Web Terminal client.

    The client owns its transport, authentication state, heartbeat, caches, and
    push subscriptions. Using it as an asynchronous context manager connects on
    entry and closes on exit; authentication still requires an explicit call to
    :meth:`login`.

    If automatic reconnection is enabled, an unexpected disconnect after a
    successful login triggers exponential backoff with jitter, reauthentication,
    and restoration of tick and order-book subscriptions. Pending commands are
    not replayed. Calling :meth:`close` cancels reconnection, clears stored
    credentials, and prevents new sessions until cleanup is quiescent.
    """

    def __init__(
        self,
        uri: str = DEFAULT_WS_URI,
        timeout: float = 30.0,
        heartbeat_interval: float = 30.0,
        tick_history_limit: int = 10000,
        max_tick_symbols: int = 0,
        auto_reconnect: bool = False,
        max_reconnect_attempts: int = 5,
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 60.0,
        rate_limit: float = 0,
        rate_burst: int = 20,
        metrics: MetricsCollector | None = None,
        symbol_cache_ttl: float = 0,
        trade_protocol: str = "legacy",
        rate_bar_format: str = "auto",
        tick_price_mode: TickPriceMode = "auto",
        tick_price_modes: Mapping[str, TickPriceMode] | None = None,
    ):
        """Initialize a client without opening a network connection.

        Args:
            uri: MetaTrader 5 Web Terminal WebSocket URI.
            timeout: Default timeout in seconds for transport operations and
                shutdown coordination.
            heartbeat_interval: Seconds between heartbeat pings after login.
            tick_history_limit: Maximum ticks retained per symbol. Zero means
                no per-symbol history limit.
            max_tick_symbols: Maximum symbols with retained tick histories.
                Zero means unlimited; otherwise the least recently updated
                symbol's history is evicted first.
            auto_reconnect: Whether to reconnect and authenticate again after
                an unexpected disconnect.
            max_reconnect_attempts: Maximum attempts made per reconnection loop.
            reconnect_delay: Base delay in seconds for exponential backoff and
                the upper bound of its random jitter.
            max_reconnect_delay: Maximum delay in seconds between attempts.
            rate_limit: Sustained command rate per second. Zero disables
                command throttling.
            rate_burst: Maximum token-bucket burst size for command throttling.
            metrics: Optional collector for connection, command, and reconnect
                metrics.
            symbol_cache_ttl: Seconds for which ``load_symbols()`` may reuse its
                cache. Zero preserves the behavior of reloading on every call.
            trade_protocol: Trade request layout, either ``"legacy"`` or
                ``"build_6090"``.
            rate_bar_format: Bar parser mode: ``"auto"``, ``"standard"``, or
                ``"extended"``.
            tick_price_mode: Default tick price interpretation: ``"auto"``,
                ``"scaled"``, or ``"normalized"``.
            tick_price_modes: Optional per-symbol tick price mode overrides.
                Symbol keys are stripped and normalized to uppercase.

        Raises:
            ValueError: If the trade protocol, a tick price mode, or a tick
                price override is invalid.
            ValidationError: If ``rate_bar_format`` or ``heartbeat_interval``
                is invalid.

        """
        if trade_protocol not in ("legacy", "build_6090"):
            raise ValueError("trade_protocol must be 'legacy' or 'build_6090'")
        if tick_price_mode not in ("auto", "scaled", "normalized"):
            raise ValueError("tick_price_mode must be 'auto', 'scaled', or 'normalized'")
        if (
            isinstance(heartbeat_interval, bool)
            or not isinstance(heartbeat_interval, (int, float))
            or not math.isfinite(heartbeat_interval)
            or heartbeat_interval <= 0
        ):
            raise ValidationError(f"heartbeat_interval must be a finite value > 0, got {heartbeat_interval!r}")
        self._tick_price_mode = tick_price_mode
        self._tick_price_modes: dict[str, TickPriceMode] = {}
        for symbol, mode in (tick_price_modes or {}).items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("tick_price_modes keys must be non-empty symbol names")
            if mode not in ("auto", "scaled", "normalized"):
                raise ValueError(f"invalid tick price mode for {symbol!r}: {mode!r}")
            self._tick_price_modes[symbol.strip().upper()] = mode
        self._tick_price_warning_keys: set[tuple[str, str]] = set()
        _validate_rate_bar_format(rate_bar_format)
        self._trade_protocol = trade_protocol
        self._rate_bar_format = rate_bar_format
        self.uri = uri
        self.timeout = timeout
        self._rate_limit = rate_limit
        self._rate_burst = rate_burst
        self._metrics = metrics
        self.transport = MT5WebSocketTransport(
            uri=uri,
            timeout=timeout,
            rate_limit=rate_limit,
            rate_burst=rate_burst,
            metrics=metrics,
        )
        self._heartbeat_interval = float(heartbeat_interval)
        self._heartbeat_task: asyncio.Task | None = None
        self._symbols: dict[str, SymbolInfo] = {}
        self._symbols_by_id: dict[int, SymbolInfo] = {}
        self._full_symbols: dict[str, Record] = {}
        self._full_symbols_loaded_at: dict[str, float] = {}
        self._symbol_cache_ttl: float = max(0.0, float(symbol_cache_ttl))
        self._symbols_loaded_at: float = 0.0
        self._unknown_symbol_misses: dict[str, float] = {}
        self._tick_cache_by_id: dict[int, Record] = {}
        self._tick_cache_by_name: dict[str, Record] = {}
        self._tick_history_limit = max(0, int(tick_history_limit))
        self._max_tick_symbols = max(0, int(max_tick_symbols))
        self._tick_history_by_id: dict[int, deque[Record]] = {}
        self._tick_history_by_name: dict[str, deque[Record]] = {}
        self._tick_history_access_order: list[int] = []
        self._book_cache_by_id: dict[int, Record] = {}
        self._book_cache_by_name: dict[str, Record] = {}
        self._last_error: tuple[int, str] = (0, "")
        self._logged_in = False
        self._session_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._session_generation = 0
        self._unfinished_close_task: asyncio.Task[None] | None = None
        self._reconnect_close_tasks: set[asyncio.Task[None]] = set()
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
        self._tick_subscription_lock = asyncio.Lock()
        self._subscribed_book_ids: list[int] = []
        self._book_subscription_lock = asyncio.Lock()
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
        self._health_degraded_callbacks: list[tuple[Callable[[HealthStatus], Any], float]] = []
        self._health_degraded_threshold_ms: float = 5000.0
        self.transport.on(CMD_TICK_PUSH, self._cache_tick_push)
        self.transport.on(CMD_BOOK_PUSH, self._cache_book_push)
        # Wire up transport disconnect handler
        self.transport._on_disconnect = self._handle_disconnect

    @property
    def is_connected(self) -> bool:
        """Whether the transport is ready and the client is authenticated.

        Returns:
            ``True`` only when the encrypted transport is ready and login has
            completed successfully; otherwise ``False``.

        """
        return self.transport.is_ready and self._logged_in

    @property
    def server_build(self) -> int:
        """Server build number extracted from the bootstrap handshake."""
        return self.transport.server_build

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register a callback for disconnect events."""
        self._on_disconnect = callback

    async def connect(self) -> "MT5WebClient":
        """Open the WebSocket and complete the encrypted bootstrap exchange.

        Connecting does not authenticate the account. Call :meth:`login` after
        this method succeeds. A concurrent close invalidates the attempt rather
        than allowing it to establish a replacement session.

        Returns:
            This client instance, ready for session initialization or login.

        Raises:
            SessionError: If the client is already logged in, cleanup from a
                previous session is still running, or close invalidates the
                connection attempt.
            MT5ConnectionError: If the bootstrap exchange fails or returns a
                malformed response.
            TimeoutError: If opening the socket or exchanging bootstrap data
                exceeds the configured timeout.
            asyncio.CancelledError: If the caller cancels the attempt. Partial
                transport resources are still cleaned up.

        """
        self._assert_close_quiescent()
        generation = self._session_generation
        await self._cancel_active_reconnect_if_foreground()
        self._assert_close_quiescent()
        if generation != self._session_generation:
            raise SessionError("connection invalidated by close")
        transport = self.transport
        if self._logged_in:
            raise SessionError("already logged in; call logout() before reconnecting")
        await transport.connect()
        if generation != self._session_generation or transport is not self.transport or self._closing:
            previous_on_disconnect = transport._on_disconnect
            await self._close_reconnect_transport(transport)
            if transport is self.transport:
                transport._on_disconnect = previous_on_disconnect
            raise SessionError("connection invalidated by close")
        transport._on_disconnect = self._handle_disconnect
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
        """Log out if necessary and close the client session.

        Heartbeat and reconnection work are stopped, the transport is closed,
        and local login state and stored credentials are cleared. Cleanup is
        coordinated with in-flight login and logout operations and is bounded
        by the configured timeout plus a short cancellation grace period.
        Non-cooperative cleanup may continue in a detached task after that
        deadline, while new sessions remain protected by generation checks.

        The first cleanup error is re-raised after best-effort cleanup. Caller
        cancellation is preserved, and authentication state is reset even when
        logout or transport closure fails.

        Returns:
            None.

        Raises:
            SessionError: If another close or incompatible reconnect cleanup is
                still preventing a new close from starting.
            TimeoutError: If session cleanup exceeds its configured deadline.
            asyncio.CancelledError: If caller cancellation is the first error
                recorded during shutdown.
            Exception: If logout, receive-task cleanup, or transport closure
                fails; the first such error is propagated.

        """
        async with self._close_lock:
            self._assert_close_quiescent(allow_reconnect_cleanup=True)
            self._session_generation += 1
            self._closing = True
            errors: list[BaseException] = []
            phase = "reconnect"
            owner_task = asyncio.current_task()

            def record(exc: BaseException) -> None:
                if not errors:
                    errors.append(exc)

            async def cleanup() -> None:
                nonlocal phase
                try:
                    self._stop_heartbeat()
                    reconnect_task = self._reconnect_task
                    self._reconnect_task = None
                    if reconnect_task is not None and reconnect_task is not owner_task:
                        reconnect_task.cancel()
                        try:
                            await reconnect_task
                        except asyncio.CancelledError as exc:
                            current = asyncio.current_task()
                            if current is not None and current.cancelling():
                                record(exc)
                        except BaseException as exc:
                            record(exc)
                    phase = "session_lock"
                    await self._session_lock.acquire()
                    try:
                        if self._logged_in:
                            phase = "logout"
                            try:
                                await self._logout_unlocked()
                            except BaseException as exc:
                                record(exc)
                        self._stop_heartbeat()
                        phase = "transport_close"
                        try:
                            await self.transport.close()
                        except BaseException as exc:
                            record(exc)
                    finally:
                        self._session_lock.release()
                except BaseException as exc:
                    record(exc)
                    # Even an interrupted session-lock wait must try to stop transport.
                    phase = "transport_close"
                    try:
                        await self.transport.close()
                    except BaseException as close_exc:
                        record(close_exc)
                finally:
                    self._stop_heartbeat()
                    self._logged_in = False
                    self._bootstrap_pristine = False
                    self._connected_at = 0.0
                    self._clear_credentials()

            cleanup_task = asyncio.create_task(cleanup())
            self._unfinished_close_task = cleanup_task
            deadline = asyncio.get_running_loop().time() + max(self.timeout, 0.01)
            cancelled_cleanup = False
            try:
                while not cleanup_task.done():
                    try:
                        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                        await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=remaining)
                    except asyncio.CancelledError as exc:
                        record(exc)
                        # Preserve an in-flight login's session-lock ordering. Interrupt
                        # other waits once so cancellation cannot strand shutdown.
                        if phase != "session_lock" and not cancelled_cleanup and not cleanup_task.done():
                            cancelled_cleanup = True
                            cleanup_task.cancel()
                    except TimeoutError as exc:
                        record(exc)
                        cleanup_task.cancel()
                        grace_deadline = asyncio.get_running_loop().time() + 0.1
                        while not cleanup_task.done():
                            try:
                                remaining = max(0.0, grace_deadline - asyncio.get_running_loop().time())
                                await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=remaining)
                            except asyncio.CancelledError as cancel_exc:
                                record(cancel_exc)
                            except TimeoutError:
                                break
                        if not cleanup_task.done():
                            cleanup_task.add_done_callback(self._consume_reconnect_close_result)
                            logger.warning("close cleanup did not stop after cancellation")
                        break
                if cleanup_task.done() and not cleanup_task.cancelled():
                    try:
                        cleanup_task.result()
                    except BaseException as exc:
                        record(exc)
            finally:
                self._stop_heartbeat()
                self._logged_in = False
                self._bootstrap_pristine = False
                self._connected_at = 0.0
                self._clear_credentials()
                self._closing = False
            if errors:
                raise errors[0]
            logger.info("connection closed")

    def _assert_close_quiescent(self, *, allow_reconnect_cleanup: bool = False) -> None:
        """Reject new sessions until the previous close has finished cleanup."""
        if self._closing or (self._unfinished_close_task is not None and not self._unfinished_close_task.done()):
            raise SessionError("close cleanup is still in progress")
        if not allow_reconnect_cleanup and any(not task.done() for task in self._reconnect_close_tasks):
            raise SessionError("reconnect cleanup is still in progress")

    async def _cancel_active_reconnect_if_foreground(self) -> None:
        """Cancel and bounded-await an active reconnect task unless called from it."""
        reconnect_task = self._reconnect_task
        current_task = asyncio.current_task()
        if reconnect_task is None or reconnect_task is current_task:
            return
        if not reconnect_task.done():
            reconnect_task.cancel()
            cancelling_before = current_task.cancelling() if current_task is not None else 0
            try:
                await asyncio.wait_for(asyncio.shield(reconnect_task), timeout=max(self.timeout, 0.01))
            except asyncio.CancelledError:
                if current_task is not None and current_task.cancelling() > cancelling_before:
                    if not reconnect_task.done():
                        self._reconnect_close_tasks.add(reconnect_task)
                        reconnect_task.add_done_callback(self._reconnect_close_tasks.discard)
                    reconnect_task.add_done_callback(self._consume_reconnect_close_result)
                    raise
            except TimeoutError as exc:
                self._reconnect_close_tasks.add(reconnect_task)
                reconnect_task.add_done_callback(self._reconnect_close_tasks.discard)
                reconnect_task.add_done_callback(self._consume_reconnect_close_result)
                if self._reconnect_task is reconnect_task:
                    self._reconnect_task = None
                raise SessionError("reconnect cleanup is still in progress") from exc
            except BaseException as exc:
                logger.warning("active reconnect task failed during cancellation: %s", exc)
        elif not reconnect_task.cancelled():
            try:
                reconnect_task.result()
            except BaseException as exc:
                logger.warning("completed reconnect task failed: %s", exc)
        if self._reconnect_task is reconnect_task:
            self._reconnect_task = None

    def _clear_subscriptions(self) -> None:
        """Clear session subscription tracking and cached market data."""
        self._subscribed_ids.clear()
        self._subscribed_book_ids.clear()
        self._tick_cache_by_id.clear()
        self._tick_cache_by_name.clear()
        self._tick_history_by_id.clear()
        self._tick_history_by_name.clear()
        self._tick_history_access_order.clear()
        self._book_cache_by_id.clear()
        self._book_cache_by_name.clear()

    def _clear_credentials(self) -> None:
        """Clear stored credentials and session subscription state from memory."""
        if self._login_kwargs is not None:
            # Replace the stored reference before dropping the kwargs dict.
            # The original immutable str cannot be wiped in place.
            pw = self._login_kwargs.get("password")
            if isinstance(pw, str) and pw:
                self._login_kwargs["password"] = "\x00" * len(pw)
            self._login_kwargs = None
            logger.debug("credentials cleared from memory")
        self._clear_subscriptions()

    async def shutdown(self) -> None:
        """Official-style alias for closing the websocket session."""
        await self.close()

    def last_error(self) -> tuple[int, str]:
        """Return the latest client-side compatibility-layer error."""
        return self._last_error

    async def __aenter__(self) -> "MT5WebClient":
        """Connect and return the client for asynchronous context management."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close the client when exiting an asynchronous context."""
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
                except (OSError, PyMT5Error, WebSocketException) as exc:
                    logger.warning("heartbeat ping failed: %s", exc)
        except asyncio.CancelledError:
            pass

    # ---- Reconnect ----

    def _call_metrics(self, method_name: str, *args: Any) -> None:
        """Invoke an optional metrics hook safely without affecting lifecycle flow."""
        if self._metrics is None:
            return
        try:
            getattr(self._metrics, method_name)(*args)
        except Exception as exc:
            logger.warning("metrics hook %s failed: %s", method_name, exc)

    def _handle_disconnect(self) -> None:
        """Handle an unexpected WebSocket disconnect reported by the transport."""
        self._logged_in = False
        self._bootstrap_pristine = False
        self._connected_at = 0.0
        self._stop_heartbeat()
        logger.warning("disconnected from server")
        if self._on_disconnect:
            try:
                self._on_disconnect()
            except Exception as exc:
                # User callback failures must not suppress reconnection. Avoid
                # logging exception text, which may contain sensitive data.
                logger.error("disconnect callback failed: %s", type(exc).__name__)
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
        generation = self._session_generation
        owned_transport = self.transport

        def still_current() -> bool:
            return (
                generation == self._session_generation
                and self.transport is owned_transport
                and not self._closing
                and (self._unfinished_close_task is None or self._unfinished_close_task.done())
                and not any(not task.done() for task in self._reconnect_close_tasks)
            )

        for attempt in range(1, self._max_reconnect_attempts + 1):
            if not still_current():
                return
            delay = min(
                self._reconnect_delay * (2 ** (attempt - 1)) + random.uniform(0, self._reconnect_delay),
                self._max_reconnect_delay,
            )
            logger.info("reconnect attempt %d/%d (delay=%.1fs)", attempt, self._max_reconnect_attempts, delay)
            self._call_metrics("on_reconnect_attempt", attempt)
            try:
                await asyncio.sleep(delay)
                if not still_current():
                    return
                self._logged_in = False
                self._stop_heartbeat()
                # Close old transport to release resources. Its listener sets
                # remain intact, so snapshot them afterwards to include any
                # registrations or removals made while close was in progress.
                old_transport = owned_transport
                await self._close_reconnect_transport(old_transport)
                if not still_current():
                    return
                # Reset transport for fresh connection and migrate both internal
                # and user callbacks. Sets prevent duplicates across retries.
                new_transport = MT5WebSocketTransport(
                    uri=self.uri,
                    timeout=self.timeout,
                    rate_limit=self._rate_limit,
                    rate_burst=self._rate_burst,
                    metrics=self._metrics,
                )
                for command, callbacks in old_transport._listeners.items():
                    for callback in callbacks:
                        new_transport.on(command, callback)
                new_transport._callback_error_handlers.extend(old_transport._callback_error_handlers)
                new_transport._on_disconnect = self._handle_disconnect
                if not still_current():
                    return
                self.transport = new_transport
                owned_transport = new_transport
                await owned_transport.connect()
                if not still_current():
                    return
                # Re-login with stored credentials
                if self._login_kwargs is None:
                    raise SessionError("cannot reconnect: no stored credentials")
                kwargs = dict(self._login_kwargs)
                kwargs["auto_heartbeat"] = True
                await self.login(**kwargs)
                if not still_current():
                    return
                # Re-subscribe to ticks if we had subscriptions
                if self._subscribed_ids:
                    await self.subscribe_ticks(self._subscribed_ids)
                    if not still_current():
                        return
                # Re-subscribe to order book if we had subscriptions
                if self._subscribed_book_ids:
                    await self.subscribe_book(self._subscribed_book_ids)
                    if not still_current():
                        return
                self._reconnect_count += 1
                self._connected_at = time.monotonic()
                logger.info("reconnected successfully on attempt %d", attempt)
                self._call_metrics("on_reconnect_success", attempt)
                return
            except (OSError, PyMT5Error, WebSocketException) as exc:
                if not still_current():
                    return
                self._logged_in = False
                self._stop_heartbeat()
                await self._close_reconnect_transport(owned_transport)
                if not still_current():
                    return
                logger.warning("reconnect attempt %d failed: %s", attempt, exc)
            except asyncio.CancelledError:
                if still_current():
                    self._logged_in = False
                    self._stop_heartbeat()
                    await self._close_reconnect_transport(owned_transport)
                raise
        logger.error("all %d reconnect attempts exhausted", self._max_reconnect_attempts)

    async def _close_reconnect_transport(self, transport: MT5WebSocketTransport | None = None) -> None:
        """Disable command dispatch and callbacks before closing a failed session."""
        if transport is None:
            transport = self.transport
        transport._on_disconnect = None
        transport.is_ready = False
        close_task = asyncio.create_task(transport.close())
        self._reconnect_close_tasks.add(close_task)
        close_task.add_done_callback(self._reconnect_close_tasks.discard)
        deadline = asyncio.get_running_loop().time() + max(0.0, self.timeout)
        cancelled = False
        while True:
            try:
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                await asyncio.wait_for(asyncio.shield(close_task), timeout=remaining)
                break
            except asyncio.CancelledError:
                cancelled = True
                if close_task.done():
                    break
            except TimeoutError:
                close_task.cancel()
                # Consume a late exception even if a transport ignores cancellation.
                close_task.add_done_callback(self._consume_reconnect_close_result)
                logger.warning("timed out closing reconnect transport")
                break
            except Exception:
                logger.warning("failed to close reconnect transport", exc_info=True)
                break
        if cancelled:
            raise asyncio.CancelledError

    @staticmethod
    def _consume_reconnect_close_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("failed to close reconnect transport", exc_info=True)

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
            except (OSError, PyMT5Error, WebSocketException):
                ping_latency_ms = None

        last_msg = self.transport._last_message_at if self.transport._last_message_at > 0 else None
        uptime = (
            (time.monotonic() - self._connected_at) if (self.transport.is_ready and self._connected_at > 0) else 0.0
        )

        status = HealthStatus(
            state=self.transport.state,
            ping_latency_ms=ping_latency_ms,
            last_message_at=last_msg,
            uptime_seconds=uptime,
            reconnect_count=self._reconnect_count,
        )

        # Check degraded health thresholds independently per callback
        if ping_latency_ms is not None:
            for cb, threshold_ms in self._health_degraded_callbacks:
                if ping_latency_ms > threshold_ms:
                    try:
                        cb(status)
                    except Exception as exc:
                        logger.error("health_degraded callback error: %s", exc)

        return status

    def on_health_degraded(
        self,
        callback: Callable[[HealthStatus], Any],
        threshold_ms: float = 5000.0,
    ) -> None:
        """Register a callback that fires when ping latency exceeds *threshold_ms*.

        Each registered callback retains its own latency threshold independently.
        The callback receives the :class:`HealthStatus` snapshot.
        """
        if (
            isinstance(threshold_ms, bool)
            or not isinstance(threshold_ms, (int, float))
            or not math.isfinite(threshold_ms)
            or threshold_ms <= 0
        ):
            raise ValidationError(f"threshold_ms must be a finite value > 0, got {threshold_ms!r}")
        self._health_degraded_callbacks.append((callback, float(threshold_ms)))

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
        """Send the MT5 session-initialization command on an open transport.

        Unlike :meth:`initialize`, this method does not connect automatically.
        The raw result is returned without interpreting its status code.

        Args:
            version: Terminal protocol version to send, or zero for the default.
            password: Optional session-initialization password.
            otp: Optional one-time password.
            cid: Optional 16-byte client identifier. A client identifier is
                generated when omitted.

        Returns:
            The decoded command result, including its server status code and
            unparsed response body.

        Raises:
            ValidationError: If ``cid`` is not exactly 16 bytes.
            SessionError: If the transport is not ready or the connection is
                invalidated while waiting.
            MT5TimeoutError: If sending or receiving exceeds the configured
                command timeout.

        """
        self._assert_close_quiescent()
        generation = self._session_generation
        transport = self.transport
        async with self._session_lock:
            self._assert_close_quiescent()
            if generation != self._session_generation or transport is not self.transport:
                raise SessionError("init_session invalidated by close")
            if not transport.is_ready:
                raise SessionError("transport not ready")
            self._bootstrap_pristine = False
            payload = self._build_init_payload(
                version=version,
                password=password,
                otp=otp,
                cid=cid,
            )
            result = await transport.send_command(CMD_INIT, payload)
            if generation != self._session_generation or transport is not self.transport or self._closing:
                raise SessionError("init_session invalidated by close")
            return result

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
        """Authenticate the current transport and start an MT5 session.

        Call :meth:`connect` before logging in. Successful authentication stores
        the login arguments for optional reconnection; logout and close clear
        them. Session-changing operations are serialized so that close cannot
        silently invalidate a successful login.

        Args:
            login: Numeric MT5 account login.
            password: Account password used to construct the login payload.
            url: Optional URL string forwarded in the login payload.
            session: Session identifier to include, or zero for a new session.
            otp: Optional one-time password.
            version: Terminal protocol version to send, or zero for the default.
            cid: Optional 16-byte client identifier. A client identifier is
                generated when omitted.
            lead_cookie_id: Optional lead-cookie identifier for attribution.
            lead_affiliate_site: Optional affiliate-site attribution string.
            utm_campaign: Optional campaign attribution string.
            utm_source: Optional source attribution string.
            auto_heartbeat: Whether to start periodic heartbeat pings after
                successful authentication.

        Returns:
            A tuple containing the authentication token as a hexadecimal string
            and the numeric MT5 session identifier.

        Raises:
            ValidationError: If ``cid`` is not exactly 16 bytes.
            AuthenticationError: If the server rejects the login.
            SessionError: If cleanup is pending, the client is already logged
                in, the transport is not ready, or close invalidates the login.
            MT5TimeoutError: If sending or receiving exceeds the configured
                command timeout. Once transmission has begun, authentication
                may have completed remotely even though no result was received.

        """
        self._assert_close_quiescent()
        generation = self._session_generation
        await self._cancel_active_reconnect_if_foreground()
        self._assert_close_quiescent()
        transport = self.transport
        async with self._session_lock:
            self._assert_close_quiescent()
            if generation != self._session_generation or transport is not self.transport:
                raise SessionError("login invalidated by close")
            if self._logged_in:
                raise SessionError("already logged in; call logout() before logging in again")
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
            result = await transport.send_command(CMD_LOGIN, payload)
            if generation != self._session_generation or transport is not self.transport or self._closing:
                raise SessionError("login invalidated by close")
            if result.code != 0:
                raise AuthenticationError(f"MT5 Web Terminal rejected login (response code {result.code})")
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
        """Send a heartbeat command and wait for its response.

        The response body and status code are not interpreted.

        Returns:
            None.

        Raises:
            SessionError: If the transport is not ready or is invalidated.
            MT5TimeoutError: If sending or receiving exceeds the configured
                command timeout.

        """
        await self.transport.send_command(CMD_PING)

    async def logout(self) -> None:
        """End the authenticated session and clear local login state.

        Active reconnection is cancelled first. If that cancellation leaves no
        ready transport, local authentication state is cleared without sending
        another command. Otherwise, a successful logout response stops the
        heartbeat and clears stored credentials. A server rejection leaves the
        login state unchanged; transport failures may independently mark the
        client disconnected and trigger configured reconnection handling.

        Returns:
            None.

        Raises:
            SessionError: If cleanup is pending, the transport is not ready,
                the server rejects logout, or close invalidates the operation.
            MT5TimeoutError: If sending or receiving exceeds the configured
                command timeout. Once transmission has begun, the server may
                have processed the logout even though no result was received.

        """
        self._assert_close_quiescent()
        generation = self._session_generation
        transport = self.transport
        async with self._session_lock:
            if generation != self._session_generation or transport is not self.transport:
                raise SessionError("logout invalidated by close")
            self._assert_close_quiescent()
            await self._logout_unlocked()

    async def _logout_unlocked(self) -> None:
        generation = self._session_generation
        transport = self.transport
        reconnect_task = self._reconnect_task
        if reconnect_task is not None and not reconnect_task.done() and reconnect_task is not asyncio.current_task():
            reconnect_task.cancel()
            try:
                await reconnect_task
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
            if generation != self._session_generation or transport is not self.transport:
                raise SessionError("logout invalidated by close")
            if self._reconnect_task is reconnect_task:
                self._reconnect_task = None
            # Cancellation closes the reconnect transport, invalidating that session.
            if not self.transport.is_ready:
                self._stop_heartbeat()
                self._logged_in = False
                self._bootstrap_pristine = False
                self._clear_credentials()
                return
        result = await transport.send_command(CMD_LOGOUT)
        if generation != self._session_generation or transport is not self.transport:
            raise SessionError("logout invalidated by close")
        if result.code != 0:
            raise SessionError(f"MT5 Web Terminal rejected logout (response code {result.code})")
        self._stop_heartbeat()
        self._logged_in = False
        self._bootstrap_pristine = False
        self._clear_credentials()
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
            (PROP_U64, 0),  # Additional field observed in Web Terminal build 6320.
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
