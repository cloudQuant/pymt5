API Reference
=============

Client
------

.. autoclass:: pymt5.MT5WebClient
   :members:
   :undoc-members:
   :show-inheritance:

Data Classes
------------

.. autoclass:: pymt5.TradeResult
   :members:
   :undoc-members:

.. autoclass:: pymt5.AccountInfo
   :members:
   :undoc-members:

.. autoclass:: pymt5.SymbolInfo
   :members:
   :undoc-members:

Constants
---------

Trade Actions
~~~~~~~~~~~~~

.. autodata:: pymt5.TRADE_ACTION_DEAL
.. autodata:: pymt5.TRADE_ACTION_PENDING
.. autodata:: pymt5.TRADE_ACTION_SLTP
.. autodata:: pymt5.TRADE_ACTION_MODIFY
.. autodata:: pymt5.TRADE_ACTION_REMOVE
.. autodata:: pymt5.TRADE_ACTION_CLOSE_BY

Order Types
~~~~~~~~~~~

.. autodata:: pymt5.ORDER_TYPE_BUY
.. autodata:: pymt5.ORDER_TYPE_SELL
.. autodata:: pymt5.ORDER_TYPE_BUY_LIMIT
.. autodata:: pymt5.ORDER_TYPE_SELL_LIMIT
.. autodata:: pymt5.ORDER_TYPE_BUY_STOP
.. autodata:: pymt5.ORDER_TYPE_SELL_STOP
.. autodata:: pymt5.ORDER_TYPE_BUY_STOP_LIMIT
.. autodata:: pymt5.ORDER_TYPE_SELL_STOP_LIMIT

Order Filling
~~~~~~~~~~~~~

.. autodata:: pymt5.ORDER_FILLING_FOK
.. autodata:: pymt5.ORDER_FILLING_IOC
.. autodata:: pymt5.ORDER_FILLING_RETURN

Time Modes
~~~~~~~~~~

.. autodata:: pymt5.ORDER_TIME_GTC
.. autodata:: pymt5.ORDER_TIME_DAY
.. autodata:: pymt5.ORDER_TIME_SPECIFIED
.. autodata:: pymt5.ORDER_TIME_SPECIFIED_DAY

Timeframe Periods
~~~~~~~~~~~~~~~~~

.. autodata:: pymt5.PERIOD_M1
.. autodata:: pymt5.PERIOD_M5
.. autodata:: pymt5.PERIOD_M15
.. autodata:: pymt5.PERIOD_M30
.. autodata:: pymt5.PERIOD_H1
.. autodata:: pymt5.PERIOD_H4
.. autodata:: pymt5.PERIOD_D1
.. autodata:: pymt5.PERIOD_W1
.. autodata:: pymt5.PERIOD_MN1

Return Codes
~~~~~~~~~~~~

.. autodata:: pymt5.TRADE_RETCODE_DONE
.. autodata:: pymt5.TRADE_RETCODE_DONE_PARTIAL
.. autodata:: pymt5.TRADE_RETCODE_PLACED

Position Types
~~~~~~~~~~~~~~

.. autodata:: pymt5.POSITION_TYPE_BUY
.. autodata:: pymt5.POSITION_TYPE_SELL

Deal Types
~~~~~~~~~~

.. autodata:: pymt5.DEAL_TYPE_BUY
.. autodata:: pymt5.DEAL_TYPE_SELL
.. autodata:: pymt5.DEAL_TYPE_BALANCE
.. autodata:: pymt5.DEAL_TYPE_CREDIT
.. autodata:: pymt5.DEAL_TYPE_CHARGE
.. autodata:: pymt5.DEAL_TYPE_CORRECTION
.. autodata:: pymt5.DEAL_TYPE_BONUS
.. autodata:: pymt5.DEAL_TYPE_COMMISSION

Deal Entry
~~~~~~~~~~

.. autodata:: pymt5.DEAL_ENTRY_IN
.. autodata:: pymt5.DEAL_ENTRY_OUT
.. autodata:: pymt5.DEAL_ENTRY_INOUT
.. autodata:: pymt5.DEAL_ENTRY_OUT_BY

Command IDs
~~~~~~~~~~~

.. autodata:: pymt5.CMD_GET_ACCOUNT
.. autodata:: pymt5.CMD_GET_SYMBOL_GROUPS
.. autodata:: pymt5.CMD_TRADE_UPDATE_PUSH
.. autodata:: pymt5.CMD_ACCOUNT_UPDATE_PUSH
.. autodata:: pymt5.CMD_SYMBOL_DETAILS_PUSH
.. autodata:: pymt5.CMD_TRADE_RESULT_PUSH
.. autodata:: pymt5.CMD_SUBSCRIBE_BOOK
.. autodata:: pymt5.CMD_BOOK_PUSH
.. autodata:: pymt5.CMD_GET_CORPORATE_LINKS
