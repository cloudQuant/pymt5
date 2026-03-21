API Reference
=============

Client
------

.. autoclass:: pymt5.MT5WebClient
   :members:
   :undoc-members:
   :show-inheritance:

Transport
---------

.. autoclass:: pymt5.TransportState
   :members:
   :undoc-members:
   :show-inheritance:

Event Classes
-------------

Typed event dataclasses for push notifications and health monitoring. These
frozen dataclasses provide a typed alternative to the raw ``dict`` records
delivered by the existing push handler callbacks.

.. autoclass:: pymt5.TickEvent
   :members:
   :undoc-members:

.. autoclass:: pymt5.BookEvent
   :members:
   :undoc-members:

.. autoclass:: pymt5.TradeResultEvent
   :members:
   :undoc-members:

.. autoclass:: pymt5.AccountEvent
   :members:
   :undoc-members:

.. autoclass:: pymt5.HealthStatus
   :members:
   :undoc-members:

Exception Classes
-----------------

All pymt5 exceptions inherit from :class:`PyMT5Error`. Each exception also
inherits from a standard library exception for backward compatibility with
existing error-handling patterns.

Exception Hierarchy
~~~~~~~~~~~~~~~~~~~

::

    Exception
    +-- PyMT5Error                  (base for all pymt5 errors)
    |   +-- MT5ConnectionError      (also inherits ConnectionError)
    |   +-- AuthenticationError
    |   +-- TradeError              (also inherits ValueError)
    |   +-- ProtocolError           (also inherits ValueError)
    |   +-- SymbolNotFoundError     (also inherits KeyError)
    |   +-- ValidationError         (also inherits ValueError)
    |   +-- SessionError            (also inherits RuntimeError)
    |   +-- MT5TimeoutError         (also inherits TimeoutError)

.. autoclass:: pymt5.PyMT5Error
   :members:
   :show-inheritance:

.. autoclass:: pymt5.MT5ConnectionError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.AuthenticationError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.TradeError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.ProtocolError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.SymbolNotFoundError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.ValidationError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.SessionError
   :members:
   :show-inheritance:

.. autoclass:: pymt5.MT5TimeoutError
   :members:
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

.. autoclass:: pymt5.VerificationStatus
   :members:
   :undoc-members:

.. autoclass:: pymt5.OpenAccountResult
   :members:
   :undoc-members:

.. autoclass:: pymt5.AccountOpeningRequest
   :members:
   :undoc-members:

.. autoclass:: pymt5.DemoAccountRequest
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: pymt5.RealAccountRequest
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: pymt5.AccountDocument
   :members:
   :undoc-members:

Subscriptions
-------------

.. autoclass:: pymt5.SubscriptionHandle
   :members:
   :undoc-members:

Metrics
-------

.. autoclass:: pymt5.MetricsCollector
   :members:
   :undoc-members:

DataFrame Integration
---------------------

.. autofunction:: pymt5.to_dataframe

Configuration Reference
-----------------------

The :class:`~pymt5.MT5WebClient` constructor accepts the following parameters:

.. list-table::
   :header-rows: 1
   :widths: 25 15 15 45

   * - Parameter
     - Type
     - Default
     - Description
   * - ``uri``
     - ``str``
     - ``"wss://web.metatrader.app/terminal"``
     - WebSocket server URI.
   * - ``timeout``
     - ``float``
     - ``30.0``
     - Timeout in seconds for commands and the initial connection.
   * - ``heartbeat_interval``
     - ``float``
     - ``30.0``
     - Interval in seconds between heartbeat pings.
   * - ``tick_history_limit``
     - ``int``
     - ``10000``
     - Maximum number of ticks retained in the per-symbol history deque.
       Set to ``0`` for unlimited.
   * - ``auto_reconnect``
     - ``bool``
     - ``False``
     - Enable automatic reconnection on unexpected disconnect.
   * - ``max_reconnect_attempts``
     - ``int``
     - ``5``
     - Maximum number of reconnection attempts before giving up.
   * - ``reconnect_delay``
     - ``float``
     - ``3.0``
     - Base delay in seconds for exponential backoff between reconnect attempts.
   * - ``max_reconnect_delay``
     - ``float``
     - ``60.0``
     - Maximum delay cap in seconds for the reconnect backoff.
   * - ``rate_limit``
     - ``float``
     - ``0``
     - Token bucket rate limit (commands per second). ``0`` disables rate
       limiting.
   * - ``rate_burst``
     - ``int``
     - ``20``
     - Maximum burst size for the token bucket rate limiter.
   * - ``metrics``
     - ``MetricsCollector | None``
     - ``None``
     - Optional metrics collector for observability hooks.
   * - ``symbol_cache_ttl``
     - ``float``
     - ``0``
     - Time-to-live in seconds for the symbol cache. ``0`` means no automatic
       refresh.

Error Handling Guide
--------------------

All pymt5 exceptions inherit from :class:`~pymt5.PyMT5Error`, so you can catch
all library errors with a single handler:

.. code-block:: python

    from pymt5 import MT5WebClient, PyMT5Error

    async with MT5WebClient() as client:
        try:
            await client.login(12345, "password")
        except PyMT5Error as exc:
            print(f"pymt5 error: {exc}")

For more granular handling, catch specific exception types. The dual-inheritance
design allows you to use either the pymt5 exception or the standard library
parent:

.. code-block:: python

    from pymt5 import (
        MT5WebClient,
        MT5ConnectionError,
        AuthenticationError,
        TradeError,
        MT5TimeoutError,
        SessionError,
    )

    async with MT5WebClient(auto_reconnect=True) as client:
        try:
            await client.login(12345, "password")
        except MT5ConnectionError as exc:
            # Also catchable as ConnectionError
            print(f"Connection failed to {exc.server_uri}: {exc}")
        except AuthenticationError:
            print("Invalid credentials")

        try:
            result = await client.buy_market("EURUSD", 0.1)
        except TradeError as exc:
            # Also catchable as ValueError
            print(f"Trade failed: retcode={exc.retcode}, symbol={exc.symbol}")
        except MT5TimeoutError:
            # Also catchable as TimeoutError
            print("Trade request timed out")
        except SessionError:
            # Also catchable as RuntimeError
            print("Not connected or session invalid")

**Key exception attributes:**

- :class:`~pymt5.MT5ConnectionError` -- ``server_uri``: the URI that failed.
- :class:`~pymt5.TradeError` -- ``retcode``: MT5 return code; ``symbol``:
  involved symbol; ``action``: trade action that failed.

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
