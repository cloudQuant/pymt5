"""Trading mixin for MT5WebClient.

Provides order placement, position/order management, trade history
retrieval, and order validation.
"""

from __future__ import annotations

import struct
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from pymt5._logging import get_logger
from pymt5._parsers import (
    _coerce_optional_timestamp,
    _currencies_equal,
    _matches_group_mask,
    _order_side,
    _parse_counted_records,
    _validate_requested_stops,
    _validate_requested_volume,
)
from pymt5.constants import (
    CMD_GET_POSITIONS_ORDERS,
    CMD_GET_TRADE_HISTORY,
    CMD_TRADE_REQUEST,
    ORDER_FILLING_FOK,
    ORDER_TIME_GTC,
    ORDER_TIME_SPECIFIED,
    ORDER_TIME_SPECIFIED_DAY,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL_STOP_LIMIT,
    PROP_F64,
    PROP_FIXED_STRING,
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
from pymt5.exceptions import ValidationError
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    DEAL_FIELD_NAMES,
    DEAL_SCHEMA,
    ORDER_FIELD_NAMES,
    ORDER_SCHEMA,
    POSITION_FIELD_NAMES,
    POSITION_SCHEMA,
)
from pymt5.types import (
    TRADE_RESPONSE_SCHEMA,
    Record,
    RecordList,
    TradeResult,
)

if TYPE_CHECKING:
    from pymt5.transport import MT5WebSocketTransport
    from pymt5.types import SymbolInfo

_T = TypeVar("_T")

logger = get_logger("pymt5.client")


