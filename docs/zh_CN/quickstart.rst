快速开始
========

本指南只建立只读连接，不会下单、改单或撤单。在验证实际服务器 build 和
应用所需的全部流程之前，请使用已获授权的模拟账户。

1. 配置凭据
-----------

通过 shell 或密钥管理器设置环境变量：

.. code-block:: bash

   export PYMT5_SERVER="wss://web.metatrader.app/terminal"
   export PYMT5_LOGIN="your-demo-login"
   export PYMT5_PASSWORD="your-demo-password"

不要在代码中硬编码凭据，不要提交 ``.env``，也不要把 token 或 session
数据粘贴到 issue。客户端不会自动加载 ``.env``；是否加载该文件由应用决定。

2. 连接并加载数据
-----------------

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

           # load_symbols() 返回以名称为键的 dict[str, SymbolInfo]。
           symbols = await client.load_symbols()
           if not symbols:
               raise RuntimeError("The server returned no symbols")

           account = await client.get_account()
           print(f"Loaded {len(symbols)} symbols")
           print(f"Account currency: {account.get('currency', 'unknown')}")


   asyncio.run(main())

无论成功还是异常，异步上下文管理器都会关闭 transport。在活动会话中再次
调用 ``login()`` 会抛出 ``SessionError``；切换账户前请先调用 ``logout()``。

3. 获取历史K线
--------------

加载交易品种后，请求一个有明确边界的历史区间：

.. code-block:: python

   from datetime import datetime, timedelta, timezone

   end = datetime.now(timezone.utc)
   start = end - timedelta(days=7)
   bars = await client.get_rates("EURUSD", 60, start, end)
   print(f"Received {len(bars)} H1 bars")

请将 ``EURUSD`` 替换为当前服务器实际返回的品种。历史响应会先经过校验再
解析；未知或有歧义的布局会抛出 ``ProtocolError``，而不是进行猜测。
详见 :doc:`market_data`。

4. 观察 tick 流
---------------

先注册回调再订阅，并选择处于活跃交易时段、流动性较好的品种：

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

休市期间没有 tick 本身既不能证明协议成功，也不能证明协议失败。订阅句柄
退出时会尝试清理；如果服务器拒绝退订，该错误会被抛出，不能把清理描述为
成功。tick 价格缩放与服务器/build 有关，使用价格前请阅读 :doc:`configuration`。

5. 增加明确的错误处理
---------------------

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
       # 已发送请求的结果不确定时，旧连接会被隔离。
       # 不要复用旧连接，也不要猜测服务器已经处理了什么。
       print("Ping timed out; wait for a fresh connection")
   except (MT5ConnectionError, ProtocolError) as exc:
       print(f"Transport or protocol failed: {exc}")

登录失败应单独捕获 ``AuthenticationError``。对于交易，超时意味着服务端
执行结果**未知**：重试前必须核对订单、成交和持仓。详见 :doc:`reliability`
和 :doc:`security`。

下一步
------

- :doc:`examples`：可运行示例及其只读/写操作范围
- :doc:`configuration`：兼容模式与连接设置
- :doc:`market_data`：tick、K线、品种元数据和订单簿
- :doc:`troubleshooting`：常见连接与协议故障
