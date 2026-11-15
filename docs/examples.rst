Examples
========

The repository's ``examples/`` directory contains live-network demonstrations.
They are not offline samples and must not be run against an account unless you
are authorized to use it. Read :doc:`security` first, start with a read-only
example on a demo account, and do not execute a trading example to test whether
the connection works.

.. danger::

   Examples 04 and 07 submit trades and can modify, cancel, or close positions
   and orders immediately. Example 08 sends a server notification and submits a
   verification code. Review every operation before running them. There is no
   dry-run wrapper around these scripts.

Prepare credentials and the environment
---------------------------------------

Install the package from PyPI or use an editable source installation:

.. code-block:: bash

   python -m pip install mt5-web-client

The numbered example files load ``SERVER``, ``LOGIN``, and ``PASSWORD`` from the
process environment:

.. code-block:: python

   import os

   SERVER = os.environ["PYMT5_SERVER"]
   LOGIN = int(os.environ["PYMT5_LOGIN"])
   PASSWORD = os.environ["PYMT5_PASSWORD"]

Set all three variables before running an example; ``PYMT5_LOGIN`` must contain
an integer. The examples do not implicitly load ``.env`` files, so export the
variables through your shell or provide them through an approved secret manager.
Never copy real values into documentation, commits, logs, or issue reports. See
:doc:`quickstart` for the basic async lifecycle and :doc:`configuration` for
timeout, heartbeat, reconnect, rate-format, tick-price, and throttling options.

The examples also set ``NO_PROXY`` and ``no_proxy`` to ``*``. This bypasses
proxy use for the process. Remove or adjust those lines when your network
requires a proxy or your security policy prohibits that bypass.

Example catalog
---------------

The labels below describe actual state-changing behavior, not just the filename.
“Read-only” means the script queries data or subscribes to streams without
placing or modifying trades; subscriptions still create temporary server-side
session state. All examples require an authorized live login.

.. list-table:: Current examples
   :header-rows: 1
   :widths: 28 22 50

   * - File
     - Classification
     - Purpose and side effects
   * - ``01_connect_and_account.py``
     - **Read-only**
     - Connect and log in; load symbols; query account details, positions,
       pending orders, deal history, and ping. It prints account and trading
       history information, so its logs are sensitive.
   * - ``02_market_data.py``
     - **Read-only**
     - Query symbol groups, cached symbol information, full contract
       specifications, spreads, and M1/H1/D1 historical bars. A server-specific
       rate layout may require explicit configuration.
   * - ``03_realtime_ticks.py``
     - **Read-only**
     - Load symbols, subscribe to four hard-coded symbols, and display tick,
       account, symbol-detail, login-status, and trade-update callbacks. It does
       not submit trades; a quiet market can produce no ticks.
   * - ``04_trading.py``
     - **Trading / mutating**
     - Submit market buy and sell requests, a buy limit, and a sell stop, then
       modify SL/TP on an existing position when available. This changes real
       server state even on a demo account.
   * - ``05_order_book.py``
     - **Read-only**
     - Subscribe to order-book and tick streams for two hard-coded symbols and
       print updates. A server may not support DOM; a caught subscription error
       is not proof of support or availability.
   * - ``06_all_push_notifications.py``
     - **Read-only**
     - Register the available push callbacks and subscribe to tick/book streams.
       Trade-related handlers only observe received frames; this script does not
       itself place a trade. Output can include sensitive account updates.
   * - ``07_all_order_types.py``
     - **Trading / mutating**
     - Submit market, limit, stop, stop-limit, and raw trade requests; modify
       SL/TP; cancel a pending order; and close a position when present. It can
       act on pre-existing account state as well as orders it just created.
   * - ``08_misc_features.py``
     - **Mutating (non-trade operations)**
     - Query links and trader parameters, enable auto-reconnect, send a server
       notification, and submit a placeholder verification code. Password change
       and demo opening are shown but skipped. The notification and verification
       calls are still live mutations, not read-only probes.

The scripts contain hard-coded watch symbols and fixed listening periods. Check
that each exact symbol exists in ``load_symbols()`` output for the target server
and that its market is open. Symbol names, suffixes, DOM support, trading rules,
and response layouts can differ by server and build.

Run an example
--------------

Run one selected script at a time from the repository root after preparing its
credentials and reviewing its classification:

.. code-block:: bash

   python examples/01_connect_and_account.py
   python examples/02_market_data.py
   python examples/03_realtime_ticks.py
   python examples/05_order_book.py
   python examples/06_all_push_notifications.py

The general form is:

.. code-block:: bash

   python examples/<example-file>.py

Start with 01 or 02. Examples 03, 05, and 06 subscribe to streams and wait only
for their configured period. No ticks or book updates during that period is not,
by itself, evidence of a client failure.

Do not include 04, 07, or 08 in a generic “run all examples” command. If a
state-changing demonstration is explicitly authorized, inspect the exact symbol,
volume, price, existing positions/orders, verification payload, and notification
first; use an authorized demo account and run only that individual file. A
nonzero response, timeout, cancellation, or disconnect after a trade request
must not trigger a blind retry. Reconcile server state as described in
:doc:`security` and :doc:`troubleshooting`.

Read-only wire-tick diagnostic
------------------------------

``scripts/demo_tick_probe.py`` is a separate diagnostic, not one of the eight
numbered examples. For an authorized demo account it records a raw cmd=8 tick,
scaled price candidates, and nearby M1 bars without placing trades. It already
reads ``PYMT5_SERVER``, ``PYMT5_LOGIN``, and ``PYMT5_PASSWORD``:

.. code-block:: bash

   python scripts/demo_tick_probe.py --symbol EURUSD --seconds 90

Use an exact active server symbol. Optionally pass ``--env-file .env`` when
``python-dotenv`` is installed, or ``--bar-format standard`` / ``extended`` only
when that layout has been established. The script emits sanitized JSON and
returns success only when a sample was received and both unsubscribe and close
succeeded. ``no_sample`` can simply mean a closed market; a rejected unsubscribe
remains an error. The report supports investigation but does not automatically
choose a tick-price mode or certify a server build.

Common adjustments
------------------

- **Connection or login failure:** verify endpoint, proxy, bootstrap build, and
  sanitized login response code. Do not guess meanings for undocumented codes.
- **No stream data:** check exact symbols and market hours; for the live
  integration test only, ``PYMT5_TEST_SYMBOL`` can select a symbol explicitly.
- **Rate ``ProtocolError``:** identify the 48-byte or 56-byte layout rather than
  selecting whichever option suppresses the error.
- **Unexpected prices:** investigate normalized wire values before choosing a
  global or per-symbol ``tick_price_mode``.
- **Timeout after a mutation:** treat the result as unknown, not as a rejected
  request; do not rerun the script automatically.

See :doc:`troubleshooting` for diagnostics, :doc:`api` for method signatures,
:doc:`protocol_reference` for observed layouts and correlation limits, and
:doc:`changelog` for release-specific compatibility changes.
