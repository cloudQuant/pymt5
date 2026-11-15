故障排查
========

按层次逐项诊断：Python 环境、WebSocket bootstrap、登录、服务器响应，最后才是协议
解析。前一层成功并不能验证后一层。初次测试应保持只读，并按照 :doc:`security`
使用已获授权的模拟账户。

安装与 Python 版本
------------------

pymt5 要求 Python 3.11 或更高版本；软件包元数据和 CI 覆盖 Linux、macOS、Windows
上的 Python 3.11、3.12 和 3.13。PyPI 发布包名为 ``mt5-web-client``，Python 导入
包名为 ``pymt5``：

.. code-block:: bash

   python --version
   python -m pip install mt5-web-client
   python -c "import pymt5; print(pymt5.MT5WebClient.__name__)"

使用 ``python -m pip``，确保安装和导入使用同一个解释器。如果安装后导入失败，应检查
当前虚拟环境和 IDE 解释器，而不是反复安装到不同环境。对于源码检出，在仓库根目录
使用 ``python -m pip install -e ".[dev,docs]"``。

依赖安装失败时，应结合完整的包管理器错误、Python 版本、操作系统和架构进行排查。
不要为了绕过问题而禁用 TLS 验证或接受不可信软件包。运行时依赖为 ``websockets``
和 ``cryptography``；Sphinx 与测试工具属于可选 extras。

连接与 bootstrap 失败
---------------------

正常顺序是先 ``connect()``，再 ``login()``。``connect()`` 打开 WebSocket 并执行
加密 bootstrap/密钥交换，但不会认证账户。

出现 ``MT5ConnectionError``、``MT5TimeoutError``、``OSError`` 或与 bootstrap 相关
的 ``ProtocolError`` 时，检查：

- 使用准确的已授权 ``wss://`` Web Terminal 地址。默认值为
  ``wss://web.metatrader.app/terminal``；不要把它与券商普通网站或桌面终端地址混淆。
- 确认 DNS、出站 WebSocket/TLS 访问、防火墙规则以及任何必需的企业代理。示例把
  ``NO_PROXY`` 和 ``no_proxy`` 设置为 ``*``；如果网络要求代理，应审查或删除这种
  绕过设置。
- 检查超时是否足以覆盖网络和 bootstrap 延迟。增加超时可能有助于较慢的只读握手，
  但并不会让重试交易变得安全。
- 区分 WebSocket 连接被拒绝与 bootstrap 响应或帧格式问题。bootstrap 成功后记录
  ``client.server_build``；build 为空或为零不能作为后续登录成功的证据。
- 在会话已失效或未完全关闭后创建新客户端。有关 close 或 reconnect cleanup 的
  ``SessionError`` 表示上一代连接仍在退出，并不表示凭据错误。

基本生命周期见 :doc:`quickstart`；URI、超时、心跳和重连设置见 :doc:`configuration`。

登录错误与 build 特定 payload
-----------------------------

cmd=28 登录响应非零时会抛出 ``AuthenticationError``，其中包含数字响应码。pymt5
目前只报告该数字，没有定义登录响应码到业务含义的完整映射。在没有该服务器的权威
证据之前，不要假设某个数字一定表示密码错误、缺少 OTP、build 不受支持或任何其他
具体原因。

修改凭据或 payload 选项之前：

1. 核实已授权地址、数字 login、密码、账户类型以及账户是否需要 OTP。绝不能把这些
   秘密写入问题报告。
2. 记录 bootstrap 的 ``client.server_build`` 和已安装的 pymt5 版本。
3. 区分非零响应（``AuthenticationError``）与成功响应正文截断或无效
   （``ProtocolError``）。后者是 payload/解析器兼容性问题，不是认证有效的证据。
4. 如果客户端提示已经登录，在同一客户端再次登录前应调用 ``logout()``。成功退出
   会清除保存的重连凭据；退出被拒绝会抛出 ``SessionError``，并保留会话状态。

当前登录 payload 末尾带有一个在 Web Terminal build 6320 上观察到的额外
``PROP_U64`` 字段。这是 build 特定的兼容性观测，不是通用规则，也不能用来解释任意
登录响应码。不要凭猜测添加 ``init_session()``、改变 ``version`` 或 ``cid``，或者
删除字段。应比较已授权终端流程，并报告观测到的 build、payload 长度和脱敏结果。
不得包含密码、CID、令牌、session ID 或经过认证的原始帧。

交易专用的 ``trade_protocol="build_6090"`` 配置与登录认证无关，不能用作登录问题的
变通办法。各版本的兼容性说明见 :doc:`changelog`。

订阅后没有 tick
---------------

订阅响应成功只确认服务器接受了请求，不保证观察窗口内一定出现 tick。

- 先调用 ``load_symbols()``，使用该服务器返回的准确名称。券商后缀和可用品种可能不同。
- 检查所选市场是否开市。周末、节假日、流动性不足的 instrument 和闭市时段都可能
  合理地没有 tick。
- 在订阅前注册回调，在活跃交易时段内留出足够时间，并确认回调过滤的是目标
  ``symbol_id``。
- 不要把空 tick 历史当作解析器缺陷的证明。已观测 tick 布局和价格归一化仍有
  服务器/build 兼容性边界，详见 :doc:`protocol_reference`。

