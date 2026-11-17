Iteration Plan v6 — OCR Review Remediation
==========================================

.. contents:: Table of Contents
   :depth: 3
   :local:

Context
-------

This plan follows Iteration Plan v5 (Phases 20–24) and targets the findings
from an OpenCodeReview full-repository scan run on 2026-11-17 against
``pymt5`` at version 1.1.0.

Scan facts:

- Tool: OpenCodeReview v1.13.10 (``alibaba/open-code-review``), full-file scan.
- Planned files: 69. Completed: 65; 4 failed on LLM timeout/connection errors.
- The three failed core modules (``client.py``, ``transport.py``,
  ``_account.py``) were rescanned successfully in a separate pass.
  ``docs/_static/custom.css`` remains unscanned.
- Comments: 205 total — 4 critical, 29 high, 98 medium, 74 low.
- Current baseline: 1383 offline tests pass at 100.00% statement coverage on
  Python 3.11, 3.12, and 3.13; CI is green; the read-only demo suite passes
  10/10 during active EURUSD trading.

Decisions carried into this plan:

- **Accepted exception:** the hard-coded login/password in
  ``scripts/live_test_v2.py``, ``v3.py``, and ``v4.py`` belong to a disposable
  demo account. They are not a work item and must not be reported again as a
  critical credential leak.
- **Confirmed false positive:** the claim that the CI configuration breaks
  hypothesis tests is wrong. The exact CI command run against
  ``tests/test_fuzz.py`` yields 5 passed.
- AI findings are hypotheses until a failing regression test reproduces them.
  Every task starts by reproducing the defect; a task that cannot reproduce it
  is closed as a false positive with a short note.

Goals
-----

1. Remove the highest-impact correctness and lifecycle risks found by the scan.
2. Make subscription, account, and protocol failure paths preserve local state
   only after explicit server acknowledgement.
3. Make callbacks, metrics, and optional integrations unable to corrupt
   transport or session control flow.
4. Tighten security hygiene around CI secrets, workflow permissions, logging,
   and dataclass representations without touching the accepted demo exception.
5. Keep 100% offline statement coverage, the 3.11–3.13 × Linux/macOS/Windows
   matrix, and the read-only demo acceptance suite green throughout.

Non-goals
---------

- No new trading features, new protocol commands, or public API redesign.
- No attempt to solve the acknowledged protocol limitation that unsolicited
  push frames can share a command ID with responses; this plan only prevents
  silent mis-assignment where a safe policy can be enforced.
- No removal of the accepted demo credentials and no credential rotation.
- No bulk rewrite of examples, scripts, or documentation beyond the listed
  defects.
- No live trading, order modification, or account mutation during verification.

Findings Summary
----------------

.. list-table::
   :header-rows: 1
   :widths: 18 10 12 60

   * - Severity
     - Count
     - Actionable
     - Notes
   * - Critical
     - 4
     - 0
     - 3 accepted demo-credential exceptions; 1 confirmed false positive
   * - High
     - 29
     - 29
     - All mapped to Phases 25–30
   * - Medium
     - 98
     - selected
     - Security/observability items in Phase 29; remainder in Appendix B
   * - Low
     - 74
     - backlog
     - Style, documentation, and maintainability; Appendix B

Highest-risk themes:

- Asynchronous listener dispatch can deadlock or skip disconnect cleanup.
- Order-book subscription state is mutated without checking server response
  codes or serializing concurrent calls.
- Account and protocol parsers accept malformed lengths/offsets and shift
  subsequent fields.
- Client session operations are not consistently serialized against close and
  reconnect.
- Trading helpers contain ambiguous reference-price, ticket-filter, and
  zero-equity fallbacks.

Phase 25 — Transport and Dispatch Safety (P0)
---------------------------------------------

**Goal:** A callback, cancellation, or malformed frame must never leave the
transport READY with a dead receive loop, nor block response delivery.

25.1 Async listener dispatch isolation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``transport.py:696-698`` awaits async listeners inline in
  ``_dispatch``. A listener that awaits ``send_command`` waits for a response
  that the same receive loop cannot deliver.
- **Files:** ``pymt5/transport.py``, ``tests/test_iteration_v6_transport_isolation.py``.
- **Change:** Define and test one policy: either dispatch async listeners as
  tracked tasks with ordered delivery and exception consumption, or reject
  command-issuing listeners explicitly. Do not leave an unbounded inline await.
