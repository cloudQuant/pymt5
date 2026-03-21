Iteration Plan v2
=================

.. contents:: Table of Contents
   :depth: 3
   :local:

Project Status Summary
----------------------

**Current version**: v0.9.0 (post Phase 1-5 implementation)

**Codebase metrics**:

- Source code: ~4,827 lines across 23 modules
- Test code: 826 tests, 99% coverage (2776/2815 statements)
- CI: 9-matrix (3 Python versions × 3 OS)
- Documentation: Sphinx + 8 examples
- Mypy: 0 errors (strict_optional, disallow_incomplete_defs enabled)
- Ruff: 0 lint errors

**Architecture (5 mixins + 1 core client)**:

.. code-block:: text

   MT5WebClient
   ├── _MarketDataMixin      (845 lines)  — symbols, ticks, bars, book, subscriptions
   ├── _TradingMixin         (698 lines)  — positions, orders, trade execution, validation
   ├── _OrderHelpersMixin    (502 lines)  — buy/sell/close/modify convenience methods
   ├── _AccountMixin         (488 lines)  — account info, OTP, verification
   └── _PushHandlersMixin    (334 lines)  — push notification handlers

   + Infrastructure:
     ├── Transport            (227 lines)  — WebSocket, state machine, rate limiter
     ├── Protocol codec       (270 lines)  — dispatch-table based serialization
     ├── Schemas             (1076 lines)  — 24 binary protocol schemas
     └── Support modules      (316 lines)  — events, logging, metrics, subscription, etc.

**Completed phases** (from iteration_plan.rst):

- ✅ Phase 1: Code Quality & Internal Cleanup
- ✅ Phase 2: Transport & Reliability
- ✅ Phase 3: Observability & Diagnostics
- ✅ Phase 4: API & Usability
- ✅ Phase 5: Protocol & Codec

**Remaining gaps identified**:

1. No integration test framework (Phase 6.1)
2. No performance benchmarks (Phase 6.2)
3. Protocol version tracking not implemented (Phase 5.2)
4. No dev-mode schema validation (Phase 5.3)
5. ``_market_data.py`` slightly over 800-line limit (845 lines)
6. ``schemas.py`` at 1076 lines (acceptable but monitor)
7. No PyPI release automation
8. No protocol documentation for external developers
9. 39 uncovered lines remain (all edge cases)
10. No typed callback registration in push handlers


Phase 6: Testing & CI Hardening (v1.0.0)
-----------------------------------------

Goal: Production-ready testing infrastructure, close coverage gaps.

6.1 Integration test framework
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``tests/test_integration.py`` with demo server connectivity tests
gated by environment variable ``PYMT5_INTEGRATION=1``.

**Scope**:

- Bootstrap handshake test (connect, receive bootstrap, disconnect)
- Login/logout cycle test
- Symbol load test (verify schema parsing against live data)
- Tick subscription test (subscribe, receive ≥1 tick, unsubscribe)
- Heartbeat round-trip test

**Implementation**:

- Add ``pytest.mark.integration`` marker
- Add ``conftest.py`` fixture for test server credentials from env vars
- Add CI job that runs integration tests on schedule (not per-commit)
- Skip all integration tests if env vars missing

6.2 Performance benchmarks
^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Create ``tests/test_benchmarks.py`` using ``pytest-benchmark``.

**Benchmarks**:

- ``SeriesCodec.serialize()`` throughput (ops/sec for typical schemas)
- ``SeriesCodec.parse()`` throughput
- ``pack_outer()`` / ``unpack_outer()`` throughput
- ``AESCipher.encrypt()`` / ``decrypt()`` throughput
- Rate limiter acquire latency under load

**Implementation**:

- Add ``pytest-benchmark`` to dev dependencies
- Gate benchmarks behind ``pytest.mark.benchmark`` marker
- Store baseline results in ``benchmarks/`` directory
- Compare in CI to detect regressions

6.3 Close remaining coverage gaps
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Target the 39 uncovered lines:

- ``transport.py``: Connection state transitions, error paths (lines 72, 119, 166, 175-176, 202, 209)
- ``exceptions.py``: ``TradeError`` attribute initialization (lines 47-50)
- ``_market_data.py``: Exception paths in ``get_full_symbol_info()`` (lines 200-202, 398-399)
- ``_push_handlers.py``: Handler dispatch edge cases (lines 200, 283-284)
- ``protocol.py``: Variable-length field error branches (lines 246, 251, 256, 260)
- ``_parsers.py``: Timestamp coercion edge cases (lines 82, 455-457, 494)
- ``client.py``: Reconnection edge cases (lines 272, 301)

Goal: ≥99.5% coverage.

6.4 Mutation testing
^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Use ``mutmut`` to verify test quality beyond line coverage.

- Run on critical modules: ``protocol.py``, ``transport.py``, ``_trading.py``
- Fix surviving mutants by adding targeted assertions
- Add to CI as optional quality gate


Phase 7: Protocol Robustness (v1.1.0)
--------------------------------------

Goal: Handle protocol evolution and improve debugging.

7.1 Protocol version tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Track the MT5 server build number from bootstrap response and use it
to select appropriate schemas.

**Implementation**:

- Extract server build from bootstrap response in ``transport.py``
- Add ``server_build: int`` property to transport
- Create schema version registry in ``schemas.py`` mapping build ranges to schema variants
- Log warning when using schemas from a different build range
- Expose ``client.server_build`` for user inspection

7.2 Dev-mode schema validation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Enable runtime validation when ``PYMT5_DEBUG=1`` environment variable is set.

**Checks**:

- Assert field count matches schema length after parsing
- Log warning on unparsed trailing bytes
- Validate parsed values against expected ranges (e.g., timestamps > 0)
- Report unexpected zero-length variable fields

