Iteration Plan v5
=================

.. contents:: Table of Contents
   :depth: 3
   :local:

Project Status Summary
----------------------

**Current version**: v0.9.0

**Codebase metrics**:

- Source code: ~7,610 lines across 26 modules
- Test code: 989 tests, 99% coverage (3167/3186 statements, 19 uncovered lines)
- CI: 9-matrix (3 Python versions × 3 OS)
- Documentation: Sphinx + 8 guides + 4 iteration plans + 8 examples
- Mypy: 0 errors (strict_optional, disallow_incomplete_defs enabled)
- Ruff: 0 lint errors

**Architecture (5 mixins + 1 currency mixin + core client)**:

.. code-block:: text

   MT5WebClient (602 lines)
   ├── _MarketDataMixin      (629 lines)  — symbols, ticks, bars, book, subscriptions
   ├── _TradingMixin         (701 lines)  — positions, orders, trade execution
   ├── _OrderHelpersMixin    (525 lines)  — buy/sell/close/modify convenience
   ├── _AccountMixin         (488 lines)  — account info, OTP, verification
   ├── _PushHandlersMixin    (460 lines)  — push notification handlers
   └── _CurrencyMixin        (301 lines)  — currency conversion, profit/margin

   + Infrastructure:
     ├── Transport            (258 lines)  — WebSocket, state machine, encryption
     ├── Protocol codec       (300 lines)  — dispatch-table serialization
     ├── Schemas             (1076 lines)  — 24 binary protocol schemas
     ├── OrderManager         (119 lines)  — order state tracking
     ├── ConnectionPool        (69 lines)  — multi-account pooling
     └── Support modules      (182 lines)  — events, logging, metrics, etc.

**Completed in previous iterations**:

- ✅ Phase 1-5: Core quality, transport, observability, API, protocol
- ✅ Phase v3: Rate limiter fix, exception narrowing, env log level
- ✅ Phase 15: Coverage to 99%, currency mixin extraction, symbol cache TTL
- ✅ Phase 16 (partial): Typed events, order manager, connection pool
- ✅ Phase 18 (partial): Protocol reference docs, PyPI release automation

**Remaining from v4 plan (carried forward)**:

- ⏳ Phase 16.2: Callback error isolation
- ⏳ Phase 16.3: Connection health monitoring
- ⏳ Phase 17: Integration tests, benchmarks, fuzz testing (partially done)
- ⏳ Phase 19.1: Protocol version tracking
- ⏳ Phase 19.4: Dev-mode schema validation


Deep Analysis Findings
----------------------

A comprehensive code review revealed the following issues beyond the v4 plan scope:

**Critical bugs**:

1. ``_rate_limiter.py:44-50`` — Manual lock release/acquire is not
   cancellation-safe; if a coroutine is cancelled during ``await asyncio.sleep(wait)``,
   the lock is never re-acquired, corrupting the rate limiter state for all
   subsequent operations. Potential deadlock in production.

2. ``transport.py:188-193`` — Timeout handler removes future from ``_pending``
   queue without checking ``future.done()``. If response arrives during timeout
   processing, orphaned futures accumulate, causing memory leaks in long-running
   sessions.

**Security issues**:

3. ``client.py:222-228`` — ``_clear_credentials()`` overwrites password with
   empty string, not cryptographic zero-fill. The original password string
   persists in Python's memory allocator.

4. ``client.py:315`` — ``assert self._login_kwargs is not None`` used for runtime
   validation. Assertions are disabled with ``python -O``, removing the check
   entirely. Should be an explicit ``SessionError`` raise.

**Concurrency issues**:

5. ``transport.py:215-222`` — Disconnect callback race condition. ``_on_disconnect``
   is invoked from receive loop without synchronization against ``close()``.
   State corruption if disconnect fires while close is executing.

6. ``_push_handlers.py:434-449`` — Tick cache deque created lazily without
   atomic check-and-create. Two concurrent tick updates can race and create
   duplicate history deques. Should use ``setdefault()``.

7. ``_push_handlers.py:425-432`` — Callback error handlers called sequentially
   without isolation. If one error handler throws, remaining handlers are skipped.

**Data integrity issues**:

8. ``_trading.py:437`` — Volume precision loss. ``_volume_to_lots()`` uses
   hardcoded precision 8, but precision varies by broker. Should use symbol's
   volume_precision from symbol info.

