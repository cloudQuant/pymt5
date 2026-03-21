Iteration Plan v4
=================

.. contents:: Table of Contents
   :depth: 3
   :local:

Project Status Summary
----------------------

**Current version**: v0.9.0

**Codebase metrics**:

- Source code: ~4,900 lines across 23 modules
- Test code: 830 tests, 99% coverage (2750/2789 statements, 39 uncovered lines)
- CI: 9-matrix (3 Python versions × 3 OS)
- Documentation: Sphinx + 8 guides + 3 iteration plans
- Mypy: 0 errors (strict_optional, disallow_incomplete_defs enabled)
- Ruff: 0 lint errors

**Architecture (5 mixins + 1 core client)**:

.. code-block:: text

   MT5WebClient (524 lines)
   ├── _MarketDataMixin      (845 lines)  — symbols, ticks, bars, book, subscriptions
   ├── _TradingMixin         (698 lines)  — positions, orders, trade execution
   ├── _OrderHelpersMixin    (502 lines)  — buy/sell/close/modify convenience
   ├── _AccountMixin         (488 lines)  — account info, OTP, verification
   └── _PushHandlersMixin    (334 lines)  — push notification handlers

   + Infrastructure:
     ├── Transport            (227 lines)  — WebSocket, state machine, rate limiter
     ├── Protocol codec       (270 lines)  — dispatch-table serialization
     ├── Schemas             (1076 lines)  — 24 binary protocol schemas
     └── Support modules      (340 lines)  — events, logging, metrics, etc.

**Completed in previous iterations**:

- ✅ Phase 1-5: Core quality, transport, observability, API, protocol
- ✅ Phase v3: Rate limiter fix, exception narrowing, env log level


Gap Analysis
------------

**Coverage gaps (39 uncovered lines)**:

.. code-block:: text

   transport.py:72,119,166,175-176,202,209    — metrics callbacks, state edges
   exceptions.py:47-50                         — TradeError optional attrs
   _market_data.py:200-202,398-399,406-407     — symbol resolution error paths
   _push_handlers.py:200,283-284              — handler dispatch edges
   protocol.py:246,251,256,260                 — variable-length field errors
   _parsers.py:82,455-457,494                  — timestamp/tick edge cases
   client.py:272,301                           — reconnection metric hooks
   _logging.py:32                              — structlog import
   __init__.py:10-11                           — ImportError fallback

**Functional gaps**:

1. No symbol cache TTL — stale data risk in long-running sessions
2. No typed push handler callbacks — raw dicts instead of dataclasses
3. No connection health monitoring — no latency or heartbeat metrics
4. No callback error isolation — one bad callback kills the connection
5. ``_market_data.py`` at 845 lines (over 800-line guideline)

**Infrastructure gaps**:

6. No integration test framework — only offline unit tests
7. No performance benchmarks — no regression detection
8. No fuzz testing — protocol parser not stress-tested
9. No PyPI release automation — manual publishing
10. No protocol reference docs — only code comments

**Advanced feature gaps**:

11. No protocol version tracking — hardcoded OUTER_PROTOCOL_VERSION
12. No order lifecycle manager — no automatic state tracking
13. No connection pool — single account per client


Phase 15: Coverage & Code Health (v0.9.0)
-----------------------------------------

Goal: Close coverage gaps, extract oversized module, harden error paths.

15.1 Close coverage to 99.5%+
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Target the 39 uncovered lines with focused tests.

**transport.py** (7 lines):

- Line 72: ``connect()`` when ``ws is not None`` — triggers close-then-reconnect
- Lines 119, 166, 202, 209: metrics callback hooks — test with ``MetricsCollector`` mock
- Lines 175-176: timeout future cleanup when future already removed from dict

**exceptions.py** (4 lines):

- Lines 47-50: ``TradeError`` with ``retcode``, ``symbol``, ``action`` keyword args

**_market_data.py** (6 lines):

- Lines 200-202: ``get_full_symbol_info()`` symbol not in cache after fetch
- Lines 398-399, 406-407: secondary exception paths in symbol resolution

**protocol.py** (4 lines):

- Lines 246, 251, 256, 260: malformed variable-length fields (short buffer)

**_parsers.py** (5 lines):

- Line 82: timestamp coercion with zero/negative value
- Lines 455-457: batch tick parsing empty/malformed input
- Line 494: copy tick record edge case

**_push_handlers.py** (3 lines):

- Line 200: dispatch with no registered handlers
- Lines 283-284: book push handler with empty book data

**client.py** (2 lines):

- Lines 272, 301: reconnection success/failure metric callbacks

