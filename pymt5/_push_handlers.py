"""Push event handler mixin for MT5WebClient.

Provides callback registration methods for real-time server push
notifications: ticks, positions, orders, trades, symbols, account
updates, login status, book updates, and trade results.
"""

from __future__ import annotations

import logging
import struct
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pymt5._parsers import _parse_account_response, _parse_book_entries, _parse_counted_records, _parse_tick_batch
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
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
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
from pymt5.types import Record, RecordList

if TYPE_CHECKING:
    from pymt5.transport import MT5WebSocketTransport
    from pymt5.types import SymbolInfo

logger = logging.getLogger("pymt5.client")

# Specific exceptions for push handler parsing failures
_PARSE_ERRORS = (struct.error, KeyError, ValueError, TypeError, IndexError)


class _PushHandlersMixin:
    """Mixin providing push event handler registration for MT5WebClient."""

    # Attributes provided by MT5WebClient.__init__
    transport: MT5WebSocketTransport
    _symbols_by_id: dict[int, SymbolInfo]
    _tick_cache_by_id: dict[int, Record]
    _tick_cache_by_name: dict[str, Record]
    _tick_history_limit: int
    _tick_history_by_id: dict[int, deque[Record]]
    _tick_history_by_name: dict[str, deque[Record]]
    _book_cache_by_id: dict[int, Record]
    _book_cache_by_name: dict[str, Record]

    def on_tick(self, callback: Callable[[RecordList], None]) -> Callable:
        def _handler(result: CommandResult) -> None:
            try:
                callback(_parse_tick_batch(result.body, self._symbols_by_id))
            except _PARSE_ERRORS as exc:
                logger.error("tick parse error: %s", exc)

        self.transport.on(CMD_TICK_PUSH, _handler)
        return _handler

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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
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
            except _PARSE_ERRORS as exc:
                logger.error("book update parse error: %s", exc)

        self.transport.on(CMD_BOOK_PUSH, _handler)
        return _handler

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
        except _PARSE_ERRORS as exc:
            logger.debug("tick cache update failed: %s", exc)

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
