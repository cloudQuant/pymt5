Iteration Plan
==============

.. contents:: Table of Contents
   :depth: 3
   :local:

Project Status Summary
----------------------

**Current version**: v0.8.0

**Codebase metrics**:

- Source code: 6,346 lines across 16 modules
- Test code: 12,467 lines, 788 tests, ~99% coverage
- CI: 9-matrix (3 Python versions x 3 OS)
- Documentation: Sphinx + 8 examples

**Architecture strengths**:

- Clean mixin-based composition (4 mixins + 1 core client)
- Async-first design with context managers
- Comprehensive binary protocol codec
- Observer pattern for push notifications
- Command/response correlation with FIFO queues

**Key findings from analysis**:

1. Custom exceptions defined but not fully adopted (RuntimeError/ValueError still used in transport and client)
2. Protocol codec has duplicated dispatch logic between serialize() and parse()
3. Some validation functions exceed 60 lines with nesting depth up to 6
4. Transport layer lacks proper connection state machine
5. No rate limiting for MT5 commands
6. No structured logging (plain logging module)
7. No integration test framework
8. ``aiohttp`` declared as dependency but not used in source code
9. Duplicate constant definitions between ``client.py`` and ``_market_data.py``
10. No subscription lifecycle management (unsubscribe/cleanup)


Phase 1: Code Quality & Internal Cleanup (v0.9.0)
--------------------------------------------------

Goal: Eliminate technical debt, strengthen type safety, remove dead code.

1.1 Adopt custom exceptions throughout
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

``exceptions.py`` defines 8 custom exceptions but the transport and client still
raise bare ``RuntimeError`` and ``ValueError``. Migrate all raises:

- ``transport.py:72-73`` — ``RuntimeError("bootstrap failed")`` -> ``MT5ConnectionError``
- ``transport.py:110`` — ``RuntimeError("transport not ready")`` -> ``SessionError``
- ``transport.py:112`` — ``RuntimeError("websocket not connected")`` -> ``MT5ConnectionError``
- ``client.py:307-311`` — ``RuntimeError("cmd=52 is only safe...")`` -> ``SessionError``
- ``client.py:413`` — ``ValueError("cid must be 16 bytes")`` -> ``ValidationError``
- ``transport.py:122`` — ``TimeoutError`` -> ``MT5TimeoutError``

**Files**: ``transport.py``, ``client.py``

1.2 Remove unused ``aiohttp`` dependency
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

``aiohttp`` is listed in ``pyproject.toml`` dependencies but grep shows zero imports
in the source tree. Either remove it or document its intended future use.

**File**: ``pyproject.toml``

1.3 Deduplicate constant definitions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

``FOREX_CALC_MODES``, ``FUTURES_CALC_MODES``, ``CFD_CALC_MODES``,
``OPTION_CALC_MODES``, ``BOND_CALC_MODES``, ``COLLATERAL_CALC_MODE`` are defined
identically in both ``client.py`` (lines 82-87) and ``_market_data.py``
(lines 67-72). Move them to ``constants.py`` and import from there.

**Files**: ``client.py``, ``_market_data.py``, ``constants.py``

1.4 Stricter mypy configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Current mypy config uses ``--no-strict-optional``. Progressively tighten:

1. Enable ``--strict-optional`` and fix resulting issues
2. Enable ``--disallow-untyped-defs``
3. Enable ``--disallow-any-generics``

