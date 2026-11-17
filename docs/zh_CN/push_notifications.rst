推送通知
========

pymt5 将解析后的服务器帧分派给同步或异步回调。本页示例仅观察数据和管理订阅，不会下单。

.. warning::

   Web Terminal 协议是非官方且通过逆向分析得到的。不同服务器/构建版本的推送布局和投递行为可能不同，而且并非每个观察到的命令都已验证为主动推送。不要把回调当作恰好一次的事件日志，也不要把它当作账户状态的独立确认。

选择回调接口
------------

.. list-table:: 可用的注册方法
   :header-rows: 1
   :widths: 10 27 63

   * - 命令
     - 注册方法
     - 回调数据与限制
   * - 8
     - ``on_tick()``
     - 报价字典列表。请在订阅报价之前注册。
   * - 4
     - ``on_trade_update()``、``on_position_update()``、``on_order_update()``
     - 合并或分开的持仓/订单快照。**普通查询响应也会调用这些回调。**
   * - 10
     - ``on_trade_transaction()``
     - 带 ``update_type`` 的交易字典；类型 2 包含余额信息、成交和持仓，其他类型描述订单事务。真实主动交易推送尚未得到验证。
   * - 14
     - ``on_account_update()``
     - 包含 balance/equity/margin 等字段的账户字典。
   * - 17
     - ``on_symbol_details()``
     - 扩展品种报价记录；原始值规则见下文。
   * - 19
     - ``on_trade_result()``
     - 异步交易结果字典；响应足够大时包含嵌套的 ``result``。它不能替代账户核对。
   * - 23
     - ``on_book_update()``
     - 订阅深度行情后，接收深度行情记录列表。
   * - 13
     - ``on_symbol_update()``
     - 因具体 schema 可能不同，接收原始 ``CommandResult``。
   * - 15
     - ``on_login_status()``
     - 表示可能的登录/会话状态变化的原始 ``CommandResult``。

类型化替代接口 ``on_tick_event()``、``on_book_event()``、``on_trade_result_event()`` 和 ``on_account_event()`` 分别发出冻结的 ``TickEvent``、``BookEvent``、``TradeResultEvent`` 和 ``AccountEvent`` 数据类。报价和深度行情的类型化注册会针对每条记录调用一次回调，而基于字典的对应接口接收整个批次。类型化 ``BookEvent.entries`` 合并 bids 和 asks；需要区分方向时请使用 ``raw``。

接收报价并安全移除处理器
------------------------

封装用户回调的注册方法返回**内部处理器**。请把该返回对象传给 ``transport.off()``，而不是原始用户函数。原始的 ``on_symbol_update()`` 和 ``on_login_status()`` 返回原始回调。

.. code-block:: python

   import asyncio
   from pymt5 import CMD_TICK_PUSH, PyMT5Error

   def show_ticks(ticks):
       for tick in ticks:
           print(tick.get("symbol", tick["symbol_id"]), tick["bid"], tick["ask"])

   handler = client.on_tick(show_ticks)
   ids = await client.subscribe_symbols(["EURUSD"])
   try:
       await asyncio.sleep(30)
   finally:
       client.transport.off(CMD_TICK_PUSH, handler)
       try:
           await client.unsubscribe_ticks(ids)
       except PyMT5Error as exc:
           # 拒绝、超时或会话失效都不代表退订成功。
           # 保留/记录失败，供应用执行后续清理。
           print(f"报价清理未获确认：{exc}")

非零报价退订响应会抛出 ``ProtocolError``，并保留上次确认的本地集合。特别是在一次 build 6320 模拟观察中，服务器拒绝了移除全部报价集合的请求。绝不能仅因尝试过清理就记录或返回成功。超时或取消后，本地集合也不能证明服务器状态。托管 ``SubscriptionHandle`` 的清理会传播同样的失败。请参阅 :doc:`market_data` 和 :doc:`reliability`。

报价可能是部分更新。解析器会尽量用之前的缓存值补齐零值 bid/ask/last。默认 ``tick_price_mode="auto"`` 使用旧版品种/数量级启发式规则，**尚未得到普遍验证**。对经授权的服务器/构建版本采样后，应配置 ``scaled`` 或 ``normalized``，必要时按品种覆盖。此设置同样适用于字典回调、类型化事件和缓存。准确模式和历史限制请参阅 :doc:`market_data`。

将 cmd=4 回调视为快照而非变化事件
----------------------------------

.. code-block:: python

   def show_snapshot(data):
       print(f"快照：持仓={len(data['positions'])}，订单={len(data['orders'])}")

   handler = client.on_trade_update(show_snapshot)
   await client.get_positions_and_orders()  # 此响应会调用 show_snapshot

