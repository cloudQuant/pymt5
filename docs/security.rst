Security and Operational Safety
===============================

pymt5 can read account data and submit state-changing requests over a live
network connection. Treat connection code, credentials, logs, and protocol
research materials as sensitive. This guide describes safeguards and limits; it
is not a guarantee that a server, broker, or integration is safe.

For a first connection, follow :doc:`quickstart` and review
:doc:`configuration`. Method-level details are in :doc:`api`, while observed
wire behavior and its evidence boundaries are in :doc:`protocol_reference`.

Authorization and project status
--------------------------------

pymt5 is an unofficial client based on a reverse-engineered MetaTrader 5 Web
Terminal protocol. It is not affiliated with or endorsed by MetaQuotes. A
working command does not establish that its use is authorized.

- Connect only to servers and accounts that you own or have explicit permission
  to access. Follow applicable law, broker terms, platform terms, and your
  organization's security policies.
- Do not use the client to bypass access controls, account restrictions, or
  technical protections. Protocol behavior can change without notice and can
  differ by broker, server build, symbol, and market session.
- Start with an authorized demo account. Validate each required read-only
  workflow before considering any state-changing operation, and validate that
  operation separately before using a real account.
- Treat observations from one server build as compatibility evidence, not as a
  universal protocol contract. Record the package version and
  ``client.server_build`` when validating an integration.

Prefer read-only operations
---------------------------

There is no client-wide switch that technically enforces read-only access.
Safety therefore depends on which methods the application calls. Begin with
``connect()``, ``login()``, ``load_symbols()``, account queries, and historical
market-data queries. Add tick or book subscriptions only when needed.

Methods such as ``trade_request()``, market and pending-order helpers, position
or order modification, cancellation and closing, password changes, account
opening, OTP setup, verification, and server notifications can mutate remote
state. Do not place them in startup code, generic reconnect callbacks, or a
supposedly read-only health check. Review the complete call path, including
compatibility wrappers and example code, before running it.

Keep automatic trading disabled unless it is an explicit, reviewed requirement.
Use separate demo credentials, minimum practical permissions, small test scope,
and an operator-controlled stop mechanism. A successful offline test never
proves live trading safety.

Credential handling
-------------------

Use environment variables or an approved secret manager rather than source
literals. The live integration tests, ``scripts/live_test*.py``, and
``scripts/demo_tick_probe.py`` use:

- ``PYMT5_SERVER`` for the authorized WebSocket endpoint;
- ``PYMT5_LOGIN`` for the numeric login; and
- ``PYMT5_PASSWORD`` for the password.

A local ``.env`` file is ignored by Git, but ignore rules are not a complete
secret-management control. Restrict file permissions, never commit the file,
and do not copy it into containers, build artifacts, or issue attachments. The
probe's ``--env-file`` option additionally requires the optional
``python-dotenv`` package. Use masked CI secrets if a separately authorized
live job is ever required; the default CI does not need live credentials.

.. note::

   The numbered files under ``examples/`` load ``SERVER``, ``LOGIN``, and
   ``PASSWORD`` from ``PYMT5_SERVER``, ``PYMT5_LOGIN``, and ``PYMT5_PASSWORD``
   through ``os.environ``. Set all three variables in the process environment
   before running an example; ``PYMT5_LOGIN`` must contain an integer. The
   examples do not implicitly load ``.env`` files. The ``scripts/live_test*.py``
   files use the same three environment variables. Do not publish terminal
   output containing real values.

Avoid putting passwords in command-line arguments, URLs, shell history, source
code, notebook cells, or exception messages. Rotate any credential that has
been committed, pasted into a report, or otherwise exposed; deleting it from a
later commit does not remove it from history or copies.

Logs, reports, and retained data
--------------------------------

Set an appropriate log level before importing pymt5. ``PYMT5_LOG_LEVEL`` is read
when ``pymt5._logging`` is imported. In particular, successful login currently
logs the numeric login and session ID at INFO, so do not treat INFO output as
automatically safe to publish. DEBUG can add protocol timing, command, and
connection details; application callbacks can add still more sensitive data.

Never include passwords, OTPs, session tokens, session IDs, CIDs, login numbers,
account or position details, raw authenticated frames, or private server data in
public logs, test fixtures, coverage artifacts, screenshots, or issue reports.
Prefer exception types, numeric response codes, UTC timestamps, and sanitized
method names. Review an entire log rather than redacting only its first lines.
See :doc:`troubleshooting` for a sanitized diagnostic and reporting checklist.

