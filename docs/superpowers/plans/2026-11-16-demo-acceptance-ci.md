# Demo Acceptance and Market-Hours CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make read-only demo acceptance accurate during active markets, expand its API coverage, add an opt-in scheduled workflow, and enforce 100% deterministic offline statement coverage.

**Architecture:** Keep network acceptance in `tests/test_integration.py` and a separate manually/scheduled GitHub Actions workflow; keep normal CI and releases explicitly offline. Preserve conservative cmd=7 state semantics, correct the test contract, and close the remaining 61 statement-coverage gaps with focused unit tests rather than live-only coverage.

**Tech Stack:** Python 3.11+, pytest/pytest-asyncio, coverage.py, GitHub Actions, Sphinx/RST, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-16-demo-acceptance-ci-design.md`

## Global Constraints

- Read-only demo calls only: no trading, account mutation, order modification/cancellation, or demo-account creation.
- Never print or commit credentials, session identifiers, account values, positions, or orders; use `PYMT5_LOG_LEVEL=CRITICAL` for live runs.
- Preserve `ProtocolError` and last-confirmed `_subscribed_ids` after a nonzero cmd=7 response; never describe the observed final-unsubscribe rejection as success.
- Scheduled demo runs are Monday–Friday at 01:00 and 13:00 UTC, opt in through `PYMT5_DEMO_ENABLED=1`, and require a real tick through `PYMT5_REQUIRE_TICK=1`.
- Ordinary CI, Pages, and releases remain offline; the offline coverage gate is 100% statements.
- English and Simplified Chinese documentation must remain complete counterparts.
- Use Python 3.11-compatible code and Ruff's 120-character line limit.
- Do not commit or push without explicit authorization. Local workflow-equivalent checks are not remote GitHub Actions runs.

## Review Focus

- Closed market or wrong symbol: scheduled/manual acceptance must fail rather than skip when `PYMT5_REQUIRE_TICK=1`; Task 1 adds timeout-mode tests.
- Final cmd=7 unsubscribe accepted or rejected: success clears state, exact code=1 preserves it, and other errors remain failures; Task 1 adds both contract tests.
- Missing credentials in CI: fail with variable names only, never values; Task 4 adds and locally checks the configuration-validation step.
- Optional/platform dependencies during coverage: tests must use controlled fakes/mocks and remain deterministic on all CI operating systems; Task 3 covers these branches.
- Partial unsubscribe cleanup: retain a control symbol and close the connection for final cleanup rather than hiding a rejected empty-set request; Task 1 pins the exact calls.

---

### Task 1: Correct tick integration contracts and required-tick behavior

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `tests/test_iteration_v6_tick_probe.py`

**Interfaces:**
- Consumes: existing `MT5WebClient.subscribe_ticks()`, `unsubscribe_ticks()`, `ProtocolError`, and demo environment variables.
- Produces: `PYMT5_REQUIRE_TICK=1` as the strict no-tick failure switch; tick integration cleanup unsubscribes only the selected symbol while retaining a control symbol until connection close.

- [ ] **Step 1: Write failing offline tests for strict/lenient tick timeout and final-unsubscribe outcomes**

Extend the dynamic `test_integration.py` loader in `tests/test_iteration_v6_tick_probe.py`. Use fake clients whose `subscribe_ticks()` records IDs and whose `unsubscribe_ticks()` either records a partial removal, raises exactly `ProtocolError("... code=1")` without clearing state, or succeeds and clears state. Monkeypatch `asyncio.wait_for` to raise `TimeoutError` immediately. Assert:
- unset `PYMT5_REQUIRE_TICK` raises pytest's skip exception;
- `PYMT5_REQUIRE_TICK=1` raises pytest's failure exception;
- subscription includes the selected and control IDs, and cleanup removes only the selected ID;
- final-unsubscribe success produces `_subscribed_ids == []`;
- exact code=1 produces the original confirmed ID list and is not treated as success.

- [ ] **Step 2: Run the focused tests and verify the new cases fail**

Run: `python -m pytest tests/test_iteration_v6_tick_probe.py -q --tb=short`
Expected: existing tests pass; new strict-timeout/final-contract tests fail because the current integration module lacks those behaviors.

- [ ] **Step 3: Implement the integration contract**

In `tests/test_integration.py`, read `PYMT5_REQUIRE_TICK` at module import. In `test_tick_subscription`, select a second, distinct control symbol, subscribe to both, wait for the requested symbol, and unsubscribe only the requested symbol in `finally`. On timeout, call `pytest.fail()` when strict mode is enabled and `pytest.skip()` otherwise. Add `test_final_tick_unsubscription_contract` to subscribe one selected symbol and assert either successful empty state or exact code=1 with preserved state; do not catch other errors.

- [ ] **Step 4: Run focused and existing subscription regressions**

Run: `python -m pytest tests/test_iteration_v6_tick_probe.py tests/test_iteration_v6_subscription_safety.py -q --tb=short`
Expected: all pass, including preservation of production cmd=7 error behavior.

### Task 2: Expand read-only demo API acceptance

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `tests/test_iteration_v6_tick_probe.py` only if shared selection helpers need offline tests

**Interfaces:**
- Consumes: `get_account()`, `get_account_summary()`, `terminal_info()`, `version()`, `get_positions_and_orders()`, `get_trade_history()`, `get_full_symbol_info()`, `get_symbol_groups()`, and `get_rates()`.
- Produces: ten total explicit live integration tests covering session, health, account/terminal, read-only trade queries, symbols/history, and tick subscription contracts.

- [ ] **Step 1: Add read-only integration assertions**

Add focused tests for:
- nonempty account mapping, `AccountInfo` summary, connected terminal info, and a three-item version tuple with the connected server build;
- `positions`/`orders` and `deals`/`orders` list shapes without logging their contents;
- full-symbol name/ID/digits matching the cached selected symbol, a list of string symbol groups, and nonempty recent H1 bars with required OHLC/time keys;
- exact configured symbol validation reused by these tests.

Use a three-day lookback and the configured active symbol. Never assert or print private account values or require nonempty positions/orders/deals.

- [ ] **Step 2: Collect offline and confirm integration remains deselected**

Run: `env -u PYMT5_INTEGRATION python -m pytest tests/ --collect-only -q -m integration`
Expected: ten integration tests collected; none executed by the default offline command added in Task 4.

- [ ] **Step 3: Run the expanded live suite during active EURUSD trading**

Run with credentials loaded only into the process environment: `PYMT5_INTEGRATION=1 PYMT5_REQUIRE_TICK=1 PYMT5_TEST_SYMBOL=EURUSD PYMT5_LOG_LEVEL=CRITICAL python -m pytest tests/test_integration.py -v --tb=short`
Expected: all ten pass; the final-unsubscribe contract records code=1 through preserved state rather than an unconditional cleanup failure.

### Task 3: Close all offline statement-coverage gaps

**Files:**
- Modify: `tests/test_new_modules.py`, `tests/test_iteration_v6_symbol_cache.py`, `tests/test_iteration_v6_tick_prices.py`
- Modify: `tests/test_phase_15_16.py`, `tests/test_push_handlers.py`, `tests/test_client_lifecycle.py`
- Modify: `tests/test_iteration_v6_close_safety.py`, `tests/test_iteration_v6_reconnect_guard.py`
- Modify: `tests/test_iteration_v6_transport_isolation.py`, `tests/test_iteration_v6_transport_client.py`, `tests/test_iteration_v6_disconnect_races.py`
- Test: baseline `/tmp/pymt5-coverage-baseline.json` (61 missed statements; regenerate during implementation)

**Interfaces:**
- Consumes: current public/private behavior at baseline missing lines 31; 210, 264–265; 274; 270, 403–404, 424–425, 457–458, 480–481, 516; 311, 385–387, 411–412, 444, 454–455, 566, 577, 602, 607, 615, 620, 625, 634, 652, 668, 687–688, 874, 978, 980; 425; and 48–50, 247–248, 312–314, 323, 515, 524, 561–562, 596–597, 609, 679, 684, 694, 701, 710 respectively.
- Produces: deterministic tests for every currently uncovered statement without production exclusions or changed public behavior.

- [ ] **Step 1: Add focused characterization tests for every uncovered branch**

Use controlled clocks, fakes, `AsyncMock`, and events; do not connect to a server or add `pragma: no cover`. The complete line-to-test map is:

| Test module and new test | Covered behavior/lines |
| --- | --- |
| `test_new_modules.py::TestToDataFrame::test_converts_records_with_stubbed_pandas` | Stub `pandas.DataFrame`; assert records and sentinel (`_dataframe.py:31`) |
| `test_iteration_v6_symbol_cache.py::test_resolve_symbol_id_evicts_expired_negative_entries` | Freeze time; expired negative entry evicted, fresh retained (`_market_data.py:210`) |
| `test_iteration_v6_symbol_cache.py::test_cache_full_symbol_lazily_initializes_timestamps` | Initialize missing timestamp map, freeze timestamp, return a copy (`_market_data.py:264-265`) |
| `test_iteration_v6_tick_prices.py::test_normalize_tick_price_value_rejects_unknown_mode_directly` | Direct invalid mode raises `ValueError` (`_parsers.py:274`) |
| `test_phase_15_16.py::test_typed_handlers_log_and_suppress_parse_errors` | Parameterize tick/book/trade-result/account parse failures; callback not called, error logged (`_push_handlers.py:403-404,424-425,457-458,480-481`) |
| `test_push_handlers.py::TestOnSymbolDetails::test_stops_if_body_shrinks_during_decode` | Fault-inject body shrink after first detail; bounds guard stops delivery (`_push_handlers.py:270`) |
| `test_push_handlers.py::TestCacheTickPush::test_eviction_stops_when_access_order_is_inconsistent` | Defensive empty access-order eviction guard (`_push_handlers.py:516`) |
| `test_client_lifecycle.py::test_connect_rejects_session_invalidated_by_transport_connect` | Transport connect invalidates generation/closing state; `SessionError` (`client.py:311`) |
| `test_client_lifecycle.py::test_reconnect_returns_when_session_is_invalidated` | Parameterize before-start, after-sleep/constructor/connect/login/ticks/book/error stages (`client.py:566,577,602,607,615,620,625,634`) |
| `test_client_lifecycle.py::test_close_reconnect_transport_defaults_to_current_transport` | Default transport disabled, callback removed, close awaited (`client.py:652`) |
| `test_client_lifecycle.py::test_close_reconnect_transport_preserves_cancellation_when_close_is_done` | Done close plus outer cancellation remains cancelled (`client.py:668`) |
| `test_client_lifecycle.py::test_login_waiting_for_session_lock_rejects_stale_generation` | Invalidate while queued on session lock; no login command (`client.py:874`) |
| `test_client_lifecycle.py::test_logout_propagates_caller_cancellation_while_cancelling_reconnect` | Non-cooperative reconnect cleanup preserves caller cancellation (`client.py:978`) |
| `test_client_lifecycle.py::test_logout_rejects_generation_changed_by_cancelled_reconnect` | Reconnect cancellation invalidates generation; `SessionError` (`client.py:980`) |
| `test_iteration_v6_close_safety.py::test_close_records_own_cancellation_while_waiting_for_reconnect` | Close-task cancellation still cleans transport/state (`client.py:385`) |
| `test_iteration_v6_close_safety.py::test_close_records_reconnect_task_failure` | Completed failing reconnect task supplies first error (`client.py:386-387`) |
| `test_iteration_v6_close_safety.py::test_close_preserves_session_lock_failure_when_fallback_close_fails` | Lock acquisition error outranks fallback close error (`client.py:411-412`) |
| `test_iteration_v6_close_safety.py::test_close_records_cancellation_during_timeout_grace_period` | Cancellation during grace wait cleans up and preserves timeout (`client.py:444`) |
| `test_iteration_v6_close_safety.py::test_close_records_cleanup_finalizer_failure` | Fault-injected cleanup finalizer error recorded, state cleared (`client.py:454-455`) |
| `test_iteration_v6_reconnect_guard.py::test_consume_reconnect_close_result_logs_late_failure` | Late close-task exception logged and suppressed (`client.py:687-688`) |
| Existing `test_phase19.py::TestDebugModeOn::test_parse_with_debug_warns_bad_timestamp` | Run alone and in suite to diagnose why debug timestamp warning missed `protocol.py:425`; add an isolated equivalent only if needed |
| `test_iteration_v6_transport_isolation.py::test_observe_isolation_task_handles_pending_task_inspection` | Direct pending-task inspection handles `InvalidStateError` (`transport.py:48-50`) |
| `test_iteration_v6_transport_isolation.py::test_acquire_rate_limit_boundaries` | Disabled/invalidated raises; enabled immediate acquire cleans tasks (`transport.py:515,524`) |
| `test_iteration_v6_transport_isolation.py::test_isolation_preserves_cancellation_during_receive_cleanup` | Cancellation during non-cooperative receive cleanup retains cleanup semantics (`transport.py:561-562`) |
| `test_iteration_v6_transport_isolation.py::test_isolation_survives_disconnect_callback_failure` | Callback error logged; socket/pending/state cleanup continues (`transport.py:596-597`) |
| `test_iteration_v6_transport_client.py::test_connect_defaults_server_build_when_prefix_decode_fails` | Fault-injected bootstrap decode error leaves build 0 (`transport.py:247-248`) |
| `test_iteration_v6_disconnect_races.py::test_close_preserves_external_cancellation_while_recv_stops` | Outer close cancellation propagates after receive/socket cleanup (`transport.py:312`) |
| `test_iteration_v6_disconnect_races.py::test_close_logs_socket_failure_after_receive_failure` | Receive error remains primary; socket close error logged (`transport.py:313-314,323`) |
| `test_iteration_v6_disconnect_races.py::test_recv_loop_without_socket_records_disconnect` | Missing socket fails pending, notifies, enters error state (`transport.py:609`) |
| `test_iteration_v6_disconnect_races.py::test_dispatch_stops_when_source_is_invalidated` | Parameterize initial/metrics/pending/callback/error-handler invalidation (`transport.py:679,684,694,701,710`) |

Some cases intentionally fault-inject defensive branches that immutable bytes or synchronous code make unreachable in normal operation. They must assert the guard's behavior, not merely execute the line.

- [ ] **Step 2: Run each changed test module**

Run: `python -m pytest <changed-test-modules> -q --tb=short`
Expected: all pass with original product behavior except any separately reproduced defect, which must return to systematic debugging before modification.

- [ ] **Step 3: Measure the complete offline suite with a 100% gate**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin -p pytest_cov.plugin tests/ -m 'not integration' -q --tb=short --cov=pymt5 --cov-report=term-missing --cov-fail-under=100`
Expected: 100% total coverage and no failed tests. If any lines remain, add a behavior-based test for those exact lines and repeat; do not weaken the threshold.