因此，调用 ``get_positions()``、``get_orders()`` 或 ``get_positions_and_orders()`` 都可能因普通响应而调用 cmd=4 回调。主动 cmd=4 变化推送尚未得到验证，回调也无法区分响应与推送。如果需要检测变化，请在应用代码中比较快照，并预期会出现重复状态。单独注册 ``on_position_update()`` 和 ``on_order_update()`` 也有相同限制。

保持接收循环及时响应
--------------------

同步回调直接在接收分派路径中运行，而异步回调通过按连接有序的后台队列分派，因此在 ``async def`` 监听器内部 await ``send_command()`` 不会死锁 socket 读取协程。即使采用队列分派，缓慢的处理仍会延迟同一连接上的后续监听器投递，因此应把耗时工作转移到应用任务，并选择明确的有界队列策略，避免内存无限增长。

.. code-block:: python

   import asyncio

   queue = asyncio.Queue(maxsize=1000)

   def enqueue_ticks(ticks):
       try:
           queue.put_nowait(ticks)
       except asyncio.QueueFull:
           # 示例策略：丢弃并计数/报告；不要悄悄阻塞接收循环。
           print("报价队列已满；丢弃一个批次")

   handler = client.on_tick(enqueue_ticks)

   async def consume():
       while True:
           ticks = await queue.get()
           try:
               process_ticks(ticks)  # 应用自有的只读处理
           finally:
               queue.task_done()

   consumer = asyncio.create_task(consume())
   # 关闭时：移除处理器，取消并等待 consumer，然后关闭 client。

在回调中直接 await ``asyncio.Queue.put()`` 会对整个接收处理过程施加背压；是否适合由应用决定。库不会持久化、重放、去重或确认用户事件。

观察回调失败
------------

传输层会记录回调异常（包括用户回调抛出的 ``KeyError``、``ValueError``、``TypeError`` 以及监听器内部 ``asyncio.CancelledError``，这些异常已与二进制解析错误处理彻底隔离），并调用已注册的错误观察器。所有封装推送注册方法现均支持 ``async def`` 异步回调，且 ``TradeResultEvent.order`` 会正确读取 schema 中的 ``trade_order`` 字段。观察器接收异常和失败的回调（通常是内部封装函数）。

.. code-block:: python

   def callback_failed(exc, callback):
       print(f"回调 {getattr(callback, '__name__', callback)!r} 失败：{type(exc).__name__}")

   client.on_callback_error(callback_failed)

错误观察器自身不应抛出异常。解析失败会被记录，但解析错误或回调失败并不能证明服务器已停止发送，也不能证明账户状态未变。请勿在回调日志中写入凭据或其他敏感负载数据。

账户、事务和结果事件
--------------------

.. code-block:: python

   client.on_account_update(
       lambda data: print(data.get("balance"), data.get("equity"))
   )

   def show_transaction(data):
       if data.get("update_type") == 2:
           print("余额帧", data.get("balance_info"))
       else:
           print("订单事务", data.get("transaction_type"))

   client.on_trade_transaction(show_transaction)
   client.on_trade_result(
       lambda data: print(data.get("result", {}).get("retcode"))
   )

这些回调只是观察结果，不是持久事务日志。build 6320 Web Terminal 会监听 cmd=10，但真实交易推送尚未得到验证。cmd=19 结果或单独的 retcode 不能证明最终执行；在作出交易决策之前应读取持仓、订单和成交，且绝不盲目重试超时或取消的交易。请参阅 :doc:`trading`。

品种详情和原始状态帧
--------------------

在观察到的 build 6320 中，cmd=17 包含不带计数的固定大小记录。价格是原始线上值；网站按品种 digits 进行缩放，最大为八位。使用字段掩码判断哪些值存在：未标记的零不是当前报价。其他构建版本使用旧版解析布局，不断言与 build 6320 一致。

.. code-block:: python

   def show_details(rows):
       for row in rows:
           print(row.get("symbol", row["symbol_id"]), row.get("bid"))

   client.on_symbol_details(show_details)
   client.on_symbol_update(lambda result: print(f"品种帧 cmd={result.command}"))
   client.on_login_status(lambda result: print(f"登录帧 code={result.code}"))

原始 ``CommandResult`` 暴露 ``command``、``code`` 和 ``body``。使用原始帧的应用需要自行执行额外验证和针对版本的解码。深度行情回调负载和托管订阅请参阅 :doc:`market_data`。
