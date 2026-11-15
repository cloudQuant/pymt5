"""Subscription lifecycle manager for tick and book subscriptions.

Provides :class:`SubscriptionHandle` — an async context manager that
automatically unsubscribes when exiting the ``async with`` block.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class SubscriptionHandle:
    """Manages the lifecycle of a tick or book subscription.

    Usage::

        async with client.subscribe_ticks_managed([symbol_id]) as handle:
            # subscribed here
            ...
        # automatically unsubscribed here

    Or without a context manager::

        handle = await client.subscribe_ticks_managed([symbol_id])
        # ... later ...
        await handle.unsubscribe()
    """

    def __init__(
        self,
        ids: list[int],
        unsubscribe_fn: Callable[[list[int]], Coroutine[Any, Any, None]],
    ) -> None:
        """Initialize an active subscription handle.

        Args:
            ids: Symbol identifiers covered by the subscription. The list is
                copied so later caller mutations do not change the handle.
            unsubscribe_fn: Coroutine function called with the stored IDs when
                the subscription is closed.

        """
        self._ids = list(ids)
        self._unsubscribe_fn = unsubscribe_fn
        self._active = True
        self._lock = asyncio.Lock()

    @property
    def ids(self) -> list[int]:
        """Symbol IDs covered by this subscription."""
        return list(self._ids)

    @property
    def active(self) -> bool:
        """Whether the subscription is still active."""
        return self._active

    async def unsubscribe(self) -> None:
        """Explicitly unsubscribe. Idempotent."""
        async with self._lock:
            if self._active:
                await self._unsubscribe_fn(self._ids)
                self._active = False

    async def __aenter__(self) -> SubscriptionHandle:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.unsubscribe()
