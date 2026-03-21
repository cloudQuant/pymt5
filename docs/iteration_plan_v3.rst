Iteration Plan v3
=================

.. contents:: Table of Contents
   :depth: 3
   :local:

Project Status Summary
----------------------

**Current version**: v0.8.0 (post Phase 1-5 + cleanup rounds)

**Codebase metrics**:

- Source code: ~4,900 lines across 23 modules
- Test code: 830 tests, 99% coverage (2750/2789 statements)
- CI: 9-matrix (3 Python versions x 3 OS)
- Documentation: Sphinx + 8 examples + 2 iteration plans
- Mypy: 0 errors (strict_optional, disallow_incomplete_defs enabled)
- Ruff: 0 lint errors

**Architecture (5 mixins + 1 core client)**:

.. code-block:: text

   MT5WebClient
   +-- _MarketDataMixin      (845 lines)  -- symbols, ticks, bars, book, subscriptions
   +-- _TradingMixin         (698 lines)  -- positions, orders, trade execution, validation
   +-- _OrderHelpersMixin    (502 lines)  -- buy/sell/close/modify convenience methods
   +-- _AccountMixin         (488 lines)  -- account info, OTP, verification
   +-- _PushHandlersMixin    (334 lines)  -- push notification handlers

   + Infrastructure:
     +-- Transport            (228 lines)  -- WebSocket, state machine, rate limiter
     +-- Protocol codec       (270 lines)  -- dispatch-table based serialization
     +-- Schemas             (1076 lines)  -- 24 binary protocol schemas
     +-- Support modules      (340 lines)  -- events, logging, metrics, subscription, etc.

**Completed in this round** (v3 cleanup):

- Fixed rate limiter race condition with ``asyncio.Lock`` (CRITICAL)
- Fixed ``__version__`` fallback from "0.7.0" to "0.8.0"
- Exported ``MetricsCollector`` in ``__init__.py``
- Narrowed 4 broad ``except Exception`` catches to specific types
- Added ``PYMT5_LOG_LEVEL`` environment variable support
- Added ``iteration_plan_v2.rst`` to docs toctree
- Removed stale ``aiohttp`` reference from docs
- Updated tests to use custom exception types (830 tests, all passing)


Remaining Gaps
--------------

**Coverage gaps (39 uncovered lines)**:

.. code-block:: text

   transport.py:72,119,166,175-176,202,209    -- connection state transitions, error paths
   exceptions.py:47-50                         -- TradeError attribute initialization
   _market_data.py:200-202,398-399,406-407     -- exception paths in get_full_symbol_info()
   _push_handlers.py:200,283-284              -- handler dispatch edge cases
   protocol.py:246,251,256,260                 -- variable-length field error branches
   _parsers.py:82,455-457,494                  -- timestamp coercion edge cases
   client.py:272,301                           -- reconnection edge cases
   _logging.py:32                              -- structlog import path
   __init__.py:10-11                           -- ImportError fallback path

**Code quality observations**:

1. ``_market_data.py`` at 845 lines (slightly over 800 guideline)
2. ``schemas.py`` at 1076 lines (data-only, acceptable but monitor)
3. Symbol cache has no TTL/invalidation mechanism
4. No integration test framework (Phase 6.1 in v2)
5. No performance benchmarks (Phase 6.2 in v2)
6. No PyPI release automation (Phase 8.3 in v2)
7. No typed push handler callbacks (Phase 9.2 in v2)
8. No protocol version tracking (Phase 7.1 in v2)


Phase 10: Code Hardening (v0.9.0)
----------------------------------

Goal: Close coverage gaps, improve error paths, prepare for v1.0.

10.1 Close coverage to 99.5%
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Target the 39 uncovered lines with focused tests:

**transport.py** (7 lines):

- Line 72: ``connect()`` when ``ws is not None`` triggers ``close()`` first
- Line 119: ``_metrics.on_connect()`` — test with metrics collector mock
- Line 166: ``_metrics.on_command_sent()`` — test with metrics collector mock
- Lines 175-176: timeout future cleanup when future already removed
- Line 202: ``_metrics.on_disconnect()`` — test recv_loop disconnect path
- Line 209: ``_dispatch`` with metrics enabled