9. ``_trading.py:395-398`` — Incomplete validation: only validates volume for
   DEAL/PENDING actions, not price or other fields for MODIFY/SLTP.

10. ``_trading.py:521-550`` — ``_normalize_order_request()`` doesn't validate
    normalized values against symbol's min/max/step constraints.

**Memory issues**:

11. ``_push_handlers.py`` — Tick history deques (``_tick_history_by_id``,
    ``_tick_history_by_name``) keep up to 10,000 ticks per symbol. For 1,000+
    symbols, this is 100+ MB. No periodic cleanup or configurable limits.

12. ``_currency.py`` — ``_find_conversion_symbol_name()`` called repeatedly for
    same currency pairs without memoization. Should cache conversion results.

**API design issues**:

13. Inconsistent error handling: ``_market_data.py`` returns ``None`` on error,
    ``_trading.py`` raises ``ValidationError``. No clear error contract.

14. Missing callback unregistration: ``on_tick()``, ``on_position_update()`` etc.
    register closures but don't return ``SubscriptionHandle`` for unregistration.

15. ``client.py:212-213`` — Bare ``except Exception: pass`` in logout during
    ``close()`` silently swallows errors, losing diagnostic context.


Phase 20: Critical Bug Fixes (v0.9.1)
--------------------------------------

Goal: Fix all critical and high-severity bugs found in deep analysis. Zero new
features — purely a hardening release.

20.1 Fix rate limiter cancellation safety
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: CRITICAL

**File**: ``pymt5/_rate_limiter.py:44-50``

**Problem**: Manual ``lock.release()`` / ``lock.acquire()`` around
``await asyncio.sleep()`` is not cancellation-safe.

**Fix**:

.. code-block:: python

   async def acquire(self) -> None:
       while True:
           async with self._lock:
               self._refill()
               if self._tokens >= 1.0:
                   self._tokens -= 1.0
                   return
               wait = (1.0 - self._tokens) / self.rate
           await asyncio.sleep(wait)

- Lock held only during token check/deduction, released before sleep
- No manual lock management — ``async with`` handles cancellation
- Sleep happens outside the lock — no deadlock possible

**Tests**:

- Test cancellation during sleep does not corrupt lock state
- Test concurrent acquire under high contention
- Test that lock is always released after cancellation

20.2 Fix transport future leak on timeout
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

**File**: ``transport.py:188-193``

**Problem**: ``queue.remove(future)`` called without checking ``future.done()``.
If response arrives during timeout window, future is orphaned.

**Fix**:

.. code-block:: python

   if queue and not future.done():
       try:
           queue.remove(future)
       except ValueError:
           pass  # Already removed by response handler

**Tests**:

- Test timeout when response arrives concurrently
- Verify no orphaned futures after repeated timeout cycles
- Measure ``_pending`` dict size after 1000 timeout/response races

20.3 Fix disconnect callback race condition
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

**File**: ``transport.py:215-222``

**Problem**: No synchronization between recv loop disconnect and ``close()``
method.

**Fix**:

- Add ``asyncio.Lock`` (``_disconnect_lock``) to serialize disconnect handling
- ``close()`` acquires lock before state transition
- Recv loop acquires lock before calling ``_on_disconnect``
- Check ``_shutdown_event.is_set()`` inside lock to prevent double disconnect

**Tests**:

- Test concurrent close() and disconnect from recv loop
- Verify on_disconnect called exactly once
- Test state machine ends in correct terminal state

20.4 Replace assert with explicit exception
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

**File**: ``client.py:315``

**Problem**: ``assert`` used for runtime invariant check, disabled with ``-O``.

**Fix**:

.. code-block:: python

   if self._login_kwargs is None:
       raise SessionError("Cannot reconnect: no stored credentials")

**Tests**:

- Test reconnect without prior login raises SessionError
- Test reconnect after explicit credential clearing

20.5 Fix credential clearing
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

**File**: ``client.py:222-228``

**Problem**: Password replaced with empty string, not securely zeroed.

**Fix**:

.. code-block:: python

   def _clear_credentials(self) -> None:
       if self._login_kwargs and "password" in self._login_kwargs:
           pw = self._login_kwargs["password"]
           if isinstance(pw, str):
               # Overwrite with random data of same length, then discard
               self._login_kwargs["password"] = "\x00" * len(pw)
           self._login_kwargs = None