**_logging.py** (1 line):

- Line 32: structlog import available path

**__init__.py** (2 lines):

- Lines 10-11: ``importlib.metadata`` fallback

15.2 Extract currency conversion mixin
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Move currency conversion methods from ``_market_data.py`` (845 lines) to ``_currency.py``:

- ``currency_rate_get()``
- ``_resolve_conversion_rates()``
- ``_calc_profit_raw()``
- ``_calc_margin_raw()``
- Related helper methods

Reduces ``_market_data.py`` from ~845 to ~695 lines (within 800-line guideline).

15.3 Symbol cache TTL
^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Add optional cache invalidation to ``_MarketDataMixin``:

- ``symbol_cache_ttl: float = 0`` constructor parameter (0 = no expiry)
- Store ``_symbols_loaded_at: float`` timestamp on ``load_symbols()``
- ``load_symbols()`` checks TTL: skips if not expired, reloads if expired
- ``invalidate_symbol_cache()`` manual reset method
- Tests for TTL logic (expired, not expired, manual invalidation)


Phase 16: Resilience & Typed Events (v0.10.0)
----------------------------------------------

Goal: Production-ready reliability features and type-safe event system.

16.1 Typed push handler callbacks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Add typed callback registration using ``events.py`` dataclasses:

.. code-block:: python

   # New typed handlers (supplement existing untyped ones)
   client.on_tick_event(lambda event: ...)           # event: TickEvent
   client.on_book_event(lambda event: ...)           # event: BookEvent
   client.on_trade_result_event(lambda event: ...)   # event: TradeResultEvent
   client.on_account_event(lambda event: ...)        # event: AccountEvent

**Implementation**:

- Add ``_typed_tick_handlers``, ``_typed_book_handlers``, etc. lists to ``_PushHandlersMixin``
- Create event objects from raw push data in handler dispatch
- Keep existing untyped ``on_tick()`` etc. for backward compatibility
- Return ``SubscriptionHandle`` from typed registrations for easy cleanup

16.2 Callback error isolation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Prevent a single bad callback from killing the connection:

- Wrap each callback invocation in try/except in ``_dispatch()``
- Log callback errors with full traceback via ``get_logger()``
- Add ``on_callback_error(callback)`` registration for error reporting
- Continue processing remaining callbacks and messages after error
- Track callback error count in metrics

16.3 Connection health monitoring
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

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
   client.on_health_degraded(lambda status: ...)  # latency > threshold

**Implementation**:

- Measure ping round-trip time in heartbeat loop
- Track ``_last_message_at`` timestamp in recv loop
- Expose ``client.health_check()`` async method
- Optional ``health_threshold_ms`` parameter (default 5000)
- Emit ``on_health_degraded`` when threshold exceeded


Phase 17: Testing Infrastructure (v1.0.0)
------------------------------------------

Goal: Comprehensive testing beyond unit tests. Mark as v1.0.0 release candidate.

17.1 Integration test framework
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``tests/test_integration.py`` gated by ``PYMT5_INTEGRATION=1``:

- Bootstrap handshake test (connect, receive bootstrap, disconnect)
- Login/logout cycle test
- Symbol load test (verify schema parsing against live data)
- Tick subscription test (subscribe, receive ≥1 tick, unsubscribe)
- Heartbeat round-trip test
- Order placement test (demo account, minimal lot)

**Implementation**:

- ``pytest.mark.integration`` marker
- ``conftest.py`` fixture for credentials from env vars
- CI job on schedule (not per-commit), skip if env vars missing
- Timeout guards on all integration tests (30s max)

17.2 Performance benchmarks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Create ``tests/test_benchmarks.py`` using ``pytest-benchmark``:

- ``SeriesCodec.serialize()`` throughput (ops/sec)
- ``SeriesCodec.parse()`` throughput
- ``pack_outer()`` / ``unpack_outer()`` throughput
- ``AESCipher.encrypt()`` / ``decrypt()`` throughput
- Rate limiter acquire latency under load
- Symbol cache lookup throughput (name → ID)

**Implementation**:

- Add ``pytest-benchmark`` to dev dependencies
- Gate behind ``pytest.mark.benchmark`` marker
- Store baselines in ``benchmarks/`` directory
- Compare in CI to detect regressions (±10% threshold)

17.3 Fuzz testing for protocol parser
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Create ``tests/test_fuzz.py`` using ``hypothesis``:

- Random byte sequences → ``parse_response_frame()`` should not crash
- Random field values → ``SeriesCodec.serialize()`` round-trip
- Malformed frames → graceful ProtocolError, no memory leaks
- Truncated messages → proper error handling