- **Tests:** A listener that calls ``send_command`` must not deadlock; listener
  ordering and callback-error handlers remain observable; cancellation of the
  receive task still tears down cleanly.
- **Acceptance:** Existing cancellation/isolation regressions pass; a new test
  proves no deadlock within a bounded timeout.

25.2 Listener cancellation must not skip disconnect cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``transport.py:699-702`` catches ``Exception`` only. A listener
  cancelled with ``CancelledError`` escapes to ``_recv_loop`` and can bypass
  the termination block.
- **Files:** ``pymt5/transport.py``, ``tests/test_iteration_v6_disconnect_races.py``.
- **Change:** Distinguish receive-task cancellation from listener cancellation,
  and route listener cancellation through the same failure/cleanup path as
  other callback failures.
- **Tests:** A listener raising ``CancelledError`` leaves no pending futures,
  notifies disconnect, and sets ``ERROR`` or a documented final state.
- **Acceptance:** No READY transport with a dead receive task in the regression.

25.3 Bounded close cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``transport.py:307-309`` waits unbounded for a cancelled receive
  task; ``ws.close()`` has the same risk.
- **Files:** ``pymt5/transport.py``, ``tests/test_iteration_v6_close_safety.py``.
- **Change:** Reuse the bounded cleanup pattern already present in
  ``_invalidate_uncertain_send``; detach and observe non-cooperative tasks.
- **Tests:** A receive task and socket that suppress cancellation cannot hang
  ``close()`` indefinitely; the first cleanup error is still preserved.
- **Acceptance:** Bounded completion under a controlled timeout; state cleared.

25.4 Ambiguous frame completion policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``transport.py:685-691`` resolves a pending future for any frame
  carrying the command, including unsolicited push frames and duplicates.
- **Files:** ``pymt5/transport.py``, protocol/reliability docs.
- **Change:** Make the policy explicit. Preferred: for commands that also
  receive pushes, require an opt-in correlation mode or fail the request
  rather than returning an unrelated body. Keep the limitation documented.
- **Tests:** A push-shaped frame while a request is pending must not silently
  satisfy that request under the chosen policy.
- **Acceptance:** No silent cross-assignment in the regression; documented
  behavior matches tests.

25.5 Bootstrap key material validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``transport.py:240-243`` accepts a body that contains the token
  but no AES key; ``AESCipher`` then raises a ``ProtocolError`` instead of the
  documented ``MT5ConnectionError``.
- **Files:** ``pymt5/transport.py``, ``tests/test_iteration_v6_transport_client.py``.
- **Change:** Validate minimum key length before constructing the cipher and
  map invalid-key material to ``MT5ConnectionError``.
- **Tests:** 66-byte, 67-byte, and malformed-key bootstrap bodies fail with the
  documented exception type and release the socket/receive task.
- **Acceptance:** Cleanup assertions pass for each short-body case.

25.6 Optional metrics isolation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``transport.py:448-449`` calls optional metrics after the send
  has started; a metrics exception enters the uncertain-send path. Other
  metrics calls are also unguarded.
- **Files:** ``pymt5/transport.py``, ``tests/test_iteration_v6_transport_isolation.py``.
- **Change:** Wrap every optional metrics callback as best-effort; metrics must
  never alter command outcome or connection state.
- **Tests:** Each metrics hook raises; command behavior and pending futures
  remain unchanged.
- **Acceptance:** No connection teardown caused solely by a metrics error.

Phase 26 — Subscription, Parsing, and Validation Integrity (P0/P1)
------------------------------------------------------------------

**Goal:** Local subscription/account state changes only after a positive server
acknowledgement, and malformed lengths cannot shift decoded fields.

26.1 Order-book subscribe acknowledgement and serialization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_market_data.py:480-482`` ignores ``CMD_SUBSCRIBE_BOOK``
  response codes and performs an unlocked read-modify-write.
- **Files:** ``pymt5/_market_data.py``, ``pymt5/client.py``,
  ``tests/test_iteration_v6_subscription_safety.py``.
- **Change:** Capture the result; raise ``ProtocolError`` on nonzero code;
  commit ``_subscribed_book_ids`` only after success; serialize the full
  operation with a dedicated lock.
