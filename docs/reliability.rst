Reliability
===========

Use these guidelines to handle timeouts, cancellation, disconnects, reconnects,
and cleanup without inventing certainty that the protocol cannot provide.
Examples on this page are read-only; none submits a trade.

.. warning::

   pymt5 implements an unofficial, reverse-engineered Web Terminal protocol.
   Response correlation and delivery semantics are limited by observed server
   behavior, not by a broker-independent protocol guarantee. Validate recovery
   procedures on an authorized demo server and reconcile account-changing
   operations with authoritative account state.

Understand command correlation and isolation
--------------------------------------------

Responses have not been shown to echo a reliable request identifier. The
transport therefore applies two protections:

1. Requests with the **same command ID** are serialized until a response is
   received. Different command IDs can proceed independently.
2. Once sending may have occurred, a timeout, cancellation, or send failure
   makes the outcome uncertain. The transport isolates that connection, fails
   its other in-flight requests, and prevents later requests from reusing it.
   A late response therefore cannot be assigned to a subsequent request on the
   old connection.

These protections do not distinguish an unsolicited push or duplicate response
from an outstanding response with the same command ID. They also do not reveal
what the server executed. In particular, cmd=4 listeners can run for ordinary
position/order query responses; see :doc:`push_notifications`.

``MT5TimeoutError`` means the client did not complete the command within its
timeout, not that the server did nothing. ``SessionError`` can indicate that a
connection generation was invalidated. ``asyncio.CancelledError`` from caller
cancellation requires the same caution and normally must be re-raised after
application cleanup.

Reconcile trades; never assume failure
--------------------------------------

After a timed-out or cancelled trade, server execution is **unknown**. It may
not have arrived, may have been accepted, or may have executed. Do not blindly
retry it, and do not use automatic reconnection as an idempotency mechanism.
The client does not automatically retry the interrupted trade.

On a fresh, logged-in session, collect read-only evidence before any further
trade decision:

.. code-block:: python

   async def reconciliation_snapshot(client):
       positions, orders, history = await asyncio.gather(
           client.get_positions(),
           client.get_orders(),
           client.get_trade_history(),
       )
       return {"positions": positions, "orders": orders, "history": history}

Compare the intended action, symbol, side, volume, prices, comment, and timing
with the results. A snapshot alone cannot always establish request identity,
and the lack of a matching record immediately after reconnect is not proof of
non-execution. If uncertainty remains, stop automated retries and use the
broker's authorized support/management channels. See :doc:`trading` for the
trade-result and demo-account warnings.

Configure reconnection without hiding gaps
------------------------------------------

Automatic reconnection is disabled by default. After a successful login, the
client retains credentials in memory for reconnection and clears its stored
reference during close/logout cleanup.

.. code-block:: python

   import asyncio
   import os

   from pymt5 import MT5WebClient, PyMT5Error

   async def monitor() -> None:
       disconnected = asyncio.Event()
       client = MT5WebClient(
           uri=os.environ["PYMT5_SERVER"],
           timeout=10.0,
           heartbeat_interval=30.0,
           auto_reconnect=True,
           max_reconnect_attempts=5,
           reconnect_delay=3.0,
           max_reconnect_delay=60.0,
       )
       client.on_disconnect(disconnected.set)
       try:
           async with client:
               await client.login(
                   int(os.environ["PYMT5_LOGIN"]),
                   os.environ["PYMT5_PASSWORD"],
               )
               await client.load_symbols()
               for _ in range(6):
                   health = await client.health_check()
                   print(health.state.value, health.ping_latency_ms)
                   await asyncio.sleep(10)
               if disconnected.is_set():
                   print("at least one disconnect occurred; cached data may have gaps")
       except PyMT5Error as exc:
           print(f"monitor stopped: {exc}")

   asyncio.run(monitor())

Reconnect uses exponential backoff with jitter, capped by
``max_reconnect_delay``. It creates a fresh transport, migrates registered
listeners, logs in again, restarts the heartbeat, and attempts to restore the
last locally retained tick and book subscription sets. A successful socket
connection does not mean every restoration step succeeded.

