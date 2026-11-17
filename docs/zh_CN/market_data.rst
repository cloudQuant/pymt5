行情数据
========

本页介绍品种发现、历史 K 线、实时报价、缓存报价历史和深度行情等只读操作流程。

.. warning::

   pymt5 使用非官方、逆向分析得到的 MT5 Web Terminal 协议。不同服务器和构建版本的字段布局与行为可能不同。请使用经授权的模拟账户测试，并在依赖解码结果之前独立核验。本页所有示例都不会下单。

连接并加载品种缓存
------------------

按名称操作的辅助方法需要基础品种缓存。``async with`` 会在退出时关闭会话；应用仍应处理登录和协议错误。

.. code-block:: python

   import asyncio
   import os

   from pymt5 import MT5WebClient, PyMT5Error

   async def main() -> None:
       client = MT5WebClient(uri=os.environ["PYMT5_SERVER"])
       try:
           async with client:
               await client.login(
                   int(os.environ["PYMT5_LOGIN"]),
                   os.environ["PYMT5_PASSWORD"],
               )
               symbols = await client.load_symbols()
               info = client.get_symbol_info("EURUSD")
               if info is None:
                   raise RuntimeError("此服务器不提供 EURUSD")
               print(info.symbol_id, info.digits, len(symbols))
       except PyMT5Error as exc:
           print(f"行情会话失败：{exc}")

   asyncio.run(main())

``load_symbols()`` 返回按名称索引的缓存副本。可使用 ``get_symbol_id()``、``get_symbol_info()`` 或 ``symbol_names`` 在本地查询。``subscribe_symbols()`` 和 ``subscribe_book_by_name()`` 依赖此缓存，名称不存在时会抛出 ``SymbolNotFoundError``。

设置正的 ``symbol_cache_ttl`` 后，``load_symbols()`` 可以复用尚未过期的基础缓存。调用 ``invalidate_symbol_cache()`` 会同时清空基础品种元数据与已缓存的完整品种（``cmd=18``）记录，强制下一次加载刷新缓存。``symbol_info(name)`` 是详细信息的官方风格接口，通常使用完整品种数据并缓存返回记录。``get_full_symbol_info(name)`` 显式查询 cmd=18，仅在品种不存在时返回 ``None``。服务器拒绝、响应格式错误或记录不匹配时会抛出 ``ProtocolError``，不会误报为品种不存在。``get_symbols(use_gzip=True)``、``get_symbol_groups()`` 与 ``get_spreads()`` 在解压失败或服务端返回非零响应码时同样会抛出 ``ProtocolError``。

.. code-block:: python

   details = await client.symbol_info("EURUSD")
   if details is not None:
       print(details.get("contract_size"), details.get("volume_min"))

   groups = await client.get_symbol_groups()
   spreads = await client.get_spreads([info.symbol_id])

获取历史 K 线
-------------

``get_rates()`` 接收以分钟为单位的周期，以及用 Unix 秒表示的请求起止范围。构造时间戳时请使用带时区的 datetime。

.. code-block:: python

   from datetime import datetime, timedelta, timezone

   end = datetime.now(timezone.utc)
   start = end - timedelta(hours=6)
   bars = await client.get_rates(
       "EURUSD",
       60,
       int(start.timestamp()),
       int(end.timestamp()),
       bar_format="standard",  # 只有在确认服务器布局后才能这样指定
   )
   for bar in bars[-3:]:
       print(bar["time"], bar["open"], bar["high"], bar["low"], bar["close"])

标准记录为 48 字节，扩展记录为 56 字节并增加 ``real_volume``。默认的 ``bar_format="auto"`` 只有在响应长度唯一匹配一种布局时才接受响应：

* 非空响应长度若同时能被 48 和 56 整除（包括 336 字节的任意非零倍数），则布局有歧义并抛出 ``ProtocolError``。
* 长度若不能被任何一种布局整除，也会抛出 ``ProtocolError``。
* ``get_rates()`` 和 ``get_rates_raw()`` 遇到非零服务器响应码时会抛出 ``ProtocolError``。
* ``get_rates_raw()`` 可暴露原始字节用于诊断，但仅凭长度无法解决布局歧义。长度检查也无法发现所有截断，或识别恰好符合所选记录大小的错误显式格式。

只有在验证经授权的服务器/构建版本后，才应通过 ``MT5WebClient(rate_bar_format="standard")`` 或 ``"extended"`` 设置客户端默认值，或在单次调用中传入 ``bar_format``。出错后不要盲目切换格式。

请求时间戳和返回的 ``time`` 使用有符号 32 位 Unix 秒；此线上布局无法表示 2038-01-19 03:14:07 UTC 之后的日期。

官方风格封装方法只是对同一命令的本地便利封装：

* ``copy_rates_range()`` 转换周期和 datetime 类型的起止边界。
* ``copy_rates_from()`` 请求向前回溯的窗口，并返回截至 ``date_from`` 的最多 ``count`` 根末尾 K 线。
* ``copy_rates_from_pos()`` 根据返回的历史数据和本地当前时间，近似实现相对当前 K 线的位置定位；它不是服务器端的位置式历史接口。