**Files**: ``pyproject.toml``, all ``pymt5/*.py``

1.5 Reduce validation function complexity
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

``_validate_order_check_request()`` in ``_trading.py`` is 62 lines with nesting
depth 6. Extract sub-validations:

- ``_validate_deal_or_pending()`` — DEAL/PENDING action checks
- ``_validate_sltp_request()`` — SLTP action checks
- ``_validate_modify_remove()`` — MODIFY/REMOVE action checks

**Files**: ``_trading.py``


Phase 2: Transport & Reliability (v1.0.0)
-----------------------------------------

Goal: Production-grade transport layer, proper state machine, rate limiting.

2.1 Transport connection state machine
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Replace boolean flags (``is_ready``, ``_logged_in``, ``_bootstrap_pristine``)
with an explicit state enum:

.. code-block:: python

   class TransportState(enum.Enum):
       DISCONNECTED = "disconnected"
       CONNECTING = "connecting"
       BOOTSTRAPPED = "bootstrapped"   # key exchange done
       AUTHENTICATED = "authenticated" # login complete
       CLOSING = "closing"

Benefits:

- Prevents invalid state transitions
- Makes state checks explicit (``self.state == TransportState.AUTHENTICATED``)
- Enables proper logging of state transitions
- Eliminates ``_bootstrap_pristine`` flag

**Files**: ``transport.py``, ``client.py``

2.2 Command rate limiting
^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

MT5 servers may drop connections on rapid command bursts (noted in README for
``trader_params``). Add a configurable rate limiter:

.. code-block:: python

   class MT5WebClient:
       def __init__(self, ..., max_commands_per_second: float = 10.0):
           self._rate_limiter = asyncio.Semaphore(...)

Options:

- Token bucket algorithm
- Per-command-type limits (trading commands stricter than data queries)
- Configurable burst allowance

**Files**: ``transport.py`` or new ``_rate_limiter.py``

2.3 Reconnection improvements
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Current reconnection is linear backoff (``delay * attempt``). Improve:

- Exponential backoff with jitter: ``delay * 2^attempt + random(0, delay)``
- Maximum backoff cap (e.g., 60 seconds)
- Reconnection event callbacks (``on_reconnecting``, ``on_reconnected``)
- Symbol cache invalidation on reconnect (prices may have changed)

**Files**: ``client.py``

2.4 Graceful shutdown improvements
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

- Add ``asyncio.Event`` for clean shutdown signaling
- Wait for in-flight commands before closing
- Flush tick history on disconnect (optional callback)
- Cancel pending futures with a specific ``SessionError`` instead of generic ``RuntimeError``

**Files**: ``transport.py``, ``client.py``


Phase 3: Observability & Diagnostics (v1.1.0)
----------------------------------------------

Goal: Structured logging, metrics, debugging tools.

3.1 Structured logging
^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Replace ``logging.getLogger()`` with ``structlog`` for machine-parseable logs:

.. code-block:: python

   import structlog
   logger = structlog.get_logger("pymt5.transport")

   logger.info("command_sent",
       cmd=command, payload_size=len(payload),
       state=self.state.value)

Benefits:

- JSON output for log aggregation (ELK, Datadog, etc.)
- Automatic context binding (login ID, session ID)
- Compatible with standard logging (drop-in replacement)

**Files**: All modules, ``pyproject.toml`` (add ``structlog`` dependency)

3.2 Metrics collection
^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Add optional metrics hooks for monitoring:

- Commands sent/received per type
- Latency histogram per command type
- Reconnection count
- Tick throughput (ticks/second)
- Error counts by type

Interface:

.. code-block:: python

   class MetricsCollector(Protocol):
       def record_command(self, cmd: int, latency_ms: float) -> None: ...
       def record_error(self, error_type: str) -> None: ...
       def record_tick(self, symbol: str) -> None: ...

**Files**: New ``_metrics.py``, ``transport.py``, ``_push_handlers.py``

3.3 Protocol debugging tool
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

CLI tool for inspecting raw protocol traffic:

.. code-block:: bash

   python -m pymt5.debug --server wss://mt5server/ws \
       --login 12345 --password xxx \
       --dump-frames

Features:

- Hex dump of encrypted/decrypted frames
- Schema auto-detection for known command IDs
- Timestamp logging
- Export to pcap-like format

**Files**: New ``pymt5/debug.py`` or ``pymt5/__main__.py``


Phase 4: API & Usability (v1.2.0)
----------------------------------

Goal: Better developer experience, DataFrame support, subscription management.

4.1 Subscription lifecycle manager
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

Current subscription tracking is manual (``_subscribed_ids`` list). Add proper
lifecycle management:

.. code-block:: python

   # Subscribe
   sub = await client.subscribe_ticks(["EURUSD", "GBPUSD"])

   # Check status
   print(sub.symbols)     # ['EURUSD', 'GBPUSD']
   print(sub.is_active)   # True

   # Unsubscribe
   await sub.cancel()

   # Or use as context manager
   async with client.subscribe_ticks(["EURUSD"]) as sub:
       async for tick in sub:
           process(tick)

**Files**: New ``_subscriptions.py``, ``_push_handlers.py``, ``_market_data.py``

4.2 DataFrame / numpy integration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Optional integration with pandas/numpy for quant workflows:

.. code-block:: python

   # Returns pandas DataFrame instead of list of dicts
   df = await client.copy_rates_from("EURUSD", "H1", count=100,
                                      as_dataframe=True)
   print(df.columns)  # ['time', 'open', 'high', 'low', 'close', 'volume']

Implementation:

- Optional ``pandas`` dependency (``pip install pymt5[pandas]``)
- ``as_dataframe=True`` parameter on data retrieval methods
- Proper dtype mapping (timestamps as ``datetime64``, prices as ``float64``)
- Zero-copy where possible via numpy buffer protocol

**Files**: New ``_dataframe.py``, ``_market_data.py``, ``pyproject.toml``

4.3 Typed event system
^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Replace untyped dict-based events with typed dataclasses:

.. code-block:: python

   @dataclass(frozen=True, slots=True)
   class TickEvent:
       symbol: str
       symbol_id: int
       bid: float
       ask: float
       last: float
       timestamp_ms: int

   # Handler receives typed event
   @client.on_tick
   async def handle(event: TickEvent):
       print(f"{event.symbol}: {event.bid}/{event.ask}")

**Files**: ``types.py``, ``_push_handlers.py``

4.4 Context-aware error messages
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Enrich exceptions with operational context:

.. code-block:: python

   class TradeError(PyMT5Error, ValueError):
       def __init__(self, message: str, *,
                    retcode: int = 0,
                    symbol: str = "",
                    action: int = 0):
           super().__init__(message)
           self.retcode = retcode
           self.symbol = symbol
           self.action = action

**Files**: ``exceptions.py``, ``_trading.py``


Phase 5: Protocol & Codec (v1.3.0)
-----------------------------------

Goal: Protocol codec optimization, versioning, extensibility.

5.1 Codec dispatch table refactor
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

``SeriesCodec.serialize()`` and ``SeriesCodec.parse()`` each have 14 identical
``if/elif`` branches for field type dispatch. Refactor to dispatch tables:

.. code-block:: python

   _SERIALIZERS: dict[int, Callable] = {
       PROP_I8:  lambda v, _: struct.pack("<b", int(v)),
       PROP_I16: lambda v, _: struct.pack("<h", int(v)),
       PROP_U32: lambda v, _: struct.pack("<I", int(v)),
       PROP_F64: lambda v, _: struct.pack("<d", float(v)),
       # ...
   }

   _PARSERS: dict[int, Callable] = {
       PROP_I8:  lambda buf, cur, _: (struct.unpack_from("<b", buf, cur)[0], 1),
       # ...
   }

Benefits:

- Eliminates code duplication (~100 lines saved)
- Easier to add new field types
- Slightly faster dispatch (dict lookup vs. if chain)

**Files**: ``protocol.py``

5.2 Protocol version tracking
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

The MT5 Web Terminal protocol evolves with server builds. Add version tracking:

- Store server build number from bootstrap response
- Log protocol version mismatches
- Schema version registry (map build ranges to schema versions)
- Graceful fallback for unknown fields

**Files**: ``transport.py``, ``client.py``, ``schemas.py``

5.3 Schema validation in development mode
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Add ``PYMT5_DEBUG=1`` environment variable to enable:

- Schema field count validation on parse
- Extra logging of unparsed trailing bytes
- Warning on unexpected field values

**Files**: ``protocol.py``


Phase 6: Testing & CI (v1.4.0)
------------------------------

Goal: Integration testing, performance benchmarks, coverage gaps.

6.1 Integration test framework
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: HIGH

All 788 current tests are offline unit tests. Add integration test infrastructure:

.. code-block:: python

   @pytest.mark.integration
   @pytest.mark.skipif(not os.getenv("MT5_TEST_SERVER"),
                       reason="no test server configured")
   async def test_live_login():
       async with MT5WebClient(os.environ["MT5_TEST_SERVER"]) as client:
           token, session = await client.login(
               login=int(os.environ["MT5_TEST_LOGIN"]),
               password=os.environ["MT5_TEST_PASSWORD"],
           )
           assert session > 0

Configuration:

- GitHub Actions secrets for test server credentials
- Separate CI job (manual trigger only)
- Test against MetaQuotes demo server

**Files**: ``tests/test_integration.py``, ``.github/workflows/integration.yml``

6.2 Performance benchmarks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Add ``pytest-benchmark`` or ``asv`` benchmarks:

- Protocol serialize/parse throughput
- Tick processing rate (ticks/second)
- Symbol cache lookup time
- Concurrent command throughput

.. code-block:: python

   def test_serialize_benchmark(benchmark):
       schema = SYMBOL_BASIC_SCHEMA
       benchmark(SeriesCodec.serialize, schema)

**Files**: ``tests/benchmarks/``, ``pyproject.toml``

6.3 Coverage gap closure
^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

25 uncovered lines remain (all edge cases):

- ``protocol.py:208-209, 239, 244, 249, 253`` — PROP_I8/I16/U8/STRING parse branches
- ``_parsers.py:82, 455-457, 494`` — data coercion edge cases
- ``_market_data.py:200-202`` — unreachable error path
- ``_trading.py:599, 684-686`` — error edge cases
- ``_push_handlers.py:199, 282-283`` — handler dispatch edge cases

**Files**: Various test files


Phase 7: Documentation & Ecosystem (v1.5.0)
--------------------------------------------

Goal: Protocol documentation, strategy integration, community tools.

7.1 Protocol reverse-engineering documentation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

Document the binary protocol for the community:

- Frame format (outer envelope, inner command structure)
- Key exchange and encryption flow
- Command ID catalog with request/response schemas
- Field type encoding reference
- Session lifecycle diagram

**Format**: Sphinx docs + diagrams (``sphinxcontrib-mermaid``)

**Files**: ``docs/protocol_internals.rst``

7.2 Strategy framework adapter
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: LOW

Thin adapter for popular strategy frameworks:

- ``backtrader`` data feed
- ``zipline`` data bundle
- ``vectorbt`` integration
- Custom event loop integration

**Files**: ``pymt5/adapters/``

7.3 PyPI release automation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Priority**: MEDIUM

GitHub Actions workflow for automated releases:

- Triggered by git tag (``v*.*.*``)
- Build sdist + wheel
- Publish to PyPI via trusted publisher
- Generate GitHub Release with changelog

**Files**: ``.github/workflows/release.yml``


Priority Matrix
---------------

.. list-table::
   :header-rows: 1
   :widths: 10 50 15 15

   * - Phase
     - Key Items
     - Priority
     - Depends On
   * - 1
     - Custom exceptions, remove aiohttp, deduplicate constants
     - HIGH
     - None
   * - 2
     - State machine, rate limiting, reconnection
     - HIGH
     - Phase 1
   * - 3
     - Structured logging, metrics
     - MEDIUM
     - Phase 2
   * - 4
     - Subscriptions, DataFrame, typed events
     - MEDIUM
     - Phase 2
   * - 5
     - Codec refactor, protocol versioning
     - LOW
     - Phase 1
   * - 6
     - Integration tests, benchmarks
     - MEDIUM
     - Phase 2
   * - 7
     - Protocol docs, strategy adapters, PyPI release
     - LOW
     - Phase 3+


Risk Assessment
---------------

**Low risk items** (Phase 1): Internal cleanup, no API changes, backward compatible.

**Medium risk items** (Phase 2-3): Transport refactoring may require test
adjustments. State machine migration needs careful backward compatibility.

**Higher risk items** (Phase 4-5): API additions need versioning consideration.
Codec refactoring must maintain exact byte-level compatibility with all existing
schemas.

**External risk**: MT5 Web Terminal protocol changes with server builds.
Protocol version tracking (Phase 5.2) mitigates this.
