Trading
=======

.. danger::

   **Trading can cause financial loss. Use only an authorized demo account while
   developing and testing.** The examples that place or change orders are
   opt-in; do not run them with live credentials. pymt5 uses an unofficial,
   reverse-engineered protocol, and its observed trade layouts are not
   broker-independent verification. Server/build behavior, execution modes,
   filling rules, and required fields can differ. A returned acknowledgement
   is not proof of a fill.

Start with read-only account state
----------------------------------

Query positions, pending orders, and history before considering any trade
operation. These calls do not place trades.

.. code-block:: python

   import asyncio
   import os
   from datetime import datetime, timedelta, timezone

   from pymt5 import MT5WebClient, PyMT5Error

   async def main() -> None:
       end = datetime.now(timezone.utc)
       start = end - timedelta(days=7)
       try:
           async with MT5WebClient(uri=os.environ["PYMT5_SERVER"]) as client:
               await client.login(
                   int(os.environ["PYMT5_LOGIN"]),
                   os.environ["PYMT5_PASSWORD"],
               )
               positions = await client.positions_get(symbol="EURUSD")
               orders = await client.orders_get(symbol="EURUSD")
               deals = await client.history_deals_get(start, end)
               print(f"positions={len(positions)} orders={len(orders)} deals={len(deals)}")
       except PyMT5Error as exc:
           print(f"read-only query failed: {exc}")

   asyncio.run(main())

``get_positions_and_orders()`` returns ``{"positions": ..., "orders": ...}``.
``get_positions()`` and ``get_orders()`` select one part. ``positions_get()``
and ``orders_get()`` support optional ``symbol``, ``group``, or ``ticket``
filters. History is available through ``get_trade_history(from_ts, to_ts)``,
``get_deals()``, ``history_orders_get()``, and ``history_deals_get()`` with
optional group/ticket/position filters (``history_deals_get(ticket=...)``
matches the deal ticket field ``deal``). The count helpers query the same data.
Volumes and prices passed to trade helpers are validated as finite values
within ``uint64`` wire bounds before sending.

Run local pre-flight checks
---------------------------

``order_check(request)`` performs local compatibility-layer validation and
margin estimation. It reads symbol/account data but does not submit the trade
and is not a server-side validation RPC. A zero retcode does not establish that
the server will accept or fill an order; however, if ``order_calc_margin()``
cannot compute margin, ``order_check()`` preserves ``last_error()`` and returns
a nonzero error retcode instead of reporting success. It also preserves explicit
zero ``equity``/``margin_free`` balances and validates Stop-Limit triggers
against independent market quotes while checking SL/TP against the order price.
``order_calc_profit()`` and ``order_calc_margin()`` likewise use best-effort
local formulas and currency conversion; they can return ``None`` and set
``last_error()``.

.. code-block:: python

   from pymt5 import ORDER_FILLING_FOK, ORDER_TYPE_BUY, TRADE_ACTION_DEAL

   candidate = {
       "action": TRADE_ACTION_DEAL,
       "symbol": "EURUSD",
       "volume": 0.01,
       "type": ORDER_TYPE_BUY,
       "price": 1.1000,
       "type_filling": ORDER_FILLING_FOK,
       "deviation": 20,
   }
   check = await client.order_check(candidate)
   print(check["retcode"], check["comment"], check["margin_free"])
   # Do not infer server acceptance or execution from this local result.

Place a demo order only after explicit opt-in
---------------------------------------------

The following snippet sends a real order to whichever account is logged in.
The environment flag is only a guard against accidental execution; it does not
prove that the account is a demo account.

.. code-block:: python

   import os

   if os.environ.get("PYMT5_ENABLE_DEMO_ORDERS") != "I_ACCEPT_THE_RISK":
       raise RuntimeError("demo order example is disabled")

   await client.load_symbols()
   result = await client.buy_market(
       "EURUSD",
       0.01,
       sl=1.0800,
       tp=1.1200,
       deviation=20,
       comment="authorized demo test",
   )
   print(result)
   # Reconcile later positions/orders/deals; do not equate this return with a fill.

High-level order-placement helpers resolve digits from the basic symbol cache,
defaulting to five digits when no cache entry or explicit ``digits`` is available. Load and
verify the symbol metadata first. Market helpers default to FOK and leave the
order price at zero for server-side current-price handling. Confirm supported
filling, stop distances, volume limits, and execution behavior on the target
demo server.