Phase 18: Documentation & Release (v1.0.0)
-------------------------------------------

Goal: Community-ready documentation and automated release pipeline.

18.1 Protocol reference documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``docs/protocol_reference.rst``:

- Frame format specification (outer header, inner header, body)
- Key exchange and AES encryption flow diagram
- Session lifecycle (bootstrap → login → authenticated → close)
- Command ID catalog with request/response schemas
- Field type encoding reference (PROP_* types, endianness, padding)
- Tick/book push notification format
- Error response codes and meaning

18.2 PyPI release automation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``.github/workflows/release.yml``:

- Trigger on git tag ``v*.*.*``
- Run full test suite (3 Python × 3 OS)
- Run mypy + ruff checks
- Build sdist and wheel (``python -m build``)
- Publish to PyPI via trusted publisher (OIDC)
- Create GitHub Release with auto-generated changelog
- Publish docs to GitHub Pages

18.3 API reference with Sphinx autodoc
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Enhance Sphinx documentation:

- Autodoc for all public methods
- Usage examples per category (trading, market data, account, events)
- Error handling guide with exception hierarchy diagram
- Configuration reference (rate_limit, reconnect, metrics, TTL)
- Migration guide from v0.x to v1.0


Phase 19: Advanced Features (v1.1.0)
--------------------------------------

Goal: Production trading enhancements post-v1.0.

19.1 Protocol version tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Track MT5 server build number from bootstrap response:

- Extract server build from bootstrap body in ``transport.py``
- Add ``server_build: int`` property
- Log warning for unknown build versions
- Expose via ``client.server_build``

19.2 Order lifecycle manager
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Create ``pymt5/_order_manager.py`` with ``OrderManager`` class:

- Track pending/active orders by ID with automatic updates from trade pushes
- Order lifecycle events: created → filled/partially_filled/canceled
- Position aggregation per symbol
- PnL tracking per position
- Export position snapshot to DataFrame

19.3 Connection pool
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

19.4 Dev-mode schema validation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Enable runtime validation when ``PYMT5_DEBUG=1``:

- Assert field count matches schema after parsing
- Log warning on unparsed trailing bytes
- Validate parsed values against expected ranges
- Zero performance impact when disabled


Implementation Roadmap
----------------------

.. list-table::
   :header-rows: 1
   :widths: 10 15 50 10

   * - Phase
     - Version
     - Scope
     - Status
   * - 15
     - v0.9.0
     - Coverage 99.5%, extract currency mixin, symbol cache TTL
     - **NEXT**
   * - 16
     - v0.10.0
     - Typed events, callback isolation, health monitoring
     - Planned
   * - 17
     - v1.0.0-rc
     - Integration tests, benchmarks, fuzz testing
     - Planned
   * - 18
     - v1.0.0
     - Protocol docs, PyPI automation, API docs
     - Planned
   * - 19
     - v1.1.0
     - Protocol versioning, order manager, connection pool
     - Planned


Priority Summary
----------------

.. list-table::
   :header-rows: 1

   * - Priority
     - Item
     - Phase
   * - HIGH
     - Close coverage to 99.5%
     - 15.1
   * - HIGH
     - Typed push handler callbacks
     - 16.1
   * - HIGH
     - Callback error isolation
     - 16.2
   * - HIGH
     - Integration test framework
     - 17.1
   * - HIGH
     - Protocol reference docs
     - 18.1
   * - HIGH
     - PyPI release automation
     - 18.2
   * - HIGH
     - Protocol version tracking
     - 19.1
   * - MEDIUM
     - Extract currency mixin
     - 15.2
   * - MEDIUM
     - Symbol cache TTL
     - 15.3
   * - MEDIUM
     - Connection health monitoring
     - 16.3
   * - MEDIUM
     - Performance benchmarks
     - 17.2
   * - MEDIUM
     - API reference docs
     - 18.3
   * - MEDIUM
     - Order lifecycle manager
     - 19.2
   * - LOW
     - Fuzz testing
     - 17.3
   * - LOW
     - Connection pool
     - 19.3
   * - LOW
     - Dev-mode schema validation
     - 19.4


Verification Checklist
----------------------

After each phase:

1. ``python -m pytest tests/ -v --tb=short`` — all tests pass
2. ``ruff check pymt5/ tests/`` — no lint errors
3. ``python -m mypy pymt5/ --ignore-missing-imports`` — no type errors
4. ``cd docs && python -m sphinx . _build/html -W --keep-going`` — docs build
5. ``python -m pytest tests/ -v --cov=pymt5 --cov-report=term-missing`` — coverage target met
