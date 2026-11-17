可靠性
======

以下准则用于处理超时、取消、断连、重连和清理，避免虚构协议无法提供的确定性。本页示例均为只读操作，不会提交交易。

.. warning::

   pymt5 实现的是非官方、逆向分析得到的 Web Terminal 协议。响应关联和投递语义受已观察到的服务器行为限制，并不存在跨券商的协议保证。请在经授权的模拟服务器上验证恢复流程，并根据权威账户状态核对会改变账户的操作。

理解命令关联与隔离
------------------

尚未证明响应会返回可靠的请求标识符。因此，传输层采用两项保护措施：

1. 对**相同命令 ID** 的请求进行串行化，直到收到响应。不同命令 ID 可以独立执行。
2. 一旦可能已经发送，超时、取消或发送失败就会使结果不确定。传输层会隔离该连接，使连接上的其他进行中请求失败，并阻止后续请求复用该连接。这样，迟到响应就不会被分配给旧连接上的后续请求。

这些保护强制执行两项帧关联边界：纯主动推送命令（如 ``cmd=8``、``10``、``13``、``14``、``15``、``19`` 与 ``23``）绝不会完成挂起请求，且 ``cmd=4``、``cmd=17`` 等共享命令可附加结构校验谓词。然而，对于同时满足相同命令 ID 与结构检查的主动推送或重复响应，这些保护仍无法将其与普通请求响应完全区分开来，也无法揭示服务器实际执行了什么。尤其是 cmd=4 监听器可能因普通持仓/订单查询响应而运行；请参阅 :doc:`push_notifications`。

``MT5TimeoutError`` 表示客户端未能在超时时间内完成命令，并不表示服务器没有执行任何操作。``SessionError`` 可能表示某一代连接已失效。调用方取消产生的 ``asyncio.CancelledError`` 同样需要谨慎对待，并且通常应在应用清理后重新抛出。

核对交易，绝不假定未执行
------------------------

交易超时或取消后，服务器端执行结果**未知**。请求可能没有到达，也可能已被接受或已经执行。不要盲目重试，也不要把自动重连当作幂等机制。客户端不会自动重试被中断的交易。

在重新建立并登录会话后，先收集只读证据，再作进一步交易决策：

.. code-block:: python

   async def reconciliation_snapshot(client):
       positions, orders, history = await asyncio.gather(
           client.get_positions(),
           client.get_orders(),
           client.get_trade_history(),
       )
       return {"positions": positions, "orders": orders, "history": history}

将预期操作、品种、方向、交易量、价格、备注和时间与结果比较。单凭快照不一定能确定请求身份，而且重连后立即找不到匹配记录，也不能证明没有执行。如果仍不确定，请停止自动重试，并通过券商授权的支持/管理渠道处理。交易结果和模拟账户警告请参阅 :doc:`trading`。

配置重连，同时正视数据缺口
--------------------------

自动重连默认关闭。成功登录后，客户端会在内存中保留用于重连的凭据，并在关闭/登出清理时清除保存的引用。

.. code-block:: python

   import asyncio
   import os

   from pymt5 import MT5WebClient, PyMT5Error

   async def monitor() -> None:
       disconnected = asyncio.Event()
       client = MT5WebClient(
           uri=os.environ["PYMT5_SERVER"],
           timeout=10.0,
           heartbeat_interval=30.0,
           auto_reconnect=True,
           max_reconnect_attempts=5,
           reconnect_delay=3.0,
           max_reconnect_delay=60.0,
       )
       client.on_disconnect(disconnected.set)
       try:
           async with client:
               await client.login(
                   int(os.environ["PYMT5_LOGIN"]),
                   os.environ["PYMT5_PASSWORD"],
               )
               await client.load_symbols()
               for _ in range(6):
                   health = await client.health_check()
                   print(health.state.value, health.ping_latency_ms)
                   await asyncio.sleep(10)
               if disconnected.is_set():
                   print("至少发生过一次断连；缓存数据可能存在缺口")
       except PyMT5Error as exc:
           print(f"监控停止：{exc}")

   asyncio.run(monitor())

重连使用带抖动且有 ``max_reconnect_delay`` 上限的指数退避。它会创建新的传输层、迁移已注册的监听器、重新登录、重启心跳，并尝试恢复本地保留的最近报价和深度行情订阅集合。前台调用 ``connect()`` 或 ``login()`` 会先取消正在运行的后台重连循环；显式调用 ``logout()`` 或 ``close()`` 会清空保留的凭据、本地报价与深度行情订阅集合及缓存，避免后续会话继承旧订阅。传输层 ``WebSocketException`` 会统一封装为 ``MT5ConnectionError`` 并由重连、心跳与健康检查安全处理，而 ``close()`` 会对接收任务、监听器与 socket 清理执行有界等待。socket 连接成功不代表每个恢复步骤都成功。

