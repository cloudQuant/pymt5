官方 Python API 兼容性
======================

本页跟踪 ``pymt5`` 对官方 ``MetaTrader5`` Python 包 API 的覆盖情况；该 API 的文档位于
``mql5.com/en/docs/python_metatrader5`` 。

包含签名和语义差异的完整逐函数矩阵，请参阅 :doc:`python_api_detailed_comparison`。

已实现且已确认 Web Terminal 支持
--------------------------------

以下辅助函数现在可以明确映射到现有的 Web Terminal 命令或前端数据路径：

- 会话/账户： ``initialize()`` 、 ``shutdown()`` 、 ``account_info()`` ，
  通过 ``cmd=29`` 和 ``cmd=3`` 实现
- 终端元数据： ``terminal_info()`` ，根据 ``cmd=3`` 派生出尽可能完整的服务器/账户视图
- 错误： ``last_error()`` ，用于兼容层故障
- 交易品种： ``symbols_total()`` 、 ``symbols_get()`` 、 ``symbol_info()`` ，
  通过 ``cmd=34`` / ``cmd=18`` 实现
- K 线： ``copy_rates_range()`` 、 ``copy_rates_from()`` 、
  ``copy_rates_from_pos()`` ，通过 ``cmd=11`` 实现
- 交易状态： ``positions_total()`` 、 ``positions_get()`` 、
  ``orders_total()`` 、 ``orders_get()`` 、 ``history_orders_total()`` 、
  ``history_orders_get()`` 、 ``history_deals_total()`` 、
  ``history_deals_get()`` ，通过 ``cmd=4`` / ``cmd=5`` 实现
- 交易请求： ``order_send()`` ，通过 ``cmd=12`` 实现
- DOM： ``market_book_add()`` / ``market_book_release()`` ，通过 ``cmd=22`` 实现

已实现为客户端缓存视图
----------------------

以下辅助函数很实用，但它们并非来自当前 Web Terminal 协议中的专用快照命令：

- ``symbol_info_tick()`` 返回由 ``cmd=8`` 推送得到的最新缓存报价
- ``market_book_get()`` 返回由 ``cmd=23`` 得到的最新缓存 DOM 快照
- ``copy_ticks_from()`` / ``copy_ticks_range()`` 返回根据 ``cmd=8`` 推送汇总的缓存报价历史；
  它们不是服务器端历史快照
- ``version()`` 根据 ``cmd=3`` 的 build 字段以及本地观察到的公开 Web Terminal 构建日期元数据，
  返回 ``(500, build, release_date)``
- ``symbol_select()`` 是对报价订阅的尽力封装，并非真正的 MarketWatch 可见性 API
- ``terminal_info()`` 仅公开 Web 账户配置能够证实的子集；它不会模拟桌面版的文件系统/社区字段
- ``order_calc_profit()`` 和 ``order_calc_margin()`` 现在使用文档所述的本地公式及缓存的
  FX 换算报价，而不是专用的 ``sendCommand(...)`` 调用
- ``order_check()`` 是本地预检验证器，它复用交易品种规则、缓存价格和本地保证金估算，
  而不是使用专用的服务器 RPC

本地公式层目前的缺口
--------------------

Web Terminal 会在客户端为许多交易品种计算盈亏和保证金。 ``pymt5`` 现在为常见的零售模式
（外汇、CFD、主要交易所股票/期货、债券）提供了这项能力，但仍有少数情况不完整：

- 债券模式 ``37`` / ``39`` 现在使用当前 ``cmd=18`` 模式中的 ``face_value`` 和
  ``accrued_interest``
- 尚未对经纪商特定的杠杆档位和 ``orderRate(...)`` 规则建模，因此交易所期权/特殊保证金的结果
  仍属于尽力估算

仍不受支持或尚未确认
--------------------

目前，在既定兼容层范围内，不再有其他官方 ``MetaTrader5`` 辅助函数未获覆盖。剩余的不确定性
在于保真度，而不在于接口覆盖范围： ``version()`` 、 ``terminal_info()`` 、 ``copy_ticks_*()`` 和
``order_check()`` 等尽力实现的辅助函数被明确记录为派生视图，而不是专用的 Web Terminal RPC。

说明
----

``pymt5`` 保留其原生 dict/dataclass 返回类型。兼容辅助函数在可行的情况下遵循官方函数名称，
但不会试图逐字节复刻桌面软件包的 namedtuple 层。