- **Tests:** Rejection preserves the previous set; concurrent subscribe calls
  cannot lose each other's additions.
- **Acceptance:** Same conservative contract as cmd=7 tick subscriptions.

26.2 Order-book unsubscribe acknowledgement and serialization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_market_data.py:516-518`` never checks the response and
  evicts caches on failure.
- **Files:** ``pymt5/_market_data.py``, tests as above.
- **Change:** Check code before mutating state/caches; preserve the last
  confirmed set on rejection; serialize.
- **Tests:** Rejected unsubscribe keeps IDs and caches; accepted unsubscribe
  clears only the requested entries.
- **Acceptance:** No divergence between local and acknowledged state.

26.3 Account trade-settings count=0 offset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_parsers.py:417-419`` returns without advancing past the
  4-byte count, so ``_parse_account_response`` rereads it as ``trade_flags``.
- **Files:** ``pymt5/_parsers.py``, ``tests/test_phase_*`` parser tests.
- **Change:** Advance the offset before the ``count <= 0`` early return.
- **Tests:** A body with count=0 decodes the following fields at the correct
  offsets; comparison with a count>0 fixture covers both layouts.
- **Acceptance:** Field values no longer shift by 4 bytes.

26.4 Negative ``propLength`` rejection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``protocol.py:186-188`` type-checks but does not range-check
  ``propLength``; a negative value moves the parse cursor backwards.
- **Files:** ``pymt5/protocol.py``, ``tests/test_protocol_smoke.py``.
- **Change:** Reject negative lengths with ``ProtocolError`` in both size and
  parse paths.
- **Tests:** Negative and non-integer lengths fail before any slicing/decoding.
- **Acceptance:** No raw ``struct.error`` or backward cursor.

26.5 Parse offset range validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``protocol.py:377-378`` accepts negative ``offset``; the
  available-bytes guard becomes meaningless.
- **Files:** ``pymt5/protocol.py``, protocol tests.
- **Change:** Require ``0 <= offset <= len(buffer)`` and raise
  ``ProtocolError`` otherwise, including ``offset == len(buffer)`` with a
  non-empty schema.
- **Tests:** Negative and out-of-range offsets; normal offsets unchanged.
- **Acceptance:** Guard applies before cursor arithmetic.

26.6 Finite volume/price validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_validation.py:8/14`` accepts ``NaN`` and ``+inf`` because
  comparisons with them are false; downstream ``int(round(...))`` raises an
  opaque error.
- **Files:** ``pymt5/_validation.py``, ``tests/test_validation.py``,
  ``tests/test_trading_coverage.py``.
- **Change:** Require ``math.isfinite`` in addition to positivity; update
  messages to say "finite value > 0".
- **Tests:** ``nan``, ``inf``, ``-inf``, zero, and negative values; valid
  boundaries remain accepted.
- **Acceptance:** Non-finite values raise ``ValidationError`` at the boundary.

26.7 Push-handler callback exception isolation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_push_handlers.py:134-138`` invokes user callbacks inside the
  ``_PARSE_ERRORS`` try block, so callback ``KeyError``/``TypeError``/
  ``ValueError`` is logged as a parse error and never reaches
  ``on_callback_error``.
- **Files:** ``pymt5/_push_handlers.py``, ``tests/test_push_handlers.py``,
  ``tests/test_phase_15_16.py``.
- **Change:** Parse inside the guarded block; invoke the callback outside it.
  Apply the same pattern to all push handlers.
- **Tests:** Parse failure still suppressed/logged; callback failure reaches
  transport dispatch and callback-error handlers.
- **Acceptance:** Callback errors are observable and not mislabeled.

26.8 Protocol version validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``protocol.py:117`` returns the header protocol version but the
  only production caller discards it.
- **Files:** ``pymt5/protocol.py``, ``pymt5/transport.py``, protocol tests.
- **Change:** Validate the version against the supported set or explicitly
  document/telemetry-log the accepted range; do not silently ignore an
  incompatible header.
- **Tests:** Known, unknown, and malformed versions.
- **Acceptance:** Unsupported versions fail with a documented exception.

Phase 27 — Client and Account Lifecycle (P1)
--------------------------------------------

