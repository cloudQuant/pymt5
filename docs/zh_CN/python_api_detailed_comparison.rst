与官方 MetaTrader5 Python API 的详细比较
========================================

本页将 `MQL5 Python 集成 <https://www.mql5.com/en/docs/python_metatrader5>`_
上发布的官方 ``MetaTrader5`` Python 包函数集，与截至 2026-03-17 检查时的当前 ``pymt5``
客户端进行比较。

比较对象
--------

官方软件包与本地安装的桌面终端通信。 ``pymt5`` 则通过逆向工程得出的 WebSocket 协议与公开的
MT5 Web Terminal 通信。因此，虽然双方名称现在高度一致，但某些语义并不相同：

- 官方软件包：同步的模块级函数，例如 ``mt5.initialize()`` 和 ``mt5.order_send()``
- ``pymt5`` ：异步实例方法，例如
  ``await client.initialize()`` 和 ``await client.order_send({...})``
- 官方软件包：由终端管理的快照、namedtuple 和类数组返回类型
- ``pymt5`` ：dict、list 和 dataclass，其中一些视图根据推送流或本地公式重建

状态标签
--------

- ``Direct`` （直接）：由已确认的 Web Terminal 命令或响应路径支持
- ``Derived`` （派生）：根据其他命令、缓存的推送数据或本地过滤构建
- ``Best-effort`` （尽力）：已公开提供，但没有具有完全等效语义的专用 Web Terminal RPC 支持

连接、会话和终端元数据
----------------------

.. list-table::
   :header-rows: 1

   * - 官方函数
     - ``pymt5`` 映射
     - 状态
     - 说明
   * - ``initialize()``
     - ``MT5WebClient.initialize()``
     - 直接
     - 映射到 ``cmd=29`` 会话初始化。与桌面软件包不同，它不会启动终端可执行文件，
       也不会选择本地数据路径。
   * - ``login()``
     - ``MT5WebClient.login()``
     - 直接
     - 通过 Web Terminal 协议登录。 ``pymt5`` 返回 ``(token, session)`` ，还公开 URL、OTP
       和潜在客户跟踪参数等 Web 特有字段。
   * - ``shutdown()``
     - ``MT5WebClient.shutdown()``
     - 直接
     - 关闭 WebSocket 会话，而不是与桌面终端进程分离。
   * - ``version()``
     - ``MT5WebClient.version()``
     - 尽力
     - 根据 ``cmd=3`` 和本地观察到的公开构建元数据返回 ``(500, build, release_date)`` 。
       未知构建的日期字符串保持为空。
   * - ``last_error()``
     - ``MT5WebClient.last_error()``
     - 尽力
     - 跟踪 ``pymt5`` 兼容层中的故障。它并非桌面终端内部错误缓冲区的逐字节复刻。
   * - ``account_info()``
     - ``MT5WebClient.account_info()``
     - 直接
     - ``get_account()`` 的别名，通过 ``cmd=3`` 实现。返回 dict，而不是桌面软件包的类型化记录。
   * - ``terminal_info()``
     - ``MT5WebClient.terminal_info()``
     - 尽力
     - 仅公开 Web 账户配置能够证实的服务器/构建/时区/交易权限子集。有意省略桌面版特有的
       文件系统和社区字段。

交易品种、MarketWatch 和 DOM
----------------------------

.. list-table::
   :header-rows: 1

   * - 官方函数
     - ``pymt5`` 映射
     - 状态
     - 说明
   * - ``symbols_total()``
     - ``MT5WebClient.symbols_total()``
     - 直接
     - 统计缓存的交易品种，或直接获取 Web 交易品种列表。
   * - ``symbols_get()``
     - ``MT5WebClient.symbols_get()``
     - 直接
     - 由 ``cmd=34``/``cmd=6`` 支持，并在客户端按通配符组进行过滤。
   * - ``symbol_info()``
     - ``MT5WebClient.symbol_info()``
     - 直接
     - 可用时使用 ``cmd=18`` ，否则回退到基础交易品种缓存。
   * - ``symbol_info_tick()``
     - ``MT5WebClient.symbol_info_tick()``
     - 派生
     - 返回最新缓存的 ``cmd=8`` 报价推送。当前公开前端中尚未确认专用的报价快照命令。
   * - ``symbol_select()``
     - ``MT5WebClient.symbol_select()``
     - 派生
     - 实现为报价订阅管理，而不是桌面版持久化的 MarketWatch 可见性。
   * - ``market_book_add()``
     - ``MT5WebClient.market_book_add()``
     - 直接
     - 使用 ``cmd=22`` 订单簿订阅。
   * - ``market_book_get()``
     - ``MT5WebClient.market_book_get()``
     - 派生
     - 返回最后缓存的 ``cmd=23`` DOM 快照。只有观察到订单簿流之后，它才有实用价值。
   * - ``market_book_release()``
     - ``MT5WebClient.market_book_release()``
     - 直接
     - 从当前 DOM 订阅集合中移除该交易品种。

K 线和报价
----------

.. list-table::
   :header-rows: 1

   * - 官方函数
     - ``pymt5`` 映射
     - 状态
     - 说明
   * - ``copy_rates_range()``
     - ``MT5WebClient.copy_rates_range()``
     - 直接
     - 对 ``cmd=11`` K 线历史的轻量封装。
   * - ``copy_rates_from()``
     - ``MT5WebClient.copy_rates_from()``
     - 派生
     - 通过 ``cmd=11`` 获取 K 线范围，并在本地截取末尾的 ``count`` 条。
   * - ``copy_rates_from_pos()``
     - ``MT5WebClient.copy_rates_from_pos()``
     - 派生
     - 通过请求足够的历史数据并在客户端代码中切片，重建相对于当前 K 线的访问方式。
   * - ``copy_ticks_from()``
     - ``MT5WebClient.copy_ticks_from()``
     - 派生
     - 返回根据观察到的 ``cmd=8`` 推送汇总而成的缓存报价历史。它不是服务器端历史报价查询。
   * - ``copy_ticks_range()``
     - ``MT5WebClient.copy_ticks_range()``
     - 派生
     - 与 ``copy_ticks_from()`` 使用相同的缓存流模型，并对整数时间戳采用包含该秒末尾的处理方式。