- Zero-fill password field before discarding reference
- Set entire ``_login_kwargs`` to ``None`` to remove all credential data
- Log credential clear event at debug level (without password content)

**Tests**:

- Verify _login_kwargs is None after _clear_credentials()
- Verify password field is zeroed before dict is discarded

20.6 Fix silent error swallowing in close()
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**File**: ``client.py:212-213``

**Problem**: Bare ``except Exception: pass`` loses diagnostic context.

**Fix**:

.. code-block:: python

   except Exception:
       logger.debug("logout during close() failed", exc_info=True)

**Tests**:

- Verify close() succeeds even when logout throws
- Verify exception is logged at debug level


Phase 21: Concurrency & Data Integrity (v0.9.2)
------------------------------------------------

Goal: Fix all concurrency bugs and data integrity issues. Production-safe for
long-running trading sessions.

21.1 Fix tick cache race condition
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**File**: ``_push_handlers.py:434-449``

**Problem**: Non-atomic deque creation for tick history.

**Fix**:

.. code-block:: python

   # Replace:
   if symbol_id not in self._tick_history_by_id:
       self._tick_history_by_id[symbol_id] = deque(maxlen=self._tick_history_maxlen)
   self._tick_history_by_id[symbol_id].append(tick)

   # With:
   history = self._tick_history_by_id.setdefault(
       symbol_id, deque(maxlen=self._tick_history_maxlen)
   )
   history.append(tick)

Apply same fix to ``_tick_history_by_name``.

**Tests**:

- Test concurrent tick updates for same symbol
- Verify only one deque exists per symbol after concurrent creation

21.2 Fix callback error handler isolation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**File**: ``_push_handlers.py:425-432``

**Problem**: Error handler chain breaks if one handler throws.

**Fix**:

.. code-block:: python

   for error_handler in self._on_callback_error_handlers:
       try:
           if asyncio.iscoroutinefunction(error_handler):
               await error_handler(callback, exc)
           else:
               error_handler(callback, exc)
       except Exception:
           logger.warning(
               "callback error handler itself raised",
               exc_info=True,
           )

**Tests**:

- Test error handler that throws does not prevent subsequent handlers
- Verify all error handlers are called even when first one fails

21.3 Fix volume precision loss
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**File**: ``_trading.py:437``

**Problem**: Hardcoded precision 8 in ``_volume_to_lots()``.

**Fix**:

- Accept ``volume_precision: int | None`` parameter in ``_volume_to_lots()``
- Default to ``None`` → use 8 (backward-compatible)
- When symbol info available, pass ``symbol_info.get("volume_precision", 8)``
- Round volume to symbol's precision before encoding

**Tests**:

- Test volume encoding with precision 4, 6, 8, 10
- Test volume round-trip at symbol's precision boundary

21.4 Strengthen order validation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**File**: ``_trading.py:395-398, 521-550``

**Problem**: Incomplete validation — price not checked, symbol constraints
not enforced.

**Fix**:

- Add price validation for DEAL/PENDING/MODIFY actions
- Add ``price_order > 0`` check for applicable actions
- Add SL/TP validation: ``sl >= 0``, ``tp >= 0``
- In ``_normalize_order_request()``: validate against symbol constraints
  (volume_min, volume_max, volume_step, trade_stops_level)
- Log warnings for soft constraint violations

**Tests**:

- Test zero price on DEAL action raises ValidationError
- Test volume below symbol minimum raises ValidationError
- Test volume not aligned to step raises ValidationError
- Test SL/TP distance within stops level

21.5 Add configurable tick history limits
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**File**: ``_push_handlers.py``

**Problem**: 10,000 ticks × 1,000+ symbols = unbounded memory.

**Fix**:

- Add ``tick_history_maxlen: int = 10_000`` constructor parameter
- Add ``max_tick_symbols: int = 0`` parameter (0 = unlimited)
- When ``max_tick_symbols`` exceeded, evict least-recently-updated symbol
- Add ``clear_tick_history(symbol_id=None)`` method for manual cleanup
- Document memory implications in docstring

**Tests**:

- Test tick history respects maxlen
- Test symbol eviction when max_tick_symbols reached
- Test clear_tick_history clears specific or all symbols


