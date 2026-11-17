更新日志
========

v1.1.0 (2026-11-17)
-------------------

- 将异步传输层监听器分派隔离到按连接有序的后台队列中，避免在回调内调用
  ``send_command()`` 导致接收循环死锁；将监听器内部 ``CancelledError`` 与用户
  回调异常统一路由至 ``on_callback_error``；为 ``close()`` 清理设置超时边界；
  在不确定发送清理期间保留外部取消信号；并隔离全部指标和断连回调异常。
- 禁止纯主动推送帧完成挂起请求 future，为共享命令（如 ``cmd=4``、``cmd=17``）
  提供结构校验谓词；校验外层协议版本与引导 AES 密钥长度（异常映射为
  ``MT5ConnectionError``）；拒绝负数/布尔型 ``propLength`` 与越界偏移；并在未配置
  ``structlog`` 时将其桥接至标准库 ``logging``（默认 ``WARNING`` 级别）。
- 为 ``subscribe_book()`` 与 ``unsubscribe_book()`` 加锁串行化并校验服务端返回码；
  在 ``invalidate_symbol_cache()`` 中同步清空完整品种缓存；支持单批多条增量
  tick 价格滚动合并；将 ``TradeResultEvent.order`` 对齐到 ``trade_order`` 字段；
  在所有推送包装器中支持 ``async def`` 回调；并为截断的账户/交易帧提供边界保护。
- 为 ``init_session()`` 加锁与代际校验；在前台 ``connect()`` / ``login()`` 时取消
  活动重连循环；在 ``logout()`` 与 ``close()`` 时清空本地报价/深度订阅集合及缓存；
  支持 ``on_health_degraded()`` 独立每回调延迟阈值；统一捕获并封装
  ``WebSocketException``；回收失效 ``connect()`` 传输连接与取消中的
  ``MT5ConnectionPool`` 客户端；并校验 OTP、改密及开户/验证命令的服务端返回码。
- 对交易量、价格、限流器与心跳间隔强制执行有限正数及 ``uint64`` 上界校验；修复
  Stop-Limit 挂单参考价校验、零净值/零可用保证金保留、``order_calc_margin()`` 失败
  传播及 ``history_deals_get(ticket=...)`` 按成交票据过滤；支持确定性券商货币符号
  前后缀匹配；强化 ``OrderManager`` 终态单调性与 ``order_state`` / ``TradeResult``
  兼容；并将 ``PositionRecord``、``DealRecord``、``SymbolGroupRecord`` 的
  ``TypedDict`` 键与运行期 schema 对齐。
- 对数据类 ``repr()`` 与实盘脚本日志中的凭据及个人隐私字段执行脱敏；收紧 GitHub
  Actions 权限作用域、固定发布 Action SHA 并升级 ``setuptools>=77.0`` 构建下限；
  隔离测试子进程覆盖率环境变量；并修复示例与实盘脚本缺陷。
- 串行执行共享同一命令 ID 的请求；当请求可能已经发送时，超时、取消或发送失败会触发连接隔离，
  防止迟到响应完成该连接上的后续请求。被中断交易的服务器端结果仍然未知，必须先核对再决定后续操作。
- 加固重连、关闭、断连回调和订阅恢复的生命周期处理，并扩充连接代际与断连竞态的离线回归测试。
- 检查 tick 订阅/退订响应码，在请求被拒绝后保留最后一次确认的本地订阅集合。服务器仍可能拒绝移除
  最后一个 tick 订阅；清理被拒绝不等于成功。
- 扩展已观察的 build 6320 登录和品种详情兼容性，同时保留显式的 tick 价格与历史 K 线兼容配置。
  单一服务器 build 的观察结果不能证明其他券商或 build 的兼容性。
