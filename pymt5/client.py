import asyncio
import logging
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable

from pymt5.constants import (
    CMD_CHANGE_PASSWORD,
    CMD_GET_FULL_SYMBOLS,
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_RATES,
    CMD_GET_SYMBOLS,
    CMD_GET_SYMBOLS_GZIP,
    CMD_GET_TRADE_HISTORY,
    CMD_INIT,
    CMD_LOGIN,
    CMD_LOGIN_STATUS_PUSH,
    CMD_LOGOUT,
    CMD_PING,
    CMD_SUBSCRIBE_TICKS,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_REQUEST,
    CMD_TRADER_PARAMS,
    DEFAULT_WS_URI,
    ORDER_FILLING_FOK,
    ORDER_TIME_GTC,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    PERIOD_MAP,
    PROP_BYTES,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I32,
    PROP_U16,
    PROP_U32,
    PROP_U64,
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
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    FULL_SYMBOL_FIELD_NAMES,
    FULL_SYMBOL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
    RATE_BAR_FIELD_NAMES,
    RATE_BAR_SCHEMA,
    SYMBOL_BASIC_FIELD_NAMES,
    SYMBOL_BASIC_SCHEMA,
    TICK_FIELD_NAMES,
    TICK_SCHEMA,
    TRADE_REQUEST_SCHEMA,
)
from pymt5.transport import CommandResult, MT5WebSocketTransport

logger = logging.getLogger("pymt5.client")


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

    async def get_symbols(self, use_gzip: bool = True) -> list[dict]:
        if use_gzip:
            result = await self.transport.send_command(CMD_GET_SYMBOLS_GZIP)
            if result.body and len(result.body) > 4:
                compressed = bytes(result.body[4:])
                raw = zlib.decompress(compressed)
                return _parse_counted_records(raw, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)
            return []
        result = await self.transport.send_command(CMD_GET_SYMBOLS)
        return _parse_counted_records(result.body, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)

    async def get_full_symbol_info(self, symbol: str) -> dict | None:
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
        payload = struct.pack(f"<{len(symbol_ids) + 1}I", len(symbol_ids), *symbol_ids)
        await self.transport.send_command(CMD_SUBSCRIBE_TICKS, payload)
        self._subscribed_ids = list(symbol_ids)
        logger.info("subscribed to %d symbol ids for ticks", len(symbol_ids))

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

    def on_tick(self, callback: Callable[[list[dict]], None]) -> None:
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
    ) -> list[dict]:
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

    async def get_positions_and_orders(self) -> dict:
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
    ) -> dict:
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

    async def get_positions(self) -> list[dict]:
        """Get open positions only."""
        data = await self.get_positions_and_orders()
        return data["positions"]

    async def get_orders(self) -> list[dict]:
        """Get pending orders only."""
        data = await self.get_positions_and_orders()
        return data["orders"]

    async def get_deals(self, from_ts: int = 0, to_ts: int = 0) -> list[dict]:
        """Get closed deals only."""
        data = await self.get_trade_history(from_ts, to_ts)
        return data["deals"]

    async def get_account_summary(self) -> AccountInfo:
        """Compute account summary from current positions.

        Note: balance is estimated from trader_params + deal history if available.
        Profit and equity are computed from open positions.
        This is a best-effort approximation — the MT5 Web Terminal protocol
        does not expose a dedicated account_info command.
        """
        data = await self.get_positions_and_orders()
        positions = data["positions"]
        orders = data["orders"]
        floating_profit = sum(p.get("profit", 0.0) for p in positions)
        floating_commission = sum(p.get("commission", 0.0) for p in positions)
        floating_swap = sum(p.get("storage", 0.0) for p in positions)
        total_profit = floating_profit + floating_commission + floating_swap
        return AccountInfo(
            profit=total_profit,
            positions_count=len(positions),
            orders_count=len(orders),
        )

    # ---- Position / Order Push Handling ----

    def on_position_update(self, callback: Callable[[list[dict]], None]) -> None:
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

    def on_order_update(self, callback: Callable[[list[dict]], None]) -> None:
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
        success = retcode in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL, TRADE_RETCODE_PLACED)
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
    def _volume_to_lots(volume: float, digits: int = 2) -> int:
        """Convert lots (e.g. 0.01) to MT5 integer volume (e.g. 100 for 2-digit lots).

        MT5 Web Terminal uses integer volumes where the value represents
        volume * 10^volume_digits. For most brokers volume_digits=2,
        meaning 0.01 lot = 100, 0.1 lot = 1000, 1.0 lot = 10000.
        """
        return int(round(volume * (10 ** digits)))

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

        If order_type is not specified, it defaults to SELL for closing a BUY position.
        """
        d = self._resolve_digits(symbol, digits)
        ot = order_type if order_type is not None else ORDER_TYPE_SELL
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


def _parse_rate_bars(body: bytes) -> list[dict]:
    if not body:
        return []
    bar_size = get_series_size(RATE_BAR_SCHEMA)
    count = len(body) // bar_size
    bars = []
    offset = 0
    for _ in range(count):
        if offset + bar_size > len(body):
            break
        vals = SeriesCodec.parse_at(body, RATE_BAR_SCHEMA, offset)
        bars.append(dict(zip(RATE_BAR_FIELD_NAMES, vals)))
        offset += bar_size
    return bars


def _parse_counted_records(
    body: bytes, schema: list[dict], field_names: list[str]
) -> list[dict]:
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
