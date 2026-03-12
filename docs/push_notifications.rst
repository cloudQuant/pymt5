Push Notifications
==================

pymt5 supports 9 types of real-time push notifications from the MT5 server.
Register callbacks before subscribing to receive updates.

Tick Updates (cmd=8)
--------------------

.. code-block:: python

   def on_ticks(ticks):
       for t in ticks:
           print(f"{t.get('symbol', t['symbol_id'])}: "
                 f"bid={t['bid']} ask={t['ask']}")

   client.on_tick(on_ticks)
   await client.subscribe_symbols(["EURUSD", "GBPUSD"])

Position & Order Updates (cmd=4)
--------------------------------

.. code-block:: python

   # Combined position+order updates
   def on_trade_change(data):
       print(f"Positions: {len(data['positions'])}")
       print(f"Orders: {len(data['orders'])}")

   client.on_trade_update(on_trade_change)

   # Or register separately
   client.on_position_update(lambda positions: print(f"Positions: {len(positions)}"))
   client.on_order_update(lambda orders: print(f"Orders: {len(orders)}"))

Trade Transactions (cmd=10)
---------------------------

Order add/update/delete and balance update notifications:

.. code-block:: python

   def on_transaction(data):
       if data.get('update_type') == 2:
           print(f"Balance update: {data['balance_info']}")
       else:
           print(f"Order transaction: type={data.get('transaction_type')}")

   client.on_trade_transaction(on_transaction)

Account Updates (cmd=14)
------------------------

Real-time balance/equity/margin changes:

.. code-block:: python

   client.on_account_update(
       lambda d: print(f"Balance: {d.get('balance')}, Equity: {d.get('equity')}")
   )

Symbol Details (cmd=17)
-----------------------

Extended quote data including options greeks:

.. code-block:: python

   client.on_symbol_details(
       lambda d: print(f"Delta: {d[0].get('delta')}, Theta: {d[0].get('theta')}")
   )

Trade Results (cmd=19)
----------------------

Async trade execution results:

.. code-block:: python

   client.on_trade_result(
       lambda d: print(f"Retcode: {d.get('result', {}).get('retcode')}")
   )

Order Book (cmd=23)
-------------------

Depth-of-market updates:

.. code-block:: python

   client.on_book_update(
       lambda entries: print(f"Book: {len(entries)} symbols")
   )
   await client.subscribe_book_by_name(["EURUSD"])

Symbol Updates (cmd=13)
-----------------------

.. code-block:: python

   client.on_symbol_update(lambda result: print(f"Symbol changed: cmd={result.command}"))

Login Status (cmd=15)
---------------------

.. code-block:: python

   client.on_login_status(lambda r: print(f"Login status: code={r.code}"))
