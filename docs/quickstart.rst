Quick Start
===========

Basic Connection
----------------

.. code-block:: python

   import asyncio
   from pymt5 import MT5WebClient


   async def main():
       async with MT5WebClient(auto_reconnect=True) as client:
           await client.login(login=12345678, password="your-password")

           # Load symbol cache
           await client.load_symbols()
           print(f"Loaded {len(client.symbol_names)} symbols")

           # Full account info
           acct = await client.get_account()
           print(f"Balance: {acct['balance']}, Currency: {acct['currency']}")

           # Symbol groups
           groups = await client.get_symbol_groups()
           print(f"Groups: {groups}")

           await asyncio.sleep(5)


   asyncio.run(main())

Tick Subscription
-----------------

.. code-block:: python

   async with MT5WebClient() as client:
       await client.login(login=12345678, password="your-password")
       await client.load_symbols()

       def on_ticks(ticks):
           for t in ticks:
               print(f"TICK {t.get('symbol', t['symbol_id'])}: "
                     f"bid={t['bid']} ask={t['ask']}")

       client.on_tick(on_ticks)
       await client.subscribe_symbols(["EURUSD", "GBPUSD"])
       await asyncio.sleep(30)

Logging
-------

Enable debug logging to see protocol details:

.. code-block:: python

   import logging
   logging.basicConfig(level=logging.DEBUG)
   # Loggers: pymt5.client, pymt5.transport