Pending-order helpers are ``buy_limit()``, ``sell_limit()``, ``buy_stop()``,
``sell_stop()``, ``buy_stop_limit()``, and ``sell_stop_limit()``. For stop-limit
orders, ``price`` is the stop trigger and ``stop_limit_price`` is the limit
price used after triggering.

.. code-block:: python

   # This also sends an order; use only after the demo opt-in above.
   pending = await client.buy_stop_limit(
       "EURUSD", 0.01, price=1.1000, stop_limit_price=1.0950,
       sl=1.0900, tp=1.1100,
   )

Interpret ``TradeResult`` conservatively
----------------------------------------

All trade submission helpers return :class:`~pymt5.TradeResult`. Important
fields are ``retcode``, ``description``, ``success``, ``deal``, ``order``,
``volume``, ``price``, ``bid``, ``ask``, ``comment``, and ``request_id``.
``success`` is true for retcode 0, placed, done, or partial-done. In particular,
placed and partial-done are not proof of a final complete fill. Inspect
subsequent trade results and authoritative account state.

Current high-level helpers and ``order_send()`` encode lot volume as
``round(volume * 10**8)`` by default: 0.01 lots becomes ``1_000_000``. The
low-level ``trade_request()`` expects this integer wire volume, not lots, and
``TradeResult.volume`` is also the wire integer. Do not reuse older examples
that assume a different scale without validating the target protocol.

Use ``order_send(request)`` for an official-style dictionary interface or
``trade_request(...)`` for explicit wire fields. These interfaces preserve the
caller-supplied action; they do not make an unsafe request safe.

Select a trade wire profile deliberately
----------------------------------------

``MT5WebClient(trade_protocol="legacy")`` is the default. The observed
``"build_6090"`` profile inserts reserved bytes and makes ``buy_market()``,
``sell_market()``, and ``close_position()`` use ``TRADE_ACTION_MARKET_DEAL``.
The client does not select this profile automatically from the handshake build.
Observations on selected demo symbols do not establish compatibility with
other brokers, builds, or execution modes.

``action_id`` and ``type_flags`` default to zero on the market helpers that
accept them. Their server requirements are not established generally, and a
request/response identifier must not be treated as a reliable execution
correlation mechanism. See :doc:`reliability`.

Modify or close existing demo positions
---------------------------------------

These operations change account state and require the same explicit demo
controls as order placement.

.. code-block:: python

   # Examples only: tickets and prices must come from the current demo account.
   changed = await client.modify_position_sltp(
       "EURUSD", position_id=123456, sl=1.0850, tp=1.0950
   )
   closed = await client.close_position("EURUSD", position_id=123456, volume=0.01)
   cancelled = await client.cancel_pending_order(order=789012)

``close_position()`` validates ``symbol`` and ``volume`` up front and requires
a positive position ID. If ``order_type`` is omitted, it queries open
positions and chooses the opposite side. It raises ``ValidationError`` before
sending a trade if inputs are invalid, the query fails, the position is absent,
its direction is unknown, or its symbol is missing or different from the
requested symbol. Pass an explicit ``order_type`` only when the intended
closing side is already known.

``close_position_by(symbol, position_id, position_by)`` requests close-by for
opposite positions on the same symbol where the server supports it.
``modify_position_sltp()``, ``modify_pending_order()``, and
``close_position_by()`` also validate ``symbol`` (and finite positive prices
for pending-order modifications) before sending. ``cancel_pending_order()``
removes a pending order. None of these methods guarantees the resulting account
state without a subsequent query.

Never blindly retry an uncertain trade
--------------------------------------

A timeout, cancellation, disconnect, or send failure after transmission may
have begun leaves server execution **unknown**. The transport isolates a
connection with an uncertain sent outcome so a late response cannot be assigned
to a later request on that socket. Automatic reconnection does not retry the
interrupted trade.

Do not catch ``MT5TimeoutError`` or ``asyncio.CancelledError`` and immediately
submit the same order again. After establishing a fresh session, perform a
read-only reconciliation and compare the intended request with current and
historical state:

.. code-block:: python

   async def read_reconciliation_state(client):
       return {
           "positions": await client.get_positions(),
           "orders": await client.get_orders(),
           "history": await client.get_trade_history(),
       }

This snapshot does not automatically prove that a particular request executed;
tickets, comments, volume, price, and timing can require application-specific
review. If the outcome cannot be established, stop automated retries and use
the broker's authorized support/management channels. Local validation errors
and returned trade retcodes are different from transport uncertainty.

For connection isolation, same-command serialization, reconnect behavior, and
subscription cleanup, see :doc:`reliability`. For asynchronous result and
account callbacks, including their verification limits, see
:doc:`push_notifications`.