对于可选在线 integration 测试，``PYMT5_TEST_SYMBOL`` 从已加载的服务器列表中选择
准确品种；否则测试选择第一个品种，而该品种可能并不活跃。这个变量供 integration
测试使用，``examples/`` 中的脚本不会读取它。测试在 30 秒没有 tick 后会跳过，因为
市场可能闭市；跳过并不等于 tick 格式已经验证。

对于单独授权的模拟账户排查，``scripts/demo_tick_probe.py`` 可以采集归一化前的线上
tick 和附近 M1 K线，不会下单。其 JSON 会区分没有样本、解析错误、K线查询、退订和
关闭结果。使用任何在线脚本前先阅读 :doc:`examples`。

历史K线格式有歧义
-----------------

已观测K线记录为 48 字节（标准）或 56 字节（扩展，包含 ``real_volume``）。在默认
``bar_format="auto"`` 模式下，非零响应、长度可同时被两种记录大小整除，或不能被
任意一种整除，都会抛出 ``ProtocolError``。这是刻意行为：客户端拒绝猜测。

确定目标服务器格式后，可以构造 ``MT5WebClient(rate_bar_format="standard")`` 或
``"extended"``，也可以向 ``get_rates()`` 或 ``copy_rates_*()`` 封装传入
``bar_format=``。如果只是为了让异常消失而选择格式，可能会静默解码出错误数据。
``get_rates_raw()`` 可辅助已获授权的排查，但长度本身不能解决有歧义的布局，长度检查
也无法发现所有截断。详见 :doc:`configuration` 和 :doc:`protocol_reference`。

全部 tick 退订被拒绝
--------------------

服务器会用每个 cmd=7 请求替换 tick 订阅集合。因此，移除本地已知的最后一个品种时，
客户端发送四字节零计数。在观测到的 build 6320 会话中，全部退订被响应 ``code=1``
拒绝。该代码的业务含义以及所需的替代 payload 均未确定；不要把它重新标记为一般
成功、权限错误或速率限制。

当前 ``unsubscribe_ticks()`` 的行为是有意设计的：

- 非零响应抛出 ``ProtocolError``。
- 保留最后确认的本地订阅集合，以便稍后重试或重连恢复。
- 请求被拒绝时，绝不能把清理报告为成功。

分别检查部分退订和全部退订，并检查脱敏后的数字响应。不要忽略异常，也不要在零计数
payload 和空 payload 之间随意切换。如果无法确认退订，关闭连接会结束该客户端会话的
网络流，但不能证明服务器此前处理了哪个确认。报告问题时应保留清理错误。

超时、连接隔离与重连
--------------------

一旦可能已经发送，超时、取消或发送错误都会使结果不确定。传输层随后使该连接失效并
隔离它，以 ``SessionError`` 使其他在途请求失败，并阻止后续命令复用该连接代际。
因此，迟到帧不能被安全地当作后续请求的应答。

默认 ``auto_reconnect=False``，由应用程序控制恢复。启用 ``auto_reconnect=True``
且存在已保存登录凭据时，意外断线会启动有上限的指数退避和抖动，创建新传输层、重新
登录，并恢复已确认的 tick/订单簿订阅意图。重连不会重放被中断请求，也不会揭示其结果。

只读查询应等新会话就绪后再考虑重试，并考虑先前响应是否可能有歧义。交易绝不能盲目
重试：先按照 :doc:`security` 核对持仓、订单和成交/订单历史。重复出现的清理或代际
``SessionError`` 应作为生命周期问题排查，而不是用紧密重连循环处理。

日志与脱敏问题报告
------------------

在启动进程或导入 pymt5 之前设置 ``PYMT5_LOG_LEVEL=DEBUG``，以捕获库诊断信息。
使用标准库 logging 回退时，还需配置 handler 和格式，例如：

.. code-block:: python

   import logging
   import os

   os.environ["PYMT5_LOG_LEVEL"] = "DEBUG"  # before importing pymt5
   logging.basicConfig(level=logging.DEBUG)

   from pymt5 import MT5WebClient  # noqa: E402

只在有界且已获授权的复现期间启用 DEBUG。目前 INFO 登录消息包含数字 login 和
session ID，URI、账户、回调或应用程序输出还可能包含其他私有数据。

在 `https://github.com/cloudQuant/pymt5/issues <https://github.com/cloudQuant/pymt5/issues>`_
创建 issue 前，删除或遮盖：

- 密码、OTP、令牌、session ID、CID、login/账号数字和 cookie；
- 账户余额、持仓、订单、成交、私有品种清单以及经过认证的原始帧；
- 私有 Web Terminal JavaScript、生成的分析摘录、可能暴露身份的本地路径，以及任何
  无权再分发的第三方材料。

有用的脱敏报告应包含 pymt5 和 Python 版本、操作系统、服务器 build、公开方法和命令
ID、异常类型、数字响应码/retcode、UTC 时间线、删除秘密后的配置名称，以及尽可能
最小的离线复现。说明市场是否开市、是否启用重连，以及操作是否只读。未知的协议含义
应明确保持未知。API 签名见 :doc:`api`；版本特定行为见 :doc:`changelog`。