Reconnection does **not** replay missed ticks, books, account events, query
responses, or trades. Tick caches and cached tick history can contain gaps or
stale values. ``copy_ticks_*()`` reads only that local push history, not a
server archive. Re-query required state after reconnect and use event/timestamp
checks appropriate to the application.

Tick subscription state deserves special care: nonzero subscribe/unsubscribe
responses retain the last confirmed local set, but after timeout or
cancellation that set is not proof of server state. Re-subscription is an
attempt to restore the local desired set, not a reconciliation protocol.

Check health without treating it as proof
-----------------------------------------

``client.is_connected`` is true only when the transport is ready and the client
considers itself logged in. ``client.transport.state`` is one of
``DISCONNECTED``, ``CONNECTING``, ``READY``, ``CLOSING``, or ``ERROR``.

``await client.health_check()`` returns a ``HealthStatus`` snapshot containing:

* ``state`` — current transport state;
* ``ping_latency_ms`` — measured round-trip time, or ``None`` if no ping could
  be measured;
* ``last_message_at`` — last receive time on the monotonic clock, or ``None``;
* ``uptime_seconds`` — time since the current client connection timestamp;
* ``reconnect_count`` — successful reconnect restorations counted by the client.

The health check sends a ping when the transport is ready and converts ping
failure to ``ping_latency_ms=None``. It is a diagnostic snapshot, not a guarantee
that a subsequent command will succeed. ``on_health_degraded(callback,
threshold_ms=...)`` runs callbacks when a measured ping exceeds the threshold.
Disconnect callbacks and health callbacks should return quickly and avoid
blocking transport processing.

Bound load and interpret errors precisely
-----------------------------------------

The optional ``rate_limit``/``rate_burst`` token bucket throttles client command
sending. It does not establish the server's quota or prevent rejection, and it
does not remove the need for same-command serialization and timeout handling.
The constructor ``timeout`` also bounds command waits and connection opening.

.. list-table:: Common failure signals
   :header-rows: 1
   :widths: 28 72

   * - Signal
     - Appropriate interpretation
   * - ``ValidationError``
     - A helper rejected local input. This does not describe other server-side
       validation.
   * - ``ProtocolError``
     - A command/layout was unsupported or a covered response was rejected or
       malformed. For example, rates reject nonzero codes and ambiguous
       lengths; tick subscribe/unsubscribe reject nonzero codes.
   * - ``MT5TimeoutError``
     - No timely completed response. A sent request's server outcome can be
       unknown and the old connection is isolated.
   * - ``SessionError`` / ``MT5ConnectionError``
     - Session state, generation, or socket is unusable. Establish and verify a
       fresh session rather than reusing an uncertain one.
   * - ``AuthenticationError``
     - Login was rejected or its response was invalid.
   * - ``TradeResult(success=False)``
     - The server returned a trade rejection/failure retcode. This differs from
       a transport timeout, whose execution outcome is unknown.

Do not convert all exceptions into automatic retries. Read-only retries can
still produce duplicated callbacks or gaps, and account-changing retries can
duplicate execution. Preserve cancellation semantics and the original error
context.

Make cleanup outcome visible
----------------------------

Use ``async with MT5WebClient(...)`` for normal session close and allow close
errors to reach the application's error handler. Subscription cleanup needs a
separate outcome: entering/exiting a managed handle does not prove that the
server accepted an unsubscribe if an exception was raised.

.. code-block:: python

   cleanup_confirmed = False
   ids = await client.subscribe_symbols(["EURUSD"])
   try:
       await consume_read_only_updates()
   finally:
       try:
           await client.unsubscribe_ticks(ids)
       except PyMT5Error as exc:
           print(f"unsubscribe rejected or uncertain: {exc}")
       else:
           cleanup_confirmed = True

   # Only cleanup_confirmed=True records a locally confirmed unsubscribe call.

A build 6320 demo observation showed that removing the full tick subscription
set can be rejected. ``SubscriptionHandle.unsubscribe()`` marks itself inactive
only after the unsubscribe coroutine returns successfully, so a failed cleanup
can be retried deliberately after considering session state; it must never be
reported as successful by default.

For tick scaling, history, and book-cache limitations, see :doc:`market_data`.
For callback backpressure and handler removal, see :doc:`push_notifications`.
