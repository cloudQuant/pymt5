API 参考
========

客户端
------

.. autoclass:: pymt5.MT5WebClient
   :members:
   :undoc-members:
   :show-inheritance:

传输
----

.. autoclass:: pymt5.TransportState
   :members:
   :undoc-members:
   :show-inheritance:

事件类
------

用于推送通知和健康监控的类型化事件数据类。这些冻结的数据类为现有推送处理程序回调交付的原始
``dict`` 记录提供了类型化替代方案。

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

异常类
------

所有 pymt5 异常均继承自 :class:`PyMT5Error`。每个异常还继承自一个标准库异常，以便向后兼容
现有的错误处理模式。

异常层级
~~~~~~~~

::

    Exception
    +-- PyMT5Error                  （所有 pymt5 错误的基类）
    |   +-- MT5ConnectionError      （同时继承 ConnectionError）
    |   +-- AuthenticationError
    |   +-- TradeError              （同时继承 ValueError）
    |   +-- ProtocolError           （同时继承 ValueError）
    |   +-- SymbolNotFoundError     （同时继承 KeyError）
    |   +-- ValidationError         （同时继承 ValueError）
    |   +-- SessionError            （同时继承 RuntimeError）
    |   +-- MT5TimeoutError         （同时继承 TimeoutError）

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

数据类
------

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

订阅
----

.. autoclass:: pymt5.SubscriptionHandle
   :members:
   :undoc-members:

指标
----

.. autoclass:: pymt5.MetricsCollector
   :members:
   :undoc-members:

DataFrame 集成
--------------

.. autofunction:: pymt5.to_dataframe

配置参考
--------

:class:`~pymt5.MT5WebClient` 构造函数接受以下参数：

.. list-table::
   :header-rows: 1
   :widths: 25 15 15 45

   * - 参数
     - 类型
     - 默认值
     - 说明
   * - ``uri``
     - ``str``
     - ``"wss://web.metatrader.app/terminal"``
     - WebSocket 服务器 URI。
   * - ``timeout``
     - ``float``
     - ``30.0``
     - 命令和初始连接的超时时间，单位为秒。
   * - ``heartbeat_interval``
     - ``float``
     - ``30.0``
     - 心跳 ping 之间的间隔，单位为秒。
   * - ``tick_history_limit``
     - ``int``
     - ``10000``
     - 每个交易品种的历史 deque 中保留的最大报价数量。设为 ``0`` 表示无限制。
   * - ``auto_reconnect``
     - ``bool``
     - ``False``
     - 在意外断开连接时启用自动重连。
   * - ``max_reconnect_attempts``
     - ``int``
     - ``5``
     - 放弃前的最大重连尝试次数。
   * - ``reconnect_delay``
     - ``float``
     - ``3.0``
     - 重连尝试之间指数退避的基础延迟，单位为秒。
   * - ``max_reconnect_delay``
     - ``float``
     - ``60.0``
     - 重连退避的最大延迟上限，单位为秒。
   * - ``rate_limit``
     - ``float``
     - ``0``
     - 令牌桶速率限制（每秒命令数）。 ``0`` 表示禁用速率限制。
   * - ``rate_burst``
     - ``int``
     - ``20``
     - 令牌桶速率限制器的最大突发容量。
   * - ``metrics``
     - ``MetricsCollector | None``
     - ``None``
     - 用于可观察性挂钩的可选指标收集器。
   * - ``symbol_cache_ttl``
     - ``float``
     - ``0``
     - 交易品种缓存的生存时间，单位为秒。 ``0`` 表示不自动刷新。

错误处理指南
------------

所有 pymt5 异常均继承自 :class:`~pymt5.PyMT5Error`，因此可以用一个处理程序捕获所有库错误：

.. code-block:: python

    from pymt5 import MT5WebClient, PyMT5Error

    async with MT5WebClient() as client:
        try:
            await client.login(12345, "password")
        except PyMT5Error as exc:
            print(f"pymt5 error: {exc}")

如需更细粒度的处理，请捕获特定的异常类型。双重继承设计允许使用 pymt5 异常或标准库父类：

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

**关键异常属性：**

- :class:`~pymt5.MT5ConnectionError` -- ``server_uri`` ：连接失败的 URI。
- :class:`~pymt5.TradeError` -- ``retcode`` ：MT5 返回代码； ``symbol`` ：涉及的交易品种；
  ``action`` ：失败的交易操作。