Phase 22: API Consistency & Ergonomics (v0.10.0)
-------------------------------------------------

Goal: Improve API design consistency, add missing ergonomic features.

22.1 Callback error isolation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH (carried from Phase 16.2)

Wrap each user callback invocation in ``_dispatch()`` with try/except:

- Log callback errors with full traceback via ``get_logger()``
- Add ``on_callback_error(handler)`` registration for error reporting
- Continue processing remaining callbacks and messages after error
- Track callback error count in metrics

**Implementation**:

- In ``_dispatch_tick()``, ``_dispatch_trade()``, etc.:

.. code-block:: python

   for callback in handlers:
       try:
           if asyncio.iscoroutinefunction(callback):
               await callback(data)
           else:
               callback(data)
       except Exception as exc:
           logger.error("callback error in %s", callback.__name__, exc_info=True)
           await self._notify_callback_error(callback, exc)

**Tests**:

- Test bad callback doesn't kill other callbacks
- Test bad callback doesn't kill connection
- Test error count tracked in metrics

22.2 Subscription handle for push handlers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

**Problem**: ``on_tick()``, ``on_position_update()`` etc. return internal
handler functions but provide no clean unregistration mechanism.

**Fix**:

.. code-block:: python

   # New API:
   handle = client.on_tick(my_callback)
   handle.cancel()  # Unregister

   # Context manager support:
   async with client.on_tick(my_callback):
       await asyncio.sleep(60)
   # Auto-unregistered

- Return ``SubscriptionHandle`` from all ``on_*()`` methods
- ``SubscriptionHandle.cancel()`` removes callback from internal list
- ``SubscriptionHandle.__aenter__``/``__aexit__`` for context manager
- Backward-compatible: existing code ignoring return value still works

**Tests**:

- Test cancel() removes callback
- Test context manager auto-cleanup
- Test cancelled handle doesn't receive events
- Test multiple handles for same callback

22.3 Standardize error handling contract
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**Problem**: Inconsistent error strategy — some methods return ``None``,
others raise exceptions.

**Principle**:

- **Validation errors**: Always raise ``ValidationError``
- **Network/protocol errors**: Always raise ``MT5ConnectionError`` or ``ProtocolError``
- **Missing data (symbol not found, etc.)**: Return ``None``
- **Trading errors (retcode != OK)**: Always raise ``TradeError``

**Changes**:

- ``get_full_symbol_info()``: Return ``None`` on missing (keep current)
- ``symbol_info_tick()``: Return ``None`` on missing (keep current)
- ``trade_request()``: Raise ``TradeError`` on non-OK retcode (enforce)
- ``_normalize_order_request()``: Raise ``ValidationError`` on constraint
  violation (new)
- Document contract in module-level docstring

**Tests**:

- Verify each method follows declared contract
- Test boundary cases at None/raise decision points

22.4 Connection health monitoring
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM (carried from Phase 16.3)

Add health check mechanism to transport:

.. code-block:: python

   @dataclass(frozen=True, slots=True)
   class HealthStatus:
       state: TransportState
       ping_latency_ms: float | None
       last_message_at: float | None
       uptime_seconds: float
       reconnect_count: int

   health = await client.health_check()
   client.on_health_degraded(lambda status: ...)

**Implementation**:

- Measure ping round-trip time in heartbeat loop
- Track ``_last_message_at`` timestamp in recv loop
- Expose ``client.health_check()`` async method
- Optional ``health_threshold_ms`` parameter (default 5000)
- Emit ``on_health_degraded`` when threshold exceeded

**Tests**:

- Test health_check() returns valid HealthStatus
- Test latency measurement accuracy
- Test health_degraded fires when threshold exceeded

22.5 Conversion rate caching
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

**File**: ``_currency.py``

**Problem**: ``_find_conversion_symbol_name()`` called repeatedly for same
currency pairs without memoization.

**Fix**:

- Add ``_conversion_cache: dict[tuple[str, str], str | None]`` to mixin
- Cache conversion symbol lookup results
- Invalidate cache on ``invalidate_symbol_cache()``
- TTL based on ``symbol_cache_ttl`` parameter

**Tests**:

- Test cache hit avoids repeated lookups
- Test cache invalidation clears conversion cache
- Test concurrent access to conversion cache