订阅实时报价并显式清理
----------------------

请先注册回调再订阅。报价订阅会把上次确认的本地集合与本次请求的 ID 取并集，并把完整目标集合发送给服务器。

.. code-block:: python

   import asyncio
   from pymt5 import ProtocolError

   def print_ticks(ticks):
       for tick in ticks:
           print(tick.get("symbol", tick["symbol_id"]), tick["bid"], tick["ask"])

   client.on_tick(print_ticks)
   ids = await client.subscribe_symbols(["EURUSD"])
   try:
       await asyncio.sleep(30)
   finally:
       try:
           await client.unsubscribe_ticks(ids)
       except ProtocolError as exc:
           # 清理被拒绝：不得把退订报告为成功。
           print(f"报价退订未获确认：{exc}")

报价订阅和退订遇到非零服务器响应时会抛出 ``ProtocolError``，并保留上次确认的本地集合。在观察到的一次 build 6320 模拟会话中，服务器拒绝了移除全部报价订阅的请求，应用必须处理这种情况。超时或取消后，即使是上次确认的本地集合，也不能证明服务器当前状态；详见 :doc:`reliability`。

托管句柄会自动执行同样的清理，但不会隐藏清理错误：

.. code-block:: python

   subscription = await client.subscribe_ticks_managed(ids)
   async with subscription:
       await asyncio.sleep(30)
   # 只有 handle.unsubscribe() 无错误返回，才会执行到这里。

``symbol_info_tick(name)`` 返回最新缓存报价的副本；若从未观察到数据流，则返回 ``None``。``symbol_select(name, True)`` 和 ``symbol_select(name, False)`` 是报价订阅的便利封装，不能证明与券商无关的“市场报价”状态已经改变。

明确选择报价价格缩放方式
------------------------

报价可能是部分更新，包括同一 ``cmd=8`` 批次内针对同一品种的多条部分更新记录。bid、ask 或 last 中的零值会按顺序由同一批次中先前记录或之前的缓存值补齐。价格归一化由 ``tick_price_mode`` 控制：

``"scaled"``
   将每个非零价格除以 ``10 ** symbol.digits``。
``"normalized"``
   保持线上价格不变。
``"auto"`` （默认）
   应用旧版品种/数量级启发式规则。

.. code-block:: python

   client = MT5WebClient(
       tick_price_mode="normalized",
       tick_price_modes={"EURUSD": "scaled", "XAUUSD": "normalized"},
   )

单品种模式覆盖全局模式，名称匹配不区分大小写。同一配置适用于原始回调、类型化事件、缓存和重连后的会话。**auto 启发式规则尚未在不同券商、品种和构建版本上得到普遍验证。** 整数形态的价格以及 XAUUSD 等品种可能存在歧义。请检查经授权的线上样本，查看每个品种最多记录一次的启发式警告，并配置显式模式，不要把 ``auto`` 当作协议保证。

使用缓存报价历史
----------------

``copy_ticks_from()`` 和 ``copy_ticks_range()`` 过滤已经从 cmd=8 推送中捕获的报价。它们**不会**读取服务器端的历史报价归档。历史初始为空，断连期间可能有缺口，并且只包含已接收推送所表达的 flags 和字段。

.. code-block:: python

   recent = await client.copy_ticks_from("EURUSD", start, count=100)
   print(client.tick_history_stats())
   client.clear_tick_history()  # 或 clear_tick_history(info.symbol_id)

``tick_history_limit`` 限制每个品种保留的报价数，零表示不限。``max_tick_symbols`` 通过淘汰最久未更新的品种来限制跟踪的品种数量，零表示不限制。请根据应用的内存预算设置这些限制。

读取深度行情
------------

.. code-block:: python

   def print_book(entries):
       for entry in entries:
           print(entry.get("symbol", entry["symbol_id"]))
           print("bids", entry["bids"][:3], "asks", entry["asks"][:3])

   client.on_book_update(print_book)
   await client.subscribe_book_by_name(["EURUSD"])
   try:
       await asyncio.sleep(30)
       snapshot = client.market_book_get("EURUSD")
   finally:
       await client.market_book_release("EURUSD")

深度行情回调接收包含 ``symbol_id``、可选 ``symbol``、``bids`` 和 ``asks`` 的记录。每档包含 ``price`` 和 ``volume``。``subscribe_book()`` 与 ``unsubscribe_book()`` 会对并发调用加锁串行化，遇到非零响应码时抛出 ``ProtocolError``，并在被拒绝时保留上次确认的深度行情订阅集合与本地缓存。``market_book_get()`` 只返回最新的本地缓存项，不保证新鲜度或完整性。``market_book_add()`` 和 ``market_book_release()`` 是订阅别名。``subscribe_book_managed()`` 提供托管清理；与报价一样，必须先 ``await`` 创建句柄，再进入 ``async with``。

回调注册、类型化事件、背压和移除处理器，请参阅 :doc:`push_notifications`。超时、重连缺口和不确定命令结果，请参阅 :doc:`reliability`。