订单、持仓、历史和计算
----------------------

.. list-table::
   :header-rows: 1

   * - 官方函数
     - ``pymt5`` 映射
     - 状态
     - 说明
   * - ``orders_total()``
     - ``MT5WebClient.orders_total()``
     - 直接
     - 基于 ``cmd=4`` 的当前挂单视图构建。
   * - ``orders_get()``
     - ``MT5WebClient.orders_get()``
     - 直接
     - 加载 ``cmd=4`` 订单状态后，在客户端代码中支持 ``symbol`` 、 ``group`` 和 ``ticket`` 过滤器。
   * - ``positions_total()``
     - ``MT5WebClient.positions_total()``
     - 直接
     - 基于 ``cmd=4`` 的当前未平仓持仓视图构建。
   * - ``positions_get()``
     - ``MT5WebClient.positions_get()``
     - 直接
     - 在客户端代码中支持 ``symbol`` 、 ``group`` 和 ``ticket`` 过滤器。
   * - ``history_orders_total()``
     - ``MT5WebClient.history_orders_total()``
     - 直接
     - 统计经本地过滤的 ``cmd=5`` 历史订单。
   * - ``history_orders_get()``
     - ``MT5WebClient.history_orders_get()``
     - 直接
     - 对 ``cmd=5`` 历史支持 ``date_from`` 、 ``date_to`` 、 ``group`` 、 ``ticket`` 和
       ``position`` 过滤器。
   * - ``history_deals_total()``
     - ``MT5WebClient.history_deals_total()``
     - 直接
     - 统计经本地过滤的 ``cmd=5`` 成交。
   * - ``history_deals_get()``
     - ``MT5WebClient.history_deals_get()``
     - 直接
     - 对 ``cmd=5`` 成交历史支持 ``date_from`` 、 ``date_to`` 、 ``group`` 、 ``ticket`` 和
       ``position`` 过滤器。
   * - ``order_send()``
     - ``MT5WebClient.order_send()``
     - 直接
     - 封装 ``cmd=12`` 并规范化官方风格的请求 dict，包括 stop-limit 映射。返回
       ``TradeResult`` ，而不是桌面软件包的结果记录。
   * - ``order_check()``
     - ``MT5WebClient.order_check()``
     - 尽力
     - 仅为本地预检验证器。它检查交易品种规则、止损止盈、到期时间、成交模式和保证金是否充足，
       不使用专用的服务器 ``order_check`` RPC。
   * - ``order_calc_margin()``
     - ``MT5WebClient.order_calc_margin()``
     - 尽力
     - 使用本地公式及缓存的 FX 换算报价。涵盖常见的外汇/CFD/期货/股票/债券模式；
       经纪商特定的保证金档位仍为近似计算。
   * - ``order_calc_profit()``
     - ``MT5WebClient.order_calc_profit()``
     - 尽力
     - 与 ``order_calc_margin()`` 使用相同的本地公式模型。债券模式 ``37``/``39`` 使用扩展
       ``cmd=18`` 模式中的 ``face_value`` 和 ``accrued_interest`` 。

桌面软件包中没有官方对应项
--------------------------

``pymt5`` 还公开了 Web Terminal 特有或协议级的辅助函数，它们在官方桌面软件包中没有直接对应项：

- 账户开户和验证：
  ``request_opening_verification()`` 、 ``submit_opening_verification()`` 、
  ``open_demo_account()`` 、 ``open_real_account()``
- OTP 和验证辅助函数：
  ``verify_code()`` 、 ``enable_otp()`` 、 ``disable_otp()``
- 仅 Web 提供的经纪商/UI 命令：
  ``trader_params()`` 、 ``send_notification()`` 、 ``get_corporate_links()``
- 协议逃生通道：
  ``send_raw_command()`` 、 ``send_bootstrap_command_52()``
- 异步流回调：
  ``on_tick()`` 、 ``on_book_update()`` 、 ``on_trade_result()`` 以及其他推送处理程序注册
- 高级交易便捷接口：
  ``buy_market()`` 、 ``sell_market()`` 、 ``buy_limit()`` 、 ``sell_limit()`` 、
  ``buy_stop()`` 、 ``sell_stop()`` 、 ``buy_stop_limit()`` 、 ``sell_stop_limit()`` 、
  ``close_position()`` 、 ``close_position_by()`` 、 ``modify_position_sltp()`` 、
  ``modify_pending_order()`` 、 ``cancel_pending_order()``

实际结论
--------

在接口层面， ``pymt5`` 现在覆盖了交易、交易品种、持仓、订单、K 线、报价、历史以及终端/账户
元数据所涉及的相同官方函数名称。真正的差异在于语义：

- 官方软件包使用桌面终端 IPC，而 ``pymt5`` 使用 Web Terminal WebSocket 传输
- 某些官方快照 API 在 ``pymt5`` 中建模为缓存视图
- 某些终端侧计算被建模为本地尽力公式
- 返回结构采用 Python 风格的 dict/list/dataclass，而不是官方 namedtuple 或面向数组的结果

如需仅包含状态的简要摘要，请参阅 :doc:`python_api_compat`。