**Goal:** Session-changing operations are serialized against close/reconnect,
and account commands never report success without server acknowledgement.

27.1 ``init_session`` serialization and generation checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``client.py:807-814`` sends ``CMD_INIT`` without
  ``_session_lock`` or post-await generation/transport revalidation.
- **Files:** ``pymt5/client.py``, ``tests/test_client_lifecycle.py``.
- **Change:** Serialize like ``login()``/``logout()``; reject stale generation
  before and after the await.
- **Tests:** Close during init; reconnect replacement during init; no late
  session mutation.
- **Acceptance:** ``SessionError`` semantics match the docstring.

27.2 Foreground connect/login vs reconnect race
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``client.py:304-308`` only checks close cleanup, not an active
  ``_reconnect_task``.
- **Files:** ``pymt5/client.py``, reconnect guard tests.
- **Change:** Reject, cancel, or await an active reconnect before a new
  foreground session; allow the reconnect task's own login path.
- **Tests:** Foreground connect/login during each reconnect stage.
- **Acceptance:** No transport replacement/close race; no duplicate session.

27.3 Per-callback health thresholds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``client.py:737-738`` overwrites one global threshold for all
  degraded callbacks.
- **Files:** ``pymt5/client.py``, ``tests/test_phase_15_16.py``.
- **Change:** Store ``(callback, threshold)`` pairs and evaluate each callback
  against its own threshold.
- **Tests:** Two callbacks with different thresholds fire independently.
- **Acceptance:** Registration order does not change other callbacks' behavior.

27.4 Clear subscription tracking on logout/close
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``client.py:481`` clears credentials only; tick/book IDs survive
  and a later reconnect can restore the previous session's subscriptions.
- **Files:** ``pymt5/client.py``, lifecycle tests.
- **Change:** Reset ``_subscribed_ids`` and ``_subscribed_book_ids`` in the
  common cleanup path.
- **Tests:** Logout/close then reconnect with no stale subscriptions; caches
  consistent with the cleared IDs.
- **Acceptance:** No cross-session subscription restoration.

27.5 Heartbeat interval validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``client.py:205`` accepts zero/negative intervals, producing a
  tight ping loop.
- **Files:** ``pymt5/client.py``, validation tests.
- **Change:** Require a positive finite interval; heartbeat can be disabled via
  ``auto_heartbeat=False``.
- **Tests:** Zero, negative, NaN, and valid intervals.
- **Acceptance:** Invalid configuration fails at construction.

27.6 Account command result validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_account.py:384-385`` returns ``True`` unconditionally from
  ``disable_otp``; the password-change path treats a truncated body as code 0.
- **Files:** ``pymt5/_account.py``, account tests.
- **Change:** Check ``result.code`` and body length; return/propagate a
  documented failure.
- **Tests:** Nonzero code, empty body, and short body; success path.
- **Acceptance:** No false success; no silent zero code from truncation.

27.7 Long password hex conversion guard
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_account.py:501-502`` assumes any 320-character password is
  hex and lets ``ValueError`` escape or misinterprets a hex-looking password.
- **Files:** ``pymt5/_account.py``, OTP tests.
- **Change:** Validate the hex alphabet and explicit conversion; fall back to
  the documented plain truncation path.
- **Tests:** Non-hex 320-char password, hex 320-char password, short password.
- **Acceptance:** No uncaught ``ValueError``; encoding choice is deterministic.

Phase 28 — Trading, Currency, and Order Manager Correctness (P1)
----------------------------------------------------------------

**Goal:** Trading helpers use independent reference prices, correct ticket
fields, and explicit missing-value checks; order state cannot regress.

28.1 Stop-limit reference price
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_trading.py:755-757`` returns ``price_trigger`` as the
  reference, so the stop-level check compares the trigger with itself.
- **Files:** ``pymt5/_trading.py``, trading tests.
- **Change:** Use the current market price (or another independent reference)
  for ``_validate_requested_stops``.
- **Tests:** Stop-limit pending order with nonzero stops level; long and short
  sides.
- **Acceptance:** Valid orders are not rejected by self-comparison.

28.2 Deal ticket filter
~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_trading.py:276-277`` filters deals by ``trade_order`` even
  when the caller supplied a deal ticket.
