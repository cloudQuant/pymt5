"""Order lifecycle manager for tracking order states and positions."""

from __future__ import annotations

import enum
import math
import warnings
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from pymt5._logging import get_logger
from pymt5.constants import (
    TRADE_RETCODE_CANCEL,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_REJECT,
)
from pymt5.types import Record, TradeResult

logger = get_logger("pymt5.order_manager")


class OrderState(enum.Enum):
    """Possible states of a tracked order."""

    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


_TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    }
)

_PUSH_STATE_MAP: dict[int, OrderState] = {
    0: OrderState.PENDING,  # ORDER_STATE_STARTED
    1: OrderState.PENDING,  # ORDER_STATE_PLACED
    2: OrderState.CANCELED,  # ORDER_STATE_CANCELED
    3: OrderState.PARTIALLY_FILLED,  # ORDER_STATE_PARTIAL
    4: OrderState.FILLED,  # ORDER_STATE_FILLED
    5: OrderState.REJECTED,  # ORDER_STATE_REJECTED
    6: OrderState.EXPIRED,  # ORDER_STATE_EXPIRED
}


def _coerce_finite_float(value: Any) -> float | None:
    """Convert *value* to a finite float, returning ``None`` for invalid/non-finite inputs."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _resolve_push_volume(raw_current: Any, raw_initial: Any, order_volume: float) -> float | None:
    """Parse and clamp remaining volume from a push record into ``[0.0, order_volume]``."""
    current_vol = _coerce_finite_float(raw_current)
    if current_vol is None:
        return None
    initial_vol = _coerce_finite_float(raw_initial)
    if (
        initial_vol is not None
        and initial_vol > 0.0
        and order_volume > 0.0
        and isinstance(raw_initial, int)
        and initial_vol / order_volume >= 1000.0
    ):
        current_vol *= order_volume / initial_vol
    return min(max(current_vol, 0.0), max(order_volume, 0.0))


@dataclass
class TrackedOrder:
    """Snapshot of a tracked order with its current state."""

    order_id: int
    symbol: str
    order_type: int
    volume: float
    price: float
    state: OrderState = OrderState.PENDING
    filled_volume: float = 0.0
    fill_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""
    raw: Record = field(default_factory=dict)


@dataclass
class PositionSummary:
    """Aggregated position summary for a symbol."""

    symbol: str
    net_volume: float  # positive=long, negative=short
    avg_price: float
    unrealized_pnl: float = 0.0
    order_count: int = 0


class OrderManager:
    """Track order lifecycle state in an in-memory registry.

    The manager can be fed data from trade results and push notifications to
    keep tracked orders up to date. It does not populate position summaries;
    use :meth:`MT5WebClient.get_positions` for authoritative positions.
    """

    def __init__(self) -> None:
        """Initialize empty order and position registries."""
        self._orders: dict[int, TrackedOrder] = {}
        self._positions: dict[str, PositionSummary] = {}
        self._state_change_callbacks: list[Callable[[TrackedOrder, OrderState], Any]] = []

    # -- Order tracking --

    def track_order(
        self,
        order_id: int,
        symbol: str,
        order_type: int,
        volume: float,
        price: float,
        **kwargs: Any,
    ) -> TrackedOrder:
        """Begin tracking a new order.

        If an order with the same *order_id* already exists, it is replaced.
        """
        order = TrackedOrder(
            order_id=order_id,
            symbol=symbol,
            order_type=order_type,
            volume=volume,
            price=price,
            sl=float(kwargs.get("sl", 0.0)),
            tp=float(kwargs.get("tp", 0.0)),
            comment=str(kwargs.get("comment", "")),
            raw=dict(kwargs.get("raw", {})),
        )
        self._orders[order_id] = order
        logger.debug("tracking order %d %s vol=%.4f price=%.5f", order_id, symbol, volume, price)
        return order

    def update_from_trade_result(self, trade_result: TradeResult | Mapping[str, Any]) -> TrackedOrder | None:
        """Update a tracked order from a :class:`TradeResult` or mapping record.

        Expected fields: ``order`` (or ``trade_order``), ``retcode``, and
        optionally ``deal``, ``volume``, ``price``, ``comment``.

        Returns the updated :class:`TrackedOrder` or ``None`` if the
        order is not being tracked.
        """
        result_data: dict[str, Any] = (
            asdict(trade_result) if isinstance(trade_result, TradeResult) else dict(trade_result)
        )
        raw_order_id = result_data.get("order")
        if not raw_order_id:
            raw_order_id = result_data.get("trade_order", 0)
        order_id = int(raw_order_id or 0)
        if not order_id:
            return None
        order = self._orders.get(order_id)
        if order is None:
            return None
        if order.state in _TERMINAL_STATES:
            return order

        retcode = result_data.get("retcode", 0)
        old_state = order.state

        # Map retcode to state
        if retcode == TRADE_RETCODE_DONE:
            order.state = OrderState.FILLED
            order.filled_volume = order.volume
        elif retcode == TRADE_RETCODE_DONE_PARTIAL:
            order.state = OrderState.PARTIALLY_FILLED
            raw_volume = result_data.get("volume", 0)
            filled = _coerce_finite_float(raw_volume)
            if filled is not None and filled > 0.0:
                if isinstance(raw_volume, int) and order.volume > 0.0 and filled > order.volume and filled >= 10_000.0:
                    filled /= 10**8
                order.filled_volume = max(order.filled_volume, min(filled, max(order.volume, 0.0)))
        elif retcode == TRADE_RETCODE_REJECT:
            order.state = OrderState.REJECTED
        elif retcode == TRADE_RETCODE_CANCEL:
            order.state = OrderState.CANCELED
        elif retcode == TRADE_RETCODE_PLACED:
            order.state = OrderState.PENDING

        fill_price = _coerce_finite_float(result_data.get("price"))
        if fill_price is not None and fill_price > 0.0:
            order.fill_price = fill_price
        if "comment" in result_data:
            order.comment = str(result_data["comment"])

        order.raw.update(result_data)

        if order.state != old_state:
            self._fire_state_change(order, old_state)

        return order

    def update_from_push(self, push_data: Mapping[str, Any]) -> None:
        """Update tracked orders from a push notification.

        Handles order-state pushes that contain ``order`` or ``trade_order``,
        ``state`` or ``order_state``, and optionally ``volume_current``.
        """
        raw_order_id = push_data.get("order")
        if not raw_order_id:
            raw_order_id = push_data.get("trade_order", 0)
        order_id = int(raw_order_id or 0)
        if not order_id:
            return
        order = self._orders.get(order_id)
        if order is None or order.state in _TERMINAL_STATES:
            return

        old_state = order.state
        state_val = push_data.get("state", push_data.get("order_state"))

        if state_val is not None:
            new_state = _PUSH_STATE_MAP.get(state_val)
            if new_state is not None:
                order.state = new_state

        current_vol = (
            _resolve_push_volume(push_data.get("volume_current"), push_data.get("volume_initial"), order.volume)
            if "volume_current" in push_data
            else None
        )
        if current_vol is not None:
            order.filled_volume = max(order.filled_volume, order.volume - current_vol)
        if order.state == OrderState.FILLED and current_vol is None:
            order.filled_volume = order.volume

        order.raw.update(push_data)

        if order.state != old_state:
            self._fire_state_change(order, old_state)

    # -- Query methods --

    def get_order(self, order_id: int) -> TrackedOrder | None:
        """Return a tracked order by ID, or ``None`` if not found."""
        return self._orders.get(order_id)

    def get_orders(self, state: OrderState | None = None) -> list[TrackedOrder]:
        """Return all tracked orders, optionally filtered by *state*."""
        if state is None:
            return list(self._orders.values())
        return [o for o in self._orders.values() if o.state == state]

    def get_position(self, symbol: str) -> PositionSummary | None:
        """Return a local position summary for *symbol*, or ``None``.

        .. deprecated:: 1.0.5
           This local summary is never populated by :class:`OrderManager` and
           will be removed in 2.0.0. Use :meth:`MT5WebClient.get_positions` for
           authoritative positions.
        """
        warnings.warn(
            "OrderManager.get_position() returns a local summary that is never populated; "
            "it is deprecated since 1.0.5 and will be removed in 2.0.0. "
            "Use MT5WebClient.get_positions() for authoritative positions.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._positions.get(symbol)

    def get_positions(self) -> list[PositionSummary]:
        """Return all local position summaries.

        .. deprecated:: 1.0.5
           These local summaries are never populated by :class:`OrderManager`
           and will be removed in 2.0.0. Use :meth:`MT5WebClient.get_positions`
           for authoritative positions.
        """
        warnings.warn(
            "OrderManager.get_positions() returns local summaries that are never populated; "
            "they are deprecated since 1.0.5 and will be removed in 2.0.0. "
            "Use MT5WebClient.get_positions() for authoritative positions.",
            DeprecationWarning,
            stacklevel=2,
        )
        return list(self._positions.values())

    # -- Callbacks --

    def on_state_change(self, callback: Callable[[TrackedOrder, OrderState], Any]) -> None:
        """Register a callback for order state changes.

        The callback receives ``(order, old_state)`` where *order*
        already reflects the new state.
        """
        self._state_change_callbacks.append(callback)

    def _fire_state_change(self, order: TrackedOrder, old_state: OrderState) -> None:
        """Invoke registered state-change callbacks."""
        for cb in self._state_change_callbacks:
            try:
                cb(order, old_state)
            except Exception as exc:
                logger.error("state_change callback error: %s", exc)

    # -- Housekeeping --

    def clear(self) -> None:
        """Clear all tracked orders and positions."""
        self._orders.clear()
        self._positions.clear()
        logger.debug("order manager cleared")