**exceptions.py** (4 lines):

- Lines 47-50: ``TradeError`` with ``retcode``, ``symbol``, ``action`` kwargs

**_market_data.py** (6 lines):

- Lines 200-202: ``get_full_symbol_info()`` when symbol not in ``_full_symbols``
- Lines 398-399: secondary exception path
- Lines 406-407: edge case in symbol resolution

**protocol.py** (4 lines):

- Lines 246, 251, 256, 260: error branches for malformed variable-length fields

**_parsers.py** (5 lines):

- Line 82: timestamp coercion edge case (negative or zero)
- Lines 455-457: batch tick parsing edge case
- Line 494: copy tick record edge case

**_push_handlers.py** (3 lines):

- Line 200: handler dispatch with no registered handlers
- Lines 283-284: book push handler edge case

**client.py** (2 lines):

- Line 272: reconnection metrics callback
- Line 301: reconnection success metrics callback

10.2 Symbol cache TTL
^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Add optional cache invalidation to ``_MarketDataMixin``:

- ``symbol_cache_ttl: float = 0`` parameter (0 = no expiry)
- Store ``_symbols_loaded_at: float`` timestamp
- ``load_symbols()`` skips refresh if TTL not expired
- ``invalidate_symbol_cache()`` manual reset method

10.3 Extract currency methods from _market_data.py
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Move currency conversion methods to ``_currency.py`` mixin (~150 lines):

- ``currency_rate_get()``
- ``_resolve_conversion_rates()``
- ``_calc_profit_raw()``
- ``_calc_margin_raw()``

Reduces ``_market_data.py`` from 845 to ~695 lines.


Phase 11: Resilience & Observability (v1.0.0)
----------------------------------------------

Goal: Production-ready reliability features.

11.1 Typed push handler callbacks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Add typed callback registration using ``events.py`` dataclasses:

.. code-block:: python

   # New typed handler registration
   client.on_tick_event(lambda event: ...)  # event: TickEvent
   client.on_book_event(lambda event: ...)  # event: BookEvent
   client.on_trade_result_event(lambda event: ...)  # event: TradeResultEvent
   client.on_account_event(lambda event: ...)  # event: AccountEvent

**Implementation**:

- Add ``_typed_tick_handlers``, ``_typed_book_handlers``, etc. to ``_PushHandlersMixin``
- Create event objects from raw push data in handler dispatch
- Keep existing untyped handlers for backward compatibility

11.2 Connection health monitoring
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Add health check mechanism to transport:

- ``health_check() -> HealthStatus`` — returns latency, state, last message time
- ``on_health_degraded(callback)`` — notify when ping latency exceeds threshold
- Expose via ``client.health`` property
- Useful for monitoring dashboards in trading systems

11.3 Graceful degradation on recv_loop errors
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Improve error handling when user callbacks raise in ``_dispatch()``:

- Catch and log callback errors without disconnecting
- Add ``on_callback_error(callback)`` for error reporting
- Prevent a single bad callback from killing the connection


Phase 12: Testing Infrastructure (v1.1.0)
-------------------------------------------

Goal: Comprehensive testing beyond unit tests.

12.1 Integration test framework
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``tests/test_integration.py`` gated by ``PYMT5_INTEGRATION=1``:

- Bootstrap handshake test (connect, receive bootstrap, disconnect)
- Login/logout cycle test
- Symbol load test (verify schema parsing against live data)
- Tick subscription test (subscribe, receive >= 1 tick, unsubscribe)
- Heartbeat round-trip test

**Implementation**:

- ``pytest.mark.integration`` marker
- ``conftest.py`` fixture for test server credentials from env vars
- CI job on schedule (not per-commit)
- Skip all integration tests if env vars missing

12.2 Performance benchmarks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Create ``tests/test_benchmarks.py`` using ``pytest-benchmark``:

- ``SeriesCodec.serialize()`` throughput
- ``SeriesCodec.parse()`` throughput
- ``pack_outer()`` / ``unpack_outer()`` throughput
- ``AESCipher.encrypt()`` / ``decrypt()`` throughput
- Rate limiter acquire latency under load

12.3 Fuzz testing for protocol parser
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Create ``tests/test_fuzz.py`` using ``hypothesis``:

- Random byte sequences → ``parse_response_frame()`` should not crash
- Random field values → ``SeriesCodec.serialize()`` round-trip
- Malformed frames → graceful error, no memory leaks


Phase 13: Documentation & Release (v1.2.0)
--------------------------------------------

Goal: Community-ready documentation and release pipeline.

13.1 Protocol reference documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``docs/protocol_reference.rst``:

- Frame format specification (outer header, inner header, body)
- Key exchange and AES encryption flow
- Session lifecycle diagram (bootstrap -> login -> authenticated -> close)
- Command ID catalog with request/response schemas
- Field type encoding reference
- Tick/book push notification format

13.2 PyPI release automation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``.github/workflows/release.yml``:

- Trigger on git tag ``v*.*.*``
- Run full test suite across all platforms
- Build sdist and wheel
- Publish to PyPI via trusted publisher (OIDC)
- Create GitHub Release with changelog
- Publish docs to GitHub Pages

13.3 API reference documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Generate comprehensive API docs with Sphinx autodoc:

- All public methods with docstrings
- Usage examples per method category
- Error handling guide with exception hierarchy
- Configuration reference (rate_limit, reconnect, metrics)


Phase 14: Advanced Features (v1.3.0)
--------------------------------------

Goal: Production trading enhancements.

14.1 Protocol version tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Track MT5 server build number from bootstrap response:

- Extract server build from bootstrap body in ``transport.py``
- Add ``server_build: int`` property
- Log warning for unknown build versions
- Expose via ``client.server_build``

14.2 Order manager with tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Create ``pymt5/_order_manager.py``:

- Track pending and active orders by ID
- Automatic state updates from trade push notifications
- Order lifecycle events (created, filled, partially_filled, canceled)
- Position aggregation per symbol
- PnL tracking per position

14.3 Connection pool
^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Support multiple concurrent MT5 connections:

.. code-block:: python

   pool = MT5ConnectionPool(accounts=[
       {"server": "...", "login": 123, "password": "..."},
       {"server": "...", "login": 456, "password": "..."},
   ])
   async with pool:
       await pool.broadcast_subscribe_ticks([symbol_id])


Verification Checklist
----------------------

After each phase:

1. ``python -m pytest tests/ -v --tb=short`` -- all 830+ tests pass
2. ``ruff check pymt5/ tests/`` -- no lint errors
3. ``python -m mypy pymt5/ --ignore-missing-imports`` -- no type errors
4. ``cd docs && python -m sphinx . _build/html -W --keep-going`` -- docs build
5. ``python -m pytest tests/ -v --cov=pymt5 --cov-report=term-missing`` -- coverage >= 99%


Priority Summary
----------------

.. list-table::
   :header-rows: 1

   * - Priority
     - Item
     - Phase
   * - HIGH
     - Close coverage to 99.5%
     - 10.1
   * - HIGH
     - Typed push handler callbacks
     - 11.1
   * - HIGH
     - Integration test framework
     - 12.1
   * - HIGH
     - Protocol reference docs
     - 13.1
   * - HIGH
     - PyPI release automation
     - 13.2
   * - HIGH
     - Protocol version tracking
     - 14.1
   * - MEDIUM
     - Symbol cache TTL
     - 10.2
   * - MEDIUM
     - Connection health monitoring
     - 11.2
   * - MEDIUM
     - Graceful degradation on callback errors
     - 11.3
   * - MEDIUM
     - Performance benchmarks
     - 12.2
   * - MEDIUM
     - API reference docs
     - 13.3
   * - MEDIUM
     - Order manager
     - 14.2
   * - LOW
     - Extract _currency.py
     - 10.3
   * - LOW
     - Fuzz testing
     - 12.3
   * - LOW
     - Connection pool
     - 14.3
