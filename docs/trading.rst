Trading
=======

pymt5 provides both low-level and high-level trading interfaces.

High-Level Helpers
------------------

All high-level helpers auto-resolve ``digits`` from the symbol cache (call
``load_symbols()`` first) and convert lot volume to MT5 integer format.

Market Orders
~~~~~~~~~~~~~

.. code-block:: python

   # Market buy 0.01 lots of EURUSD with SL/TP
   result = await client.buy_market("EURUSD", 0.01, sl=1.0800, tp=1.1200)

   # Market sell 0.05 lots with custom deviation
   result = await client.sell_market("EURUSD", 0.05, deviation=30)

   # Check result
   if result.success:
       print(f"Order placed: deal={result.deal}, price={result.price}")
   else:
       print(f"Failed: {result.description}")

Pending Orders
~~~~~~~~~~~~~~

.. code-block:: python

   # Buy limit
   result = await client.buy_limit("EURUSD", 0.1, price=1.0800,
                                    sl=1.0750, tp=1.0900)

   # Sell stop
   result = await client.sell_stop("GBPUSD", 0.1, price=1.2500)

   # Buy stop-limit (trigger at price, then place limit at stop_limit_price)
   result = await client.buy_stop_limit("EURUSD", 0.1, price=1.1000,
                                         stop_limit_price=1.0950,
                                         sl=1.0900, tp=1.1100)

Position Management
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Close position (auto-detects BUY/SELL direction)
   result = await client.close_position("EURUSD", position_id=123456,
                                         volume=0.1)

   # Close by opposite position (hedge netting)
   result = await client.close_position_by("EURUSD", position_id=123456,
                                            position_by=789012)

   # Modify SL/TP
   result = await client.modify_position_sltp("EURUSD", position_id=123456,
                                               sl=1.0850, tp=1.0950)

   # Cancel pending order
   result = await client.cancel_pending_order(order=789012)

Low-Level Trade Request
-----------------------

For full control over all trade fields, use ``trade_request()`` directly:

.. code-block:: python

   from pymt5 import (TRADE_ACTION_DEAL, ORDER_TYPE_BUY,
                       ORDER_FILLING_IOC)

   result = await client.trade_request(
       trade_action=TRADE_ACTION_DEAL,
       symbol="EURUSD",
       volume=client._volume_to_lots(0.01),
       digits=5,
       trade_type=ORDER_TYPE_BUY,
       type_filling=ORDER_FILLING_IOC,
       deviation=20,
       comment="my trade",
   )

TradeResult
-----------

All trade methods return a :class:`~pymt5.TradeResult` dataclass:

- ``retcode`` — MT5 return code (e.g. 10009 = done)
- ``description`` — human-readable description
- ``success`` — ``True`` if retcode indicates success
- ``deal`` — deal ticket number
- ``order`` — order ticket number
- ``volume`` — executed volume (MT5 integer format)
- ``price`` — execution price
- ``bid`` / ``ask`` — market prices at execution
- ``comment`` — server comment
- ``request_id`` — request identifier