- **Files:** ``pymt5/_trading.py``, API compatibility tests.
- **Change:** Filter by the deal identifier for deal queries; update tests that
  encoded the wrong behavior.
- **Tests:** Deal ticket, order ticket, and no-filter cases.
- **Acceptance:** Returned records match the documented identifier.

28.3 Zero-equity fallback
~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_trading.py:567-571`` uses ``account.get("equity", balance)
  or balance``, treating a legitimate zero equity as missing.
- **Files:** ``pymt5/_trading.py``, margin tests.
- **Change:** Use an explicit ``is None`` check for equity and margin-free
  fallbacks.
- **Tests:** equity=0 with nonzero balance; equity=None; positive equity.
- **Acceptance:** Zero equity is preserved in margin decisions.

28.4 Currency pair prefix/suffix matching
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_currency.py:157`` only matches pairs at the exact start/end
  of the broker symbol; common suffixes/prefixes fail silently.
- **Files:** ``pymt5/_currency.py``, currency conversion tests.
- **Change:** Match after removing known broker decorations, or use a
  documented normalized-symbol lookup with deterministic ambiguity handling.
- **Tests:** ``EURUSD.pro``, ``EURUSDm``, ``FX.EURUSD``, ``EURUSD-ECN``,
  inverse pairs, and ambiguous matches.
- **Acceptance:** Conversion succeeds or fails with a documented reason, never
  silently returns ``None`` for a supported symbol.

28.5 Order state monotonicity
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_order_manager.py:181-183`` accepts out-of-order pushes and
  can move a terminal order back to a non-terminal state.
- **Files:** ``pymt5/_order_manager.py``, order-manager tests.
- **Change:** Define a terminal-state set and ignore stale transitions; only
  state-change callbacks for accepted transitions.
- **Tests:** FILLED→PENDING, CANCELED→PLACED, duplicate completion.
- **Acceptance:** Terminal states never regress.

28.6 ``volume_current`` handling and filled volume
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_order_manager.py:185-187`` does not validate or clamp
  ``volume_current``; a missing value on FILLED leaves filled volume at zero.
- **Files:** ``pymt5/_order_manager.py``, order-manager tests.
- **Change:** Parse defensively, clamp to ``[0, order.volume]``, and set
  ``filled_volume = order.volume`` for FILLED without a current volume.
- **Tests:** None, NaN, negative, oversized, out-of-order values, FILLED.
- **Acceptance:** No exception, no impossible filled volume.

Phase 29 — Security and CI Hardening (P1/P2)
--------------------------------------------

**Goal:** Limit secret exposure, privilege, and supply-chain risk; do not
re-open the accepted demo-credential exception.

29.1 Dataclass representation redaction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``_pool.py:21`` and ``types.py:101`` include passwords, phone
  numbers, tax IDs, and addresses in auto-generated ``repr``.
- **Files:** ``pymt5/_pool.py``, ``pymt5/types.py``, tests.
- **Change:** Mark sensitive fields ``repr=False`` (and keep equality/hash
  semantics unchanged unless documented).
- **Tests:** ``repr`` contains redaction markers and no secret values for
  ``PoolAccount``, ``TradeRequest``, and account-opening requests.
- **Acceptance:** No credential/PII in default representations.

29.2 Remove token logging from live scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``scripts/live_test_client.py:40`` and
  ``scripts/live_test_v4.py:42`` log session token material.
- **Files:** ``scripts/live_test_client.py``, ``scripts/live_test_v4.py``.
- **Change:** Log only session IDs or non-reversible hashes; use lazy logging.
- **Tests:** A log-capture script test asserts no token substring.
- **Acceptance:** No token material in logs.

29.3 Scope demo secrets to the test step
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``.github/workflows/demo.yml:28-31`` injects demo credentials
  into every job step, including dependency installation.
- **Files:** ``.github/workflows/demo.yml``.
- **Change:** Keep non-sensitive config at job level; move
  ``PYMT5_PASSWORD``/``PYMT5_LOGIN`` to the integration-test step only.
- **Tests:** YAML parse plus a local workflow-equivalent check; no secret
  references in checkout/install steps.
- **Acceptance:** Secrets are absent from steps that execute third-party code.

29.4 Pin third-party release actions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``.github/workflows/release.yml:130`` uses mutable refs for
  ``pypa/gh-action-pypi-publish`` and ``softprops/action-gh-release``.
