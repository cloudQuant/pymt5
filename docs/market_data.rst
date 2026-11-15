Market Data
===========

This page shows read-only workflows for symbol discovery, historical bars,
streaming ticks, cached tick history, and depth of market.

.. warning::

   pymt5 uses an unofficial, reverse-engineered MT5 Web Terminal protocol.
   Field layouts and server behavior can differ by server and build. Test with
   an authorized demo account and independently validate decoded values before
   relying on them. None of the examples in this page places a trade.

Connect and load the symbol cache
---------------------------------

Name-based helpers need the basic symbol cache. ``async with`` closes the
session on exit; login and protocol failures should still be handled by the
application.

.. code-block:: python

   import asyncio
   import os

   from pymt5 import MT5WebClient, PyMT5Error

   async def main() -> None:
       client = MT5WebClient(uri=os.environ["PYMT5_SERVER"])
       try:
           async with client:
               await client.login(
                   int(os.environ["PYMT5_LOGIN"]),
                   os.environ["PYMT5_PASSWORD"],
               )
               symbols = await client.load_symbols()
               info = client.get_symbol_info("EURUSD")
               if info is None:
                   raise RuntimeError("EURUSD is not available on this server")
               print(info.symbol_id, info.digits, len(symbols))
       except PyMT5Error as exc:
           print(f"market-data session failed: {exc}")

   asyncio.run(main())

``load_symbols()`` returns a copy of the name-keyed cache. Use
``get_symbol_id()``, ``get_symbol_info()``, or ``symbol_names`` for local
lookups. ``subscribe_symbols()`` and ``subscribe_book_by_name()`` require this
cache and raise ``SymbolNotFoundError`` for missing names.

With a positive ``symbol_cache_ttl``, ``load_symbols()`` can reuse a fresh basic
cache. Call ``invalidate_symbol_cache()`` to force the next load to refresh it.
``symbol_info(name)`` is the detailed, official-style interface; it normally
uses full-symbol data and caches returned records. ``get_full_symbol_info(name)``
explicitly queries cmd=18 and returns ``None`` only when the symbol is absent.
A rejected, malformed, or mismatched full-symbol response raises
``ProtocolError`` instead of being treated as a missing symbol.

.. code-block:: python

   details = await client.symbol_info("EURUSD")
   if details is not None:
       print(details.get("contract_size"), details.get("volume_min"))

   groups = await client.get_symbol_groups()
   spreads = await client.get_spreads([info.symbol_id])

Retrieve historical bars
------------------------

``get_rates()`` takes a period in minutes and a request range in Unix seconds. Use timezone-aware datetimes when constructing timestamps.

.. code-block:: python

   from datetime import datetime, timedelta, timezone

   end = datetime.now(timezone.utc)
   start = end - timedelta(hours=6)
   bars = await client.get_rates(
       "EURUSD",
       60,
       int(start.timestamp()),
       int(end.timestamp()),
       bar_format="standard",  # choose only after confirming the server layout
   )
   for bar in bars[-3:]:
       print(bar["time"], bar["open"], bar["high"], bar["low"], bar["close"])

The standard record is 48 bytes. The extended record is 56 bytes and adds
``real_volume``. The default ``bar_format="auto"`` accepts a response only when
its length uniquely matches one layout:

* A nonempty length divisible by both 48 and 56, including nonzero multiples
  of 336 bytes, is ambiguous and raises ``ProtocolError``.
* A length divisible by neither layout also raises ``ProtocolError``.
* ``get_rates()`` and ``get_rates_raw()`` raise ``ProtocolError`` for a nonzero
  server response code.
* ``get_rates_raw()`` exposes bytes for diagnosis, but length alone cannot
  resolve an ambiguous layout. Length checks also cannot detect every
  truncation or an incorrect explicit format that happens to fit.

Set ``MT5WebClient(rate_bar_format="standard")`` or ``"extended"`` for a
client-wide default, or pass ``bar_format`` per call, only after validating the
authorized server/build. Do not silently switch formats after an error.

The request timestamps and returned ``time`` use signed 32-bit Unix seconds;
dates after 2038-01-19 03:14:07 UTC cannot be represented by this wire layout.

The official-style wrappers are local conveniences over the same command:

* ``copy_rates_range()`` converts its timeframe and datetime-like bounds.
* ``copy_rates_from()`` requests a lookback window and returns at most the last
  ``count`` bars ending at ``date_from``.
* ``copy_rates_from_pos()`` approximates current-bar-relative positioning from
  the returned history and local current time. It is not a server-side
  positional history API.

Stream ticks and clean up explicitly
------------------------------------

