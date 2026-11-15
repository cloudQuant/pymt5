Push Notifications
==================

pymt5 dispatches parsed server frames to synchronous or asynchronous callbacks.
The examples on this page only observe data and manage subscriptions; they do
not place trades.

.. warning::

   The Web Terminal protocol is unofficial and reverse-engineered. Push layouts
   and delivery behavior vary by server/build, and not every observed command
   has been verified as an unsolicited push. Do not treat callbacks as an
   exactly-once event log or as independent confirmation of account state.

Choose the callback interface
-----------------------------

.. list-table:: Available registrations
   :header-rows: 1
   :widths: 10 27 63

   * - Command
     - Registration
     - Callback data and limitations
   * - 8
     - ``on_tick()``
     - A list of tick dictionaries. Register before tick subscription.
   * - 4
     - ``on_trade_update()``, ``on_position_update()``, ``on_order_update()``
     - Combined or separate position/order snapshots. **Ordinary query
       responses also invoke these callbacks.**
   * - 10
     - ``on_trade_transaction()``
     - A transaction dictionary with ``update_type``; type 2 contains balance
       information, deals, and positions, while other types describe an order
       transaction. A real unsolicited trade push has not been verified.
   * - 14
     - ``on_account_update()``
     - An account dictionary containing balance/equity/margin fields.
   * - 17
     - ``on_symbol_details()``
     - Extended symbol quote records; see the raw-value rules below.
   * - 19
     - ``on_trade_result()``
     - An asynchronous trade-result dictionary with a nested ``result`` when
       the response is large enough. It does not replace reconciliation.
   * - 23
     - ``on_book_update()``
     - A list of depth-of-market records after book subscription.
   * - 13
     - ``on_symbol_update()``
     - Raw ``CommandResult`` because the exact schema varies.
   * - 15
     - ``on_login_status()``
     - Raw ``CommandResult`` for a possible login/session status change.

The typed alternatives ``on_tick_event()``, ``on_book_event()``,
``on_trade_result_event()``, and ``on_account_event()`` emit frozen
``TickEvent``, ``BookEvent``, ``TradeResultEvent``, and ``AccountEvent``
dataclasses. Tick and book registration invokes the callback once per record,
whereas their dictionary-based counterparts receive the whole batch. Typed
``BookEvent.entries`` combines bids and asks; use ``raw`` when side-specific
lists are needed.

Receive ticks and remove handlers safely
----------------------------------------

The registration methods that wrap a callback return the **internal handler**.
Pass that returned object to ``transport.off()``, not the original user
function. Raw ``on_symbol_update()`` and ``on_login_status()`` return the
original callback.

.. code-block:: python

   import asyncio
   from pymt5 import CMD_TICK_PUSH, PyMT5Error

   def show_ticks(ticks):
       for tick in ticks:
           print(tick.get("symbol", tick["symbol_id"]), tick["bid"], tick["ask"])

   handler = client.on_tick(show_ticks)
   ids = await client.subscribe_symbols(["EURUSD"])
   try:
       await asyncio.sleep(30)
   finally:
       client.transport.off(CMD_TICK_PUSH, handler)
       try:
           await client.unsubscribe_ticks(ids)
       except PyMT5Error as exc:
           # A rejection, timeout, or invalidated session is not a successful
           # unsubscribe. Preserve/log the failure for application cleanup.
           print(f"tick cleanup was not confirmed: {exc}")

A nonzero tick unsubscribe response raises ``ProtocolError`` and retains the
last confirmed local set. In particular, a build 6320 demo observation rejected
removal of the complete tick set. Never log or return success merely because
cleanup was attempted. After a timeout or cancellation, the local set is not
proof of server state. Managed ``SubscriptionHandle`` cleanup propagates the
same failures. See :doc:`market_data` and :doc:`reliability`.

Ticks may be partial updates. The parser fills zero bid/ask/last values from
its previous cache when possible. The default ``tick_price_mode="auto"`` uses
a legacy symbol/magnitude heuristic that is **not universally validated**.
Configure ``scaled`` or ``normalized`` after sampling the authorized
server/build, using per-symbol overrides where necessary. The setting applies
equally to dictionary callbacks, typed events, and caches. See
:doc:`market_data` for the exact modes and history limits.

