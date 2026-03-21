"""Typed event dataclasses for push notifications and health monitoring.

These frozen dataclasses provide a typed alternative to the raw ``dict``
records delivered by the existing push handler callbacks.

Register typed handlers via::

    client.on_tick_event(my_callback)   # receives TickEvent
    client.on_book_event(my_callback)   # receives BookEvent
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymt5.transport import TransportState


@dataclass(frozen=True, slots=True)
class TickEvent:
    """A single tick update from the server."""

    symbol_id: int
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: float
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BookEvent:
    """An order book / depth-of-market update."""

    symbol_id: int
    symbol: str
    entries: list[dict[str, Any]]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TradeResultEvent:
    """A trade result push notification."""

    retcode: int
    order: int
    deal: int
    volume: float
    price: float
    comment: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AccountEvent:
    """An account update push notification."""

    balance: float
    equity: float
    margin: float
    margin_free: float
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Snapshot of connection health metrics."""

    state: TransportState
    ping_latency_ms: float | None
    last_message_at: float | None
    uptime_seconds: float
    reconnect_count: int