常量
----

交易操作
~~~~~~~~

.. autodata:: pymt5.TRADE_ACTION_DEAL
.. autodata:: pymt5.TRADE_ACTION_PENDING
.. autodata:: pymt5.TRADE_ACTION_SLTP
.. autodata:: pymt5.TRADE_ACTION_MODIFY
.. autodata:: pymt5.TRADE_ACTION_REMOVE
.. autodata:: pymt5.TRADE_ACTION_CLOSE_BY

订单类型
~~~~~~~~

.. autodata:: pymt5.ORDER_TYPE_BUY
.. autodata:: pymt5.ORDER_TYPE_SELL
.. autodata:: pymt5.ORDER_TYPE_BUY_LIMIT
.. autodata:: pymt5.ORDER_TYPE_SELL_LIMIT
.. autodata:: pymt5.ORDER_TYPE_BUY_STOP
.. autodata:: pymt5.ORDER_TYPE_SELL_STOP
.. autodata:: pymt5.ORDER_TYPE_BUY_STOP_LIMIT
.. autodata:: pymt5.ORDER_TYPE_SELL_STOP_LIMIT

订单成交方式
~~~~~~~~~~~~

.. autodata:: pymt5.ORDER_FILLING_FOK
.. autodata:: pymt5.ORDER_FILLING_IOC
.. autodata:: pymt5.ORDER_FILLING_RETURN

有效期模式
~~~~~~~~~~

.. autodata:: pymt5.ORDER_TIME_GTC
.. autodata:: pymt5.ORDER_TIME_DAY
.. autodata:: pymt5.ORDER_TIME_SPECIFIED
.. autodata:: pymt5.ORDER_TIME_SPECIFIED_DAY

时间框架周期
~~~~~~~~~~~~

.. autodata:: pymt5.PERIOD_M1
.. autodata:: pymt5.PERIOD_M5
.. autodata:: pymt5.PERIOD_M15
.. autodata:: pymt5.PERIOD_M30
.. autodata:: pymt5.PERIOD_H1
.. autodata:: pymt5.PERIOD_H4
.. autodata:: pymt5.PERIOD_D1
.. autodata:: pymt5.PERIOD_W1
.. autodata:: pymt5.PERIOD_MN1

返回代码
~~~~~~~~

.. autodata:: pymt5.TRADE_RETCODE_DONE
.. autodata:: pymt5.TRADE_RETCODE_DONE_PARTIAL
.. autodata:: pymt5.TRADE_RETCODE_PLACED

持仓类型
~~~~~~~~

.. autodata:: pymt5.POSITION_TYPE_BUY
.. autodata:: pymt5.POSITION_TYPE_SELL

成交类型
~~~~~~~~

.. autodata:: pymt5.DEAL_TYPE_BUY
.. autodata:: pymt5.DEAL_TYPE_SELL
.. autodata:: pymt5.DEAL_TYPE_BALANCE
.. autodata:: pymt5.DEAL_TYPE_CREDIT
.. autodata:: pymt5.DEAL_TYPE_CHARGE
.. autodata:: pymt5.DEAL_TYPE_CORRECTION
.. autodata:: pymt5.DEAL_TYPE_BONUS
.. autodata:: pymt5.DEAL_TYPE_COMMISSION

成交入场方式
~~~~~~~~~~~~

.. autodata:: pymt5.DEAL_ENTRY_IN
.. autodata:: pymt5.DEAL_ENTRY_OUT
.. autodata:: pymt5.DEAL_ENTRY_INOUT
.. autodata:: pymt5.DEAL_ENTRY_OUT_BY

命令 ID
~~~~~~~

.. autodata:: pymt5.CMD_GET_ACCOUNT
.. autodata:: pymt5.CMD_GET_SYMBOL_GROUPS
.. autodata:: pymt5.CMD_TRADE_UPDATE_PUSH
.. autodata:: pymt5.CMD_ACCOUNT_UPDATE_PUSH
.. autodata:: pymt5.CMD_SYMBOL_DETAILS_PUSH
.. autodata:: pymt5.CMD_TRADE_RESULT_PUSH
.. autodata:: pymt5.CMD_SUBSCRIBE_BOOK
.. autodata:: pymt5.CMD_BOOK_PUSH
.. autodata:: pymt5.CMD_GET_CORPORATE_LINKS
