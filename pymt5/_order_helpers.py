"""Order convenience methods mixin for MT5WebClient.

Provides shortcut methods for common order types: market, limit, stop,
stop-limit, position close, position modify, and pending order management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymt5._validation import validate_symbol_name, validate_volume
from pymt5.constants import (
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
    POSITION_TYPE_BUY,
    POSITION_TYPE_SELL,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MARKET_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
)
from pymt5.exceptions import ValidationError

if TYPE_CHECKING:
    from pymt5.types import RecordList, SymbolInfo, TradeResult

class _OrderHelpersMixin:
    """Mixin providing order convenience methods for MT5WebClient.

    Depends on methods from ``_TradingMixin``:
    - ``trade_request(...)``
    - ``get_positions()``

    Depends on attributes from ``MT5WebClient``:
    - ``_symbols: dict[str, SymbolInfo]``
    """

    _symbols: dict[str, SymbolInfo]

    if TYPE_CHECKING:

        async def trade_request(
            self,
            *,
            action_id: int = ...,
            trade_action: int = ...,
            symbol: str = ...,
            volume: int = ...,
            digits: int = ...,
            order: int = ...,
            trade_type: int = ...,
            type_filling: int = ...,
            type_time: int = ...,
            type_flags: int = ...,
            type_reason: int = ...,
            price_order: float = ...,
            price_trigger: float = ...,
            price_sl: float = ...,
            price_tp: float = ...,
            deviation: int = ...,
            comment: str = ...,
            position_id: int = ...,
            position_by: int = ...,
            time_expiration: int = ...,
        ) -> TradeResult: ...
        async def get_positions(self) -> RecordList: ...

    def _resolve_digits(self, symbol: str, digits: int | None) -> int:
        """Resolve digits from cache or explicit parameter."""
        if digits is not None:
            return digits
        info = self._symbols.get(symbol)
        return info.digits if info else 5

    def _market_trade_action(self) -> int:
        if getattr(self, "_trade_protocol", "legacy") == "build_6090":
            return TRADE_ACTION_MARKET_DEAL
        return TRADE_ACTION_DEAL

    @staticmethod
    def _volume_to_lots(volume: float, precision: int = 8) -> int:
        """Convert lots (e.g. 0.01) to MT5 integer volume.

        MT5 Web Terminal uses integer volumes where the value represents
        volume * 10^precision. The default precision=8 is based on the
        MetaQuotes demo server (1.0 lot = 100000000).
        """
        return int(round(volume * (10**precision)))

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
        action_id: int = 0,
        type_flags: int = 0,
    ) -> TradeResult:
        """Internal helper for all order placement methods."""
        validate_symbol_name(symbol)
        if trade_action in (TRADE_ACTION_DEAL, TRADE_ACTION_MARKET_DEAL, TRADE_ACTION_PENDING):
            validate_volume(volume)
        d = self._resolve_digits(symbol, digits)
        return await self.trade_request(
            action_id=action_id,
            trade_action=trade_action,
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=trade_type,
            type_filling=filling,
            type_time=time_type,
            type_flags=type_flags,
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
        action_id: int = 0,
        type_flags: int = 0,
    ) -> TradeResult:
        """Place a market buy order."""
        return await self._place_order(
            trade_action=self._market_trade_action(),
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_BUY,
            digits=digits,
            sl=sl,
            tp=tp,
            deviation=deviation,
            comment=comment,
            filling=filling,
            magic=magic,
            action_id=action_id,
            type_flags=type_flags,
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
        action_id: int = 0,
        type_flags: int = 0,
    ) -> TradeResult:
        """Place a market sell order."""
        return await self._place_order(
            trade_action=self._market_trade_action(),
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_SELL,
            digits=digits,
            sl=sl,
            tp=tp,
            deviation=deviation,
            comment=comment,
            filling=filling,
            magic=magic,
            action_id=action_id,
            type_flags=type_flags,
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
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_BUY_LIMIT,
            digits=digits,
            price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            filling=filling,
            time_type=time_type,
            expiration=expiration,
            magic=magic,
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
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_SELL_LIMIT,
            digits=digits,
            price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            filling=filling,
            time_type=time_type,
            expiration=expiration,
            magic=magic,
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
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_BUY_STOP,
            digits=digits,
            price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            filling=filling,
            time_type=time_type,
            expiration=expiration,
            magic=magic,
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
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_SELL_STOP,
            digits=digits,
            price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            filling=filling,
            time_type=time_type,
            expiration=expiration,
            magic=magic,
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
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_BUY_STOP_LIMIT,
            digits=digits,
            price=stop_limit_price,
            trigger_price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            filling=filling,
            time_type=time_type,
            expiration=expiration,
            magic=magic,
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
            trade_action=TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            trade_type=ORDER_TYPE_SELL_STOP_LIMIT,
            digits=digits,
            price=stop_limit_price,
            trigger_price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            filling=filling,
            time_type=time_type,
            expiration=expiration,
            magic=magic,
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
        action_id: int = 0,
        type_flags: int = 0,
    ) -> TradeResult:
        """Close a position by placing an opposite market order.

        If order_type is not specified, auto-detects direction from open positions:
        BUY positions are closed with SELL and vice versa. Raises ValidationError
        when the position or its direction cannot be determined or its symbol
        does not match.
        """
        if position_id <= 0:
            raise ValidationError(f"position_id must be > 0 to close a position, got {position_id}")
        d = self._resolve_digits(symbol, digits)
        if order_type is not None:
            ot = order_type
        else:
            ot = await self._detect_close_direction(position_id, symbol)
        return await self.trade_request(
            action_id=action_id,
            trade_action=self._market_trade_action(),
            symbol=symbol,
            volume=self._volume_to_lots(volume),
            digits=d,
            trade_type=ot,
            type_filling=filling,
            type_flags=type_flags,
            deviation=deviation,
            comment=comment,
            position_id=position_id,
            type_reason=magic,
        )

    async def _detect_close_direction(self, position_id: int, symbol: str) -> int:
        """Detect the order type needed to close a position (opposite direction)."""
        try:
            positions = await self.get_positions()
        except Exception as exc:
            raise ValidationError(f"unable to query positions for position_id={position_id}") from exc
        for position in positions:
            if position.get("position_id") == position_id:
                position_symbol = position.get("trade_symbol")
                if not position_symbol or position_symbol != symbol:
                    raise ValidationError(
                        f"symbol mismatch for position_id={position_id}: "
                        f"requested {symbol!r}, position has {position_symbol!r}"
                    )
                direction = position.get("trade_action")
                if direction == POSITION_TYPE_SELL:
                    return ORDER_TYPE_BUY
                if direction == POSITION_TYPE_BUY:
                    return ORDER_TYPE_SELL
                raise ValidationError(f"unknown direction for position_id={position_id}: {direction!r}")
        raise ValidationError(f"position_id={position_id} not found in open positions")

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
