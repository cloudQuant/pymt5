import asyncio
import logging
import os
import struct
import zlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, TypeVar

from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_CHANGE_PASSWORD,
    CMD_GET_ACCOUNT,
    CMD_GET_CORPORATE_LINKS,
    CMD_GET_FULL_SYMBOLS,
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_RATES,
    CMD_GET_SPREADS,
    CMD_GET_SYMBOL_GROUPS,
    CMD_GET_SYMBOLS,
    CMD_GET_SYMBOLS_GZIP,
    CMD_GET_TRADE_HISTORY,
    CMD_INIT,
    CMD_LOGIN,
    CMD_LOGIN_STATUS_PUSH,
    CMD_LOGOUT,
    CMD_NOTIFY,
    CMD_OPEN_DEMO,
    CMD_OPEN_REAL,
    CMD_OTP_SETUP,
    CMD_PING,
    CMD_SEND_VERIFY_CODES,
    CMD_SUBSCRIBE_BOOK,
    CMD_SUBSCRIBE_TICKS,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_REQUEST,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
    CMD_TRADER_PARAMS,
    CMD_VERIFY_CODE,
    COPY_TICKS_ALL,
    COPY_TICKS_INFO,
    COPY_TICKS_TRADE,
    DEFAULT_WS_URI,
    ORDER_FILLING_FOK,
    ORDER_TIME_GTC,
    ORDER_TIME_SPECIFIED,
    ORDER_TIME_SPECIFIED_DAY,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    ORDER_TYPE_SELL_STOP_LIMIT,
    PERIOD_MAP,
    POSITION_TYPE_SELL,
    PROP_BYTES,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I16,
    PROP_I32,
    PROP_I64,
    PROP_TIME,
    PROP_U8,
    PROP_U16,
    PROP_U32,
    PROP_U64,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_DESCRIPTIONS,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_INVALID,
    TRADE_RETCODE_INVALID_EXPIRATION,
    TRADE_RETCODE_INVALID_FILL,
    TRADE_RETCODE_INVALID_ORDER,
    TRADE_RETCODE_INVALID_PRICE,
    TRADE_RETCODE_INVALID_STOPS,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_TRADE_DISABLED,
)
from pymt5.helpers import build_client_id, bytes_to_hex
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    ACCOUNT_WEB_COMMISSION_FIELD_NAMES,
    ACCOUNT_WEB_COMMISSION_SCHEMA,
    ACCOUNT_WEB_COMMISSION_TIER_FIELD_NAMES,
    ACCOUNT_WEB_COMMISSION_TIER_SCHEMA,
    ACCOUNT_WEB_LEVERAGE_RULE_FIELD_NAMES,
    ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA,
    ACCOUNT_WEB_LEVERAGE_TIER_FIELD_NAMES,
    ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA,
    ACCOUNT_WEB_MAIN_FIELD_NAMES,
    ACCOUNT_WEB_MAIN_SCHEMA,
    ACCOUNT_WEB_TRADE_SETTINGS_FIELD_NAMES,
    ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA,
    BOOK_HEADER_FIELD_NAMES,
    BOOK_HEADER_SCHEMA,
    BOOK_LEVEL_FIELD_NAMES,
    BOOK_LEVEL_SCHEMA,
    CORPORATE_LINK_FIELD_NAMES,
    CORPORATE_LINK_SCHEMA,
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    FULL_SYMBOL_FIELD_NAMES,
    FULL_SYMBOL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
    RATE_BAR_FIELD_NAMES,
    RATE_BAR_FIELD_NAMES_EXT,
    RATE_BAR_SCHEMA,
    RATE_BAR_SCHEMA_EXT,
    SPREAD_FIELD_NAMES,
    SPREAD_SCHEMA,
    SYMBOL_BASIC_FIELD_NAMES,
    SYMBOL_BASIC_SCHEMA,
    SYMBOL_DETAILS_FIELD_NAMES,
    SYMBOL_DETAILS_SCHEMA,
    SYMBOL_GROUP_SCHEMA,
    TICK_FIELD_NAMES,
    TICK_SCHEMA,
    TRADE_RESULT_PUSH_FIELD_NAMES,
    TRADE_RESULT_PUSH_SCHEMA,
    TRADE_RESULT_RESPONSE_FIELD_NAMES,
    TRADE_RESULT_RESPONSE_SCHEMA,
    TRADE_TRANSACTION_FIELD_NAMES,
    TRADE_TRANSACTION_SCHEMA,
    TRADE_UPDATE_BALANCE_FIELD_NAMES,
    TRADE_UPDATE_BALANCE_SCHEMA,
)
from pymt5.transport import CommandResult, MT5WebSocketTransport

logger = logging.getLogger("pymt5.client")

Record = dict[str, Any]
RecordList = list[Record]
_T = TypeVar("_T")
PERIOD_MINUTES_MAP = {code: minutes for minutes, code in PERIOD_MAP.items()}
BUY_ORDER_TYPES = frozenset({
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_BUY_STOP_LIMIT,
})
SELL_ORDER_TYPES = frozenset({
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    ORDER_TYPE_SELL_STOP_LIMIT,
})
FOREX_CALC_MODES = frozenset({0, 5})
FUTURES_CALC_MODES = frozenset({1, 33, 34})
CFD_CALC_MODES = frozenset({2, 3, 4, 32, 38})
OPTION_CALC_MODES = frozenset({35, 36})
BOND_CALC_MODES = frozenset({37, 39})
COLLATERAL_CALC_MODE = 64
MT5_TERMINAL_VERSION = 500
OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES = {
    5687: "15 Mar 2026",
}


@dataclass
class TradeResult:
    """Result of a trade request."""
    retcode: int
    description: str
    success: bool
    deal: int = 0
    order: int = 0
    volume: int = 0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""
    request_id: int = 0

    def __repr__(self) -> str:
        parts = [f"retcode={self.retcode}", f"success={self.success}", f"desc='{self.description}'"]
        if self.deal:
            parts.append(f"deal={self.deal}")
        if self.order:
            parts.append(f"order={self.order}")
        if self.price:
            parts.append(f"price={self.price}")
        return f"TradeResult({', '.join(parts)})"


@dataclass
class AccountInfo:
    """Account summary computed from positions and deals."""
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    credit: float = 0.0
    leverage: int = 0
    currency: str = ""
    server: str = ""
    positions_count: int = 0
    orders_count: int = 0


@dataclass
class SymbolInfo:
    """Cached symbol info for quick lookup."""
    name: str
    symbol_id: int
    digits: int
    description: str = ""
    path: str = ""
    trade_calc_mode: int = 0
    basis: str = ""
    sector: int = 0


@dataclass
class VerificationStatus:
    """Two-channel verification state used by account-opening flows."""
    email: bool = False
    phone: bool = False

    def __bool__(self) -> bool:
        return self.email or self.phone


@dataclass
class OpenAccountResult:
    """Result returned by demo/real account opening commands."""
    code: int
    login: int
    password: str
    investor_password: str

    @property
    def success(self) -> bool:
        return self.code == 0


@dataclass
class AccountOpeningRequest:
    """Shared fields used by the Web Terminal's account opening payloads."""
    first_name: str
    second_name: str
    email: str = ""
    phone: str = ""
    group: str = ""
    deposit: float = 100000.0
    leverage: int = 100
    agreements: int = 0
    country: str = ""
    city: str = ""
    state: str = ""
    zipcode: str = ""
    address: str = ""
    domain: str = ""
    phone_password: str = ""
    email_confirm_code: int = 0
    phone_confirm_code: int = 0
    language: str = ""
    utm_campaign: str = ""
    utm_source: str = ""


@dataclass
class DemoAccountRequest(AccountOpeningRequest):
    """Demo account opening request for cmd=30."""


@dataclass
class AccountDocument:
    """Document upload payload for real account opening.

    ``data_type`` and ``document_type`` are frontend/broker-defined enums.
    """
    data_type: int
    document_type: int
    front_name: str
    front_buffer: bytes
    back_name: str = ""
    back_buffer: bytes = b""


@dataclass
class RealAccountRequest(AccountOpeningRequest):
    """Real account opening request for cmd=39.

    ``birth_date_ms`` uses Unix milliseconds, matching the frontend's
    ``propType=9`` date encoding.
    """
    middle_name: str = ""
    birth_date_ms: int = 0
    gender: int = 0
    citizenship: str = ""
    tax_id: str = ""
    employment: int = 0
    industry: int = 0
    education: int = 0
    wealth: int = 0
    annual_income: int = 0
    net_worth: int = 0
    annual_deposit: int = 0
    experience_fx: int = 0
    experience_cfd: int = 0
    experience_futures: int = 0
    experience_stocks: int = 0
    documents: list[AccountDocument] = field(default_factory=list)


LOGIN_RESPONSE_SCHEMA = [
    {"propType": PROP_BYTES, "propLength": 160},
    {"propType": PROP_U64},
]

TRADER_PARAMS_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 32},
    {"propType": PROP_FIXED_STRING, "propLength": 32},
]

# Trade response: retcode(u32) + deal(u64) + order(u64) + volume(u64) + price(f64)
#                 + bid(f64) + ask(f64) + comment(fs64) + request_id(u32)
TRADE_RESPONSE_SCHEMA = [
    {"propType": PROP_U32},                               # retcode
    {"propType": PROP_U64},                               # deal ticket
    {"propType": PROP_U64},                               # order ticket
    {"propType": PROP_U64},                               # volume
    {"propType": PROP_F64},                               # price
    {"propType": PROP_F64},                               # bid
    {"propType": PROP_F64},                               # ask
    {"propType": PROP_FIXED_STRING, "propLength": 64},    # comment
    {"propType": PROP_U32},                               # request_id
]

OPEN_ACCOUNT_RESPONSE_SCHEMA = [
    {"propType": PROP_U32},
    {"propType": PROP_I64},
    {"propType": PROP_FIXED_STRING, "propLength": 32},
    {"propType": PROP_FIXED_STRING, "propLength": 32},
]

VERIFICATION_STATUS_SCHEMA = [
    {"propType": PROP_U8},
    {"propType": PROP_U8},
]

REAL_ACCOUNT_RESERVED_PAYLOAD = struct.pack("<128i", *([0] * 128))