**Implementation**:

- Add ``_debug_validate()`` hook in ``SeriesCodec.parse()``
- Check ``os.environ.get("PYMT5_DEBUG")`` once at import time
- Zero performance impact when disabled (no-op function pointer)

7.3 Protocol debugging CLI
^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Create ``pymt5/__main__.py`` for command-line protocol analysis.

.. code-block:: bash

   python -m pymt5 dump --server wss://... --login ... --frames 100

**Features**:

- Hex dump of encrypted/decrypted frames
- Schema auto-detection by command ID
- Parsed field display with type annotations
- Timestamp conversion to human-readable format
- Export to JSON for external analysis


Phase 8: Documentation & Ecosystem (v1.2.0)
--------------------------------------------

Goal: Community-ready documentation and ecosystem integration.

8.1 Protocol reference documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``docs/protocol_reference.rst`` documenting the MT5 WebSocket protocol.

**Content**:

- Frame format specification (outer header, inner header, body)
- Key exchange and AES encryption flow
- Session lifecycle diagram (bootstrap → login → authenticated → close)
- Command ID catalog with request/response schemas
- Field type encoding reference (PROP_* types, endianness, padding)
- Tick/book push notification format

8.2 API reference documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Generate comprehensive API docs using Sphinx autodoc.

- All public methods with docstrings
- Usage examples for each method category (trading, market data, account)
- Error handling guide with exception hierarchy diagram
- Configuration reference (rate_limit, reconnect, metrics)

8.3 PyPI release automation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Create ``.github/workflows/release.yml`` for automated publishing.

**Triggers**: Git tag matching ``v*.*.*``

**Pipeline**:

1. Run full test suite (all 3 Python versions, all 3 OS)
2. Run mypy and ruff checks
3. Build sdist and wheel (``python -m build``)
4. Publish to PyPI via trusted publisher (OIDC)
5. Create GitHub Release with auto-generated changelog
6. Publish documentation to GitHub Pages

8.4 Strategy framework adapters
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Create adapter packages for popular Python trading frameworks.

**Candidates**:

- ``pymt5-backtrader``: Backtrader data feed adapter
- ``pymt5-zipline``: Zipline data bundle adapter
- ``pymt5-vectorbt``: VectorBT data source adapter

**Scope**: Separate packages, not part of core pymt5. Create skeleton repos
with basic data feed integration.


Phase 9: Advanced Features (v1.3.0)
------------------------------------

Goal: Production trading enhancements.

9.1 Order manager with tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Create ``pymt5/_order_manager.py`` with ``OrderManager`` class.

**Features**:

- Track all pending and active orders by ID
- Automatic state updates from trade push notifications
- Order lifecycle events (created, filled, partially_filled, canceled)
- Position aggregation per symbol
- PnL tracking per position

9.2 Typed push handler callbacks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Add typed callback registration to ``_PushHandlersMixin``.

**API**:

.. code-block:: python

   # Current (untyped)
   client.on_tick(lambda data: ...)

   # New (typed)
   client.on_tick_typed(lambda event: ...)  # TickEvent
   client.on_book_typed(lambda event: ...)  # BookEvent
   client.on_trade_result_typed(lambda event: ...)  # TradeResultEvent

**Implementation**:

- Use existing ``TickEvent``, ``BookEvent``, etc. from ``events.py``
- Create event from raw push data in handler dispatch
- Keep untyped handlers for backward compatibility

9.3 Connection pool
^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Support multiple concurrent MT5 connections for multi-account strategies.

**API**:

.. code-block:: python

   pool = MT5ConnectionPool(accounts=[
       {"server": "...", "login": 123, "password": "..."},
       {"server": "...", "login": 456, "password": "..."},
   ])
   async with pool:
       await pool.broadcast_subscribe_ticks([symbol_id])
       # Ticks from all accounts

9.4 Extract _currency.py mixin
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

If ``_market_data.py`` grows beyond 900 lines, extract currency conversion
methods (``currency_rate_get``, ``_resolve_conversion_rates``,
``_calc_profit_raw``, ``_calc_margin_raw``) into a separate
``_currency.py`` mixin (~150 lines).


Verification Checklist
----------------------

After each phase:

1. ``python -m pytest tests/ -v --tb=short`` — all 826+ tests pass
2. ``ruff check pymt5/ tests/`` — no lint errors
3. ``python -m mypy pymt5/ --ignore-missing-imports`` — no type errors
4. ``cd docs && python -m sphinx . _build/html -W --keep-going`` — docs build
5. ``python -m pytest tests/ -v --cov=pymt5 --cov-report=term-missing`` — coverage ≥ 99%


Priority Summary
----------------

.. list-table::
   :header-rows: 1

   * - Priority
     - Item
     - Phase
   * - HIGH
     - Integration test framework
     - 6.1
   * - HIGH
     - Protocol version tracking
     - 7.1
   * - HIGH
     - Protocol reference docs
     - 8.1
   * - HIGH
     - PyPI release automation
     - 8.3
   * - MEDIUM
     - Performance benchmarks
     - 6.2
   * - MEDIUM
     - Close coverage gaps
     - 6.3
   * - MEDIUM
     - Dev-mode schema validation
     - 7.2
   * - MEDIUM
     - API reference docs
     - 8.2
   * - MEDIUM
     - Order manager
     - 9.1
   * - MEDIUM
     - Typed push handlers
     - 9.2
   * - LOW
     - Mutation testing
     - 6.4
   * - LOW
     - Protocol debugging CLI
     - 7.3
   * - LOW
     - Strategy framework adapters
     - 8.4
   * - LOW
     - Connection pool
     - 9.3
   * - LOW
     - Extract _currency.py
     - 9.4
