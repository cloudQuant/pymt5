"""Push event handler mixin for MT5WebClient.

Provides callback registration methods for real-time server push
notifications: ticks, positions, orders, trades, symbols, account
updates, login status, book updates, and trade results.
"""

from __future__ import annotations

import inspect
import struct
from collections import deque
from collections.abc import Awaitable, Callable, Generator
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pymt5._parsers import (
    _extract_tick_wire_presence,
    _parse_account_response,
    _parse_book_entries,
    _parse_counted_records,
    _parse_tick_batch,
)
from pymt5.constants import (
    CMD_ACCOUNT_UPDATE_PUSH,
    CMD_BOOK_PUSH,
    CMD_GET_POSITIONS_ORDERS,
    CMD_LOGIN_STATUS_PUSH,
    CMD_SYMBOL_DETAILS_PUSH,
    CMD_SYMBOL_UPDATE_PUSH,
    CMD_TICK_PUSH,
    CMD_TRADE_RESULT_PUSH,
    CMD_TRADE_UPDATE_PUSH,
)
from pymt5.events import AccountEvent, BookEvent, TickEvent, TradeResultEvent
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
    SYMBOL_DETAILS_6320_FIELD_NAMES,
    SYMBOL_DETAILS_6320_SCHEMA,
    SYMBOL_DETAILS_FIELD_NAMES,
    SYMBOL_DETAILS_SCHEMA,
    TRADE_RESULT_PUSH_FIELD_NAMES,
    TRADE_RESULT_PUSH_SCHEMA,
    TRADE_RESULT_RESPONSE_FIELD_NAMES,
    TRADE_RESULT_RESPONSE_SCHEMA,
    TRADE_TRANSACTION_FIELD_NAMES,
    TRADE_TRANSACTION_SCHEMA,
    TRADE_UPDATE_BALANCE_FIELD_NAMES,
    TRADE_UPDATE_BALANCE_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import Record, RecordList, TickPriceMode

if TYPE_CHECKING:
    from pymt5.transport import MT5WebSocketTransport
    from pymt5.types import SymbolInfo

from pymt5._logging import get_logger

logger = get_logger("pymt5.client")

# Specific exceptions for push handler parsing failures
_PARSE_ERRORS = (struct.error, KeyError, ValueError, TypeError, IndexError)
_EventT = TypeVar("_EventT")


class _SequentialAsyncCallbacks(Generic[_EventT]):
    """Awaitable sequence that closes an unstarted first awaitable if discarded."""

    def __init__(
        self,
        first: Awaitable[Any],
        callback: Callable[[_EventT], Any],
        rest: list[_EventT],
    ) -> None:
        self._first = first
        self._callback = callback
        self._rest = rest
        self._started = False

    def close(self) -> None:
        """Close the eagerly created first awaitable if never awaited."""
        if not self._started:
            self._started = True
            close = getattr(self._first, "close", None)
            if callable(close):
                close()

    def __await__(self) -> Generator[Any, None, None]:
        return self._run().__await__()

    async def _run(self) -> None:
        self._started = True
        await self._first
        for item in self._rest:
            next_maybe = self._callback(item)
            if inspect.isawaitable(next_maybe):
                await next_maybe


def _dispatch_event_list(callback: Callable[[_EventT], Any], events: list[_EventT]) -> Awaitable[None] | None:
    """Invoke ``callback`` for each event, switching to async sequential awaiting if needed."""
    for idx, event in enumerate(events):
        maybe = callback(event)
        if inspect.isawaitable(maybe):
            return _SequentialAsyncCallbacks(maybe, callback, events[idx + 1 :])
    return None


def _parse_optional_counted_segment(
    body: bytes,
    offset: int,
    schema: list[dict[str, int]],
    field_names: list[str],
    segment_name: str,
) -> tuple[RecordList, int] | None:
    """Parse an optional counted array segment, returning ``None`` if truncated."""
    if offset >= len(body):
        return [], offset
    if len(body) - offset < 4:
        logger.error("trade transaction push parse error: truncated %s header", segment_name)
        return None
    count = struct.unpack_from("<I", body, offset)[0]
    record_size = get_series_size(schema)
    if offset + 4 + count * record_size > len(body):
        logger.error("trade transaction push parse error: truncated %s payload", segment_name)
        return None
    records = _parse_counted_records(body[offset:], schema, field_names)
    return records, offset + 4 + count * record_size