class MT5WebClient:
    def __init__(
        self,
        uri: str = DEFAULT_WS_URI,
        timeout: float = 30.0,
        heartbeat_interval: float = 30.0,
        tick_history_limit: int = 10000,
        auto_reconnect: bool = False,
        max_reconnect_attempts: int = 5,
        reconnect_delay: float = 3.0,
    ):
        self.uri = uri
        self.timeout = timeout
        self.transport = MT5WebSocketTransport(uri=uri, timeout=timeout)
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: asyncio.Task | None = None
        self._symbols: dict[str, SymbolInfo] = {}
        self._symbols_by_id: dict[int, SymbolInfo] = {}
        self._full_symbols: dict[str, Record] = {}
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
        self._reconnect_task: asyncio.Task | None = None
        self._closing = False
        # Stored credentials for reconnect
        self._login_kwargs: dict | None = None
        self._subscribed_ids: list[int] = []
        self._subscribed_book_ids: list[int] = []
        # User disconnect callback
        self._on_disconnect: Callable[[], None] | None = None
        self.transport.on(CMD_TICK_PUSH, self._cache_tick_push)
        self.transport.on(CMD_BOOK_PUSH, self._cache_book_push)
        # Wire up transport disconnect handler
        self.transport._on_disconnect = self._handle_disconnect

    @property
    def is_connected(self) -> bool:
        return self.transport.is_ready and self._logged_in

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register a callback for disconnect events."""
        self._on_disconnect = callback

    async def connect(self) -> "MT5WebClient":
        await self.transport.connect()
        self._bootstrap_pristine = True
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
        logger.info("connection closed")

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
                except Exception as exc:
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
        """Try to reconnect and re-login with stored credentials."""
        for attempt in range(1, self._max_reconnect_attempts + 1):
            logger.info("reconnect attempt %d/%d", attempt, self._max_reconnect_attempts)
            await asyncio.sleep(self._reconnect_delay * attempt)
            try:
                # Close old transport to release resources
                try:
                    await self.transport.close()
                except Exception:
                    pass
                # Reset transport for fresh connection
                self.transport = MT5WebSocketTransport(uri=self.uri, timeout=self.timeout)
                self.transport._on_disconnect = self._handle_disconnect
                await self.transport.connect()
                # Re-login with stored credentials
                kwargs = dict(self._login_kwargs)
                kwargs["auto_heartbeat"] = True
                await self.login(**kwargs)
                # Re-subscribe to ticks if we had subscriptions
                if self._subscribed_ids:
                    await self.subscribe_ticks(self._subscribed_ids)
                # Re-subscribe to order book if we had subscriptions
                if self._subscribed_book_ids:
                    await self.subscribe_book(self._subscribed_book_ids)
                logger.info("reconnected successfully on attempt %d", attempt)
                return
            except Exception as exc:
                logger.warning("reconnect attempt %d failed: %s", attempt, exc)
        logger.error("all %d reconnect attempts exhausted", self._max_reconnect_attempts)

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
            raise RuntimeError("transport not ready")
        if self._logged_in or not self._bootstrap_pristine:
            raise RuntimeError(
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
            "login": login, "password": password, "url": url,
            "session": session, "otp": otp, "version": version,
            "cid": cid, "lead_cookie_id": lead_cookie_id,
            "lead_affiliate_site": lead_affiliate_site,
            "utm_campaign": utm_campaign, "utm_source": utm_source,
        }
        logger.info("logged in: login=%d session=%d", login, int(session_id))
        if auto_heartbeat:
            self._start_heartbeat()
        return bytes_to_hex(token_bytes), int(session_id)

    async def ping(self) -> None:
        await self.transport.send_command(CMD_PING)

    async def trader_params(self) -> tuple[str, str]:
        result = await self.transport.send_command(CMD_TRADER_PARAMS)
        first, second = SeriesCodec.parse(result.body, TRADER_PARAMS_SCHEMA)
        return str(first), str(second)

    async def logout(self) -> None:
        self._stop_heartbeat()
        await self.transport.send_command(CMD_LOGOUT)
        self._logged_in = False
        self._bootstrap_pristine = False
        logger.info("logged out")

    async def change_password(self, new_password: str, old_password: str, is_investor: bool = False) -> int:
        payload = SeriesCodec.serialize([
            (4, int(is_investor)),
            (PROP_FIXED_STRING, (new_password or "")[:32], 64),
            (PROP_FIXED_STRING, (old_password or "")[:32], 64),
        ])
        result = await self.transport.send_command(CMD_CHANGE_PASSWORD, payload)
        return int.from_bytes(result.body[:4], "little", signed=True)

    async def verify_code(self, code: str) -> CommandResult:
        """Send a verification code (cmd=27), e.g. for two-factor authentication.

        Args:
            code: The verification/OTP code string.

        Returns:
            Raw CommandResult with server response.
        """
        payload = SeriesCodec.serialize([
            (PROP_FIXED_STRING, code[:32], 64),
        ])
        return await self.transport.send_command(CMD_VERIFY_CODE, payload)

    async def request_opening_verification(
        self,
        request: AccountOpeningRequest,
        *,
        build: int = 0,
        cid: bytes | None = None,
        initialize: bool = True,
    ) -> VerificationStatus:
        """Request email/SMS verification requirements for account opening (cmd=27).

        The current frontend passes the Web Terminal build number in the first
        field. When that value is unavailable, ``build=0`` is a safe fallback.
        """
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_opening_verification_payload(
            request=request,
            build=build,
            cid=client_id,
        )
        result = await self.transport.send_command(CMD_VERIFY_CODE, payload)
        return _parse_verification_status(result.body)

    async def submit_opening_verification(
        self,
        request: AccountOpeningRequest,
        *,
        cid: bytes | None = None,
        initialize: bool = False,
    ) -> VerificationStatus:
        """Submit email/SMS verification codes for account opening (cmd=40)."""
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_opening_base_payload(request)
        result = await self.transport.send_command(CMD_SEND_VERIFY_CODES, payload)
        return _parse_verification_status(result.body)

    async def get_account(self) -> Record:
        """Get full account information (cmd=3): balance, equity, margin, leverage, etc.

        Returns a dict with all account fields. This is the proper way to get
        balance/equity/margin information from the Web Terminal.

        The response has a complex multi-section format (header + trade settings).
        """
        result = await self.transport.send_command(CMD_GET_ACCOUNT)
        return _parse_account_response(result.body)

    async def account_info(self) -> Record:
        """Official-style alias for get_account()."""
        return await self.get_account()

    async def terminal_info(self) -> Record:
        """Best-effort terminal/server info derived from the Web account config."""
        account = await self.get_account()
        trade_allowed = bool(account.get("trade_allowed", False))
        return {
            "build": int(account.get("server_build", 0) or 0),
            "company": str(account.get("company", "") or ""),
            "name": str(account.get("server_name", "") or ""),
            "server": str(account.get("server_name", "") or ""),
            "connected": bool(self.transport.is_ready),
            "trade_allowed": trade_allowed,
            "tradeapi_disabled": bool(account.get("is_read_only", False) or not trade_allowed),
            "timezone_shift": int(account.get("timezone_shift", 0) or 0),
            "server_offset_time": int(account.get("server_offset_time", 0) or 0),
            "path": "",
            "data_path": "",
            "commondata_path": "",
        }

    async def version(self) -> tuple[int, int, str] | None:
        """Best-effort official-style terminal version tuple.

        The Web Terminal does not expose a dedicated ``version()`` RPC in the
        current command surface. This compatibility helper combines the
        ``cmd=3`` build field with locally observed public Web Terminal
        release-date metadata. Unknown builds return an empty release-date
        string.
        """
        try:
            account = await self.get_account()
            build = int(account.get("server_build", 0) or 0)
            if build <= 0:
                return self._fail_last_error(-7, "terminal build unavailable for version()")
            self._clear_last_error()
            return (
                MT5_TERMINAL_VERSION,
                build,
                OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES.get(build, ""),
            )
        except Exception as exc:
            return self._fail_last_error(-99, f"version() failed: {exc}")

    async def order_calc_profit(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        """Best-effort official-style profit calculation using local symbol formulas."""
        try:
            is_buy = _order_side(action)
            if is_buy is None:
                return self._fail_last_error(-1, f"unsupported action for order_calc_profit(): {action}")
            info = await self.symbol_info(symbol)
            if info is None:
                return self._fail_last_error(-2, f"symbol not found: {symbol}")
            account = await self.get_account()
            account_currency = str(account.get("currency", "") or "")
            if not account_currency:
                return self._fail_last_error(-3, "account currency unavailable")
            raw_profit = self._calc_profit_raw(info, is_buy, volume, price_open, price_close)
            if raw_profit is None:
                return None
            if raw_profit == 0.0:
                self._clear_last_error()
                return 0.0
            profit_currency = str(info.get("currency_profit", "") or "")
            if not profit_currency:
                return self._fail_last_error(-4, f"symbol {symbol} is missing currency_profit")
            if _currencies_equal(profit_currency, account_currency):
                self._clear_last_error()
                return float(raw_profit)
            rates = await self._resolve_conversion_rates(
                source=profit_currency,
                target=account_currency,
                current_symbol=str(info.get("trade_symbol", symbol) or symbol),
                fallback_rate=price_close,
            )
            if rates is None:
                return self._fail_last_error(
                    -5,
                    f"unable to convert profit currency {profit_currency} to {account_currency}",
                )
            rate_buy, rate_sell = rates
            mode = int(info.get("trade_calc_mode", 0) or 0)
            if mode in FOREX_CALC_MODES:
                profit = raw_profit * (rate_buy if is_buy else rate_sell)
            else:
                profit = raw_profit * (rate_sell if raw_profit > 0 else rate_buy)
            self._clear_last_error()
            return float(profit)
        except Exception as exc:
            return self._fail_last_error(-99, f"order_calc_profit() failed: {exc}")

    async def order_calc_margin(
        self,
        action: int,
        symbol: str,
        volume: float,
        price: float,
    ) -> float | None:
        """Best-effort official-style margin calculation using local symbol formulas."""
        try:
            is_buy = _order_side(action)
            if is_buy is None:
                return self._fail_last_error(-1, f"unsupported action for order_calc_margin(): {action}")
            info = await self.symbol_info(symbol)
            if info is None:
                return self._fail_last_error(-2, f"symbol not found: {symbol}")
            account = await self.get_account()
            account_currency = str(account.get("currency", "") or "")
            if not account_currency:
                return self._fail_last_error(-3, "account currency unavailable")
            leverage = int(account.get("leverage", 0) or 0)
            raw_margin = self._calc_margin_raw(info, is_buy, volume, price, leverage)
            if raw_margin is None:
                return None
            if raw_margin == 0.0:
                self._clear_last_error()
                return 0.0
            margin_currency = str(info.get("currency_margin", "") or "") or account_currency
            if _currencies_equal(margin_currency, account_currency):
                self._clear_last_error()
                return float(raw_margin)
            rates = await self._resolve_conversion_rates(
                source=margin_currency,
                target=account_currency,
                current_symbol=str(info.get("trade_symbol", symbol) or symbol),
                fallback_rate=price,
            )
            if rates is None:
                return self._fail_last_error(
                    -6,
                    f"unable to convert margin currency {margin_currency} to {account_currency}",
                )
            rate_buy, rate_sell = rates
            margin = raw_margin * (rate_buy if is_buy else rate_sell)
            self._clear_last_error()
            return float(margin)
        except Exception as exc:
            return self._fail_last_error(-99, f"order_calc_margin() failed: {exc}")

    async def get_symbol_groups(self) -> list[str]:
        """Get symbol type/group names (cmd=9), e.g. 'Forex', 'Crypto', 'Indices'.

        Returns list of group name strings.
        """
        result = await self.transport.send_command(CMD_GET_SYMBOL_GROUPS)
        if not result.body or len(result.body) < 4:
            return []
        count = struct.unpack_from("<I", result.body, 0)[0]
        group_size = get_series_size(SYMBOL_GROUP_SCHEMA)
        groups = []
        offset = 4
        for _ in range(count):
            if offset + group_size > len(result.body):
                break
            vals = SeriesCodec.parse_at(result.body, SYMBOL_GROUP_SCHEMA, offset)
            groups.append(vals[0])
            offset += group_size
        logger.info("loaded %d symbol groups", len(groups))
        return groups

    async def get_spreads(self, symbol_ids: list[int] | None = None) -> RecordList:
        """Request spread data (cmd=20).

        Args:
            symbol_ids: Optional list of symbol IDs to query. If None, sends empty payload.

        Returns:
            List of spread dicts with keys: spread_id, flags, trade_symbol, param1, param2, spread_value.
        """
        if symbol_ids:
            payload = struct.pack(f"<{len(symbol_ids) + 1}I", len(symbol_ids), *symbol_ids)
        else:
            payload = b""
        result = await self.transport.send_command(CMD_GET_SPREADS, payload)
        return _parse_counted_records(result.body, SPREAD_SCHEMA, SPREAD_FIELD_NAMES)

    async def open_demo(
        self,
        *,
        password: str = "",
        otp: str = "",
        cid: bytes | None = None,
        version: int = 0,
    ) -> CommandResult:
        """Request demo account creation (cmd=30).

        Returns:
            Raw CommandResult with server response (account details).
        """
        payload = self._build_init_payload(
            version=version,
            password=password,
            otp=otp,
            cid=cid,
        )
        return await self.transport.send_command(CMD_OPEN_DEMO, payload)

    async def open_demo_account(
        self,
        request: DemoAccountRequest,
        *,
        cid: bytes | None = None,
        initialize: bool = True,
    ) -> OpenAccountResult:
        """Open a demo account using the current frontend registration payload (cmd=30)."""
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_opening_base_payload(request)
        result = await self.transport.send_command(CMD_OPEN_DEMO, payload)
        return _parse_open_account_result(result.body)

    async def open_real_account(
        self,
        request: RealAccountRequest,
        *,
        cid: bytes | None = None,
        initialize: bool = True,
    ) -> OpenAccountResult:
        """Open a real account using the current frontend onboarding payload (cmd=39)."""
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_real_account_payload(request)
        result = await self.transport.send_command(CMD_OPEN_REAL, payload)
        return _parse_open_account_result(result.body)

    async def enable_otp(
        self,
        login: int,
        password: str,
        *,
        otp_secret: str,
        otp_secret_check: str,
        cid: bytes | None = None,
    ) -> CommandResult:
        """Enable/configure TOTP for an account via cmd=43."""
        payload = self._build_otp_setup_payload(
            login=login,
            password=password,
            otp_secret=otp_secret,
            otp_secret_check=otp_secret_check,
            cid=cid,
        )
        return await self.transport.send_command(CMD_OTP_SETUP, payload)

    async def disable_otp(
        self,
        login: int,
        password: str,
        *,
        otp: str,
        cid: bytes | None = None,
    ) -> bool:
        """Disable TOTP for an account via cmd=43.

        The current frontend treats any non-error response as success.
        """
        payload = self._build_otp_setup_payload(
            login=login,
            password=password,
            otp=otp,
            cid=cid,
        )
        await self.transport.send_command(CMD_OTP_SETUP, payload)
        return True

    async def send_notification(self, message: str) -> CommandResult:
        """Send a notification message to the server (cmd=42).

        Args:
            message: Notification text.

        Returns:
            Raw CommandResult with server acknowledgement.
        """
        payload = SeriesCodec.serialize([
            (PROP_FIXED_STRING, message[:128], 256),
        ])
        return await self.transport.send_command(CMD_NOTIFY, payload)

    async def get_corporate_links(self) -> RecordList:
        """Get broker corporate links (cmd=44): support, education, social, etc.

        Returns list of dicts with keys: link_type, url, label, flags, icon_data.
        """
        result = await self.transport.send_command(CMD_GET_CORPORATE_LINKS)
        return _parse_counted_records(result.body, CORPORATE_LINK_SCHEMA, CORPORATE_LINK_FIELD_NAMES)

    async def subscribe_book(self, symbol_ids: list[int]) -> None:
        """Subscribe to order book / depth-of-market for given symbols (cmd=22).

        After subscribing, the server pushes DOM updates via cmd=23.
        Register a handler with on_book_update() to receive them.
        """
        existing = set(self._subscribed_book_ids)
        merged = sorted(existing | set(symbol_ids))
        payload = struct.pack(f"<{len(merged) + 1}I", len(merged), *merged)
        await self.transport.send_command(CMD_SUBSCRIBE_BOOK, payload)
        self._subscribed_book_ids = merged
        logger.info(
            "subscribed to order book for %d symbols (added %d new)",
            len(merged),
            len(set(symbol_ids) - existing),
        )

    async def subscribe_book_by_name(self, symbol_names: list[str]) -> list[int]:
        """Subscribe to order book by symbol names (requires load_symbols first).

        Returns list of resolved symbol IDs.
        Raises ValueError if any symbol name is not found in cache.
        """
        ids = []
        missing = []
        for name in symbol_names:
            info = self._symbols.get(name)
            if info is None:
                missing.append(name)
            else:
                ids.append(info.symbol_id)
        if missing:
            raise ValueError(f"symbols not found in cache (call load_symbols first): {missing}")
        await self.subscribe_book(ids)
        return ids

    async def unsubscribe_book(self, symbol_ids: list[int]) -> None:
        """Remove specific symbol IDs from the current order-book subscription set."""
        existing = set(self._subscribed_book_ids)
        remaining = sorted(existing - set(symbol_ids))
        if remaining:
            payload = struct.pack(f"<{len(remaining) + 1}I", len(remaining), *remaining)
        else:
            payload = struct.pack("<I", 0)
        await self.transport.send_command(CMD_SUBSCRIBE_BOOK, payload)
        removed = existing - set(remaining)
        self._subscribed_book_ids = remaining
        for symbol_id in removed:
            entry = self._book_cache_by_id.pop(symbol_id, None)
            if entry and entry.get("symbol"):
                self._book_cache_by_name.pop(entry["symbol"], None)

    async def market_book_add(self, symbol: str) -> bool:
        """Official-style alias for subscribing to a symbol's DOM stream."""
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return False
        await self.subscribe_book([symbol_id])
        return True

    def market_book_get(self, symbol: str) -> Record | None:
        """Return the latest cached order-book snapshot for a symbol."""
        book = self._book_cache_by_name.get(symbol)
        return dict(book) if book is not None else None

    async def market_book_release(self, symbol: str) -> bool:
        """Official-style alias for removing a symbol from DOM subscriptions."""
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return False
        await self.unsubscribe_book([symbol_id])
        self._book_cache_by_name.pop(symbol, None)
        self._book_cache_by_id.pop(symbol_id, None)
        return True

    # ---- Symbol Cache ----

    async def load_symbols(self, use_gzip: bool = True) -> dict[str, SymbolInfo]:
        """Load symbols and build internal cache for name→id lookup."""
        raw = await self.get_symbols(use_gzip=use_gzip)
        self._symbols.clear()
        self._symbols_by_id.clear()
        self._full_symbols.clear()
        for s in raw:
            info = SymbolInfo(
                name=s["trade_symbol"],
                symbol_id=s["symbol_id"],
                digits=s["digits"],
                description=s.get("symbol_description", ""),
                path=s.get("symbol_path", ""),
                trade_calc_mode=s.get("trade_calc_mode", 0),
                basis=s.get("basis", ""),
                sector=s.get("sector", 0),
            )
            self._symbols[info.name] = info
            self._symbols_by_id[info.symbol_id] = info
            history = self._tick_history_by_id.get(info.symbol_id)
            if history is not None:
                self._tick_history_by_name[info.name] = history
        logger.info("symbol cache loaded: %d symbols", len(self._symbols))
        return dict(self._symbols)

    def get_symbol_info(self, name: str) -> SymbolInfo | None:
        """Look up cached symbol info by name."""
        return self._symbols.get(name)

    def get_symbol_id(self, name: str) -> int | None:
        """Look up cached symbol id by name."""
        info = self._symbols.get(name)
        return info.symbol_id if info else None

    @property
    def symbol_names(self) -> list[str]:
        """Return list of all cached symbol names."""
        return list(self._symbols.keys())

    async def _resolve_symbol_id(self, symbol: str) -> int | None:
        info = self._symbols.get(symbol)
        if info is None:
            await self.load_symbols()
            info = self._symbols.get(symbol)
        return info.symbol_id if info is not None else None

    def _clear_last_error(self) -> None:
        self._last_error = (0, "")

    def _fail_last_error(self, code: int, message: str) -> _T | None:
        self._last_error = (int(code), message)
        logger.debug("compat helper error %d: %s", code, message)
        return None

    def _calc_profit_raw(
        self,
        info: Record,
        is_buy: bool,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        mode = int(info.get("trade_calc_mode", 0) or 0)
        if mode in BOND_CALC_MODES:
            contract_size = float(info.get("contract_size", 0.0) or 0.0)
            face_value = float(info.get("face_value", 0.0) or 0.0)
            accrued_interest = float(info.get("accrued_interest", 0.0) or 0.0)
            if contract_size <= 0.0 or face_value <= 0.0:
                return self._fail_last_error(
                    -10,
                    f"trade_calc_mode={mode} requires contract_size and face_value for bond profit calculation",
                )
            direction = 1.0 if is_buy else -1.0
            open_value = (price_open / 100.0) * face_value
            close_value = (price_close / 100.0) * face_value + accrued_interest
            return direction * (close_value - open_value) * contract_size * float(volume)
        if mode == COLLATERAL_CALC_MODE:
            return 0.0
        direction = 1.0 if is_buy else -1.0
        contract_size = float(info.get("contract_size", 0.0) or 0.0)
        tick_size = float(info.get("tick_size", 0.0) or 0.0)
        tick_value = float(info.get("tick_value", 0.0) or 0.0)
        if mode in FOREX_CALC_MODES or mode in CFD_CALC_MODES:
            if contract_size <= 0.0:
                return self._fail_last_error(-11, "contract_size must be > 0 for profit calculation")
            return direction * (price_close - price_open) * contract_size * float(volume)
        if mode in {1, 33, 35, 36}:
            if tick_value <= 0.0:
                return self._fail_last_error(-12, "tick_value must be > 0 for futures/options profit calculation")
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return direction * (price_close - price_open) * float(volume) * point_value
        if mode == 34:
            if tick_value <= 0.0:
                return self._fail_last_error(-12, "tick_value must be > 0 for FORTS profit calculation")
            open_value = price_open * tick_value / tick_size if tick_size > 0.0 else price_open * tick_value
            close_value = price_close * tick_value / tick_size if tick_size > 0.0 else price_close * tick_value
            return direction * (close_value - open_value) * float(volume)
        if contract_size <= 0.0:
            return self._fail_last_error(-11, "contract_size must be > 0 for profit calculation")
        return direction * (price_close - price_open) * contract_size * float(volume)

    def _calc_margin_raw(
        self,
        info: Record,
        is_buy: bool,
        volume: float,
        price: float,
        leverage: int,
    ) -> float | None:
        del is_buy
        mode = int(info.get("trade_calc_mode", 0) or 0)
        contract_size = float(info.get("contract_size", 0.0) or 0.0)
        tick_size = float(info.get("tick_size", 0.0) or 0.0)
        tick_value = float(info.get("tick_value", 0.0) or 0.0)
        margin_initial = float(info.get("margin_initial", 0.0) or 0.0)
        lots = float(volume)
        units = lots * contract_size
        if mode in BOND_CALC_MODES:
            face_value = float(info.get("face_value", 0.0) or 0.0)
            if contract_size <= 0.0 or face_value <= 0.0:
                return self._fail_last_error(
                    -13,
                    f"trade_calc_mode={mode} requires contract_size and face_value for bond margin calculation",
                )
            return lots * contract_size * face_value * float(price) / 100.0
        if mode == COLLATERAL_CALC_MODE:
            return 0.0
        if mode == 5:
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            return units
        if mode == 0 or mode == 4:
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            if leverage <= 0:
                return self._fail_last_error(-15, "account leverage must be > 0 for leveraged margin calculation")
            base_margin = units / float(leverage)
            if mode == 4:
                base_margin *= float(price)
            return base_margin
        if mode in FUTURES_CALC_MODES:
            if margin_initial > 0.0:
                return lots * margin_initial
            if tick_value <= 0.0:
                return self._fail_last_error(
                    -16,
                    "margin_initial or tick_value is required for futures margin calculation",
                )
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return lots * float(price) * point_value
        if mode == 3:
            if contract_size <= 0.0 or tick_value <= 0.0:
                return self._fail_last_error(
                    -17,
                    "contract_size and tick_value are required for CFD index margin calculation",
                )
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return units * float(price) * point_value
        if mode in {2, 32, 35, 36, 38}:
            if margin_initial > 0.0 and mode in {32, 35, 36, 38}:
                return lots * margin_initial
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            return units * float(price)
        return self._fail_last_error(-18, f"trade_calc_mode={mode} is not supported yet")

    async def _resolve_conversion_rates(
        self,
        *,
        source: str,
        target: str,
        current_symbol: str,
        fallback_rate: float,
    ) -> tuple[float, float] | None:
        if not source or not target:
            return None
        if _currencies_equal(source, target):
            return 1.0, 1.0
        buy = await self._resolve_side_rate(
            source,
            target,
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell = await self._resolve_side_rate(
            source,
            target,
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        if buy > 0.0 and sell > 0.0:
            return buy, sell
        buy_usd = await self._resolve_side_rate(
            source,
            "USD",
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell_usd = await self._resolve_side_rate(
            source,
            "USD",
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        buy_target = await self._resolve_side_rate(
            "USD",
            target,
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell_target = await self._resolve_side_rate(
            "USD",
            target,
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        if buy_usd > 0.0 and sell_usd > 0.0 and buy_target > 0.0 and sell_target > 0.0:
            return buy_usd * buy_target, sell_usd * sell_target
        return None

    async def _resolve_side_rate(
        self,
        source: str,
        target: str,
        *,
        prefer_ask_when_direct: bool,
        current_symbol: str,
        fallback_rate: float,
    ) -> float:
        direct = await self._find_conversion_symbol_name(source, target)
        if direct is not None:
            prices = self._get_conversion_prices(
                direct,
                current_symbol=current_symbol,
                fallback_rate=fallback_rate,
            )
            if prices is not None:
                bid, ask = prices
                return ask if prefer_ask_when_direct else bid
        inverse = await self._find_conversion_symbol_name(target, source)
        if inverse is not None:
            prices = self._get_conversion_prices(
                inverse,
                current_symbol=current_symbol,
                fallback_rate=fallback_rate,
            )
            if prices is not None:
                bid, ask = prices
                denominator = bid if prefer_ask_when_direct else ask
                if denominator > 0.0:
                    return 1.0 / denominator
        return 0.0

    async def _find_conversion_symbol_name(self, base: str, quote: str) -> str | None:
        if not base or not quote:
            return None
        if not self._symbols:
            try:
                await self.load_symbols()
            except Exception:
                return None
        direct = f"{base}{quote}"
        if direct in self._symbols:
            return direct
        matches = sorted(
            name for name in self._symbols
            if name.startswith(base) and name.endswith(quote)
        )
        if matches:
            return matches[0]
        return None

    def _get_conversion_prices(
        self,
        symbol_name: str,
        *,
        current_symbol: str,
        fallback_rate: float,
    ) -> tuple[float, float] | None:
        tick = self._tick_cache_by_name.get(symbol_name)
        bid = 0.0
        ask = 0.0
        if tick is not None:
            bid = float(tick.get("bid", 0.0) or 0.0)
            ask = float(tick.get("ask", 0.0) or 0.0)
        if symbol_name == current_symbol and fallback_rate > 0.0:
            if bid <= 0.0:
                bid = float(fallback_rate)
            if ask <= 0.0:
                ask = float(fallback_rate)
        if bid <= 0.0:
            bid = ask
        if ask <= 0.0:
            ask = bid
        if bid > 0.0 and ask > 0.0:
            return bid, ask
        return None

    async def _get_tick_history(self, symbol: str) -> list[Record]:
        history = self._tick_history_by_name.get(symbol)
        if history is not None:
            return list(history)
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return []
        history = self._tick_history_by_id.get(symbol_id)
        if history is not None:
            self._tick_history_by_name[symbol] = history
            return list(history)
        return []

    def _cache_tick_push(self, result: CommandResult) -> None:
        try:
            for tick in _parse_tick_batch(result.body, self._symbols_by_id):
                symbol_id = int(tick["symbol_id"])
                self._tick_cache_by_id[symbol_id] = tick
                history = self._tick_history_by_id.get(symbol_id)
                if history is None:
                    history = deque(maxlen=self._tick_history_limit or None)
                    self._tick_history_by_id[symbol_id] = history
                history.append(dict(tick))
                symbol_name = tick.get("symbol")
                if symbol_name:
                    self._tick_cache_by_name[str(symbol_name)] = tick
                    self._tick_history_by_name[str(symbol_name)] = history
        except Exception as exc:
            logger.debug("tick cache update failed: %s", exc)

    def _cache_book_push(self, result: CommandResult) -> None:
        try:
            for entry in _parse_book_entries(result.body, self._symbols_by_id):
                symbol_id = int(entry["symbol_id"])
                self._book_cache_by_id[symbol_id] = entry
                symbol_name = entry.get("symbol")
                if symbol_name:
                    self._book_cache_by_name[str(symbol_name)] = entry
        except Exception as exc:
            logger.debug("book cache update failed: %s", exc)

    # ---- Market Data ----

    async def get_symbols(self, use_gzip: bool = True) -> RecordList:
        if use_gzip:
            result = await self.transport.send_command(CMD_GET_SYMBOLS_GZIP)
            if result.body and len(result.body) > 4:
                compressed = bytes(result.body[4:])
                raw = zlib.decompress(compressed)
                return _parse_counted_records(raw, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)
            return []
        result = await self.transport.send_command(CMD_GET_SYMBOLS)
        return _parse_counted_records(result.body, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)

    async def symbols_total(self, use_gzip: bool = True) -> int:
        """Official-style helper returning the number of known symbols."""
        if self._symbols:
            return len(self._symbols)
        return len(await self.get_symbols(use_gzip=use_gzip))

    async def symbols_get(self, group: str | None = None, use_gzip: bool = True) -> RecordList:
        """Official-style symbol list with optional wildcard group filtering."""
        symbols = await self.get_symbols(use_gzip=use_gzip)
        if not group:
            return symbols
        return [item for item in symbols if _matches_group_mask(item.get("trade_symbol", ""), group)]

    async def get_full_symbol_info(self, symbol: str) -> Record | None:
        """Get detailed symbol specification (cmd=18): contract_size, tick_size, tick_value,
        volume_min/max/step, margin_initial/maintenance, spread, currencies, etc.

        Args:
            symbol: Symbol name, e.g. "EURUSD"

        Returns:
            Dict with full symbol properties, or None if symbol not found.
        """
        payload = SeriesCodec.serialize([
            (PROP_FIXED_STRING, symbol[:32], 64),
        ])
        try:
            result = await self.transport.send_command(CMD_GET_FULL_SYMBOLS, payload)
            if not result.body:
                return None
            records = _parse_counted_records(result.body, FULL_SYMBOL_SCHEMA, FULL_SYMBOL_FIELD_NAMES)
            if records:
                info = _normalize_full_symbol_record(records[0])
                self._full_symbols[symbol] = info
                return dict(info)
            full_sym_size = get_series_size(FULL_SYMBOL_SCHEMA)
            if len(result.body) >= full_sym_size:
                vals = SeriesCodec.parse(result.body, FULL_SYMBOL_SCHEMA)
                info = _normalize_full_symbol_record(dict(zip(FULL_SYMBOL_FIELD_NAMES, vals)))
                self._full_symbols[symbol] = info
                return dict(info)
            return None
        except Exception as exc:
            logger.warning("get_full_symbol_info(%s) failed: %s", symbol, exc)
            return None

    async def symbol_info(self, symbol: str) -> Record | None:
        """Official-style symbol_info() wrapper backed by cmd=18."""
        cached = self._full_symbols.get(symbol)
        if cached is not None:
            return dict(cached)
        info = await self.get_full_symbol_info(symbol)
        if info is not None:
            return dict(info)
        if not self._symbols:
            await self.load_symbols()
        basic = self._symbols.get(symbol)
        if basic is None:
            return None
        return {
            "trade_symbol": basic.name,
            "symbol_id": basic.symbol_id,
            "digits": basic.digits,
            "symbol_description": basic.description,
            "symbol_path": basic.path,
            "trade_calc_mode": basic.trade_calc_mode,
            "basis": basic.basis,
            "sector": basic.sector,
        }

    def symbol_info_tick(self, symbol: str) -> Record | None:
        """Return the latest cached tick for a symbol if a tick stream has been seen."""
        tick = self._tick_cache_by_name.get(symbol)
        return dict(tick) if tick is not None else None

    async def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        """Best-effort official-style symbol selection via tick subscription."""
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return False
        if enable:
            await self.subscribe_ticks([symbol_id])
        else:
            await self.unsubscribe_ticks([symbol_id])
            self._tick_cache_by_id.pop(symbol_id, None)
            self._tick_cache_by_name.pop(symbol, None)
        return True

    async def subscribe_ticks(self, symbol_ids: list[int]) -> None:
        # Accumulate rather than replace — MT5 subscribe replaces server-side,
        # so we must always send the full set of desired subscriptions.
        existing = set(getattr(self, "_subscribed_ids", None) or [])
        merged = sorted(existing | set(symbol_ids))
        payload = struct.pack(f"<{len(merged) + 1}I", len(merged), *merged)
        await self.transport.send_command(CMD_SUBSCRIBE_TICKS, payload)
        self._subscribed_ids = merged
        logger.info("subscribed to %d symbol ids for ticks (added %d new)",
                     len(merged), len(set(symbol_ids) - existing))

    async def unsubscribe_ticks(self, symbol_ids: list[int]) -> None:
        """Remove specific symbol IDs from tick subscriptions.

        If the resulting subscription set is empty, sends an empty subscription
        to the server to stop all tick pushes.
        """
        existing = set(self._subscribed_ids)
        to_remove = set(symbol_ids)
        remaining = sorted(existing - to_remove)
        if remaining:
            payload = struct.pack(f"<{len(remaining) + 1}I", len(remaining), *remaining)
        else:
            payload = struct.pack("<I", 0)
        await self.transport.send_command(CMD_SUBSCRIBE_TICKS, payload)
        removed_count = len(existing) - len(remaining)
        self._subscribed_ids = remaining
        logger.info("unsubscribed %d symbol ids from ticks (%d remaining)",
                     removed_count, len(remaining))

    async def subscribe_symbols(self, symbol_names: list[str]) -> list[int]:
        """Subscribe to tick updates by symbol name (requires load_symbols first).

        Returns list of resolved symbol IDs.
        Raises ValueError if any symbol name is not found in cache.
        """
        ids = []
        missing = []
        for name in symbol_names:
            info = self._symbols.get(name)
            if info is None:
                missing.append(name)
            else:
                ids.append(info.symbol_id)
        if missing:
            raise ValueError(f"symbols not found in cache (call load_symbols first): {missing}")
        await self.subscribe_ticks(ids)
        return ids

    def on_tick(self, callback: Callable[[RecordList], None]) -> Callable:
        def _handler(result: CommandResult) -> None:
            try:
                callback(_parse_tick_batch(result.body, self._symbols_by_id))
            except Exception as exc:
                logger.error("tick parse error: %s", exc)
        self.transport.on(CMD_TICK_PUSH, _handler)
        return _handler

    async def get_rates(
        self, symbol: str, period_minutes: int, from_ts: int, to_ts: int
    ) -> RecordList:
        """Get kline/rate bars.

        Args:
            symbol: Symbol name, e.g. "EURUSD"
            period_minutes: Bar period in minutes (1, 5, 15, 30, 60, 240, 1440, 10080, 43200)
            from_ts: Start time as UNIX seconds
            to_ts: End time as UNIX seconds

        Returns:
            List of bar dicts with keys: time, open, high, low, close, tick_volume, spread, real_volume
        """
        mapped = PERIOD_MAP.get(period_minutes, period_minutes)
        payload = SeriesCodec.serialize([
            (PROP_FIXED_STRING, symbol[:32], 64),
            (PROP_U16, mapped),
            (PROP_I32, from_ts),
            (PROP_I32, to_ts),
        ])
        result = await self.transport.send_command(CMD_GET_RATES, payload)
        return _parse_rate_bars(result.body)

    async def get_rates_raw(
        self, symbol: str, period_minutes: int, from_ts: int, to_ts: int
    ) -> bytes:
        """Get raw rate bytes (for debugging)."""
        mapped = PERIOD_MAP.get(period_minutes, period_minutes)
        payload = SeriesCodec.serialize([
            (PROP_FIXED_STRING, symbol[:32], 64),
            (PROP_U16, mapped),
            (PROP_I32, from_ts),
            (PROP_I32, to_ts),
        ])
        result = await self.transport.send_command(CMD_GET_RATES, payload)
        return result.body

    async def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: int | float | datetime,
        date_to: int | float | datetime,
    ) -> RecordList:
        """Official-style range wrapper for cmd=11 bars."""
        period_minutes = _normalize_timeframe_minutes(timeframe)
        return await self.get_rates(
            symbol,
            period_minutes,
            _coerce_timestamp(date_from),
            _coerce_timestamp(date_to),
        )

    async def copy_rates_from(
        self,
        symbol: str,
        timeframe: int,
        date_from: int | float | datetime,
        count: int,
    ) -> RecordList:
        """Best-effort official-style wrapper returning bars up to date_from."""
        if count <= 0:
            return []
        period_minutes = _normalize_timeframe_minutes(timeframe)
        end_ts = _coerce_timestamp(date_from)
        lookback = _history_lookback_seconds(period_minutes, count)
        bars = await self.get_rates(symbol, period_minutes, max(0, end_ts - lookback), end_ts)
        return bars[-count:]

    async def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> RecordList:
        """Best-effort current-bar-relative wrapper built on cmd=11 history."""
        if start_pos < 0:
            raise ValueError(f"start_pos must be >= 0, got {start_pos}")
        if count <= 0:
            return []
        period_minutes = _normalize_timeframe_minutes(timeframe)
        end_ts = int(datetime.now().timestamp())
        lookback = _history_lookback_seconds(period_minutes, start_pos + count)
        bars = await self.get_rates(symbol, period_minutes, max(0, end_ts - lookback), end_ts)
        if not bars:
            return []
        end_index = max(0, len(bars) - start_pos)
        start_index = max(0, end_index - count)
        return bars[start_index:end_index]

    async def copy_ticks_from(
        self,
        symbol: str,
        date_from: int | float | datetime,
        count: int,
        flags: int = COPY_TICKS_ALL,
    ) -> RecordList:
        """Best-effort cached tick-history view built from cmd=8 pushes."""
        if count <= 0:
            return []
        history = await self._get_tick_history(symbol)
        from_ms = _coerce_timestamp_ms(date_from)
        result = [
            _to_copy_tick_record(tick)
            for tick in history
            if int(tick.get("tick_time_ms", 0) or 0) >= from_ms
            and _tick_matches_copy_flags(tick, flags)
        ]
        return result[:count]

    async def copy_ticks_range(
        self,
        symbol: str,
        date_from: int | float | datetime,
        date_to: int | float | datetime,
        flags: int = COPY_TICKS_ALL,
    ) -> RecordList:
        """Best-effort cached tick-range view built from cmd=8 pushes."""
        history = await self._get_tick_history(symbol)
        from_ms = _coerce_timestamp_ms(date_from)
        to_ms = _coerce_timestamp_ms_end(date_to)
        if to_ms < from_ms:
            return []
        return [
            _to_copy_tick_record(tick)
            for tick in history
            if from_ms <= int(tick.get("tick_time_ms", 0) or 0) <= to_ms
            and _tick_matches_copy_flags(tick, flags)
        ]

    # ---- Account / Positions / Orders ----

    async def get_positions_and_orders(self) -> dict[str, RecordList]:
        result = await self.transport.send_command(CMD_GET_POSITIONS_ORDERS)
        body = result.body
        positions = _parse_counted_records(body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
        pos_size = get_series_size(POSITION_SCHEMA)
        pos_count = struct.unpack_from("<I", body, 0)[0] if body else 0
        order_offset = 4 + pos_count * pos_size
        orders = _parse_counted_records(body[order_offset:], ORDER_SCHEMA, ORDER_FIELD_NAMES)
        return {"positions": positions, "orders": orders}

    async def get_trade_history(
        self, from_ts: int = 0, to_ts: int = 0
    ) -> dict[str, RecordList]:
        payload = struct.pack("<ii", from_ts, to_ts)
        result = await self.transport.send_command(CMD_GET_TRADE_HISTORY, payload)
        body = result.body
        deal_size = get_series_size(DEAL_SCHEMA)
        deal_count = struct.unpack_from("<I", body, 0)[0] if body else 0
        deals = []
        offset = 4
        for _ in range(deal_count):
            vals = SeriesCodec.parse_at(body, DEAL_SCHEMA, offset)
            d = dict(zip(DEAL_FIELD_NAMES, vals))
            d["time_create"] = d["time_create"] * 1000 + d.get("time_create_ms", 0)
            d["time_update"] = d["time_update"] * 1000 + d.get("time_update_ms", 0)
            deals.append(d)
            offset += deal_size
        orders = _parse_counted_records(body[offset:], ORDER_SCHEMA, ORDER_FIELD_NAMES)
        return {"deals": deals, "orders": orders}

    # ---- Convenience Wrappers ----

    async def get_positions(self) -> RecordList:
        """Get open positions only."""
        data = await self.get_positions_and_orders()
        return data["positions"]

    async def get_orders(self) -> RecordList:
        """Get pending orders only."""
        data = await self.get_positions_and_orders()
        return data["orders"]

    async def get_deals(self, from_ts: int = 0, to_ts: int = 0) -> RecordList:
        """Get closed deals only."""
        data = await self.get_trade_history(from_ts, to_ts)
        return data["deals"]

    async def positions_total(self) -> int:
        """Official-style total count helper for open positions."""
        return len(await self.get_positions())

    async def positions_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> RecordList:
        """Official-style filtered positions getter."""
        positions = await self.get_positions()
        if symbol is not None:
            return [item for item in positions if item.get("trade_symbol") == symbol]
        if group is not None:
            return [item for item in positions if _matches_group_mask(item.get("trade_symbol", ""), group)]
        if ticket is not None:
            return [item for item in positions if int(item.get("position_id", 0)) == int(ticket)]
        return positions

    async def orders_total(self) -> int:
        """Official-style total count helper for open pending orders."""
        return len(await self.get_orders())

    async def orders_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> RecordList:
        """Official-style filtered pending-orders getter."""
        orders = await self.get_orders()
        if symbol is not None:
            return [item for item in orders if item.get("trade_symbol") == symbol]
        if group is not None:
            return [item for item in orders if _matches_group_mask(item.get("trade_symbol", ""), group)]
        if ticket is not None:
            return [item for item in orders if int(item.get("trade_order", 0)) == int(ticket)]
        return orders

    async def history_orders_get(
        self,
        date_from: int | float | datetime | None = None,
        date_to: int | float | datetime | None = None,
        *,
        group: str | None = None,
        ticket: int | None = None,
        position: int | None = None,
    ) -> RecordList:
        """Official-style historical-orders getter built on cmd=5."""
        history = await self.get_trade_history(
            _coerce_optional_timestamp(date_from),
            _coerce_optional_timestamp(date_to),
        )
        orders = history["orders"]
        if group is not None:
            orders = [item for item in orders if _matches_group_mask(item.get("trade_symbol", ""), group)]
        if ticket is not None:
            orders = [item for item in orders if int(item.get("trade_order", 0)) == int(ticket)]
        if position is not None:
            orders = [item for item in orders if int(item.get("position_id", 0)) == int(position)]
        return orders

    async def history_orders_total(
        self,
        date_from: int | float | datetime | None = None,
        date_to: int | float | datetime | None = None,
    ) -> int:
        """Official-style historical-order count helper."""
        return len(await self.history_orders_get(date_from, date_to))

    async def history_deals_get(
        self,
        date_from: int | float | datetime | None = None,
        date_to: int | float | datetime | None = None,
        *,
        group: str | None = None,
        ticket: int | None = None,
        position: int | None = None,
    ) -> RecordList:
        """Official-style historical-deals getter built on cmd=5."""
        deals = await self.get_deals(_coerce_optional_timestamp(date_from), _coerce_optional_timestamp(date_to))
        if group is not None:
            deals = [item for item in deals if _matches_group_mask(item.get("trade_symbol", ""), group)]
        if ticket is not None:
            deals = [item for item in deals if int(item.get("trade_order", 0)) == int(ticket)]
        if position is not None:
            deals = [item for item in deals if int(item.get("position_id", 0)) == int(position)]
        return deals

    async def history_deals_total(
        self,
        date_from: int | float | datetime | None = None,
        date_to: int | float | datetime | None = None,
    ) -> int:
        """Official-style historical-deal count helper."""
        return len(await self.history_deals_get(date_from, date_to))

    async def get_account_summary(self) -> AccountInfo:
        """Get account summary using get_account (cmd=3) and positions/orders.

        This uses the proper account info command to get balance, equity,
        margin, leverage, etc., and supplements with position/order counts.
        Falls back to computing from positions if cmd=3 fails.
        """
        data = await self.get_positions_and_orders()
        positions = data["positions"]
        orders = data["orders"]
        try:
            acct = await self.get_account()
            if acct:
                return AccountInfo(
                    balance=acct.get("balance", 0.0),
                    equity=acct.get("equity", 0.0),
                    margin=acct.get("margin", 0.0),
                    margin_free=acct.get("margin_free", 0.0),
                    margin_level=acct.get("margin_level", 0.0),
                    profit=acct.get("profit", 0.0),
                    credit=acct.get("credit", 0.0),
                    leverage=int(acct.get("leverage", 0)),
                    currency=acct.get("currency", ""),
                    server=acct.get("server", ""),
                    positions_count=len(positions),
                    orders_count=len(orders),
                )
        except Exception as exc:
            logger.debug("get_account failed, falling back to positions: %s", exc)
        floating_profit = sum(p.get("profit", 0.0) for p in positions)
        floating_commission = sum(p.get("commission", 0.0) for p in positions)
        floating_swap = sum(p.get("storage", 0.0) for p in positions)
        total_profit = floating_profit + floating_commission + floating_swap
        return AccountInfo(
            profit=total_profit,
            positions_count=len(positions),
            orders_count=len(orders),
        )

    # ---- Push Event Handling ----

    def on_position_update(self, callback: Callable[[RecordList], None]) -> Callable:
        """Register callback for position change push notifications.

        The server pushes cmd=4 data when positions or orders change.
        Returns the internal handler for use with transport.off().
        """
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                positions = _parse_counted_records(body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
                callback(positions)
            except Exception as exc:
                logger.error("position update parse error: %s", exc)
        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)
        return _handler

    def on_order_update(self, callback: Callable[[RecordList], None]) -> Callable:
        """Register callback for order change push notifications.

        Parses orders from the same cmd=4 push.
        Returns the internal handler for use with transport.off().
        """
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                pos_size = get_series_size(POSITION_SCHEMA)
                pos_count = struct.unpack_from("<I", body, 0)[0] if body else 0
                order_offset = 4 + pos_count * pos_size
                orders = _parse_counted_records(body[order_offset:], ORDER_SCHEMA, ORDER_FIELD_NAMES)
                callback(orders)
            except Exception as exc:
                logger.error("order update parse error: %s", exc)
        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)
        return _handler

    def on_trade_update(self, callback: Callable[[dict[str, RecordList]], None]) -> Callable:
        """Register callback for combined position+order push notifications.

        Parses both positions and orders from cmd=4 push into a single dict
        with keys 'positions' and 'orders'. More efficient than registering
        separate on_position_update and on_order_update handlers.
        Returns the internal handler for use with transport.off().
        """
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                positions = _parse_counted_records(body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
                pos_size = get_series_size(POSITION_SCHEMA)
                pos_count = struct.unpack_from("<I", body, 0)[0] if body else 0
                order_offset = 4 + pos_count * pos_size
                orders = _parse_counted_records(body[order_offset:], ORDER_SCHEMA, ORDER_FIELD_NAMES)
                callback({"positions": positions, "orders": orders})
            except Exception as exc:
                logger.error("trade update parse error: %s", exc)
        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)
        return _handler

    def on_symbol_update(self, callback: Callable[[CommandResult], None]) -> Callable:
        """Register callback for symbol update push notifications (cmd=13).

        The server pushes symbol changes (e.g. spread updates, trading hours).
        Raw CommandResult is passed since the exact schema varies.
        Returns the callback for use with transport.off().
        """
        self.transport.on(CMD_SYMBOL_UPDATE_PUSH, callback)
        return callback

    def on_account_update(self, callback: Callable[[Record], None]) -> Callable:
        """Register callback for account update push notifications (cmd=14).

        Server pushes account balance/margin/equity changes in real-time.
        Callback receives a dict with the same fields as get_account().
        Returns the internal handler for use with transport.off().
        """
        def _handler(result: CommandResult) -> None:
            try:
                data = _parse_account_response(result.body)
                callback(data)
            except Exception as exc:
                logger.error("account update parse error: %s", exc)
        self.transport.on(CMD_ACCOUNT_UPDATE_PUSH, _handler)
        return _handler

    def on_login_status(self, callback: Callable[[CommandResult], None]) -> Callable:
        """Register callback for login status push notifications (cmd=15).

        The server may push login status changes (e.g. forced logout, session expiry).
        Returns the callback for use with transport.off().
        """
        self.transport.on(CMD_LOGIN_STATUS_PUSH, callback)
        return callback

    def on_symbol_details(self, callback: Callable[[RecordList], None]) -> Callable:
        """Register callback for extended symbol quote data (cmd=17).

        Receives detailed quote data including options greeks (delta, gamma, theta,
        vega, rho, omega), session statistics, and price limits.
        """
        detail_size = get_series_size(SYMBOL_DETAILS_SCHEMA)
        def _handler(result: CommandResult) -> None:
            try:
                count = len(result.body) // detail_size
                details = []
                offset = 0
                for _ in range(count):
                    if offset + detail_size > len(result.body):
                        break
                    vals = SeriesCodec.parse_at(result.body, SYMBOL_DETAILS_SCHEMA, offset)
                    d = dict(zip(SYMBOL_DETAILS_FIELD_NAMES, vals))
                    sym_info = self._symbols_by_id.get(d["symbol_id"])
                    if sym_info:
                        d["symbol"] = sym_info.name
                    details.append(d)
                    offset += detail_size
                callback(details)
            except Exception as exc:
                logger.error("symbol details parse error: %s", exc)
        self.transport.on(CMD_SYMBOL_DETAILS_PUSH, _handler)
        return _handler

    def on_trade_result(self, callback: Callable[[Record], None]) -> Callable:
        """Register callback for async trade execution results (cmd=19).

        Server pushes trade results asynchronously. Callback receives a dict
        with trade action details and execution result (retcode, order, price, etc.).
        """
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                action_size = get_series_size(TRADE_RESULT_PUSH_SCHEMA)
                resp_size = get_series_size(TRADE_RESULT_RESPONSE_SCHEMA)
                data: dict[str, Any] = {}
                if len(body) >= action_size:
                    vals = SeriesCodec.parse(body, TRADE_RESULT_PUSH_SCHEMA)
                    data.update(zip(TRADE_RESULT_PUSH_FIELD_NAMES, vals))
                if len(body) >= action_size + resp_size:
                    resp_vals = SeriesCodec.parse_at(body, TRADE_RESULT_RESPONSE_SCHEMA, action_size)
                    data["result"] = dict(zip(TRADE_RESULT_RESPONSE_FIELD_NAMES, resp_vals))
                callback(data)
            except Exception as exc:
                logger.error("trade result push parse error: %s", exc)
        self.transport.on(CMD_TRADE_RESULT_PUSH, _handler)
        return _handler

    def on_trade_transaction(self, callback: Callable[[Record], None]) -> Callable:
        """Register callback for trade update push (cmd=10).

        Receives real-time trade state changes:
        - type=2: Balance update with deals and positions arrays
        - type!=2: Order transaction (add/update/delete)

        Callback receives a dict with:
        - 'update_type': 2 for balance update, other for transaction
        - For balance updates: 'balance_info', 'deals', 'positions'
        - For transactions: 'flag_mask', 'transaction_id', 'transaction_type', 'order'
        """
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                if len(body) < 4:
                    return
                update_type = struct.unpack_from("<I", body, 0)[0]
                data: dict[str, Any] = {"update_type": update_type}
                if update_type == 2:
                    offset = 4
                    balance_size = get_series_size(TRADE_UPDATE_BALANCE_SCHEMA)
                    if len(body) >= offset + balance_size:
                        vals = SeriesCodec.parse_at(body, TRADE_UPDATE_BALANCE_SCHEMA, offset)
                        data["balance_info"] = dict(zip(TRADE_UPDATE_BALANCE_FIELD_NAMES, vals))
                        offset += balance_size
                    data["deals"] = _parse_counted_records(body[offset:], DEAL_SCHEMA, DEAL_FIELD_NAMES)
                    deal_size = get_series_size(DEAL_SCHEMA)
                    if len(body) > offset and len(body[offset:]) >= 4:
                        deal_count = struct.unpack_from("<I", body, offset)[0]
                        offset += 4 + deal_count * deal_size
                    data["positions"] = _parse_counted_records(body[offset:], POSITION_SCHEMA, POSITION_FIELD_NAMES)
                else:
                    offset = 4
                    txn_size = get_series_size(TRADE_TRANSACTION_SCHEMA)
                    if len(body) >= offset + txn_size:
                        vals = SeriesCodec.parse_at(body, TRADE_TRANSACTION_SCHEMA, offset)
                        data.update(zip(TRADE_TRANSACTION_FIELD_NAMES, vals))
                        offset += txn_size
                    order_size = get_series_size(ORDER_SCHEMA)
                    if len(body) >= offset + order_size:
                        order_vals = SeriesCodec.parse_at(body, ORDER_SCHEMA, offset)
                        data["order"] = dict(zip(ORDER_FIELD_NAMES, order_vals))
                callback(data)
            except Exception as exc:
                logger.error("trade transaction push parse error: %s", exc)
        self.transport.on(CMD_TRADE_UPDATE_PUSH, _handler)
        return _handler

    def on_book_update(self, callback: Callable[[RecordList], None]) -> Callable:
        """Register callback for order book / DOM push (cmd=23).

        Receives list of dicts, each with 'symbol_id', 'bids', 'asks'.
        bids/asks are lists of {'price': float, 'volume': int}.
        """
        def _handler(result: CommandResult) -> None:
            try:
                callback(_parse_book_entries(result.body, self._symbols_by_id))
            except Exception as exc:
                logger.error("book update parse error: %s", exc)
        self.transport.on(CMD_BOOK_PUSH, _handler)
        return _handler

    # ---- Trading ----

    async def trade_request(
        self,
        *,
        action_id: int = 0,
        trade_action: int = 0,
        symbol: str = "",
        volume: int = 0,
        digits: int = 0,
        order: int = 0,
        trade_type: int = 0,
        type_filling: int = 0,
        type_time: int = 0,
        type_flags: int = 0,
        type_reason: int = 0,
        price_order: float = 0.0,
        price_trigger: float = 0.0,
        price_sl: float = 0.0,
        price_tp: float = 0.0,
        deviation: int = 0,
        comment: str = "",
        position_id: int = 0,
        position_by: int = 0,
        time_expiration: int = 0,
    ) -> TradeResult:
        if trade_action in (TRADE_ACTION_DEAL, TRADE_ACTION_PENDING) and volume <= 0:
            raise ValueError(f"volume must be > 0 for trade_action={trade_action}, got {volume}")
        if trade_action == TRADE_ACTION_PENDING and price_order <= 0.0:
            raise ValueError(f"price_order must be > 0 for pending orders, got {price_order}")
        payload = SeriesCodec.serialize([
            (PROP_U32, action_id),
            (PROP_U32, trade_action),
            (PROP_FIXED_STRING, symbol[:32], 64),
            (PROP_U64, volume),
            (PROP_U32, digits),
            (PROP_U64, order),
            (PROP_U32, trade_type),
            (PROP_U32, type_filling),
            (PROP_U32, type_time),
            (PROP_U32, type_flags),
            (PROP_U32, type_reason),
            (PROP_F64, price_order),
            (PROP_F64, price_trigger),
            (PROP_F64, price_sl),
            (PROP_F64, price_tp),
            (PROP_U64, deviation),
            (PROP_FIXED_STRING, comment[:32], 64),
            (PROP_U64, position_id),
            (PROP_U64, position_by),
            (PROP_U32, time_expiration),
        ])
        result = await self.transport.send_command(CMD_TRADE_REQUEST, payload)
        return self._parse_trade_response(result.body, symbol, trade_action, volume)

    async def order_send(self, request: Record) -> TradeResult:
        """Official-style order_send() wrapper over cmd=12 trade_request."""
        symbol = str(request.get("symbol", "") or "")
        order_type = int(request.get("type", 0) or 0)
        price = float(request.get("price", 0.0) or 0.0)
        stoplimit = float(request.get("stoplimit", 0.0) or 0.0)
        price_order = price
        price_trigger = 0.0
        if order_type in {ORDER_TYPE_BUY_STOP_LIMIT, ORDER_TYPE_SELL_STOP_LIMIT}:
            price_order = stoplimit
            price_trigger = price
        volume = self._volume_to_lots(float(request.get("volume", 0.0) or 0.0))
        digits = self._resolve_digits(symbol, None) if symbol else 0
        return await self.trade_request(
            action_id=int(request.get("action_id", request.get("request_id", 0)) or 0),
            trade_action=int(request.get("action", 0) or 0),
            symbol=symbol,
            volume=volume,
            digits=digits,
            order=int(request.get("order", 0) or 0),
            trade_type=order_type,
            type_filling=int(request.get("type_filling", ORDER_FILLING_FOK) or 0),
            type_time=int(request.get("type_time", ORDER_TIME_GTC) or 0),
            type_flags=int(request.get("type_flags", 0) or 0),
            type_reason=int(request.get("type_reason", 0) or 0),
            price_order=price_order,
            price_trigger=price_trigger,
            price_sl=float(request.get("sl", 0.0) or 0.0),
            price_tp=float(request.get("tp", 0.0) or 0.0),
            deviation=int(request.get("deviation", 0) or 0),
            comment=str(request.get("comment", "") or ""),
            position_id=int(request.get("position", 0) or 0),
            position_by=int(request.get("position_by", 0) or 0),
            time_expiration=_coerce_optional_timestamp(request.get("expiration")),
        )

    async def order_check(self, request: Record) -> Record:
        """Best-effort local pre-flight validation for an MT5 trade request."""
        try:
            normalized = self._normalize_order_request(request)
            retcode, comment = await self._validate_order_check_request(normalized)
            account = await self.get_account()
            balance = float(account.get("balance", 0.0) or 0.0)
            equity = float(account.get("equity", balance) or balance)
            profit = float(account.get("profit", 0.0) or 0.0)
            margin = float(account.get("margin", 0.0) or 0.0)
            additional_margin = 0.0
            if retcode in {0, TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL}:
                additional_margin = await self._estimate_order_margin(normalized)
            margin_post = margin + additional_margin
            margin_free = equity - margin_post
            margin_level = 0.0 if margin_post <= 0.0 else (equity / margin_post) * 100.0
            if (
                retcode in {0, TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL}
                and margin_free < 0.0
            ):
                retcode = TRADE_RETCODE_NO_MONEY
                comment = TRADE_RETCODE_DESCRIPTIONS.get(retcode, "Not enough money")
            if retcode == 0:
                self._clear_last_error()
            else:
                self._last_error = (int(retcode), comment)
            return {
                "retcode": int(retcode),
                "balance": balance,
                "equity": equity,
                "profit": profit,
                "margin": margin_post,
                "margin_free": margin_free,
                "margin_level": margin_level,
                "comment": comment,
                "request": normalized,
            }
        except Exception as exc:
            message = f"order_check() failed: {exc}"
            self._last_error = (-99, message)
            account = await self.get_account()
            balance = float(account.get("balance", 0.0) or 0.0)
            equity = float(account.get("equity", balance) or balance)
            profit = float(account.get("profit", 0.0) or 0.0)
            margin = float(account.get("margin", 0.0) or 0.0)
            margin_free = float(account.get("margin_free", equity - margin) or (equity - margin))
            margin_level = float(account.get("margin_level", 0.0) or 0.0)
            return {
                "retcode": -99,
                "balance": balance,
                "equity": equity,
                "profit": profit,
                "margin": margin,
                "margin_free": margin_free,
                "margin_level": margin_level,
                "comment": message,
                "request": dict(request),
            }

    def _normalize_order_request(self, request: Record) -> Record:
        symbol = str(request.get("symbol", "") or "")
        order_type = int(request.get("type", 0) or 0)
        action = int(request.get("action", 0) or 0)
        price = float(request.get("price", 0.0) or 0.0)
        stoplimit = float(request.get("stoplimit", 0.0) or 0.0)
        price_order = price
        price_trigger = 0.0
        if order_type in {ORDER_TYPE_BUY_STOP_LIMIT, ORDER_TYPE_SELL_STOP_LIMIT}:
            price_order = stoplimit
            price_trigger = price
        return {
            "action": action,
            "symbol": symbol,
            "volume": float(request.get("volume", 0.0) or 0.0),
            "type": order_type,
            "price": price,
            "price_order": price_order,
            "price_trigger": price_trigger,
            "sl": float(request.get("sl", 0.0) or 0.0),
            "tp": float(request.get("tp", 0.0) or 0.0),
            "type_filling": int(request.get("type_filling", ORDER_FILLING_FOK) or 0),
            "type_time": int(request.get("type_time", ORDER_TIME_GTC) or 0),
            "expiration": _coerce_optional_timestamp(request.get("expiration")),
            "position": int(request.get("position", 0) or 0),
            "position_by": int(request.get("position_by", 0) or 0),
            "order": int(request.get("order", 0) or 0),
            "deviation": int(request.get("deviation", 0) or 0),
            "comment": str(request.get("comment", "") or ""),
        }

    async def _validate_order_check_request(self, request: Record) -> tuple[int, str]:
        action = int(request.get("action", 0) or 0)
        symbol = str(request.get("symbol", "") or "")
        order_type = int(request.get("type", 0) or 0)
        volume = float(request.get("volume", 0.0) or 0.0)
        symbol_info = await self.symbol_info(symbol) if symbol else None
        if action in {TRADE_ACTION_DEAL, TRADE_ACTION_PENDING}:
            if not symbol:
                return TRADE_RETCODE_INVALID, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID]
            if symbol_info is None:
                return TRADE_RETCODE_INVALID, f"symbol not found: {symbol}"
            if volume <= 0.0:
                return TRADE_RETCODE_INVALID_VOLUME, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID_VOLUME]
            if _order_side(order_type) is None:
                return TRADE_RETCODE_INVALID_ORDER, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID_ORDER]
            trade_mode = int(symbol_info.get("trade_mode", 0) or 0)
            if trade_mode == 0:
                return TRADE_RETCODE_TRADE_DISABLED, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_TRADE_DISABLED]
            side = _order_side(order_type)
            if trade_mode == 1 and side is False:
                return TRADE_RETCODE_INVALID_ORDER, "symbol is long-only"
            if trade_mode == 2 and side is True:
                return TRADE_RETCODE_INVALID_ORDER, "symbol is short-only"
            if trade_mode == 3:
                return TRADE_RETCODE_TRADE_DISABLED, "symbol is close-only"
            volume_check = _validate_requested_volume(symbol_info, volume)
            if volume_check is not None:
                return TRADE_RETCODE_INVALID_VOLUME, volume_check
            expiration = int(request.get("expiration", 0) or 0)
            type_time = int(request.get("type_time", ORDER_TIME_GTC) or 0)
            if type_time in {ORDER_TIME_SPECIFIED, ORDER_TIME_SPECIFIED_DAY} and expiration <= 0:
                return (
                    TRADE_RETCODE_INVALID_EXPIRATION,
                    TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID_EXPIRATION],
                )
            fill_flags = int(symbol_info.get("filling_mode", 0) or 0)
            type_filling = int(request.get("type_filling", ORDER_FILLING_FOK) or 0)
            if fill_flags and type_filling not in {0, 1, 2}:
                return TRADE_RETCODE_INVALID_FILL, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID_FILL]
            price_check = await self._resolve_order_check_price(request, symbol_info)
            if price_check <= 0.0:
                return TRADE_RETCODE_INVALID_PRICE, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID_PRICE]
            if action == TRADE_ACTION_PENDING and float(request.get("price_order", 0.0) or 0.0) <= 0.0:
                return TRADE_RETCODE_INVALID_PRICE, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID_PRICE]
            stops_error = _validate_requested_stops(symbol_info, request, price_check)
            if stops_error is not None:
                return TRADE_RETCODE_INVALID_STOPS, stops_error
        elif action == TRADE_ACTION_SLTP:
            if int(request.get("position", 0) or 0) <= 0:
                return TRADE_RETCODE_INVALID, "position is required for SLTP modification"
        elif action == TRADE_ACTION_MODIFY:
            if int(request.get("order", 0) or 0) <= 0:
                return TRADE_RETCODE_INVALID, "order is required for order modification"
        elif action == TRADE_ACTION_REMOVE:
            if int(request.get("order", 0) or 0) <= 0:
                return TRADE_RETCODE_INVALID, "order is required for order removal"
        elif action == TRADE_ACTION_CLOSE_BY:
            if int(request.get("position", 0) or 0) <= 0 or int(request.get("position_by", 0) or 0) <= 0:
                return TRADE_RETCODE_INVALID, "position and position_by are required for close_by"
        else:
            return TRADE_RETCODE_INVALID, TRADE_RETCODE_DESCRIPTIONS[TRADE_RETCODE_INVALID]
        return 0, TRADE_RETCODE_DESCRIPTIONS[0]

    async def _estimate_order_margin(self, request: Record) -> float:
        action = int(request.get("action", 0) or 0)
        if action not in {TRADE_ACTION_DEAL, TRADE_ACTION_PENDING}:
            return 0.0
        price = await self._resolve_order_check_price(
            request,
            await self.symbol_info(str(request.get("symbol", "") or "")),
        )
        if price <= 0.0:
            return 0.0
        margin = await self.order_calc_margin(
            int(request.get("type", 0) or 0),
            str(request.get("symbol", "") or ""),
            float(request.get("volume", 0.0) or 0.0),
            price,
        )
        return float(margin or 0.0)

    async def _resolve_order_check_price(self, request: Record, symbol_info: Record | None) -> float:
        if symbol_info is None:
            return 0.0
        action = int(request.get("action", 0) or 0)
        order_type = int(request.get("type", 0) or 0)
        if action == TRADE_ACTION_PENDING:
            price = float(request.get("price_order", 0.0) or 0.0)
            if order_type in {ORDER_TYPE_BUY_STOP_LIMIT, ORDER_TYPE_SELL_STOP_LIMIT}:
                price = float(request.get("price_trigger", 0.0) or 0.0)
            return price
        price = float(request.get("price_order", 0.0) or request.get("price", 0.0) or 0.0)
        if price > 0.0:
            return price
        tick = self.symbol_info_tick(str(request.get("symbol", "") or ""))
        if tick is not None:
            if _order_side(order_type):
                return float(tick.get("ask", 0.0) or tick.get("bid", 0.0) or 0.0)
            return float(tick.get("bid", 0.0) or tick.get("ask", 0.0) or 0.0)
        return 0.0

    def _parse_trade_response(self, body: bytes, symbol: str, trade_action: int, volume: int) -> TradeResult:
        """Parse trade response body into TradeResult with full details."""
        if not body:
            tr = TradeResult(retcode=-1, description="Empty response", success=False)
            logger.warning("trade_request %s action=%d vol=%d → empty response", symbol, trade_action, volume)
            return tr
        retcode = struct.unpack_from("<I", body, 0)[0]
        desc = TRADE_RETCODE_DESCRIPTIONS.get(retcode, f"Unknown retcode {retcode}")
        success = retcode in (0, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL, TRADE_RETCODE_PLACED)
        # Parse extended fields if response is large enough
        resp_schema_size = get_series_size(TRADE_RESPONSE_SCHEMA)
        if len(body) >= resp_schema_size:
            try:
                vals = SeriesCodec.parse(body, TRADE_RESPONSE_SCHEMA)
                tr = TradeResult(
                    retcode=vals[0], description=desc, success=success,
                    deal=int(vals[1]), order=int(vals[2]), volume=int(vals[3]),
                    price=vals[4], bid=vals[5], ask=vals[6],
                    comment=vals[7].strip("\x00") if isinstance(vals[7], str) else "",
                    request_id=int(vals[8]),
                )
            except Exception as exc:
                logger.debug("trade response extended parse failed: %s, using retcode only", exc)
                tr = TradeResult(retcode=retcode, description=desc, success=success)
        else:
            tr = TradeResult(retcode=retcode, description=desc, success=success)
        logger.info("trade_request %s action=%d vol=%d → %s", symbol, trade_action, volume, tr)
        return tr

    # ---- High-Level Trading Helpers ----

    def _resolve_digits(self, symbol: str, digits: int | None) -> int:
        """Resolve digits from cache or explicit parameter."""
        if digits is not None:
            return digits
        info = self._symbols.get(symbol)
        return info.digits if info else 5

    @staticmethod
    def _volume_to_lots(volume: float, precision: int = 8) -> int:
        """Convert lots (e.g. 0.01) to MT5 integer volume.

        MT5 Web Terminal uses integer volumes where the value represents
        volume * 10^precision. The default precision=8 is based on the
        MetaQuotes demo server (1.0 lot = 100000000).
        """
        return int(round(volume * (10 ** precision)))

    async def _place_order(
        self,
        *,
        trade_action: int,
        symbol: str,
        volume: float,
        trade_type: int,
        digits: int | None = None,
        price: float = 0.0,
        trigger_price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        deviation: int = 0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        magic: int = 0,
    ) -> TradeResult:
        """Internal helper for all order placement methods."""
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=trade_action,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=trade_type,
            type_filling=filling,
            type_time=time_type,
            price_order=price,
            price_trigger=trigger_price,
            price_sl=sl,
            price_tp=tp,
            deviation=deviation,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
        )

    async def buy_market(
        self,
        symbol: str,
        volume: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        deviation: int = 20,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a market buy order."""
        return await self._place_order(
            trade_action=TRADE_ACTION_DEAL, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_BUY, digits=digits, sl=sl, tp=tp,
            deviation=deviation, comment=comment, filling=filling, magic=magic,
        )

    async def sell_market(
        self,
        symbol: str,
        volume: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        deviation: int = 20,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a market sell order."""
        return await self._place_order(
            trade_action=TRADE_ACTION_DEAL, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_SELL, digits=digits, sl=sl, tp=tp,
            deviation=deviation, comment=comment, filling=filling, magic=magic,
        )

    async def buy_limit(
        self,
        symbol: str,
        volume: float,
        price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a buy limit pending order."""
        return await self._place_order(
            trade_action=TRADE_ACTION_PENDING, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_BUY_LIMIT, digits=digits, price=price,
            sl=sl, tp=tp, comment=comment, filling=filling,
            time_type=time_type, expiration=expiration, magic=magic,
        )

    async def sell_limit(
        self,
        symbol: str,
        volume: float,
        price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a sell limit pending order."""
        return await self._place_order(
            trade_action=TRADE_ACTION_PENDING, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_SELL_LIMIT, digits=digits, price=price,
            sl=sl, tp=tp, comment=comment, filling=filling,
            time_type=time_type, expiration=expiration, magic=magic,
        )

    async def buy_stop(
        self,
        symbol: str,
        volume: float,
        price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a buy stop pending order."""
        return await self._place_order(
            trade_action=TRADE_ACTION_PENDING, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_BUY_STOP, digits=digits, price=price,
            sl=sl, tp=tp, comment=comment, filling=filling,
            time_type=time_type, expiration=expiration, magic=magic,
        )

    async def sell_stop(
        self,
        symbol: str,
        volume: float,
        price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a sell stop pending order."""
        return await self._place_order(
            trade_action=TRADE_ACTION_PENDING, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_SELL_STOP, digits=digits, price=price,
            sl=sl, tp=tp, comment=comment, filling=filling,
            time_type=time_type, expiration=expiration, magic=magic,
        )

    async def buy_stop_limit(
        self,
        symbol: str,
        volume: float,
        price: float,
        stop_limit_price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a buy stop limit pending order.

        Args:
            price: Stop trigger price (price_trigger).
            stop_limit_price: Limit price placed after stop triggers (price_order).
        """
        return await self._place_order(
            trade_action=TRADE_ACTION_PENDING, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_BUY_STOP_LIMIT, digits=digits,
            price=stop_limit_price, trigger_price=price,
            sl=sl, tp=tp, comment=comment, filling=filling,
            time_type=time_type, expiration=expiration, magic=magic,
        )

    async def sell_stop_limit(
        self,
        symbol: str,
        volume: float,
        price: float,
        stop_limit_price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Place a sell stop limit pending order.

        Args:
            price: Stop trigger price (price_trigger).
            stop_limit_price: Limit price placed after stop triggers (price_order).
        """
        return await self._place_order(
            trade_action=TRADE_ACTION_PENDING, symbol=symbol, volume=volume,
            trade_type=ORDER_TYPE_SELL_STOP_LIMIT, digits=digits,
            price=stop_limit_price, trigger_price=price,
            sl=sl, tp=tp, comment=comment, filling=filling,
            time_type=time_type, expiration=expiration, magic=magic,
        )

    async def close_position(
        self,
        symbol: str,
        position_id: int,
        volume: float,
        *,
        order_type: int | None = None,
        deviation: int = 20,
        comment: str = "",
        filling: int = ORDER_FILLING_FOK,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Close a position by placing an opposite market order.

        If order_type is not specified, auto-detects direction from open positions:
        BUY positions are closed with SELL and vice versa. Falls back to SELL if
        the position is not found in the current position list.
        """
        d = self._resolve_digits(symbol, digits)
        if order_type is not None:
            ot = order_type
        else:
            ot = await self._detect_close_direction(position_id)
        return await self.trade_request(
            trade_action=TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ot,
            type_filling=filling,
            deviation=deviation,
            comment=comment,
            position_id=position_id,
            type_reason=magic,
        )

    async def _detect_close_direction(self, position_id: int) -> int:
        """Detect the order type needed to close a position (opposite direction)."""
        try:
            positions = await self.get_positions()
            for p in positions:
                if p.get("position_id") == position_id:
                    if p.get("trade_action") == POSITION_TYPE_SELL:
                        return ORDER_TYPE_BUY
                    return ORDER_TYPE_SELL
            logger.warning(
                "position %d not found in open positions; "
                "defaulting to SELL (may open an unwanted short position)",
                position_id,
            )
        except Exception as exc:
            logger.debug("failed to detect position direction: %s", exc)
        return ORDER_TYPE_SELL

    async def close_position_by(
        self,
        symbol: str,
        position_id: int,
        position_by: int,
        *,
        filling: int = ORDER_FILLING_FOK,
        digits: int | None = None,
        magic: int = 0,
    ) -> TradeResult:
        """Close a position by an opposite position (close-by).

        This closes position_id using the opposite position position_by.
        Both positions must be for the same symbol but in opposite directions.
        """
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_CLOSE_BY,
            symbol=symbol,
            digits=d,
            type_filling=filling,
            position_id=position_id,
            position_by=position_by,
            type_reason=magic,
        )

    async def modify_position_sltp(
        self,
        symbol: str,
        position_id: int,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> TradeResult:
        """Modify stop-loss and take-profit of an open position."""
        return await self.trade_request(
            trade_action=TRADE_ACTION_SLTP,
            symbol=symbol,
            position_id=position_id,
            price_sl=sl,
            price_tp=tp,
        )

    async def modify_pending_order(
        self,
        symbol: str,
        order: int,
        price: float,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        time_type: int = ORDER_TIME_GTC,
        expiration: int = 0,
    ) -> TradeResult:
        """Modify a pending order's price, SL, TP, or expiration."""
        return await self.trade_request(
            trade_action=TRADE_ACTION_MODIFY,
            symbol=symbol,
            order=order,
            price_order=price,
            price_sl=sl,
            price_tp=tp,
            type_time=time_type,
            time_expiration=expiration,
        )

    async def cancel_pending_order(self, order: int) -> TradeResult:
        """Cancel/remove a pending order."""
        return await self.trade_request(
            trade_action=TRADE_ACTION_REMOVE,
            order=order,
        )

    def _resolve_client_id(self, cid: bytes | None) -> bytes:
        client_id = bytes(cid) if cid is not None else build_client_id(
            platform=os.name,
            device_pixel_ratio="1",
            language="en-US",
            screen="0x0",
        )
        if len(client_id) != 16:
            raise ValueError(f"cid must be 16 bytes, got {len(client_id)}")
        return client_id

    @staticmethod
    def _split_password_blob(password: str) -> tuple[str, bytes | None]:
        if password and len(password) == 320:
            return "", bytes.fromhex(password)
        return (password or "")[:32], None

    @staticmethod
    def _coerce_bytes(value: bytes | bytearray | memoryview) -> bytes:
        return bytes(value)

    def _build_opening_base_payload(self, request: AccountOpeningRequest) -> bytes:
        full_name = " ".join(part for part in (request.first_name, request.second_name) if part)
        fields: list[tuple[Any, ...]] = [
            (PROP_FIXED_STRING, full_name[:128], 256),
            (PROP_FIXED_STRING, (request.group or "")[:64], 128),
            (PROP_FIXED_STRING, (request.phone_password or "")[:32], 64),
            (PROP_FIXED_STRING, (request.country or "")[:32], 64),
            (PROP_FIXED_STRING, (request.city or "")[:32], 64),
            (PROP_FIXED_STRING, (request.state or "")[:32], 64),
            (PROP_FIXED_STRING, (request.zipcode or "")[:16], 32),
            (PROP_FIXED_STRING, (request.address or "")[:128], 256),
            (PROP_FIXED_STRING, (request.phone or "")[:32], 64),
            (PROP_FIXED_STRING, (request.email or "")[:64], 128),
            (PROP_F64, float(request.deposit or 0.0)),
            (PROP_U32, int(request.leverage or 0)),
            (PROP_U32, 0),
            (PROP_U32, 1),
            (PROP_FIXED_STRING, (request.domain or "")[:64], 128),
            (PROP_FIXED_STRING, (request.utm_campaign or "")[:32], 64),
            (PROP_FIXED_STRING, (request.utm_source or "")[:32], 64),
            (PROP_U32, int(request.email_confirm_code or 0)),
            (PROP_U32, int(request.phone_confirm_code or 0)),
            (PROP_FIXED_STRING, (request.first_name or "")[:64], 128),
            (PROP_FIXED_STRING, (request.second_name or "")[:64], 128),
            (PROP_U32, int(request.agreements or 0)),
        ]
        return SeriesCodec.serialize(fields)

    def _build_document_payload(self, document: AccountDocument) -> bytes:
        front_buffer = self._coerce_bytes(document.front_buffer)
        back_buffer = self._coerce_bytes(document.back_buffer)
        fields: list[tuple[Any, ...]] = [
            (PROP_U32, int(document.data_type)),
            (PROP_U32, int(document.document_type)),
            (PROP_FIXED_STRING, (document.front_name or "")[:260], 520),
            (PROP_U32, len(front_buffer)),
            (PROP_FIXED_STRING, (document.back_name or "")[:260], 520),
            (PROP_U32, len(back_buffer)),
            (PROP_BYTES, front_buffer),
            (PROP_BYTES, back_buffer),
        ]
        return SeriesCodec.serialize(fields)

    def _build_opening_verification_payload(
        self,
        *,
        request: AccountOpeningRequest,
        build: int,
        cid: bytes | None,
    ) -> bytes:
        client_id = self._resolve_client_id(cid)
        base_payload = self._build_opening_base_payload(request)
        fields: list[tuple[Any, ...]] = [
            (PROP_I16, int(build or 0)),
            (PROP_BYTES, client_id, 16),
            (PROP_BYTES, base_payload),
        ]
        return SeriesCodec.serialize(fields)

    def _build_real_account_payload(self, request: RealAccountRequest) -> bytes:
        base_payload = self._build_opening_base_payload(request)
        extra_fields: list[tuple[Any, ...]] = [
            (PROP_FIXED_STRING, (request.first_name or "")[:64], 128),
            (PROP_FIXED_STRING, (request.second_name or "")[:64], 128),
            (PROP_FIXED_STRING, (request.middle_name or "")[:64], 128),
            (PROP_TIME, int(request.birth_date_ms or 0)),
            (PROP_U32, int(request.gender or 0)),
            (PROP_FIXED_STRING, (request.language or "")[:64], 128),
            (PROP_FIXED_STRING, (request.citizenship or "")[:32], 64),
            (PROP_FIXED_STRING, (request.tax_id or "")[:64], 128),
            (PROP_U32, int(request.employment or 0)),
            (PROP_U32, int(request.industry or 0)),
            (PROP_U32, int(request.education or 0)),
            (PROP_U32, int(request.wealth or 0)),
            (PROP_U64, int(request.annual_income or 0)),
            (PROP_U64, int(request.net_worth or 0)),
            (PROP_U64, int(request.annual_deposit or 0)),
            (PROP_U32, int(request.experience_fx or 0)),
            (PROP_U32, int(request.experience_cfd or 0)),
            (PROP_U32, int(request.experience_futures or 0)),
            (PROP_U32, int(request.experience_stocks or 0)),
            (PROP_BYTES, REAL_ACCOUNT_RESERVED_PAYLOAD, 512),
        ]
        extra_payload = SeriesCodec.serialize(extra_fields)
        document_payload = b"".join(self._build_document_payload(doc) for doc in request.documents)
        return base_payload + extra_payload + document_payload


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


def _order_side(action: int) -> bool | None:
    if action in BUY_ORDER_TYPES:
        return True
    if action in SELL_ORDER_TYPES:
        return False
    return None


def _currencies_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    aliases = ({"RUB", "RUR"},)
    return any(left in group and right in group for group in aliases)


def _coerce_timestamp(value: int | float | datetime) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(value)


def _coerce_optional_timestamp(value: int | float | datetime | None) -> int:
    if value is None:
        return 0
    return _coerce_timestamp(value)


def _coerce_timestamp_ms(value: int | float | datetime) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, float):
        return int(value * 1000)
    return int(value) * 1000


def _coerce_timestamp_ms_end(value: int | float | datetime) -> int:
    if isinstance(value, datetime):
        return _coerce_timestamp_ms(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value) * 1000 + 999
    if isinstance(value, float) and value.is_integer():
        return int(value) * 1000 + 999
    return _coerce_timestamp_ms(value)


def _normalize_timeframe_minutes(timeframe: int) -> int:
    if timeframe in PERIOD_MINUTES_MAP:
        return PERIOD_MINUTES_MAP[timeframe]
    return int(timeframe)


def _history_lookback_seconds(period_minutes: int, bars: int) -> int:
    return max(60, period_minutes * 60) * max(16, bars * 4 + 8)


def _matches_group_mask(symbol: str, group: str) -> bool:
    patterns = [item.strip() for item in group.split(",") if item.strip()]
    if not patterns:
        return True
    positive = [item for item in patterns if not item.startswith("!")]
    negative = [item[1:] for item in patterns if item.startswith("!")]
    included = any(fnmatchcase(symbol, pattern) for pattern in positive) if positive else True
    if not included:
        return False
    return not any(fnmatchcase(symbol, pattern) for pattern in negative)


def _parse_full_symbol_schedule(buffer: bytes) -> Record:
    sessions: Record = {"quote_sessions": [], "trade_sessions": []}
    if not buffer:
        return sessions
    offset = 0
    for key in ("quote_sessions", "trade_sessions"):
        for _ in range(7):
            day_sessions = []
            for _ in range(16):
                if offset + 4 > len(buffer):
                    break
                day_sessions.append(struct.unpack_from("<HH", buffer, offset))
                offset += 4
            sessions[key].append(day_sessions)
    return sessions


def _parse_full_symbol_subscription(buffer: bytes) -> Record:
    if not buffer or len(buffer) < 8:
        return {"delay": 0, "status": 0, "level": 0, "reserved": 0}
    delay, status, level, reserved = struct.unpack_from("<IBBH", buffer, 0)
    return {
        "delay": int(delay),
        "status": int(status),
        "level": int(level),
        "reserved": int(reserved),
    }


def _normalize_full_symbol_record(record: Record) -> Record:
    info = dict(record)
    trade_raw = info.get("trade", b"")
    if isinstance(trade_raw, (bytes, bytearray, memoryview)):
        trade_buffer = bytes(trade_raw)
        record_size = get_series_size(ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA)
        if len(trade_buffer) >= record_size:
            info["trade"] = _parse_account_trade_settings_record(trade_buffer, 0)
        else:
            info["trade"] = {}
    else:
        info["trade"] = dict(trade_raw or {})
    schedule_raw = info.get("schedule", b"")
    if isinstance(schedule_raw, (bytes, bytearray, memoryview)):
        info["schedule"] = _parse_full_symbol_schedule(bytes(schedule_raw))
    subscription_raw = info.get("subscription", b"")
    if isinstance(subscription_raw, (bytes, bytearray, memoryview)):
        info["subscription"] = _parse_full_symbol_subscription(bytes(subscription_raw))
    trade = info.get("trade", {})
    if isinstance(trade, dict):
        info["symbol_path"] = trade.get("symbol_path", info.get("symbol_path", ""))
        info["trade_mode"] = trade.get("trade_mode", info.get("trade_mode", 0))
        info["trade_stops_level"] = trade.get("trade_stops_level", info.get("trade_stops_level", 0))
        info["trade_freeze_level"] = trade.get("trade_freeze_level", info.get("trade_freeze_level", 0))
        info["volume_min"] = trade.get("volume_min", info.get("volume_min", 0.0))
        info["volume_max"] = trade.get("volume_max", info.get("volume_max", 0.0))
        info["volume_step"] = trade.get("volume_step", info.get("volume_step", 0.0))
        info["volume_limit"] = trade.get("volume_limit", info.get("volume_limit", 0.0))
        info["margin_initial"] = trade.get("margin_initial", info.get("margin_initial", 0.0))
        info["margin_maintenance"] = trade.get("margin_maintenance", info.get("margin_maintenance", 0.0))
        info["filling_mode"] = trade.get("trade_fill_flags", info.get("filling_mode", 0))
        info["expiration_mode"] = trade.get("trade_time_flags", info.get("expiration_mode", 0))
        info["order_mode"] = trade.get("trade_order_flags", info.get("order_mode", 0))
    info["trade_face_value"] = info.get("face_value", 0.0)
    info["trade_accrued_interest"] = info.get("accrued_interest", 0.0)
    return info


def _tick_matches_copy_flags(tick: Record, flags: int) -> bool:
    if flags == COPY_TICKS_ALL:
        return True
    has_quote = bool(float(tick.get("bid", 0.0) or 0.0) or float(tick.get("ask", 0.0) or 0.0))
    has_trade = bool(float(tick.get("last", 0.0) or 0.0) or int(tick.get("tick_volume", 0) or 0))
    if flags & COPY_TICKS_INFO and has_quote:
        return True
    return bool(flags & COPY_TICKS_TRADE and has_trade)


def _to_copy_tick_record(tick: Record) -> Record:
    return {
        "time": int(tick.get("tick_time", 0) or 0),
        "time_msc": int(tick.get("tick_time_ms", 0) or 0),
        "bid": float(tick.get("bid", 0.0) or 0.0),
        "ask": float(tick.get("ask", 0.0) or 0.0),
        "last": float(tick.get("last", 0.0) or 0.0),
        "volume": int(tick.get("tick_volume", 0) or 0),
        "volume_real": float(tick.get("tick_volume", 0) or 0),
        "flags": int(tick.get("flags", 0) or 0),
        "symbol": tick.get("symbol", ""),
    }


def _validate_requested_volume(symbol_info: Record, volume: float) -> str | None:
    volume_min = float(symbol_info.get("volume_min", 0.0) or 0.0)
    volume_max = float(symbol_info.get("volume_max", 0.0) or 0.0)
    volume_step = float(symbol_info.get("volume_step", 0.0) or 0.0)
    if volume_min > 0.0 and volume < volume_min:
        return f"volume {volume} is below symbol minimum {volume_min}"
    if volume_max > 0.0 and volume > volume_max:
        return f"volume {volume} is above symbol maximum {volume_max}"
    if volume_step > 0.0 and volume_min >= 0.0:
        steps = (volume - volume_min) / volume_step if volume >= volume_min else volume / volume_step
        if abs(steps - round(steps)) > 1e-9:
            return f"volume {volume} does not align with step {volume_step}"
    return None


def _validate_requested_stops(symbol_info: Record, request: Record, reference_price: float) -> str | None:
    point = float(symbol_info.get("point", 0.0) or 0.0)
    stops_level = float(symbol_info.get("trade_stops_level", 0.0) or 0.0)
    if point <= 0.0 or stops_level <= 0.0:
        return None
    min_distance = point * stops_level
    for label in ("sl", "tp"):
        value = float(request.get(label, 0.0) or 0.0)
        if value > 0.0 and abs(reference_price - value) < min_distance:
            return f"{label} is closer than symbol stop level"
    if int(request.get("action", 0) or 0) == TRADE_ACTION_PENDING:
        trigger = float(request.get("price_trigger", 0.0) or 0.0)
        if trigger > 0.0 and abs(reference_price - trigger) < min_distance:
            return "pending trigger is closer than symbol stop level"
    return None


def _parse_tick_batch(body: bytes | None, symbols_by_id: dict[int, SymbolInfo]) -> RecordList:
    if not body:
        return []
    tick_size = get_series_size(TICK_SCHEMA)
    count = len(body) // tick_size
    ticks: RecordList = []
    for idx in range(count):
        vals = SeriesCodec.parse_at(body, TICK_SCHEMA, idx * tick_size)
        tick = dict(zip(TICK_FIELD_NAMES, vals))
        tick["tick_time_ms"] = tick["tick_time"] * 1000 + tick["time_ms_delta"]
        sym_info = symbols_by_id.get(int(tick["symbol_id"]))
        if sym_info:
            tick["symbol"] = sym_info.name
        ticks.append(tick)
    return ticks


def _parse_book_entries(body: bytes | None, symbols_by_id: dict[int, SymbolInfo]) -> RecordList:
    if not body or len(body) < 4:
        return []
    header_size = get_series_size(BOOK_HEADER_SCHEMA)
    level_size = get_series_size(BOOK_LEVEL_SCHEMA)
    count = struct.unpack_from("<I", body, 0)[0]
    entries: RecordList = []
    offset = 4
    for _ in range(count):
        if offset + header_size > len(body):
            break
        header_vals = SeriesCodec.parse_at(body, BOOK_HEADER_SCHEMA, offset)
        header = dict(zip(BOOK_HEADER_FIELD_NAMES, header_vals))
        offset += header_size
        total_levels = int(header["bid_count"]) + int(header["ask_count"])
        levels: RecordList = []
        for _ in range(total_levels):
            if offset + level_size > len(body):
                break
            level_vals = SeriesCodec.parse_at(body, BOOK_LEVEL_SCHEMA, offset)
            levels.append(dict(zip(BOOK_LEVEL_FIELD_NAMES, level_vals)))
            offset += level_size
        entry: Record = {
            "symbol_id": header["symbol_id"],
            "bids": levels[: int(header["bid_count"])],
            "asks": levels[int(header["bid_count"]):],
        }
        sym_info = symbols_by_id.get(int(header["symbol_id"]))
        if sym_info:
            entry["symbol"] = sym_info.name
        entries.append(entry)
    return entries


def _parse_f64_array(buffer: bytes, expected_count: int) -> list[float]:
    if not buffer:
        return []
    count = min(expected_count, len(buffer) // 8)
    if count <= 0:
        return []
    return list(struct.unpack(f"<{count}d", buffer[: count * 8]))


def _parse_account_trade_settings(body: bytes, offset: int) -> tuple[RecordList, int]:
    items: RecordList = []
    record_size = get_series_size(ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA)
    if len(body) < offset + 4:
        return items, offset
    count = struct.unpack_from("<I", body, offset)[0]
    if count <= 0:
        return items, offset
    offset += 4
    for _ in range(count):
        if offset + record_size > len(body):
            break
        items.append(_parse_account_trade_settings_record(body, offset))
        offset += record_size
    return items, offset


def _parse_account_trade_settings_record(body: bytes, offset: int) -> Record:
    values = SeriesCodec.parse_at(body, ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA, offset)
    item = dict(zip(ACCOUNT_WEB_TRADE_SETTINGS_FIELD_NAMES, values))
    item["margin_rates_initial"] = _parse_f64_array(item["margin_rates_initial"], 8)
    item["margin_rates_maintenance"] = _parse_f64_array(item["margin_rates_maintenance"], 8)
    item["swap_rates"] = _parse_f64_array(item["swap_rates"], 7)
    return item


def _parse_account_leverage_rules(body: bytes, offset: int) -> tuple[RecordList, int]:
    rules: RecordList = []
    rule_size = get_series_size(ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA)
    tier_size = get_series_size(ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA)
    if len(body) < offset + 4:
        return rules, offset
    count = struct.unpack_from("<i", body, offset)[0]
    offset += 4
    for _ in range(max(count, 0)):
        if offset + rule_size > len(body):
            break
        values = SeriesCodec.parse_at(body, ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA, offset)
        rule = dict(zip(ACCOUNT_WEB_LEVERAGE_RULE_FIELD_NAMES, values))
        offset += rule_size
        tiers: RecordList = []
        for _ in range(max(int(rule.get("tier_count", 0) or 0), 0)):
            if offset + tier_size > len(body):
                break
            tier_values = SeriesCodec.parse_at(body, ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA, offset)
            tiers.append(dict(zip(ACCOUNT_WEB_LEVERAGE_TIER_FIELD_NAMES, tier_values)))
            offset += tier_size
        rule["tiers"] = tiers
        rules.append(rule)
    return rules, offset


def _parse_account_commissions(body: bytes, offset: int) -> tuple[RecordList, int]:
    commissions: RecordList = []
    commission_size = get_series_size(ACCOUNT_WEB_COMMISSION_SCHEMA)
    tier_size = get_series_size(ACCOUNT_WEB_COMMISSION_TIER_SCHEMA)
    if len(body) < offset + 4:
        return commissions, offset
    count = struct.unpack_from("<I", body, offset)[0]
    offset += 4
    for _ in range(count):
        if offset + commission_size > len(body):
            break
        values = SeriesCodec.parse_at(body, ACCOUNT_WEB_COMMISSION_SCHEMA, offset)
        commission = dict(zip(ACCOUNT_WEB_COMMISSION_FIELD_NAMES, values))
        offset += commission_size
        tiers: RecordList = []
        if offset + 4 > len(body):
            commission["tiers"] = tiers
            commissions.append(commission)
            break
        tier_count = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        for _ in range(tier_count):
            if offset + tier_size > len(body):
                break
            tier_values = SeriesCodec.parse_at(body, ACCOUNT_WEB_COMMISSION_TIER_SCHEMA, offset)
            tiers.append(dict(zip(ACCOUNT_WEB_COMMISSION_TIER_FIELD_NAMES, tier_values)))
            offset += tier_size
        commission["tiers"] = tiers
        commissions.append(commission)
    return commissions, offset


def _parse_account_response(body: bytes) -> Record:
    """Parse the cmd=3 / cmd=14 account response.

    The current frontend parses cmd=3 with a 26-field account header followed
    by trade settings, leverage rules, and commission tables.
    """

    min_size = get_series_size(ACCOUNT_WEB_MAIN_SCHEMA)
    if not body or len(body) < min_size:
        return {}
    try:
        values = SeriesCodec.parse(body, ACCOUNT_WEB_MAIN_SCHEMA)
        result = dict(zip(ACCOUNT_WEB_MAIN_FIELD_NAMES, values))
        result["auth_password_min"] = int(result.get("auth_password_min", 0) or 0) or 8
        offset = min_size
        trade_settings, offset = _parse_account_trade_settings(body, offset)
        result["trade_settings"] = trade_settings
        result["trade_flags"] = 0
        if len(body) >= offset + 4:
            result["trade_flags"] = struct.unpack_from("<i", body, offset)[0]
            offset += 4
        result["symbols_count"] = 0
        if len(body) >= offset + 4:
            result["symbols_count"] = struct.unpack_from("<i", body, offset)[0]
            offset += 4
        result["leverage_flags"] = 0
        if len(body) >= offset + 8:
            result["leverage_flags"] = struct.unpack_from("<Q", body, offset)[0]
            offset += 8
        leverage_rules, offset = _parse_account_leverage_rules(body, offset)
        commissions, _ = _parse_account_commissions(body, offset)
        result["leverage_rules"] = leverage_rules
        result["rules"] = leverage_rules
        result["commissions"] = commissions
        result["currency"] = result.get("account_currency", "")
        result["leverage"] = int(result.get("margin_leverage", 0) or 0)
        result["name"] = result.get("account_name", "")
        result["server"] = result.get("server_name", "")
        result["daylight_mode"] = int(result.get("daylightmode", 0) or 0)
        result["server_offset_time"] = int(result.get("timezone_shift", 0) or 0) + (
            int(result.get("daylight_mode", 0) or 0) * 3600
        )
        account_type_value = result.get("account_type")
        account_type = -1 if account_type_value is None else int(account_type_value)
        result["is_real"] = account_type == 0
        result["is_demo"] = account_type == 1
        result["is_contest"] = account_type == 2
        result["is_preliminary"] = account_type == 3
        result["trade_allowed"] = not bool(int(result.get("rights", 0) or 0) & 4)
        result["is_investor"] = bool(int(result.get("rights", 0) or 0) & 8)
        result["is_read_only"] = bool(int(result.get("rights", 0) or 0) & 512)
        result["is_reset_pass"] = bool(int(result.get("rights", 0) or 0) & 1024)
        result["is_trade_disabled"] = not result["trade_allowed"]
        result["is_hedged_margin"] = int(result.get("margin_mode", 0) or 0) == 2
        result["risk_warning"] = bool(int(result.get("permissions_flags", 0) or 0) & 16)
        result["profit"] = float(result.get("acc_profit", 0.0) or 0.0)
        result["equity"] = (
            float(result.get("balance", 0.0) or 0.0)
            + float(result.get("credit", 0.0) or 0.0)
            + float(result.get("profit", 0.0) or 0.0)
        )
    except Exception as exc:
        logger.debug("account response parse failed: %s", exc)
        return {}
    return result


def _parse_verification_status(body: bytes | None) -> VerificationStatus:
    if not body or len(body) < 2:
        return VerificationStatus()
    email, phone = SeriesCodec.parse(body, VERIFICATION_STATUS_SCHEMA)
    return VerificationStatus(bool(email), bool(phone))


def _parse_open_account_result(body: bytes | None) -> OpenAccountResult:
    if not body or len(body) < get_series_size(OPEN_ACCOUNT_RESPONSE_SCHEMA):
        return OpenAccountResult(code=-1, login=0, password="", investor_password="")
    code, login, password, investor_password = SeriesCodec.parse(body, OPEN_ACCOUNT_RESPONSE_SCHEMA)
    return OpenAccountResult(
        code=int(code),
        login=int(login),
        password=str(password),
        investor_password=str(investor_password),
    )


def _parse_rate_bars(body: bytes | None) -> RecordList:
    if not body:
        return []
    bar_size_std = get_series_size(RATE_BAR_SCHEMA)
    bar_size_ext = get_series_size(RATE_BAR_SCHEMA_EXT)
    if len(body) % bar_size_ext == 0 and bar_size_ext != bar_size_std:
        schema, names, bar_size = RATE_BAR_SCHEMA_EXT, RATE_BAR_FIELD_NAMES_EXT, bar_size_ext
    else:
        schema, names, bar_size = RATE_BAR_SCHEMA, RATE_BAR_FIELD_NAMES, bar_size_std
    count = len(body) // bar_size
    bars = []
    offset = 0
    for _ in range(count):
        if offset + bar_size > len(body):
            break
        vals = SeriesCodec.parse_at(body, schema, offset)
        bars.append(dict(zip(names, vals)))
        offset += bar_size
    return bars


def _parse_counted_records(
    body: bytes,
    schema: list[dict[str, int]],
    field_names: list[str],
) -> RecordList:
    if not body or len(body) < 4:
        return []
    count = struct.unpack_from("<I", body, 0)[0]
    record_size = get_series_size(schema)
    offset = 4
    records = []
    for _ in range(count):
        if offset + record_size > len(body):
            break
        vals = SeriesCodec.parse_at(body, schema, offset)
        records.append(dict(zip(field_names, vals)))
        offset += record_size
    return records