### Task 4: Add market-hours CI and bilingual operating documentation

**Files:**
- Create: `.github/workflows/demo.yml`
- Modify: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `Makefile`
- Modify: `README.md`, `README.zh-CN.md`, `docs/development.rst`, `docs/zh_CN/development.rst`

**Interfaces:**
- Consumes: `PYMT5_REQUIRE_TICK=1` and the ten integration tests.
- Produces: opt-in `demo` workflow; repository variables `PYMT5_DEMO_ENABLED`, optional `PYMT5_SERVER`/`PYMT5_TEST_SYMBOL`; protected `demo` environment secrets `PYMT5_LOGIN`/`PYMT5_PASSWORD`; offline CI coverage gate.

- [ ] **Step 1: Add the demo workflow**

Use `schedule` cron `0 1,13 * * 1-5` and `workflow_dispatch` with a string `symbol` input defaulting to `EURUSD`. Skip scheduled jobs unless `vars.PYMT5_DEMO_ENABLED == '1'`; always permit manual dispatch. Set `contents: read`, a non-cancelling demo concurrency group, a 15-minute timeout, and the `demo` environment. Validate login/password presence and integer login without printing values. Run only `tests/test_integration.py` with `PYMT5_INTEGRATION=1`, `PYMT5_REQUIRE_TICK=1`, `PYMT5_LOG_LEVEL=CRITICAL`, and explicit pytest-asyncio loading.

