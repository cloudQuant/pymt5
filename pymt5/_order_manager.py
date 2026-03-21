"""Order lifecycle manager for tracking order states and positions."""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pymt5._logging import get_logger
from pymt5.types import Record

logger = get_logger("pymt5.order_manager")


class OrderState(enum.Enum):
    """Possible states of a tracked order."""

    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


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
    """Tracks order lifecycle and aggregates position data.

    This manager maintains an in-memory registry of orders and their
    state transitions.  It can be fed data from trade results and push
    notifications to keep the local state up to date.
    """

    def __init__(self) -> None:
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

    def update_from_trade_result(self, trade_result: Record) -> TrackedOrder | None:
        """Update a tracked order from a trade result record.

        Expected keys in *trade_result*: ``order``, ``retcode``, and
        optionally ``deal``, ``volume``, ``price``, ``comment``.

        Returns the updated :class:`TrackedOrder` or ``None`` if the
        order is not being tracked.
        """
        order_id = trade_result.get("order", 0)
        if not order_id:
            return None
        order = self._orders.get(order_id)
        if order is None:
            return None

        retcode = trade_result.get("retcode", 0)
        old_state = order.state

        # Map retcode to state
        if retcode == 10009:  # TRADE_RETCODE_DONE
            order.state = OrderState.FILLED
            order.filled_volume = order.volume
        elif retcode == 10010:  # TRADE_RETCODE_DONE_PARTIAL
            order.state = OrderState.PARTIALLY_FILLED
            filled = trade_result.get("volume", 0)
            if filled:
                order.filled_volume = float(filled)
        elif retcode == 10006:  # TRADE_RETCODE_REJECT
            order.state = OrderState.REJECTED
        elif retcode == 10007:  # TRADE_RETCODE_CANCEL
            order.state = OrderState.CANCELED
        elif retcode == 10008:  # TRADE_RETCODE_PLACED
            order.state = OrderState.PENDING

        if "price" in trade_result and trade_result["price"]:
            order.fill_price = float(trade_result["price"])
        if "comment" in trade_result:
            order.comment = str(trade_result["comment"])

        order.raw.update(trade_result)

        if order.state != old_state:
            self._fire_state_change(order, old_state)

        return order

    def update_from_push(self, push_data: Record) -> None:
        """Update tracked orders from a push notification.

        Handles order-state pushes that contain ``order``, ``state``,
        and optionally ``volume_current``.
        """
        order_id = push_data.get("order", push_data.get("trade_order", 0))
        if not order_id:
            return
        order = self._orders.get(order_id)
        if order is None:
            return

        old_state = order.state
        state_val = push_data.get("state")

        if state_val is not None:
            state_map = {
                0: OrderState.PENDING,      # ORDER_STATE_STARTED
                1: OrderState.PENDING,      # ORDER_STATE_PLACED
                2: OrderState.CANCELED,     # ORDER_STATE_CANCELED
                3: OrderState.PARTIALLY_FILLED,  # ORDER_STATE_PARTIAL
                4: OrderState.FILLED,       # ORDER_STATE_FILLED
                5: OrderState.REJECTED,     # ORDER_STATE_REJECTED
                6: OrderState.EXPIRED,      # ORDER_STATE_EXPIRED
            }
            new_state = state_map.get(state_val)
            if new_state is not None:
                order.state = new_state

        if "volume_current" in push_data:
            remaining = float(push_data["volume_current"])
            order.filled_volume = order.volume - remaining

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
        """Return the position summary for *symbol*, or ``None``."""
        return self._positions.get(symbol)

    def get_positions(self) -> list[PositionSummary]:
        """Return all position summaries."""
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