Register callbacks before subscribing. Tick subscriptions use the union of the
last confirmed local set and the requested IDs; the server is sent the full
desired set.

.. code-block:: python

   import asyncio
   from pymt5 import ProtocolError

   def print_ticks(ticks):
       for tick in ticks:
           print(tick.get("symbol", tick["symbol_id"]), tick["bid"], tick["ask"])

   client.on_tick(print_ticks)
   ids = await client.subscribe_symbols(["EURUSD"])
   try:
       await asyncio.sleep(30)
   finally:
       try:
           await client.unsubscribe_ticks(ids)
       except ProtocolError as exc:
           # Cleanup was rejected: do not report an unsubscribe as successful.
           print(f"tick unsubscribe was not confirmed: {exc}")

Tick subscribe and unsubscribe raise ``ProtocolError`` on a nonzero server
response and retain the last confirmed local set. In an observed build 6320
demo session, the server rejected a request to remove all tick subscriptions.
Applications must handle that case. After a timeout or cancellation, even the
last confirmed local set is not proof of current server state; see
:doc:`reliability`.

A managed handle automates the same cleanup but does not hide cleanup errors:

.. code-block:: python

   subscription = await client.subscribe_ticks_managed(ids)
   async with subscription:
       await asyncio.sleep(30)
   # Reaching this line means handle.unsubscribe() returned without an error.

``symbol_info_tick(name)`` returns a copy of the latest cached tick, or ``None``
if no stream has been observed. ``symbol_select(name, True)`` and
``symbol_select(name, False)`` are convenience wrappers around tick
subscription; they are not proof that a broker-independent Market Watch state
changed.

Choose tick price scaling explicitly
------------------------------------

Ticks can contain partial updates. Zero-valued bid, ask, or last fields are
filled from the previous cached values when available. Price normalization is
controlled by ``tick_price_mode``:

``"scaled"``
   Divide every nonzero price by ``10 ** symbol.digits``.
``"normalized"``
   Leave wire prices unchanged.
``"auto"`` (default)
   Apply the legacy symbol/magnitude heuristic.

.. code-block:: python

   client = MT5WebClient(
       tick_price_mode="normalized",
       tick_price_modes={"EURUSD": "scaled", "XAUUSD": "normalized"},
   )

Per-symbol modes override the global mode and are matched case-insensitively.
The same configuration applies to raw callbacks, typed events, caches, and
reconnected sessions. **The auto heuristic has not been universally validated
across brokers, symbols, or builds.** Integer-like prices and instruments such
as XAUUSD can be ambiguous. Inspect authorized wire samples, review the
heuristic warning logged at most once per symbol, and configure explicit modes
rather than treating ``auto`` as a protocol guarantee.

Use cached tick history
-----------------------

``copy_ticks_from()`` and ``copy_ticks_range()`` filter ticks already captured
from cmd=8 pushes. They do **not** retrieve a server-side historical tick
archive. History starts empty, can contain gaps during disconnects, and only
includes flags and fields represented by received pushes.

.. code-block:: python

   recent = await client.copy_ticks_from("EURUSD", start, count=100)
   print(client.tick_history_stats())
   client.clear_tick_history()  # or clear_tick_history(info.symbol_id)

``tick_history_limit`` bounds retained ticks per symbol; zero means unlimited.
``max_tick_symbols`` bounds the number of tracked symbols using least-recently
updated eviction; zero disables that cap. Choose limits appropriate for the
application's memory budget.

Read depth of market
--------------------

.. code-block:: python

   def print_book(entries):
       for entry in entries:
           print(entry.get("symbol", entry["symbol_id"]))
           print("bids", entry["bids"][:3], "asks", entry["asks"][:3])

   client.on_book_update(print_book)
   await client.subscribe_book_by_name(["EURUSD"])
   try:
       await asyncio.sleep(30)
       snapshot = client.market_book_get("EURUSD")
   finally:
       await client.market_book_release("EURUSD")

Book callbacks receive records with ``symbol_id``, optional ``symbol``,
``bids``, and ``asks``. Each level contains ``price`` and ``volume``.
``market_book_get()`` is only the latest local cache entry, not a freshness or
completeness guarantee. ``market_book_add()`` and ``market_book_release()`` are
subscription aliases. ``subscribe_book_managed()`` provides managed cleanup;
create it with ``await`` before entering ``async with`` just as for ticks.

For callback registration, typed events, backpressure, and handler removal, see
:doc:`push_notifications`. For timeouts, reconnect gaps, and uncertain command
outcomes, see :doc:`reliability`.
