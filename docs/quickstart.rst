Quick Start
===========

This guide makes a read-only connection. It does not place, modify, or cancel
an order. Use an authorized demo account until you have validated the exact
server build and every workflow your application needs.

1. Configure credentials
------------------------

Set environment variables in your shell or secret manager:

.. code-block:: bash

   export PYMT5_SERVER="wss://web.metatrader.app/terminal"
   export PYMT5_LOGIN="your-demo-login"
   export PYMT5_PASSWORD="your-demo-password"

Do not hard-code credentials, commit ``.env``, or paste tokens and session data
into an issue. The client does not implicitly load a ``.env`` file. Loading that
file is the application's responsibility.

2. Connect and load data
------------------------

.. code-block:: python

   import asyncio
   import os

   from pymt5 import MT5WebClient


   async def main() -> None:
       client = MT5WebClient(
           uri=os.environ["PYMT5_SERVER"],
           auto_reconnect=True,
       )
       async with client:
           await client.login(
               login=int(os.environ["PYMT5_LOGIN"]),
               password=os.environ["PYMT5_PASSWORD"],
           )

           # load_symbols() returns a name-keyed dict[str, SymbolInfo].
           symbols = await client.load_symbols()
           if not symbols:
               raise RuntimeError("The server returned no symbols")

           account = await client.get_account()
           print(f"Loaded {len(symbols)} symbols")
           print(f"Account currency: {account.get('currency', 'unknown')}")


   asyncio.run(main())

The async context manager closes the transport on both success and failure.
Calling ``login()`` again on an active session raises ``SessionError``; call
``logout()`` before switching accounts.

3. Retrieve historical bars
---------------------------

After loading symbols, request a bounded historical range:

.. code-block:: python

   from datetime import datetime, timedelta, timezone

   end = datetime.now(timezone.utc)
   start = end - timedelta(days=7)
   bars = await client.get_rates("EURUSD", 60, start, end)
   print(f"Received {len(bars)} H1 bars")

Replace ``EURUSD`` with a symbol returned by your server. Historical responses
are validated before parsing; an unknown or ambiguous layout raises
``ProtocolError`` rather than being guessed. See :doc:`market_data`.

4. Observe a tick stream
------------------------

Register the callback before subscribing. Choose a liquid symbol that is in an
active trading session:

.. code-block:: python

   import os

   symbol_name = os.environ.get("PYMT5_TEST_SYMBOL", "EURUSD")
   info = symbols.get(symbol_name)
   if info is None:
       raise ValueError(f"Symbol not available on this server: {symbol_name}")

   def on_ticks(ticks):
       for tick in ticks:
           print(tick["symbol"], tick.get("bid"), tick.get("ask"))

   client.on_tick(on_ticks)
   subscription = await client.subscribe_ticks_managed([info.symbol_id])
   async with subscription:
       await asyncio.sleep(30)

No tick during a closed market is not a protocol success or failure by itself.
The subscription handle attempts cleanup on exit; a server rejection is surfaced
and must not be described as a successful unsubscribe. Tick price scaling is
server/build-sensitive; review :doc:`configuration` before using price values.

5. Add explicit error handling
------------------------------

.. code-block:: python

   from pymt5 import (
       AuthenticationError,
       MT5ConnectionError,
       MT5TimeoutError,
       ProtocolError,
   )

   try:
       await client.ping()
   except MT5TimeoutError:
       # The previous connection is isolated after an uncertain sent request.
       # Do not reuse it or assume what the server processed.
       print("Ping timed out; wait for a fresh connection")
   except (MT5ConnectionError, ProtocolError) as exc:
       print(f"Transport or protocol failed: {exc}")

For login failures, catch ``AuthenticationError`` separately. For trading,
a timeout leaves server execution **unknown**: reconcile orders, deals, and
positions before any retry. See :doc:`reliability` and :doc:`security`.

Next steps
----------

- :doc:`examples` — runnable examples and their read-only/mutating scope
- :doc:`configuration` — compatibility modes and connection settings
- :doc:`market_data` — ticks, bars, symbol metadata, and order book
- :doc:`troubleshooting` — common connection and protocol failures