- 增加完整的中英文文档、使用环境变量读取凭据的示例，以及面向任务的安全、可靠性和故障排查指南。
- 发布检查会核对 tag、中英文更新日志和源码版本回退值。只有同时通过元数据与干净 wheel 安装检查的
  wheel/sdist 才会发布到 PyPI，并附加到 GitHub Release。

v1.0.4 (2026-11-13)
-------------------

- 在 WebSocket 连接建立期间发送与浏览器兼容的 User-Agent，以适配拒绝默认 Python 客户端身份的
  Web Terminal 网关。
- 将非零登录响应代码报告为身份验证错误。被截断的成功响应仍视为协议错误。
- 拒绝在活动客户端上重复登录；成功注销会清除保存的重连凭据。将登录、注销和关闭操作串行化，
  并在重试前清理失败的重连尝试。
- 拒绝非零 K 线响应代码、有歧义的响应长度，以及与所选历史 K 线格式不匹配的长度。
  配置 ``rate_bar_format`` 或传入 ``bar_format`` ，以显式选择已知格式。
- 默认保留旧版交易请求布局。新增可选的 ``build_6090`` 配置和 ``TRADE_ACTION_MARKET_DEAL`` ，
  用于所报告的市价执行格式；调用方可显式提供 ``action_id`` 和 ``type_flags`` 。
- 拒绝无效的平仓持仓 ID；无法确定持仓、方向或匹配的交易品种时，停止自动平仓。
- build 6090 配置基于一位贡献者报告的模拟环境观察结果；本版本尚未独立验证经纪商兼容性。

v1.0.3 (2026-11-13)
-------------------

- 以 ``mt5-web-client`` 为名在 PyPI 上发布项目；Python 导入名仍为 ``pymt5`` 。

v1.0.1 (2026-04-02)
--------------------

- 修复报价价格规范化和部分报价更新合并问题。

v1.0.0 (2026-03-21)
--------------------

- **生产稳定版本**
- 修复关键的速率限制器取消安全问题—— ``acquire()`` 现在完全支持安全取消，锁仅在检查令牌时持有
- 修复超时时传输层 future 泄漏问题——现在会在取消时清理待处理的 future
- 修复断开连接回调竞态条件——现在使用 ``asyncio.Lock`` 将 close/recv_loop 的断开连接操作串行化
- 将重连时凭据缺失的 ``assert`` 替换为显式的 ``SessionError``
- 改进凭据清除——丢弃密码前先将其置零
- 修复 ``close()`` 中注销期间静默吞掉错误的问题——现在以 DEBUG 级别记录日志
- 修复报价缓存竞态条件——改用 ``setdefault()`` 以原子方式创建 deque
- 修复回调错误处理程序隔离问题——单个处理程序失败不再中断处理程序链
- 加强订单验证——新增对 MODIFY、REMOVE、SLTP、CLOSE_BY 操作的检查；新增 SL/TP 非负检查和
  交易量溢出防护
- 新增可配置的报价历史限制及 LRU 淘汰机制
  （ ``max_tick_symbols`` 参数、 ``clear_tick_history()`` 方法）
- 清理所有源文件的代码格式
- 1020+ 个离线单元测试，99% 测试覆盖率

v0.9.0 (2026-03-20)
--------------------

- 提取货币 mixin，新增类型化事件、订单管理器、连接池和协议文档

v0.8.0 (2026-03-19)
--------------------

- 重大重构——mixin 架构、自定义异常、99% 测试覆盖率

v0.7.0 (2026-03-17)
-------------------

- 新增与前端一致的开户命令：
  ``request_opening_verification`` （cmd=27 结构化流程）、
  ``submit_opening_verification`` （cmd=40）、
  ``open_demo_account`` （cmd=30）、 ``open_real_account`` （cmd=39）
- 新增 TOTP 管理辅助函数：
  ``enable_otp`` / ``disable_otp`` （cmd=43）