- **Files:** ``.github/workflows/release.yml``.
- **Change:** Pin to full commit SHAs with version comments; update
  deliberately.
- **Tests:** YAML parse and diff review; release flow unchanged.
- **Acceptance:** No mutable action refs in privileged jobs.

29.5 Least-privilege Pages workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``.github/workflows/pages.yml:8`` declares ``pages: write`` and
  ``id-token: write`` workflow-wide.
- **Files:** ``.github/workflows/pages.yml``.
- **Change:** Move write permissions to the deploy job; keep the build job at
  ``contents: read``.
- **Tests:** YAML parse; build/deploy job permission review.
- **Acceptance:** Build job cannot deploy or mint OIDC tokens.

29.6 Build metadata floor
~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``pyproject.toml:2`` allows setuptools 68, but SPDX license
  string metadata requires setuptools >= 77; ``wheel`` is obsolete for the
  backend.
- **Files:** ``pyproject.toml``.
- **Change:** Raise the setuptools floor to a supported version and remove
  ``wheel`` if the chosen backend no longer needs it; verify isolated build.
- **Tests:** ``make package-check`` and a clean isolated build.
- **Acceptance:** Metadata generation succeeds with the declared floor.

Phase 30 — Examples, Scripts, Documentation, and Re-validation (P2)
-------------------------------------------------------------------

**Goal:** Fix user-facing example/script defects, record the iteration, and
prove the remediation with the full local and live gates.

30.1 Example trading guards
~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``examples/04_trading.py:54/59`` dereferences a possibly
  missing symbol and indexes a possibly empty bar list.
- **Files:** ``examples/04_trading.py``.
- **Change:** Guard ``None`` before ``digits``; guard empty bars before
  ``bars[-1]``; clear diagnostics.
- **Tests:** Static/example smoke where feasible; manual dry-run review.
- **Acceptance:** No uncaught ``AttributeError``/``IndexError`` on missing data.

30.2 Example SL/TP direction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``examples/07_all_order_types.py:141-142`` assumes a long
  position when computing SL/TP.
- **Files:** ``examples/07_all_order_types.py``.
- **Change:** Derive direction from the selected position and place SL/TP on
  the correct side.
- **Tests:** Dry-run/manual review; no live mutation in automated tests.
- **Acceptance:** Both buy and sell positions produce valid levels.

30.3 Live script symbol key
~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``scripts/live_test_client.py:101`` reads ``"id"``; the schema
  field is ``symbol_id``, so tick subscription is always skipped.
- **Files:** ``scripts/live_test_client.py``.
- **Change:** Use ``symbol_id`` and remove the redundant guard.
- **Tests:** Script-level test with mocked symbols asserts a non-empty ID list
  and a subscription call.
- **Acceptance:** Tick step is no longer a silent no-op.

30.4 Docstring analyzer sort order
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``scripts/analyze_docstrings.py:138-144`` sorts test files first
  and fewest-issues first.
- **Files:** ``scripts/analyze_docstrings.py``.
- **Change:** Remove the inverted ordering; test files last, most issues first.
- **Tests:** Controlled fixture files with known counts.
- **Acceptance:** Recommended file is a non-test file with the most gaps.

30.5 Live script bootstrap assertions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Finding:** ``scripts/live_test_v3.py:112-113`` uses ``assert`` (stripped
  under ``-O``) and slices a bootstrap body without a length check.
- **Files:** ``scripts/live_test_v3.py``.
- **Change:** Explicit length validation and exception; keep the script
  read-only.
- **Tests:** Short bootstrap body raises a documented error.
- **Acceptance:** No truncated AES key in optimized mode.

30.6 Regression and coverage gates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Tests:** Add a failing regression for every Phase 25–29 task; keep 100%
  statement coverage without ``pragma: no cover`` exceptions.
- **Commands:** The exact CI command with ``-m "not integration"`` and
  ``--cov-fail-under=100`` on Python 3.11, 3.12, and 3.13; Ruff; mypy; bilingual
  docs; package check.
- **Acceptance:** All green; no new skips.

30.7 Read-only demo acceptance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Tests:** ``tests/test_integration.py`` with
  ``PYMT5_INTEGRATION=1``, ``PYMT5_REQUIRE_TICK=1``, and an active symbol.
