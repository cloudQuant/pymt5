交易
====

.. danger::

   **交易可能造成资金损失。开发和测试时只能使用经授权的模拟账户。** 下单或修改订单的示例采用显式启用方式；请勿使用真实账户凭据运行。pymt5 使用非官方、逆向分析得到的协议，观察到的交易布局并不构成跨券商验证。不同服务器/构建版本、执行模式、成交规则和必需字段可能不同。返回确认也不代表已经成交。

首先只读查询账户状态
--------------------

在考虑任何交易操作之前，先查询持仓、挂单和历史记录。以下调用不会下单。

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
               print(f"持仓={len(positions)} 挂单={len(orders)} 成交={len(deals)}")
       except PyMT5Error as exc:
           print(f"只读查询失败：{exc}")

   asyncio.run(main())

``get_positions_and_orders()`` 返回 ``{"positions": ..., "orders": ...}``。``get_positions()`` 和 ``get_orders()`` 分别选取其中一部分。``positions_get()`` 和 ``orders_get()`` 支持可选的 ``symbol``、``group`` 或 ``ticket`` 过滤条件。历史数据可通过 ``get_trade_history(from_ts, to_ts)``、``get_deals()``、``history_orders_get()`` 和 ``history_deals_get()`` 获取，并可按 group/ticket/position 过滤。计数辅助方法查询相同的数据。

执行本地预检
------------

``order_check(request)`` 执行本地兼容层验证和保证金估算。它会读取品种/账户数据，但不会提交交易，也不是服务器端验证 RPC。retcode 为零不能证明服务器会接受或成交订单。``order_calc_profit()`` 和 ``order_calc_margin()`` 同样使用尽力而为的本地公式和货币换算；它们可能返回 ``None`` 并设置 ``last_error()``。

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
   # 不要根据这个本地结果推断服务器已接受或执行订单。

仅在显式启用后提交模拟订单
--------------------------

以下片段会向当前登录账户实际发送订单。环境变量开关只能防止意外执行，不能证明登录的是模拟账户。

.. code-block:: python

   import os

   if os.environ.get("PYMT5_ENABLE_DEMO_ORDERS") != "I_ACCEPT_THE_RISK":
       raise RuntimeError("模拟订单示例处于禁用状态")

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
   # 稍后核对持仓/订单/成交；不要把此返回值等同于成交。

高级下单辅助方法从基础品种缓存解析 digits；如果没有缓存项或显式 ``digits``，则默认使用五位小数。请先加载并核验品种元数据。市价单辅助方法默认使用 FOK，并将订单价格保留为零，由服务器按当前价格处理。请在目标模拟服务器上确认所支持的成交方式、止损距离、交易量限制和执行行为。

挂单辅助方法包括 ``buy_limit()``、``sell_limit()``、``buy_stop()``、``sell_stop()``、``buy_stop_limit()`` 和 ``sell_stop_limit()``。对于 stop-limit 订单，``price`` 是 stop 触发价，``stop_limit_price`` 是触发后使用的 limit 价格。

.. code-block:: python

   # 此调用也会发送订单；只能在完成上述模拟交易启用步骤后使用。
   pending = await client.buy_stop_limit(
       "EURUSD", 0.01, price=1.1000, stop_limit_price=1.0950,
       sl=1.0900, tp=1.1100,
   )

谨慎解释 ``TradeResult``
------------------------

所有交易提交辅助方法都返回 :class:`~pymt5.TradeResult`。重要字段包括 ``retcode``、``description``、``success``、``deal``、``order``、``volume``、``price``、``bid``、``ask``、``comment`` 和 ``request_id``。retcode 为 0、已下单、已完成或部分完成时，``success`` 为真。尤其是“已下单”和“部分完成”不能证明最终已全部成交。请检查后续交易结果和权威账户状态。

当前高级辅助方法和 ``order_send()`` 默认按 ``round(volume * 10**8)`` 编码手数：0.01 手变为 ``1_000_000``。底层 ``trade_request()`` 需要这种整数线上交易量，而不是手数；``TradeResult.volume`` 同样是线上整数。如果旧示例采用其他比例，未经目标协议验证不得复用。

使用 ``order_send(request)`` 可以获得官方风格的字典接口，使用 ``trade_request(...)`` 可以显式指定线上字段。这些接口保留调用方提供的 action，不会把不安全的请求变安全。

谨慎选择交易线上配置
--------------------

``MT5WebClient(trade_protocol="legacy")`` 是默认配置。观察到的 ``"build_6090"`` 配置会插入保留字节，并使 ``buy_market()``、``sell_market()`` 和 ``close_position()`` 使用 ``TRADE_ACTION_MARKET_DEAL``。客户端不会根据握手构建号自动选择此配置。对特定模拟品种的观察不能证明与其他券商、构建版本或执行模式兼容。

支持这些参数的市价单辅助方法将 ``action_id`` 和 ``type_flags`` 默认设为零。服务器是否普遍要求这些字段尚未确定，而且不能把请求/响应标识符当作可靠的执行关联机制。请参阅 :doc:`reliability`。

修改或平掉现有模拟持仓
----------------------

这些操作会改变账户状态，需要与下单相同的显式模拟交易控制。

.. code-block:: python

   # 仅为示例：票号和价格必须来自当前模拟账户。
   changed = await client.modify_position_sltp(
       "EURUSD", position_id=123456, sl=1.0850, tp=1.0950
   )
   closed = await client.close_position("EURUSD", position_id=123456, volume=0.01)
   cancelled = await client.cancel_pending_order(order=789012)

``close_position()`` 要求持仓 ID 为正数。如果省略 ``order_type``，它会查询当前持仓并选择相反方向。如果查询失败、持仓不存在、方向未知，或者品种缺失或与请求品种不同，它会在发送交易之前抛出 ``ValidationError``。只有已经明确预期的平仓方向时，才应传入显式 ``order_type``。

在服务器支持的情况下，``close_position_by(symbol, position_id, position_by)`` 请求对同一品种的反向持仓执行 close-by。``modify_pending_order()`` 修改挂单；``cancel_pending_order()`` 删除挂单。未经后续查询，这些方法都不保证账户已经达到预期状态。

绝不盲目重试结果不确定的交易
----------------------------

在可能已经开始传输之后发生超时、取消、断连或发送失败，会使服务器端执行结果保持**未知**。传输层会隔离发送结果不确定的连接，避免迟到响应被分配给该 socket 上的后续请求。自动重连不会重试被中断的交易。

不要捕获 ``MT5TimeoutError`` 或 ``asyncio.CancelledError`` 后立即重新提交同一订单。建立新会话后，应执行只读核对，并将预期请求与当前状态及历史状态比较：

.. code-block:: python

   async def read_reconciliation_state(client):
       return {
           "positions": await client.get_positions(),
           "orders": await client.get_orders(),
           "history": await client.get_trade_history(),
       }

这份快照不会自动证明某个特定请求是否执行；票号、备注、交易量、价格和时间可能需要应用自行审查。如果无法确定结果，请停止自动重试，并通过券商授权的支持/管理渠道处理。本地验证错误和返回的交易 retcode 与传输不确定性不是同一类情况。

连接隔离、同命令串行化、重连行为和订阅清理，请参阅 :doc:`reliability`。异步结果和账户回调及其验证限制，请参阅 :doc:`push_notifications`。