class _TradingMixin:
    """Mixin providing trading methods for MT5WebClient."""

    # Attributes provided by MT5WebClient.__init__ / other mixins
    transport: MT5WebSocketTransport
    _symbols: dict[str, SymbolInfo]
    _last_error: tuple[int, str]

    if TYPE_CHECKING:

        def _fail_last_error(self, code: int, message: str) -> _T | None: ...
        def _clear_last_error(self) -> None: ...
        async def symbol_info(self, symbol: str) -> Record | None: ...
        def symbol_info_tick(self, symbol: str) -> Record | None: ...
        async def get_account(self) -> Record: ...
        def _calc_profit_raw(
            self,
            info: Record,
            is_buy: bool,
            volume: float,
            price_open: float,
            price_close: float,
        ) -> float | None: ...
        def _calc_margin_raw(
            self,
            info: Record,
            is_buy: bool,
            volume: float,
            price: float,
            leverage: int,
        ) -> float | None: ...
        async def _resolve_conversion_rates(
            self,
            *,
            source: str,
            target: str,
            current_symbol: str,
            fallback_rate: float,
        ) -> tuple[float, float] | None: ...
        def _resolve_digits(self, symbol: str, digits: int | None) -> int: ...
        @staticmethod
        def _volume_to_lots(volume: float, precision: int = 8) -> int: ...

    async def get_positions_and_orders(self) -> dict[str, RecordList]:
        result = await self.transport.send_command(CMD_GET_POSITIONS_ORDERS)
        body = result.body
        positions = _parse_counted_records(body, POSITION_SCHEMA, POSITION_FIELD_NAMES)
        pos_size = get_series_size(POSITION_SCHEMA)
        pos_count = struct.unpack_from("<I", body, 0)[0] if body else 0
        order_offset = 4 + pos_count * pos_size
        orders = _parse_counted_records(body[order_offset:], ORDER_SCHEMA, ORDER_FIELD_NAMES)
        return {"positions": positions, "orders": orders}

    async def get_trade_history(self, from_ts: int = 0, to_ts: int = 0) -> dict[str, RecordList]:
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
            from pymt5.constants import FOREX_CALC_MODES

            mode = int(info.get("trade_calc_mode", 0) or 0)
            if mode in FOREX_CALC_MODES:
                profit = raw_profit * (rate_buy if is_buy else rate_sell)
            else:
                profit = raw_profit * (rate_sell if raw_profit > 0 else rate_buy)
            self._clear_last_error()
            return float(profit)
        except (KeyError, ValueError, TypeError, struct.error) as exc:
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
        except (KeyError, ValueError, TypeError, struct.error) as exc:
            return self._fail_last_error(-99, f"order_calc_margin() failed: {exc}")

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
            raise ValidationError(f"volume must be > 0 for trade_action={trade_action}, got {volume}")
        if trade_action == TRADE_ACTION_DEAL and price_order <= 0.0:
            logger.debug("market order with price_order=0; server will use current price")
        if trade_action == TRADE_ACTION_PENDING and price_order <= 0.0:
            raise ValidationError(f"price_order must be > 0 for pending orders, got {price_order}")
        if trade_action == TRADE_ACTION_MODIFY and order <= 0:
            raise ValidationError(f"order ticket must be > 0 for MODIFY action, got {order}")
        if trade_action == TRADE_ACTION_REMOVE and order <= 0:
            raise ValidationError(f"order ticket must be > 0 for REMOVE action, got {order}")
        if trade_action == TRADE_ACTION_SLTP and position_id <= 0:
            raise ValidationError(f"position_id must be > 0 for SLTP action, got {position_id}")
        if trade_action == TRADE_ACTION_CLOSE_BY and (position_id <= 0 or position_by <= 0):
            raise ValidationError(
                f"position_id and position_by must be > 0 for CLOSE_BY, "
                f"got position_id={position_id}, position_by={position_by}"
            )
        if price_sl < 0.0:
            raise ValidationError(f"price_sl must be >= 0, got {price_sl}")
        if price_tp < 0.0:
            raise ValidationError(f"price_tp must be >= 0, got {price_tp}")
        payload = SeriesCodec.serialize(
            [
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
            ]
        )
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
        raw_volume = float(request.get("volume", 0.0) or 0.0)
        volume = self._volume_to_lots(raw_volume)
        if volume < 0:
            raise ValidationError(f"volume encoding overflow: {raw_volume} -> {volume}")
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
        except (KeyError, ValueError, TypeError, struct.error) as exc:
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

    async def _validate_deal_or_pending(
        self,
        request: Record,
        symbol_info: dict | None,
    ) -> tuple[int, str] | None:
        """Validate fields for DEAL/PENDING actions.

        Returns ``(retcode, message)`` on validation failure, ``None`` on success.
        """
        symbol = str(request.get("symbol", "") or "")
        order_type = int(request.get("type", 0) or 0)
        volume = float(request.get("volume", 0.0) or 0.0)
        action = int(request.get("action", 0) or 0)
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
        return None

    async def _validate_order_check_request(self, request: Record) -> tuple[int, str]:
        action = int(request.get("action", 0) or 0)
        symbol = str(request.get("symbol", "") or "")
        symbol_info = await self.symbol_info(symbol) if symbol else None
        if action in {TRADE_ACTION_DEAL, TRADE_ACTION_PENDING}:
            result = await self._validate_deal_or_pending(request, symbol_info)
            if result is not None:
                return result
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
            logger.warning("trade_request %s action=%d vol=%d -> empty response", symbol, trade_action, volume)
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
                    retcode=vals[0],
                    description=desc,
                    success=success,
                    deal=int(vals[1]),
                    order=int(vals[2]),
                    volume=int(vals[3]),
                    price=vals[4],
                    bid=vals[5],
                    ask=vals[6],
                    comment=vals[7].strip("\x00") if isinstance(vals[7], str) else "",
                    request_id=int(vals[8]),
                )
            except (struct.error, KeyError, ValueError, TypeError, IndexError) as exc:
                logger.debug("trade response extended parse failed: %s, using retcode only", exc)
                tr = TradeResult(retcode=retcode, description=desc, success=success)
        else:
            tr = TradeResult(retcode=retcode, description=desc, success=success)
        logger.info("trade_request %s action=%d vol=%d -> %s", symbol, trade_action, volume, tr)
        return tr
