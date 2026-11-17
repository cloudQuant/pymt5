配置
====

连接设置
--------

``MT5WebClient`` 构造函数最常用的设置如下：

.. list-table::
   :header-rows: 1
   :widths: 25 18 43

   * - 设置
     - 默认值
     - 用途
   * - ``uri``
     - MT5 Web Terminal URL
     - WebSocket 地址，应使用当前环境获授权的入口。
   * - ``timeout``
     - ``30.0``
     - 命令与初次连接的超时秒数。可能已经发送的请求超时后会隔离连接；
       超时不等于服务器 ACK 或 NACK。
   * - ``heartbeat_interval``
     - ``30.0``
     - 登录后心跳 ping 的间隔秒数（必须为有限正数）。
   * - ``auto_reconnect``
     - ``False``
     - 意外断线后重新连接并登录。
   * - ``max_reconnect_attempts``
     - ``5``
     - 每轮重连的最大尝试次数。
   * - ``reconnect_delay`` / ``max_reconnect_delay``
     - ``3.0`` / ``60.0``
     - 带抖动指数退避的基础值和上限。
   * - ``rate_limit`` / ``rate_burst``
     - ``0`` / ``20``
     - 可选令牌桶命令限流（有限值 ``rate >= 0`` 且 ``burst >= 1``）；零表示不限流。
   * - ``metrics``
     - ``None``
     - 可选 ``MetricsCollector`` 实现。

完整构造函数和缓存设置见 :doc:`api`。

协议兼容设置
------------

历史K线：``rate_bar_format``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``auto`` 仅在响应长度能唯一识别 48 字节标准记录或 56 字节扩展记录时解析。
长度有歧义或不匹配会抛出 ``ProtocolError``。已知服务器布局时可显式选择
``standard`` 或 ``extended``，也可向支持的 rates 方法传入 ``bar_format``。
长度校验不能证明所有 payload 在语义上都正确。

tick 价格：``tick_price_mode`` 和 ``tick_price_modes``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``auto`` 是默认值，保留旧版按品种/幅度判断的启发式行为。这是兼容行为，
  不是通用线格式保证。
- ``scaled`` 将非零价格除以 ``10 ** symbol.digits``。
- ``normalized`` 保留原始 wire 价格。
- ``tick_price_modes={"EURUSD": "scaled"}`` 可覆盖指定品种的全局模式；
  品种键不区分大小写且优先级更高。

同一配置会应用于原始回调、强类型事件和缓存。依赖价格前，应对目标服务器/
build 的小规模授权样本进行验证。可以使用 ``scripts/demo_tick_probe.py``
进行只读探测；其输出需要人工判断，不是自动验收结论。

交易请求：``trade_protocol``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

默认线格式为 ``legacy``。``build_6090`` 显式启用某个环境报告的保留字节和
market-deal 行为。客户端不会根据握手 build 号自动推断该配置。券商、执行
模式和 build 兼容性必须独立验证。

环境变量
--------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - 变量
     - 用途
   * - ``PYMT5_SERVER``
     - 示例和 integration 测试使用的 WebSocket URI。
   * - ``PYMT5_LOGIN`` / ``PYMT5_PASSWORD``
     - 显式启用 demo integration 测试时使用的凭据，绝不能提交其值。
   * - ``PYMT5_TEST_SYMBOL``
     - tick integration/探测使用的活跃、高流动性品种。
   * - ``PYMT5_INTEGRATION=1``
     - 显式启用在线 integration 测试；离线门禁不设置它。
   * - ``PYMT5_LOG_LEVEL``
     - 库日志阈值（默认为 ``WARNING``；未显式配置的 ``structlog`` 会桥接至标准库 ``logging``）。
       采样报告中避免输出凭据/session 等敏感日志。

核心客户端不会自动加载 ``.env``。请使用应用的密钥管理器或显式 dotenv
加载器，并确保本地环境文件已被 Git 忽略。

推荐的初始配置
--------------

.. code-block:: python

   import os
   from pymt5 import MT5WebClient

   client = MT5WebClient(
       uri=os.environ["PYMT5_SERVER"],
       timeout=30.0,
       auto_reconnect=True,
       rate_bar_format="auto",
       tick_price_mode="auto",
       trade_protocol="legacy",
   )

先执行只读查询。只有取得证据后才调整兼容模式；交易前请阅读
:doc:`reliability` 和 :doc:`security`。