- 新增开户结果数据类和文档负载支持
- 为协议编解码器新增 ``propType=9`` 时间编码支持，用于真实账户出生日期序列化
- 新增实验性 ``send_bootstrap_command_52()`` 辅助函数，对应当前唯一具有可重复线上行为的
  可观察保留命令
- 新增官方 ``MetaTrader5`` 风格的兼容辅助函数，涵盖会话、交易品种、K 线、订单/持仓/历史、DOM
  和 ``order_send()``
- 新增基于本地公式的尽力兼容辅助函数：
  ``last_error()`` 、 ``order_calc_profit()`` 、 ``order_calc_margin()``
- 新增缓存报价历史兼容辅助函数：
  基于观察到的 ``cmd=8`` 推送实现 ``copy_ticks_from()`` 和 ``copy_ticks_range()``
- 新增本地 ``order_check()`` 兼容预检，使用交易品种规则、缓存价格和本地保证金估算
- 扩展 ``cmd=3`` 账户解析，从当前前端账户标头中公开服务器/公司/时区和权限元数据，
  以及交易设置、杠杆规则和佣金表
- 新增保守的 ``terminal_info()`` 兼容辅助函数，根据 ``cmd=3`` 账户/服务器元数据派生
- 新增尽力实现的 ``version()`` 兼容辅助函数，根据 ``cmd=3`` 和观察到的公开 Web Terminal
  构建元数据返回 ``(500, build, release_date)``
- 通过为客户端错误辅助函数和解析后的计划结构添加显式类型，加强 ``client.py`` 的 ``mypy``
  兼容性
- 新增 ``make check`` 和 ``make package-check`` 目标，以镜像主要的本地 CI 预检步骤，
  包括离线 ``build --no-isolation`` 包验证和 ``twine check`` 元数据验证
- 将 ``cmd=18`` 交易品种解析扩展到当前前端模式，包括债券 ``face_value`` /
  ``accrued_interest`` ，以及嵌套的交易设置、计划和订阅部分
- 为交易计算模式 ``37`` 和 ``39`` 新增尽力估算的债券盈亏/保证金公式
- 修复 ``cmd=22`` 订单簿订阅负载，使其与前端的 ``count + symbol_ids`` 格式一致
- 根据 build 5687（构建于 2026-03-15）重新验证公开 Web Terminal 命令接口

v0.5.0 (2026-03-12)
--------------------

- **新命令** ： ``get_account`` （cmd=3）、 ``get_symbol_groups`` （cmd=9）、
  ``get_spreads`` （cmd=20）、 ``subscribe_book`` （cmd=22）、
  ``get_corporate_links`` （cmd=44）
- **新推送处理程序** ： ``on_trade_transaction`` （cmd=10）、
  ``on_account_update`` （cmd=14）、 ``on_symbol_details`` （cmd=17）、
  ``on_trade_result`` （cmd=19）、 ``on_book_update`` （cmd=23）
- **交易** ：全部 9 种订单类型，包括 stop-limit、close-by、modify、cancel
- **TradeResult** 数据类，包含 retcode、description、deal、order、volume、price
- **AccountInfo** 数据类，包含 balance、equity、margin、leverage
- **SymbolInfo** 数据类，包含 name、symbol_id、digits、description
- **交易品种缓存** ： ``load_symbols()`` 、 ``get_symbol_info()`` 、 ``get_symbol_id()``
- **自动重连** ，支持指数退避和凭据复用
- **自动心跳** ，支持配置间隔
- 104 个离线单元测试
- 使用 GitHub Actions 的 CI/CD（Python 3.11/3.12/3.13 × Linux/macOS/Windows）
- Sphinx 文档及 ReadTheDocs 集成

v0.1.0 (2026-03-01)
--------------------

- 初始版本
- 引导握手和 AES-CBC 加密
- 登录、注销、心跳
- 交易品种列表（普通及 gzip 压缩）
- 报价订阅和推送
- 历史 OHLCV K 线
- 持仓和订单检索
- 交易历史（成交）
- 基本交易请求