def _parse_trade_result_payload(body: bytes) -> dict[str, Any]:
    """Parse action and optional response sections from a ``cmd=19`` push body."""
    action_size = get_series_size(TRADE_RESULT_PUSH_SCHEMA)
    resp_size = get_series_size(TRADE_RESULT_RESPONSE_SCHEMA)
    data: dict[str, Any] = {}
    if len(body) >= action_size:
        vals = SeriesCodec.parse(body, TRADE_RESULT_PUSH_SCHEMA)
        data.update(zip(TRADE_RESULT_PUSH_FIELD_NAMES, vals))
    if len(body) >= action_size + resp_size:
        resp_vals = SeriesCodec.parse_at(body, TRADE_RESULT_RESPONSE_SCHEMA, action_size)
        data["result"] = dict(zip(TRADE_RESULT_RESPONSE_FIELD_NAMES, resp_vals))
    return data


def _parse_orders_after_positions(body: bytes) -> RecordList:
    """Parse the orders section that follows positions in a ``cmd=4`` frame."""
    pos_size = get_series_size(POSITION_SCHEMA)
    pos_count = struct.unpack_from("<I", body, 0)[0] if body else 0
    order_offset = 4 + pos_count * pos_size
    return _parse_counted_records(body[order_offset:], ORDER_SCHEMA, ORDER_FIELD_NAMES)


