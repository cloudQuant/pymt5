"""Subscription lifecycle manager for tick and book subscriptions.

Provides :class:`SubscriptionHandle` — an async context manager that
automatically unsubscribes when exiting the ``async with`` block.
"""

from __future__ import annotations

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
        self._ids = list(ids)
        self._unsubscribe_fn = unsubscribe_fn
        self._active = True

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
        if self._active:
            self._active = False
            await self._unsubscribe_fn(self._ids)

    async def __aenter__(self) -> SubscriptionHandle:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.unsubscribe()
