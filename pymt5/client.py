import asyncio
import logging
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable

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
    CMD_PING,
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
    DEFAULT_WS_URI,
    ORDER_FILLING_FOK,
    ORDER_TIME_GTC,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    ORDER_TYPE_SELL_STOP_LIMIT,
    PERIOD_MAP,
    POSITION_TYPE_BUY,
    POSITION_TYPE_SELL,
    PROP_BYTES,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I32,
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
    TRADE_RETCODE_PLACED,
)
from pymt5.helpers import build_client_id, bytes_to_hex
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    ACCOUNT_BASE_FIELD_NAMES,
    ACCOUNT_BASE_SCHEMA,
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
    SYMBOL_GROUP_FIELD_NAMES,
    SYMBOL_GROUP_SCHEMA,
    TICK_FIELD_NAMES,
    TICK_SCHEMA,
    TRADE_REQUEST_SCHEMA,
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


class MT5WebClient:
    def __init__(
        self,
        uri: str = DEFAULT_WS_URI,
        timeout: float = 30.0,
        heartbeat_interval: float = 30.0,
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
        self._logged_in = False
        # Reconnect settings
        self._auto_reconnect = auto_reconnect
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._reconnect_task: asyncio.Task | None = None
        self._closing = False
        # Stored credentials for reconnect
        self._login_kwargs: dict | None = None
        self._subscribed_ids: list[int] = []
        # User disconnect callback
        self._on_disconnect: Callable[[], None] | None = None
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
        logger.info("connected to %s", self.uri)
        return self

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
        self._closing = False
        logger.info("connection closed")

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
        self._stop_heartbeat()
        logger.warning("disconnected from server")
        if self._on_disconnect:
            self._on_disconnect()
        if self._auto_reconnect and not self._closing and self._login_kwargs:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Try to reconnect and re-login with stored credentials."""
        for attempt in range(1, self._max_reconnect_attempts + 1):
            logger.info("reconnect attempt %d/%d", attempt, self._max_reconnect_attempts)
            await asyncio.sleep(self._reconnect_delay * attempt)
            try:
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
                logger.info("reconnected successfully on attempt %d", attempt)
                return
            except Exception as exc:
                logger.warning("reconnect attempt %d failed: %s", attempt, exc)
        logger.error("all %d reconnect attempts exhausted", self._max_reconnect_attempts)

    async def send_raw_command(self, command: int, payload: bytes | None = None) -> CommandResult:
        return await self.transport.send_command(command, payload or b"")

    async def init_session(
        self,
        version: int = 0,
        password: str = "",
        otp: str = "",
        cid: bytes | None = None,
    ) -> CommandResult:
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

    async def get_account(self) -> Record:
        """Get full account information (cmd=3): balance, equity, margin, leverage, etc.

        Returns a dict with all account fields. This is the proper way to get
        balance/equity/margin information from the Web Terminal.

        The response has a complex multi-section format (header + trade settings).
        """
        result = await self.transport.send_command(CMD_GET_ACCOUNT)
        return _parse_account_response(result.body)

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
        payload = struct.pack(f"<{len(symbol_ids)}I", *symbol_ids)
        await self.transport.send_command(CMD_SUBSCRIBE_BOOK, payload)
        logger.info("subscribed to order book for %d symbols", len(symbol_ids))

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

    # ---- Symbol Cache ----

    async def load_symbols(self, use_gzip: bool = True) -> dict[str, SymbolInfo]:
        """Load symbols and build internal cache for name→id lookup."""
        raw = await self.get_symbols(use_gzip=use_gzip)
        self._symbols.clear()
        self._symbols_by_id.clear()
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
            full_sym_size = get_series_size(FULL_SYMBOL_SCHEMA)
            if len(result.body) >= full_sym_size:
                vals = SeriesCodec.parse(result.body, FULL_SYMBOL_SCHEMA)
                return dict(zip(FULL_SYMBOL_FIELD_NAMES, vals))
            # Fallback: try to parse as counted records
            records = _parse_counted_records(result.body, FULL_SYMBOL_SCHEMA, FULL_SYMBOL_FIELD_NAMES)
            return records[0] if records else None
        except Exception as exc:
            logger.warning("get_full_symbol_info(%s) failed: %s", symbol, exc)
            return None

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

    def on_tick(self, callback: Callable[[RecordList], None]) -> None:
        tick_size = get_series_size(TICK_SCHEMA)
        def _handler(result: CommandResult) -> None:
            count = len(result.body) // tick_size
            ticks = []
            for i in range(count):
                vals = SeriesCodec.parse_at(result.body, TICK_SCHEMA, i * tick_size)
                d = dict(zip(TICK_FIELD_NAMES, vals))
                d["tick_time_ms"] = d["tick_time"] * 1000 + d["time_ms_delta"]
                # Resolve symbol name from cache if available
                sym_info = self._symbols_by_id.get(d["symbol_id"])
                if sym_info:
                    d["symbol"] = sym_info.name
                ticks.append(d)
            callback(ticks)
        self.transport.on(CMD_TICK_PUSH, _handler)

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

    def on_position_update(self, callback: Callable[[RecordList], None]) -> None:
        """Register callback for position change push notifications.

        The server pushes cmd=4 data when positions or orders change.
        """
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                positions = _parse_counted_records(body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
                callback(positions)
            except Exception as exc:
                logger.error("position update parse error: %s", exc)
        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)

    def on_order_update(self, callback: Callable[[RecordList], None]) -> None:
        """Register callback for order change push notifications.

        Parses orders from the same cmd=4 push.
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

    def on_trade_update(self, callback: Callable[[dict[str, RecordList]], None]) -> None:
        """Register callback for combined position+order push notifications.

        Parses both positions and orders from cmd=4 push into a single dict
        with keys 'positions' and 'orders'. More efficient than registering
        separate on_position_update and on_order_update handlers.
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

    def on_symbol_update(self, callback: Callable[[CommandResult], None]) -> None:
        """Register callback for symbol update push notifications (cmd=13).

        The server pushes symbol changes (e.g. spread updates, trading hours).
        Raw CommandResult is passed since the exact schema varies.
        """
        self.transport.on(CMD_SYMBOL_UPDATE_PUSH, callback)

    def on_account_update(self, callback: Callable[[Record], None]) -> None:
        """Register callback for account update push notifications (cmd=14).

        Server pushes account balance/margin/equity changes in real-time.
        Callback receives a dict with the same fields as get_account().
        """
        def _handler(result: CommandResult) -> None:
            try:
                data = _parse_account_response(result.body)
                callback(data)
            except Exception as exc:
                logger.error("account update parse error: %s", exc)
        self.transport.on(CMD_ACCOUNT_UPDATE_PUSH, _handler)

    def on_login_status(self, callback: Callable[[CommandResult], None]) -> None:
        """Register callback for login status push notifications (cmd=15).

        The server may push login status changes (e.g. forced logout, session expiry).
        """
        self.transport.on(CMD_LOGIN_STATUS_PUSH, callback)

    def on_symbol_details(self, callback: Callable[[RecordList], None]) -> None:
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

    def on_trade_result(self, callback: Callable[[Record], None]) -> None:
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

    def on_trade_transaction(self, callback: Callable[[Record], None]) -> None:
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

    def on_book_update(self, callback: Callable[[RecordList], None]) -> None:
        """Register callback for order book / DOM push (cmd=23).

        Receives list of dicts, each with 'symbol_id', 'bids', 'asks'.
        bids/asks are lists of {'price': float, 'volume': int}.
        """
        header_size = get_series_size(BOOK_HEADER_SCHEMA)
        level_size = get_series_size(BOOK_LEVEL_SCHEMA)
        def _handler(result: CommandResult) -> None:
            try:
                body = result.body
                if len(body) < 4:
                    return
                count = struct.unpack_from("<I", body, 0)[0]
                entries = []
                offset = 4
                for _ in range(count):
                    if offset + header_size > len(body):
                        break
                    hdr_vals = SeriesCodec.parse_at(body, BOOK_HEADER_SCHEMA, offset)
                    hdr = dict(zip(BOOK_HEADER_FIELD_NAMES, hdr_vals))
                    offset += header_size
                    total_levels = hdr["bid_count"] + hdr["ask_count"]
                    levels = []
                    for _ in range(total_levels):
                        if offset + level_size > len(body):
                            break
                        lv = SeriesCodec.parse_at(body, BOOK_LEVEL_SCHEMA, offset)
                        levels.append(dict(zip(BOOK_LEVEL_FIELD_NAMES, lv)))
                        offset += level_size
                    bids = levels[:hdr["bid_count"]]
                    asks = levels[hdr["bid_count"]:]
                    entry: dict[str, Any] = {"symbol_id": hdr["symbol_id"], "bids": bids, "asks": asks}
                    sym_info = self._symbols_by_id.get(hdr["symbol_id"])
                    if sym_info:
                        entry["symbol"] = sym_info.name
                    entries.append(entry)
                callback(entries)
            except Exception as exc:
                logger.error("book update parse error: %s", exc)
        self.transport.on(CMD_BOOK_PUSH, _handler)

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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_BUY,
            type_filling=filling,
            price_sl=sl,
            price_tp=tp,
            deviation=deviation,
            comment=comment,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_SELL,
            type_filling=filling,
            price_sl=sl,
            price_tp=tp,
            deviation=deviation,
            comment=comment,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_BUY_LIMIT,
            type_filling=filling,
            type_time=time_type,
            price_order=price,
            price_sl=sl,
            price_tp=tp,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_SELL_LIMIT,
            type_filling=filling,
            type_time=time_type,
            price_order=price,
            price_sl=sl,
            price_tp=tp,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_BUY_STOP,
            type_filling=filling,
            type_time=time_type,
            price_order=price,
            price_sl=sl,
            price_tp=tp,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_SELL_STOP,
            type_filling=filling,
            type_time=time_type,
            price_order=price,
            price_sl=sl,
            price_tp=tp,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_BUY_STOP_LIMIT,
            type_filling=filling,
            type_time=time_type,
            price_order=stop_limit_price,
            price_trigger=price,
            price_sl=sl,
            price_tp=tp,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
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
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ORDER_TYPE_SELL_STOP_LIMIT,
            type_filling=filling,
            type_time=time_type,
            price_order=stop_limit_price,
            price_trigger=price,
            price_sl=sl,
            price_tp=tp,
            comment=comment,
            time_expiration=expiration,
            type_reason=magic,
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
        client_id = cid or build_client_id(
            platform=os.name,
            device_pixel_ratio="1",
            language="en-US",
            screen="0x0",
        )
        if len(client_id) != 16:
            raise ValueError(f"cid must be 16 bytes, got {len(client_id)}")
        if len(password) == 320:
            password_prefix = ""
            password_blob = bytes.fromhex(password)
        else:
            password_prefix = (password or "")[:32]
            password_blob = bytes(160)
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
            (PROP_BYTES, password_blob, 160),
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
        client_id = cid or build_client_id(
            platform=os.name,
            device_pixel_ratio="1",
            language="en-US",
            screen="0x0",
        )
        if len(client_id) != 16:
            raise ValueError(f"cid must be 16 bytes, got {len(client_id)}")
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


def _parse_account_response(body: bytes) -> Record:
    """Parse the cmd=3 / cmd=14 account response.

    The response has a complex multi-section format:
    - byte 0: u8 flags
    - offset 1: u32 param (possibly related to account group)
    - offset 5: u32 param2
    - offset 9: f64 balance
    - offset 17: f64 credit
    - offset 25: fixed_string[64] currency (UTF-16LE)
    - offset 89: u32 trade_mode
    - offset 93: u32 leverage
    - offset 97: fixed_string name (UTF-16LE, variable end)
    Additional sections follow for trade settings and margin tiers.
    """
    from pymt5.helpers import decode_utf16le

    if not body or len(body) < 97:
        return {}
    result: Record = {}
    try:
        result["balance"] = struct.unpack_from("<d", body, 9)[0]
        result["credit"] = struct.unpack_from("<d", body, 17)[0]
        result["currency"] = decode_utf16le(body[25:89])
        result["trade_mode"] = struct.unpack_from("<I", body, 89)[0]
        result["leverage"] = struct.unpack_from("<I", body, 93)[0]
        if len(body) > 97:
            name_end = min(97 + 128, len(body))
            result["name"] = decode_utf16le(body[97:name_end])
        # Equity = balance + credit + floating P/L (not directly in response header)
        result["equity"] = result["balance"] + result["credit"]
    except Exception as exc:
        logger.debug("account response partial parse: %s", exc)
    return result


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