重连**不会**重放错过的报价、深度行情、账户事件、查询响应或交易。报价缓存和缓存报价历史可能含有缺口或陈旧值。``copy_ticks_*()`` 只读取本地推送历史，不读取服务器归档。重连后应重新查询所需状态，并采用适合应用的事件/时间戳检查。

报价与深度行情订阅状态需要特别注意：非零订阅/退订响应会抛出 ``ProtocolError`` 并保留上次确认的本地集合（及深度行情缓存），但超时或取消后，该集合不能证明服务器状态。重新订阅只是尝试恢复本地目标集合，并不是状态核对协议。

检查健康状态，但不要视为成功保证
--------------------------------

只有传输层就绪且客户端认为已登录时，``client.is_connected`` 才为真。``client.transport.state`` 是 ``DISCONNECTED``、``CONNECTING``、``READY``、``CLOSING`` 或 ``ERROR`` 之一。

``await client.health_check()`` 返回包含以下字段的 ``HealthStatus`` 快照：

* ``state``：当前传输层状态；
* ``ping_latency_ms``：测得的往返时间；无法测量 ping 时为 ``None``；
* ``last_message_at``：基于单调时钟的最近接收时间，或 ``None``；
* ``uptime_seconds``：自当前客户端连接时间戳以来经过的时间；
* ``reconnect_count``：客户端记录的成功重连恢复次数。

传输层就绪时，健康检查会发送 ping，并把 ping 失败转换为 ``ping_latency_ms=None``。它只是诊断快照，不能保证下一条命令成功。``on_health_degraded(callback, threshold_ms=...)`` 会在测得的 ping 超过阈值时执行回调。断连回调和健康回调应尽快返回，避免阻塞传输处理。

限制负载并准确解释错误
----------------------

可选的 ``rate_limit``/``rate_burst`` 令牌桶会限制客户端命令发送速率。它不能确定服务器配额，也不能防止服务器拒绝；同命令串行化和超时处理仍然必要。构造参数 ``timeout`` 也限制命令等待和连接打开时间。

.. list-table:: 常见失败信号
   :header-rows: 1
   :widths: 28 72

   * - 信号
     - 正确解释
   * - ``ValidationError``
     - 辅助方法拒绝了本地输入。这不代表其他服务器端验证的结果。
   * - ``ProtocolError``
     - 命令/布局不受支持，或已覆盖的响应被拒绝或格式错误。例如，K 线会拒绝非零响应码和歧义长度；报价订阅/退订会拒绝非零响应码。
   * - ``MT5TimeoutError``
     - 没有及时获得完整响应。已发送请求的服务器结果可能未知，旧连接会被隔离。
   * - ``SessionError`` / ``MT5ConnectionError``
     - 会话状态、连接代次或 socket 不可用。应建立并验证新会话，而不是复用结果不确定的会话。
   * - ``AuthenticationError``
     - 登录被拒绝或登录响应无效。
   * - ``TradeResult(success=False)``
     - 服务器返回了交易拒绝/失败 retcode。这不同于传输超时；后者的执行结果未知。

不要把所有异常都转换为自动重试。只读重试仍可能产生重复回调或数据缺口，而改变账户的重试可能造成重复执行。请保留取消语义和原始错误上下文。

让清理结果可见
--------------

正常使用 ``async with MT5WebClient(...)`` 关闭会话，并让关闭错误到达应用的错误处理器。订阅清理需要单独记录结果：如果退出托管句柄时抛出异常，进入/退出过程本身不能证明服务器接受了退订。

.. code-block:: python

   cleanup_confirmed = False
   ids = await client.subscribe_symbols(["EURUSD"])
   try:
       await consume_read_only_updates()
   finally:
       try:
           await client.unsubscribe_ticks(ids)
       except PyMT5Error as exc:
           print(f"退订被拒绝或结果不确定：{exc}")
       else:
           cleanup_confirmed = True

   # 只有 cleanup_confirmed=True 才记录本地已确认的退订调用。

一次 build 6320 模拟观察表明，移除全部报价订阅集合可能被拒绝。``SubscriptionHandle.unsubscribe()`` 只有在退订协程成功返回后才把自身标记为非活动，因此可以在考虑会话状态后有意识地重试失败的清理；默认情况下绝不能将其报告为成功。

报价缩放、历史和深度缓存限制请参阅 :doc:`market_data`。回调背压和处理器移除请参阅 :doc:`push_notifications`。