Phase 23: Testing Infrastructure (v1.0.0-rc)
---------------------------------------------

Goal: Comprehensive testing beyond unit tests. Mark as v1.0.0 release candidate.

23.1 Integration test framework
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH (carried from Phase 17.1)

Expand ``tests/test_integration.py`` gated by ``PYMT5_INTEGRATION=1``:

- Bootstrap handshake test (connect, receive bootstrap, disconnect)
- Login/logout cycle test
- Symbol load test (verify schema parsing against live data)
- Tick subscription test (subscribe, receive ≥1 tick, unsubscribe)
- Heartbeat round-trip test
- Order placement test (demo account, minimal lot)
- Book subscription test (subscribe, receive ≥1 update, unsubscribe)
- Reconnection test (force disconnect, verify auto-reconnect)

**Implementation**:

- ``pytest.mark.integration`` marker
- ``conftest.py`` fixture for credentials from env vars
- CI job on schedule (weekly), skip if env vars missing
- Timeout guards on all integration tests (30s max)
- JSON report output for historical comparison

**Tests**:

- At least 8 integration tests covering the full lifecycle

23.2 Enhance fuzz testing
^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM (carried from Phase 17.3)

Expand ``tests/test_fuzz.py`` using hypothesis:

- Random byte sequences → ``parse_response_frame()`` should not crash
- Random field values → ``SeriesCodec.serialize()`` round-trip invariant
- Malformed frames → graceful ``ProtocolError``, no memory leaks
- Truncated messages → proper error handling
- Oversized fields → no buffer overflow
- Unicode edge cases in symbol names → no encoding crash

**Implementation**:

- Gate behind ``pytest.mark.fuzz`` marker
- Run in CI on schedule (weekly)
- Max examples: 10,000 per test
- Use ``@given(st.binary())`` for raw byte fuzzing
- Use ``@given(st.dictionaries(...))`` for structured field fuzzing

23.3 Enhance performance benchmarks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM (carried from Phase 17.2)

Expand ``tests/test_benchmarks.py``:

- ``SeriesCodec.serialize()`` throughput (ops/sec)
- ``SeriesCodec.parse()`` throughput
- ``AESCipher.encrypt()`` / ``decrypt()`` throughput
- Rate limiter acquire latency under contention
- Symbol cache lookup throughput (name → ID, ID → name)
- Tick dispatch throughput (N callbacks × M ticks)
- ``_normalize_order_request()`` throughput

**Implementation**:

- Gate behind ``pytest.mark.benchmark`` marker
- Store baselines in ``benchmarks/`` directory
- Compare in CI to detect regressions (±10% threshold)
- Add ``pytest-benchmark`` to dev dependencies

23.4 Async race condition test suite
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

**New test file**: ``tests/test_concurrency.py``

- Concurrent ``load_symbols()`` calls (verify single fetch)
- Concurrent tick cache updates (verify single deque per symbol)
- Tick update during book unsubscribe cleanup
- Transport reconnect while client is closing
- Rate limiter under 100 concurrent tasks
- Health check during disconnect
- Callback registration during dispatch

**Implementation**:

- Use ``asyncio.gather()`` with ``return_exceptions=True``
- Use ``asyncio.Event`` for precise timing control
- Verify no exceptions, no duplicate state, no deadlocks
- Each test has a 5s timeout guard


Phase 24: Documentation & v1.0.0 Release (v1.0.0)
--------------------------------------------------

Goal: Community-ready documentation and final v1.0.0 release.

24.1 Comprehensive API docstrings
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Add detailed docstrings to all public methods:

- Parameter descriptions with types and defaults
- Return value structure for complex Records
- Exception contracts (which exceptions can be raised)
- Usage examples per method
- Cross-references to related methods

**Target modules**:

- ``client.py`` — lifecycle methods
- ``_trading.py`` — all trade methods
- ``_market_data.py`` — all market data methods
- ``_order_helpers.py`` — all convenience methods
- ``_account.py`` — all account methods
- ``_push_handlers.py`` — all registration methods

24.2 Migration guide
^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``docs/migration.rst``:

- v0.x to v1.0 breaking changes
- Error handling contract changes
- New subscription handle API
- Health monitoring setup
- Rate limiter changes
- Callback error isolation behavior

24.3 Official MT5 API compatibility reference
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Update ``docs/python_api_compat.rst``:

- Per-method compatibility matrix (pymt5 vs official API)
- Missing fields in AccountInfo, SymbolInfo
- Behavioral differences (async vs sync)
- Limitations and workarounds
- Data type mapping (Record vs official types)

24.4 Protocol version tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM (carried from Phase 19.1)

Track MT5 server build number from bootstrap response:

- Extract server build from bootstrap body in ``transport.py``
- Add ``server_build: int`` property
- Log warning for unknown build versions
- Expose via ``client.server_build``

**Tests**:

- Test build extraction from bootstrap response
- Test unknown build version logs warning

24.5 Dev-mode schema validation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW (carried from Phase 19.4)

Enable runtime validation when ``PYMT5_DEBUG=1``:

- Assert field count matches schema after parsing
- Log warning on unparsed trailing bytes
- Validate parsed values against expected ranges
- Zero performance impact when disabled

**Tests**:

- Test validation fires only when env var set
- Test warning on field count mismatch
- Test no performance regression when disabled


Implementation Roadmap
----------------------

.. list-table::
   :header-rows: 1
   :widths: 10 15 50 10

   * - Phase
     - Version
     - Scope
     - Status
   * - 20
     - v0.9.1
     - Critical bug fixes: rate limiter, futures leak, credentials, race conditions
     - **NEXT**
   * - 21
     - v0.9.2
     - Concurrency & data integrity: tick cache, error handlers, volume precision
     - Planned
   * - 22
     - v0.10.0
     - API consistency: callback isolation, subscription handles, health monitoring
     - Planned
   * - 23
     - v1.0.0-rc
     - Testing: integration tests, fuzz, benchmarks, concurrency suite
     - Planned
   * - 24
     - v1.0.0
     - Documentation, migration guide, protocol versioning, release
     - Planned


Priority Summary
----------------

.. list-table::
   :header-rows: 1

   * - Priority
     - Item
     - Phase
   * - CRITICAL
     - Fix rate limiter cancellation safety
     - 20.1
   * - HIGH
     - Fix transport future leak on timeout
     - 20.2
   * - HIGH
     - Fix disconnect callback race condition
     - 20.3
   * - HIGH
     - Replace assert with explicit exception
     - 20.4
   * - HIGH
     - Fix credential clearing
     - 20.5
   * - HIGH
     - Callback error isolation
     - 22.1
   * - HIGH
     - Subscription handle for push handlers
     - 22.2
   * - HIGH
     - Integration test framework
     - 23.1
   * - HIGH
     - Comprehensive API docstrings
     - 24.1
   * - HIGH
     - Migration guide
     - 24.2
   * - MEDIUM
     - Fix silent error swallowing in close()
     - 20.6
   * - MEDIUM
     - Fix tick cache race condition
     - 21.1
   * - MEDIUM
     - Fix callback error handler isolation
     - 21.2
   * - MEDIUM
     - Fix volume precision loss
     - 21.3
   * - MEDIUM
     - Strengthen order validation
     - 21.4
   * - MEDIUM
     - Configurable tick history limits
     - 21.5
   * - MEDIUM
     - Standardize error handling contract
     - 22.3
   * - MEDIUM
     - Connection health monitoring
     - 22.4
   * - MEDIUM
     - Enhance fuzz testing
     - 23.2
   * - MEDIUM
     - Enhance benchmarks
     - 23.3
   * - MEDIUM
     - Async race condition test suite
     - 23.4
   * - MEDIUM
     - Official MT5 API compatibility reference
     - 24.3
   * - MEDIUM
     - Protocol version tracking
     - 24.4
   * - LOW
     - Conversion rate caching
     - 22.5
   * - LOW
     - Dev-mode schema validation
     - 24.5


Verification Checklist
----------------------

After each phase:

1. ``python -m pytest tests/ -v --tb=short`` — all tests pass
2. ``ruff check pymt5/ tests/`` — no lint errors
3. ``python -m mypy pymt5/ --ignore-missing-imports`` — no type errors
4. ``cd docs && python -m sphinx . _build/html -W --keep-going`` — docs build
5. ``python -m pytest tests/ -v --cov=pymt5 --cov-report=term-missing`` — coverage ≥ 99%
6. No new ``except Exception: pass`` patterns (grep check)
7. No new ``assert`` for runtime validation (grep check)