- [ ] **Step 2: Keep ordinary CI/release explicitly offline and enforce coverage**

Add `-m "not integration"` to Makefile, CI, and release full-suite commands. Add `--cov-fail-under=100` to CI's existing coverage command. Do not add demo credentials or network tests to Pages/release.

- [ ] **Step 3: Document exact setup and limitations in both languages**

Document local commands, strict tick behavior, cron times in UTC, protected-environment setup, exact variable/secret names, symbol market-hour responsibility, 100% offline statement coverage, and the build 6320 final-unsubscribe rejection. State that schedules are a market-hours heuristic and remote GitHub execution requires the workflow to be pushed/available on the applicable ref.

- [ ] **Step 4: Validate workflow syntax and offline command behavior**

Run: `ruby -e 'require "yaml"; Dir[".github/workflows/*.yml"].each { |path| YAML.load_file(path); puts "YAML OK: #{path}" }'`
Run: `make test`
Expected: all workflows parse; all offline tests pass with integration deselected and no network access.

### Task 5: Run final gates and report verified outcomes

**Files:**
- Modify only if verification identifies a documented, reproducible issue in the files above

**Interfaces:**
- Consumes: all previous tasks.
- Produces: sanitized final evidence for offline tests, 100% coverage, lint, types, docs, package checks, YAML, and live demo acceptance.

- [ ] **Step 1: Run static and complete local delivery gates**

Run: `ruff check pymt5/ tests/`
Run: `python -m mypy pymt5/ --ignore-missing-imports`
Run: `python docs/build_docs.py && python docs/check_docs.py`
Run: `make package-check`
Run: `git diff --check`
Expected: every command exits zero; report actual output if any does not.

- [ ] **Step 2: Rerun the exact CI coverage command and live demo suite**

Expected: offline total coverage is exactly 100%; all ten live tests pass during active trading with no sensitive output. Keep any skip/failure explicit.

- [ ] **Step 3: Review the final diff and remote-execution boundary**

Run: `git status --short && git diff --stat && git diff -- .github/workflows tests/test_integration.py`
Confirm no credentials or trading calls were added. Do not claim a remote GitHub Actions run unless one was actually executed and its run ID/status is available; otherwise state that remote execution remains pending repository publication/configuration.