class _PushHandlersMixin:
    """Mixin providing push event handler registration for MT5WebClient."""

    # Attributes provided by MT5WebClient.__init__
    transport: MT5WebSocketTransport
    _symbols_by_id: dict[int, SymbolInfo]
    _tick_cache_by_id: dict[int, Record]
    _tick_cache_by_name: dict[str, Record]
    _tick_history_limit: int
    _max_tick_symbols: int
    _tick_history_by_id: dict[int, deque[Record]]
    _tick_history_by_name: dict[str, deque[Record]]
    _tick_history_access_order: list[int]
    _tick_price_mode: TickPriceMode
    _tick_price_modes: dict[str, TickPriceMode]
    _tick_price_warning_keys: set[tuple[str, str]]
    _book_cache_by_id: dict[int, Record]
    _book_cache_by_name: dict[str, Record]
    # Typed handler lists (Phase 16.1)
    _typed_tick_handlers: list[Callable]
    _typed_book_handlers: list[Callable]
    _typed_trade_result_handlers: list[Callable]
    _typed_account_handlers: list[Callable]
    # Callback error handlers (Phase 16.2)
    _callback_error_handlers: list[Callable]

    def _parse_ticks(self, body: bytes | None) -> RecordList:
        """Parse every tick delivery path with the same client configuration."""
        warning_keys = getattr(self, "_tick_price_warning_keys", None)
        if warning_keys is None:
            warning_keys = set()
            self._tick_price_warning_keys = warning_keys

        def report_auto_decision(symbol: str, decision: str, raw: float, parsed: float) -> None:
            key = (symbol or "<unknown>", "auto")
            if key in warning_keys:
                return
            warning_keys.add(key)
            logger.warning(
                "tick price auto mode chose %s for %s (raw=%r parsed=%r); "
                "this is a compatibility heuristic, configure an explicit tick price mode after wire-format sampling",
                decision,
                symbol or "<unknown>",
                raw,
                parsed,
            )

        return _parse_tick_batch(
            body,
            self._symbols_by_id,
            self._tick_cache_by_id,
            getattr(self, "_tick_price_mode", "auto"),
            getattr(self, "_tick_price_modes", {}),
            report_auto_decision,
        )

    def on_tick(self, callback: Callable[[RecordList], Awaitable[None] | None]) -> Callable:
        """Register a callback for decoded tick batches.

        Args:
            callback: Callable (sync or async) invoked with the list of ticks
                decoded from each tick command frame. Parsing failures are
                logged and are not propagated to the transport dispatch loop.

        Returns:
            The internal transport handler, suitable for passing to
            :meth:`MT5WebSocketTransport.off` when unregistering.

        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                ticks = self._parse_ticks(result.body)
            except _PARSE_ERRORS as exc:
                logger.error("tick parse error: %s", exc)
                return None
            return callback(ticks)

        self.transport.on(CMD_TICK_PUSH, _handler)
        return _handler

    def on_position_update(self, callback: Callable[[RecordList], Awaitable[None] | None]) -> Callable:
        """Register a callback for position records from cmd=4 frames.

        Ordinary position/order query responses also invoke this callback.
        An unsolicited cmd=4 change push has not been verified; the callback
        does not distinguish responses from pushes. Returns the internal handler
        for use with transport.off().
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                positions = _parse_counted_records(result.body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
            except _PARSE_ERRORS as exc:
                logger.error("position update parse error: %s", exc)
                return None
            return callback(positions)

        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)
        return _handler

    def on_order_update(self, callback: Callable[[RecordList], Awaitable[None] | None]) -> Callable:
        """Register a callback for order records from cmd=4 frames.

        Ordinary position/order query responses also invoke this callback.
        An unsolicited cmd=4 change push has not been verified; the callback
        does not distinguish responses from pushes. Returns the internal handler
        for use with transport.off().
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                orders = _parse_orders_after_positions(result.body)
            except _PARSE_ERRORS as exc:
                logger.error("order update parse error: %s", exc)
                return None
            return callback(orders)

        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)
        return _handler

    def on_trade_update(self, callback: Callable[[dict[str, RecordList]], Awaitable[None] | None]) -> Callable:
        """Register a callback for combined position/order cmd=4 frames.

        The callback receives a dict with ``positions`` and ``orders`` and is
        also invoked by ordinary position/order query responses. An unsolicited
        cmd=4 change push has not been verified, and this callback does not
        distinguish responses from pushes. Returns the internal handler for use
        with transport.off().
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                body = result.body
                positions = _parse_counted_records(body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
                orders = _parse_orders_after_positions(body)
            except _PARSE_ERRORS as exc:
                logger.error("trade update parse error: %s", exc)
                return None
            return callback({"positions": positions, "orders": orders})

        self.transport.on(CMD_GET_POSITIONS_ORDERS, _handler)
        return _handler

    def on_symbol_update(self, callback: Callable[[CommandResult], Awaitable[None] | None]) -> Callable:
        """Register callback for symbol update push notifications (cmd=13).

        The server pushes symbol changes (e.g. spread updates, trading hours).
        Raw CommandResult is passed since the exact schema varies.
        Returns the callback for use with transport.off().
        """
        self.transport.on(CMD_SYMBOL_UPDATE_PUSH, callback)
        return callback

    def on_account_update(self, callback: Callable[[Record], Awaitable[None] | None]) -> Callable:
        """Register callback for account update push notifications (cmd=14).

        Server pushes account balance/margin/equity changes in real-time.
        Callback receives a dict with the same fields as get_account().
        Returns the internal handler for use with transport.off().
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                data = _parse_account_response(result.body)
            except _PARSE_ERRORS as exc:
                logger.error("account update parse error: %s", exc)
                return None
            return callback(data)

        self.transport.on(CMD_ACCOUNT_UPDATE_PUSH, _handler)
        return _handler

    def on_login_status(self, callback: Callable[[CommandResult], Awaitable[None] | None]) -> Callable:
        """Register callback for login status push notifications (cmd=15).

        The server may push login status changes (e.g. forced logout, session expiry).
        Returns the callback for use with transport.off().
        """
        self.transport.on(CMD_LOGIN_STATUS_PUSH, callback)
        return callback

    def on_symbol_details(self, callback: Callable[[RecordList], Awaitable[None] | None]) -> Callable:
        """Register callback for extended symbol quote data (cmd=17).

        Build 6320 prices are raw wire values: divide by 10 ** symbol digits
        when appropriate. The fields mask indicates presence; an unmarked zero
        must not be interpreted as a current quote. Other builds use the legacy
        layout and are not asserted to match build 6320.
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                build_6320 = self.transport.server_build == 6320
                schema = SYMBOL_DETAILS_6320_SCHEMA if build_6320 else SYMBOL_DETAILS_SCHEMA
                field_names = SYMBOL_DETAILS_6320_FIELD_NAMES if build_6320 else SYMBOL_DETAILS_FIELD_NAMES
                detail_size = get_series_size(schema)
                if build_6320 and len(result.body) % detail_size:
                    logger.error("symbol details body length is not a multiple of %d", detail_size)
                    return None
                count = len(result.body) // detail_size
                details = []
                offset = 0
                for _ in range(count):
                    if offset + detail_size > len(result.body):
                        break
                    vals = SeriesCodec.parse_at(result.body, schema, offset)
                    d = dict(zip(field_names, vals))
                    sym_info = self._symbols_by_id.get(d["symbol_id"])
                    if sym_info:
                        d["symbol"] = sym_info.name
                    details.append(d)
                    offset += detail_size
            except _PARSE_ERRORS as exc:
                logger.error("symbol details parse error: %s", exc)
                return None
            return callback(details)

        self.transport.on(CMD_SYMBOL_DETAILS_PUSH, _handler)
        return _handler

    def on_trade_result(self, callback: Callable[[Record], Awaitable[None] | None]) -> Callable:
        """Register callback for async trade execution results (cmd=19).

        Server pushes trade results asynchronously. Callback receives a dict
        with trade action details and execution result (retcode, order, price, etc.).
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                data = _parse_trade_result_payload(result.body)
            except _PARSE_ERRORS as exc:
                logger.error("trade result push parse error: %s", exc)
                return None
            return callback(data)

        self.transport.on(CMD_TRADE_RESULT_PUSH, _handler)
        return _handler

    def on_trade_transaction(self, callback: Callable[[Record], Awaitable[None] | None]) -> Callable:
        """Register callback for cmd=10 trade transaction frames.

        Build 6320 Web Terminal listens for trade state changes on cmd=10;
        a real trade push has not been verified. The parser supports:
        - type=2: Balance update with deals and positions arrays
        - type!=2: Order transaction (add/update/delete)

        Callback receives a dict with:
        - 'update_type': 2 for balance update, other for transaction
        - For balance updates: 'balance_info', 'deals', 'positions'
        - For transactions: 'flag_mask', 'transaction_id', 'transaction_type', 'order'
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                body = result.body
                if len(body) < 4:
                    return None
                update_type = struct.unpack_from("<I", body, 0)[0]
                data: dict[str, Any] = {"update_type": update_type}
                if update_type == 2:
                    offset = 4
                    balance_size = get_series_size(TRADE_UPDATE_BALANCE_SCHEMA)
                    if len(body) < offset + balance_size:
                        logger.error("trade transaction push parse error: truncated balance payload")
                        return None
                    vals = SeriesCodec.parse_at(body, TRADE_UPDATE_BALANCE_SCHEMA, offset)
                    data["balance_info"] = dict(zip(TRADE_UPDATE_BALANCE_FIELD_NAMES, vals))
                    offset += balance_size
                    deals_result = _parse_optional_counted_segment(body, offset, DEAL_SCHEMA, DEAL_FIELD_NAMES, "deals")
                    if deals_result is None:
                        return None
                    data["deals"], offset = deals_result
                    positions_result = _parse_optional_counted_segment(
                        body, offset, POSITION_SCHEMA, POSITION_FIELD_NAMES, "positions"
                    )
                    if positions_result is None:
                        return None
                    data["positions"], _ = positions_result
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
            except _PARSE_ERRORS as exc:
                logger.error("trade transaction push parse error: %s", exc)
                return None
            return callback(data)

        self.transport.on(CMD_TRADE_UPDATE_PUSH, _handler)
        return _handler

    def on_book_update(self, callback: Callable[[RecordList], Awaitable[None] | None]) -> Callable:
        """Register callback for order book / DOM push (cmd=23).

        Receives list of dicts, each with 'symbol_id', 'bids', 'asks'.
        bids/asks are lists of {'price': float, 'volume': int}.
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                entries = _parse_book_entries(result.body, self._symbols_by_id)
            except _PARSE_ERRORS as exc:
                logger.error("book update parse error: %s", exc)
                return None
            return callback(entries)

        self.transport.on(CMD_BOOK_PUSH, _handler)
        return _handler

    # ---- Typed push handler registration (Phase 16.1) ----

    def on_tick_event(self, callback: Callable[[TickEvent], Awaitable[None] | None]) -> Callable:
        """Register a typed callback that receives :class:`TickEvent` objects.

        Unlike :meth:`on_tick` which passes a raw ``RecordList``, this
        handler converts each tick into a frozen :class:`TickEvent` dataclass.
        """

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                ticks = self._parse_ticks(result.body)
                events = [
                    TickEvent(
                        symbol_id=int(tick.get("symbol_id", 0)),
                        symbol=str(tick.get("symbol", "")),
                        bid=float(tick.get("bid", 0.0)),
                        ask=float(tick.get("ask", 0.0)),
                        last=float(tick.get("last", 0.0)),
                        volume=float(tick.get("tick_volume", 0)),
                        timestamp=float(tick.get("tick_time_ms", 0) or tick.get("tick_time", 0)),
                        raw=tick,
                    )
                    for tick in ticks
                ]
            except _PARSE_ERRORS as exc:
                logger.error("typed tick event parse error: %s", exc)
                return None
            return _dispatch_event_list(callback, events)

        self.transport.on(CMD_TICK_PUSH, _handler)
        self._typed_tick_handlers.append(callback)
        return _handler

    def on_book_event(self, callback: Callable[[BookEvent], Awaitable[None] | None]) -> Callable:
        """Register a typed callback that receives :class:`BookEvent` objects."""

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                entries = _parse_book_entries(result.body, self._symbols_by_id)
                events = [
                    BookEvent(
                        symbol_id=int(entry.get("symbol_id", 0)),
                        symbol=str(entry.get("symbol", "")),
                        entries=entry.get("bids", []) + entry.get("asks", []),
                        raw=entry,
                    )
                    for entry in entries
                ]
            except _PARSE_ERRORS as exc:
                logger.error("typed book event parse error: %s", exc)
                return None
            return _dispatch_event_list(callback, events)

        self.transport.on(CMD_BOOK_PUSH, _handler)
        self._typed_book_handlers.append(callback)
        return _handler

    def on_trade_result_event(self, callback: Callable[[TradeResultEvent], Awaitable[None] | None]) -> Callable:
        """Register a typed callback that receives :class:`TradeResultEvent` objects."""

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                data = _parse_trade_result_payload(result.body)
                result_data = data.get("result", {})
                event = TradeResultEvent(
                    retcode=int(result_data.get("retcode", data.get("action_result_code", 0))),
                    order=int(result_data.get("trade_order", result_data.get("order", data.get("trade_order", 0)))),
                    deal=int(result_data.get("deal", 0)),
                    volume=float(result_data.get("volume", 0)),
                    price=float(result_data.get("price", 0.0)),
                    comment=str(result_data.get("comment", "")),
                    raw=data,
                )
            except _PARSE_ERRORS as exc:
                logger.error("typed trade result event parse error: %s", exc)
                return None
            return callback(event)

        self.transport.on(CMD_TRADE_RESULT_PUSH, _handler)
        self._typed_trade_result_handlers.append(callback)
        return _handler

    def on_account_event(self, callback: Callable[[AccountEvent], Awaitable[None] | None]) -> Callable:
        """Register a typed callback that receives :class:`AccountEvent` objects."""

        def _handler(result: CommandResult) -> Awaitable[None] | None:
            try:
                data = _parse_account_response(result.body)
                if not data:
                    return None
                event = AccountEvent(
                    balance=float(data.get("balance", 0.0)),
                    equity=float(data.get("equity", 0.0)),
                    margin=float(data.get("margin", 0.0)),
                    margin_free=float(data.get("margin_free", 0.0)),
                    raw=data,
                )
            except _PARSE_ERRORS as exc:
                logger.error("typed account event parse error: %s", exc)
                return None
            return callback(event)

        self.transport.on(CMD_ACCOUNT_UPDATE_PUSH, _handler)
        self._typed_account_handlers.append(callback)
        return _handler

    # ---- Callback error registration (Phase 16.2) ----

    def on_callback_error(self, callback: Callable[[Exception, Callable], None]) -> None:
        """Register a handler that is invoked when a push callback raises.

        The handler receives the exception and the failing callback.
        """
        self._callback_error_handlers.append(callback)
        self.transport._callback_error_handlers.append(callback)

    def _cache_tick_push(self, result: CommandResult) -> None:
        try:
            ticks = self._parse_ticks(result.body)
            wire_presence = _extract_tick_wire_presence(result.body)
            for idx, tick in enumerate(ticks):
                symbol_id = int(tick["symbol_id"])
                self._tick_cache_by_id[symbol_id] = tick
                # Atomic deque creation via setdefault to avoid race conditions
                history = self._tick_history_by_id.setdefault(
                    symbol_id,
                    deque(maxlen=self._tick_history_limit or None),
                )
                entry = dict(tick)
                if idx < len(wire_presence):
                    entry["_has_quote"], entry["_has_trade"] = wire_presence[idx]
                history.append(entry)
                # Update LRU access order for eviction
                if symbol_id in self._tick_history_access_order:
                    self._tick_history_access_order.remove(symbol_id)
                self._tick_history_access_order.append(symbol_id)
                # Evict least-recently-updated symbol if limit exceeded
                if self._max_tick_symbols > 0:
                    while len(self._tick_history_by_id) > self._max_tick_symbols:
                        if not self._tick_history_access_order:
                            break
                        evict_id = self._tick_history_access_order.pop(0)
                        evicted = self._tick_history_by_id.pop(evict_id, None)
                        if evicted is not None:
                            # Also remove from name-keyed history
                            evict_info = self._symbols_by_id.get(evict_id)
                            if evict_info:
                                self._tick_history_by_name.pop(evict_info.name, None)
                symbol_name = tick.get("symbol")
                if symbol_name:
                    self._tick_cache_by_name[str(symbol_name)] = tick
                    self._tick_history_by_name[str(symbol_name)] = history
        except _PARSE_ERRORS as exc:
            logger.debug("tick cache update failed: %s", exc)

    def clear_tick_history(self, symbol_id: int | None = None) -> None:
        """Clear tick history for a specific symbol or all symbols.

        Args:
            symbol_id: If provided, clear only that symbol's history.
                       If ``None``, clear all tick history.

        """
        if symbol_id is not None:
            self._tick_history_by_id.pop(symbol_id, None)
            if symbol_id in self._tick_history_access_order:
                self._tick_history_access_order.remove(symbol_id)
            sym_info = self._symbols_by_id.get(symbol_id)
            if sym_info:
                self._tick_history_by_name.pop(sym_info.name, None)
        else:
            self._tick_history_by_id.clear()
            self._tick_history_by_name.clear()
            self._tick_history_access_order.clear()

    def _cache_book_push(self, result: CommandResult) -> None:
        try:
            for entry in _parse_book_entries(result.body, self._symbols_by_id):
                symbol_id = int(entry["symbol_id"])
                self._book_cache_by_id[symbol_id] = entry
                symbol_name = entry.get("symbol")
                if symbol_name:
                    self._book_cache_by_name[str(symbol_name)] = entry
        except _PARSE_ERRORS as exc:
            logger.debug("book cache update failed: %s", exc)