Treat cmd=4 callbacks as snapshots, not change events
-----------------------------------------------------

.. code-block:: python

   def show_snapshot(data):
       print(f"snapshot: positions={len(data['positions'])}, orders={len(data['orders'])}")

   handler = client.on_trade_update(show_snapshot)
   await client.get_positions_and_orders()  # this response invokes show_snapshot

Calling ``get_positions()``, ``get_orders()``, or
``get_positions_and_orders()`` can therefore invoke the cmd=4 callbacks for an
ordinary response. An unsolicited cmd=4 change push has not been verified, and
the callback cannot distinguish a response from a push. If change detection is
needed, compare snapshots in application code and expect duplicate state.
Separate ``on_position_update()`` and ``on_order_update()`` registrations have
the same limitation.

Keep the receive loop responsive
--------------------------------

Callbacks run in the transport receive dispatch path. An asynchronous callback
is awaited, so slow user work can delay later frames and command responses.
Move expensive work to an application task and choose an explicit bounded-queue
policy rather than growing memory without limit.

.. code-block:: python

   import asyncio

   queue = asyncio.Queue(maxsize=1000)

   def enqueue_ticks(ticks):
       try:
           queue.put_nowait(ticks)
       except asyncio.QueueFull:
           # Example policy: drop and count/report; do not silently block recv.
           print("tick queue full; dropping one batch")

   handler = client.on_tick(enqueue_ticks)

   async def consume():
       while True:
           ticks = await queue.get()
           try:
               process_ticks(ticks)  # application-owned, read-only processing
           finally:
               queue.task_done()

   consumer = asyncio.create_task(consume())
   # On shutdown: remove the handler, cancel/await consumer, then close client.

Awaiting ``asyncio.Queue.put()`` directly in a callback applies backpressure to
all receive processing; whether that is appropriate is an application decision.
The library does not persist, replay, deduplicate, or acknowledge user events.

Observe callback failures
-------------------------

The transport logs callback exceptions and invokes registered error observers.
The observer receives the exception and the failing callback (often the
internal wrapper).

.. code-block:: python

   def callback_failed(exc, callback):
       print(f"callback {getattr(callback, '__name__', callback)!r} failed: {type(exc).__name__}")

   client.on_callback_error(callback_failed)

An error observer should not raise. Parsing failures are logged, but a parse
error or callback failure does not prove that the server stopped sending or
that account state is unchanged. Avoid putting credentials or other sensitive
payload data in callback logs.

Account, transaction, and result events
---------------------------------------

.. code-block:: python

   client.on_account_update(
       lambda data: print(data.get("balance"), data.get("equity"))
   )

   def show_transaction(data):
       if data.get("update_type") == 2:
           print("balance frame", data.get("balance_info"))
       else:
           print("order transaction", data.get("transaction_type"))

   client.on_trade_transaction(show_transaction)
   client.on_trade_result(
       lambda data: print(data.get("result", {}).get("retcode"))
   )

These callbacks are observations, not a durable transaction journal. Cmd=10 is
listened to by the build 6320 Web Terminal, but a real trade push has not been
verified. A cmd=19 result or a retcode alone does not prove final execution;
read positions, orders, and deals before making a trading decision, and never
blindly retry a timed-out or cancelled trade. See :doc:`trading`.

Symbol details and raw status frames
------------------------------------

On observed build 6320, cmd=17 contains fixed-size records without a count.
Prices are raw wire values; the website scales them by symbol digits capped at
eight. Use the field mask to determine which values are present: an unmarked
zero is not a current quote. Other builds use the legacy parser layout and are
not asserted to match build 6320.

.. code-block:: python

   def show_details(rows):
       for row in rows:
           print(row.get("symbol", row["symbol_id"]), row.get("bid"))

   client.on_symbol_details(show_details)
   client.on_symbol_update(lambda result: print(f"symbol frame cmd={result.command}"))
   client.on_login_status(lambda result: print(f"login frame code={result.code}"))

Raw ``CommandResult`` exposes ``command``, ``code``, and ``body``. Applications
using raw frames own the additional validation and version-specific decoding.
For book callback payloads and managed subscriptions, see :doc:`market_data`.
