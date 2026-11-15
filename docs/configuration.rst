Configuration
=============

Connection settings
-------------------

The most commonly used ``MT5WebClient`` constructor settings are:

.. list-table::
   :header-rows: 1
   :widths: 25 18 43

   * - Setting
     - Default
     - Purpose
   * - ``uri``
     - MT5 Web Terminal URL
     - WebSocket endpoint. Use the endpoint authorized for your environment.
   * - ``timeout``
     - ``30.0``
     - Command and initial connection timeout in seconds. A timeout after a
       possible send isolates the connection; it is not a server ACK/NACK.
   * - ``heartbeat_interval``
     - ``30.0``
     - Seconds between heartbeat pings after login.
   * - ``auto_reconnect``
     - ``False``
     - Reconnect and log in again after an unexpected disconnect.
   * - ``max_reconnect_attempts``
     - ``5``
     - Maximum attempts per reconnect cycle.
   * - ``reconnect_delay`` / ``max_reconnect_delay``
     - ``3.0`` / ``60.0``
     - Base and cap for exponential backoff with jitter.
   * - ``rate_limit`` / ``rate_burst``
     - ``0`` / ``20``
     - Optional token-bucket command throttling; zero disables the limit.
   * - ``metrics``
     - ``None``
     - Optional ``MetricsCollector`` implementation.

See :doc:`api` for the complete constructor and cache-related settings.

Protocol compatibility settings
-------------------------------

Historical bars: ``rate_bar_format``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``auto`` decodes only when response length uniquely identifies the standard
48-byte or extended 56-byte record. Ambiguous or mismatched lengths raise
``ProtocolError``. If the server layout is known, select ``standard`` or
``extended`` explicitly, or pass ``bar_format`` to supported rate methods.
Length validation cannot prove semantic correctness for every payload.

Tick prices: ``tick_price_mode`` and ``tick_price_modes``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``auto`` preserves the legacy symbol/magnitude heuristic and is the default.
  It is a compatibility behavior, not a universal wire-format guarantee.
- ``scaled`` divides nonzero prices by ``10 ** symbol.digits``.
- ``normalized`` leaves wire prices unchanged.
- ``tick_price_modes={"EURUSD": "scaled"}`` overrides the global mode for a
  symbol; keys are case-insensitive and take precedence.

Apply the same setting to raw callbacks, typed events, and caches. Validate
against a small authorized sample from the target server/build before relying
on the values. A live probe is available as ``scripts/demo_tick_probe.py``;
its output is evidence to inspect, not an automatic acceptance decision.

Trade requests: ``trade_protocol``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``legacy`` is the default wire profile. ``build_6090`` opts into the reserved
bytes and market-deal behavior reported for one environment. The client does
not infer this profile from the handshake build number. Broker, execution-mode,
and build compatibility must be established independently.

Environment variables
---------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Variable
     - Use
   * - ``PYMT5_SERVER``
     - WebSocket URI used by examples and integration tests.
   * - ``PYMT5_LOGIN`` / ``PYMT5_PASSWORD``
     - Credentials for explicitly enabled demo integration tests. Never commit
       their values.
   * - ``PYMT5_TEST_SYMBOL``
     - Liquid, active symbol for tick integration/probe runs.
   * - ``PYMT5_INTEGRATION=1``
     - Explicit opt-in to live integration tests; absent from the offline gate.
   * - ``PYMT5_LOG_LEVEL``
     - Library log threshold. Avoid verbose credential/session logging in
       captured reports.

The core client does not automatically load ``.env``. Use your application's
secret manager or an explicit dotenv loader. Keep local environment files
ignored by Git.

Recommended starting point
--------------------------

.. code-block:: python

   import os
   from pymt5 import MT5WebClient

   client = MT5WebClient(
       uri=os.environ["PYMT5_SERVER"],
       timeout=30.0,
       auto_reconnect=True,
       rate_bar_format="auto",
       tick_price_mode="auto",
       trade_protocol="legacy",
   )

Start with read-only queries. Change compatibility modes only from evidence,
and review :doc:`reliability` and :doc:`security` before trading.