- **Acceptance:** 10/10 pass during active trading; no trading commands; no
  credentials in output. If a cross-build behavior differs, record it instead
  of weakening the assertions.

30.8 OCR re-review
~~~~~~~~~~~~~~~~~~

- **Action:** Re-run OpenCodeReview on the changed modules and the remaining
  unprocessed CSS file; compare sessions and close each finding as fixed,
  accepted, or false positive with a one-line reason.
- **Acceptance:** No unclassified high/critical finding remains; the accepted
  demo exception and the CI hypothesis false positive remain recorded.

30.9 Documentation and changelog
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Files:** ``README.md``, ``README.zh-CN.md``, relevant guides, changelog.
- **Change:** Record behavior changes (subscription acknowledgement, validation
  errors, bounded cleanup, execution policies) in both languages.
- **Acceptance:** Translation parity and docs build checks pass.

Implementation Order
--------------------

1. **Phase 25** — transport deadlock/cancellation/cleanup; highest blast radius.
2. **Phase 26** — subscription acknowledgement and parser bounds.
3. **Phase 27** — session serialization and account result contracts.
4. **Phase 28** — trading/currency/order-manager correctness.
5. **Phase 29** — security/CI hardening.
6. **Phase 30** — examples/scripts, final gates, OCR re-review.

Each task follows test-first development: reproduce the finding with a failing
test, implement the smallest fix, run the focused module, then the full offline
suite. A finding that cannot be reproduced is closed as a false positive rather
than “fixed” by test-only accommodation.

Verification Checklist
----------------------

After every phase:

- [ ] Focused regressions pass.
- [ ] Full offline suite passes with 100.00% statement coverage.
- [ ] ``ruff check pymt5/ tests/`` passes.
- [ ] ``python -m mypy pymt5/ --ignore-missing-imports`` passes.
- [ ] ``python docs/build_docs.py`` and ``python docs/check_docs.py`` pass.
- [ ] ``make package-check`` passes.
- [ ] ``git diff --check`` passes.
- [ ] No credentials or private account data in output or commits.

Before closing the iteration:

- [ ] 3.11/3.12/3.13 × Linux/macOS/Windows CI matrix is green.
- [ ] Read-only demo acceptance is 10/10 during active trading.
- [ ] OCR re-review has no unclassified high/critical finding.
- [ ] Accepted exception and false positive are documented in the changelog or
      review record.

Risks and Open Decisions
------------------------

- **Async listener policy (25.1):** task isolation changes ordering and error
  propagation semantics. Decide and document before implementation; do not
  silently change public callback behavior.
- **Ambiguous frame policy (25.4):** the protocol cannot distinguish every
  push from a response. Prefer failing a request over returning a foreign body,
  but confirm this does not break existing demo flows.
- **Currency normalization (28.4):** broker suffix/prefix rules are not
  universal. Implement a documented, deterministic matcher and test ambiguity.
- **Build metadata (29.6):** raising the setuptools floor may affect downstream
  builders; verify with a clean isolated build.
- **OCR coverage:** ``docs/_static/custom.css`` remains unscanned; a CSS scan
  is optional and does not block the library phases.

Appendix A — Accepted Exceptions and False Positives
----------------------------------------------------

- **Accepted:** hard-coded disposable demo credentials in
  ``scripts/live_test_v2.py``, ``v3.py``, and ``v4.py``. Not a work item.
- **False positive:** CI hypothesis plugin failure claim. Exact CI command on
  ``tests/test_fuzz.py``: 5 passed.
- **Out of scope:** cross-build protocol behavior not reproducible on the
  available demo server; record rather than infer.

Appendix B — Deferred Medium/Low Backlog
----------------------------------------

The remaining medium/low items are tracked here by theme; each is picked up
only after the phases above are green, or when a user-facing defect requires it.

- Documentation accuracy and bilingual parity.
- Example diagnostics and script ergonomics.
- Low-risk style/performance cleanups in analysis and live-test scripts.
- Additional protocol/telemetry validation beyond Phase 26.
- Further workflow pinning and permission reductions beyond Phase 29.

The full OCR output, including per-comment suggested code, is retained as the
review record for this iteration; findings are not copied wholesale into this
plan to avoid stale duplication.
