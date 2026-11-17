"""Token bucket rate limiter for command throttling."""

from __future__ import annotations

import asyncio
import math
import time

from pymt5.exceptions import ValidationError


class TokenBucketRateLimiter:
    """Async token bucket rate limiter.

    Limits the rate of operations to ``rate`` tokens per second with a
    maximum burst of ``burst`` tokens.  When the bucket is empty, callers
    of :meth:`acquire` will ``await`` until a token is available.

    A ``rate`` of ``0`` disables rate limiting (all calls pass immediately).

    Thread-safe within a single event loop via :class:`asyncio.Lock`.
    """

    def __init__(self, rate: float = 10.0, burst: int = 20) -> None:
        """Initialize a token bucket at full capacity.

        Args:
            rate: Number of tokens replenished per second. Zero disables
                rate limiting.
            burst: Maximum number of accumulated tokens and initial bucket capacity.

        Raises:
            ValidationError: If ``rate`` is negative/non-finite or ``burst`` is
                less than ``1``/non-finite.

        """
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
            raise ValidationError(f"rate must be a finite number >= 0, got {rate!r}")
        if isinstance(burst, bool) or not isinstance(burst, (int, float)) or not math.isfinite(burst) or burst < 1:
            raise ValidationError(f"burst must be a finite number >= 1, got {burst!r}")
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        if self.rate <= 0:
            return
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            # Sleep outside the lock — cancellation-safe
            await asyncio.sleep(wait)