Apply the same rules to metrics, traces, crash reports, and retained tick or
account caches. Data minimization and bounded retention are preferable to
trying to sanitize a large archive later.

Timeouts and trade outcome uncertainty
--------------------------------------

A timeout means that the client stopped waiting; it does **not** mean that the
server rejected or failed to execute a request. Cancellation and a send failure
can be equally ambiguous once sending may have started.

When the outcome may be unknown, the transport isolates that connection, fails
other in-flight requests, and prevents a late response from being assigned to a
later request on the same connection. With ``auto_reconnect=True``, the client
can create a fresh connection, log in again, and restore tick and book
subscriptions. It does not establish the server-side result of the interrupted
operation.

.. warning::

   Never blindly retry a trade after ``MT5TimeoutError``, cancellation, a send
   failure, or a disconnect. Automatic reconnection is not proof that retrying
   is safe, and it must not be used as an automatic trade-replay mechanism.

After an uncertain trade, stop the affected workflow. On a fresh authorized
session, reconcile server positions, pending orders, and relevant deal/order
history before any operator-approved next action. Allow for propagation delay
and remember that queries reduce uncertainty but may not provide absolute proof
in every failure scenario. A local request ID, comment, or callback also does
not prove execution. See :doc:`protocol_reference` for the response-correlation
boundary and :doc:`api` for query and exception interfaces.

Rates, ticks, and request throttling
------------------------------------

Protocol-sensitive values require explicit validation:

- Historical bars have observed 48-byte standard and 56-byte extended layouts.
  In ``rate_bar_format="auto"`` mode, a response length compatible with both or
  neither layout raises ``ProtocolError`` instead of guessing. Select
  ``"standard"`` or ``"extended"`` only after identifying the authorized
  server's format. Length checks cannot detect every malformed or truncated
  response.
- ``tick_price_mode="auto"`` uses a legacy symbol/magnitude heuristic, not a
  wire-format guarantee. Compare a normalized wire sample with nearby bars on
  the target server before choosing ``"scaled"`` or ``"normalized"``. Per-symbol
  overrides may be necessary, and no universal cross-broker result has been
  established.
- A subscription acknowledgement does not promise that ticks will arrive.
  Closed markets, inactive symbols, and server-specific behavior can produce a
  quiet stream. Cached ticks are observations, not a complete authoritative
  feed.
- A server can reject a tick unsubscribe, including the request that removes
  the final symbol. A rejected request raises ``ProtocolError`` and retains the
  last confirmed local set; it must not be logged as successful cleanup.

The optional ``rate_limit`` token bucket is a local client-side throttle, not a
statement of the server's limits. Keep request rates and concurrency bounded,
avoid tight retry loops, and follow server restrictions. A burst that succeeds
once is not evidence that the same rate is safe or permitted continuously.

Private protocol-analysis materials
-----------------------------------

Third-party Web Terminal JavaScript snapshots and generated analysis excerpts
are private research inputs, not redistributable pymt5 source files. Keep
lawfully obtained snapshots in a private directory outside the checkout and
point analysis helpers to it with ``PYMT5_ANALYSIS_DIR``. The ``analysis/``
directory ignores ``*.js`` to reduce accidental inclusion.

Do not commit snapshots, generated output, copied excerpts, or private research
captures, and do not attach them to issues or pull requests. Removing a file
from the current worktree does not erase Git history. The repository's MIT
license does not grant rights to third-party bundles, and private retention does
not replace any applicable license obligations. Public protocol documentation
should state observed behavior and uncertainty without reproducing private
materials.

Pre-run checklist
-----------------

- Authorization, server build, account type, and intended operations are known.
- Credentials come from an approved source and are absent from source and logs.
- The first run is read-only and uses an authorized demo account.
- State-changing examples and reconnect paths have been reviewed explicitly.
- Timeout handling treats trade outcomes as unknown and prohibits blind retry.
- Rate, tick-price, subscription, and server-rate assumptions are validated.
- Reports and research artifacts have been checked for secrets and private
  third-party materials.

Review :doc:`changelog` before upgrades because compatibility and safety
behavior can change between releases.
