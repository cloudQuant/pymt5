协议参考
========

本文档描述根据官方 MetaTrader 5 Web Terminal JavaScript 客户端逆向工程得出的 MT5 WebSocket
二进制协议。除非另有说明，所有字节序均为 **小端序** 。

概述
----

MT5 Web Terminal 通过单个 WebSocket 连接（默认为 ``wss://web.metatrader.app/terminal`` ）
与交易服务器通信。每个 WebSocket 消息都是一个 **二进制帧** ，由以下部分组成：

1. 一个 8 字节外层标头。
2. 一个包含命令帧的 AES-CBC 加密正文。

该协议严格采用请求/响应模式，此外还支持由服务器发起的 **推送通知** ，用于实时数据
（报价、订单簿、交易更新、账户变更）。

帧格式
------

外层帧
~~~~~~

每个 WebSocket 消息都有一个固定的 8 字节标头，后接加密正文::

    +-----------+-----------+---------------------------+
    | 偏移      | 大小      | 说明                      |
    +-----------+-----------+---------------------------+
    | 0         | 4 字节    | body_length (uint32 LE)   |
    | 4         | 4 字节    | version (uint32 LE = 1)   |
    | 8         | N 字节    | 加密正文                  |
    +-----------+-----------+---------------------------+

- ``body_length`` 是标头之后的字节数（即 ``len(encrypted_body)`` ）。
- 客户端出站帧的 ``version`` 为 ``1`` （ ``OUTER_PROTOCOL_VERSION`` ），而观察到的
  build 6320 服务器响应中该字段为 ``0`` ； ``unpack_outer`` 接受 ``{0, 1}`` ，并
  以 ``ProtocolError`` 拒绝其他版本。

外层帧由 :func:`pymt5.protocol.pack_outer` 构造，并由 :func:`pymt5.protocol.unpack_outer` 解析。

内部命令帧
~~~~~~~~~~

解密后，内部正文具有以下布局::

    +-----------+-----------+---------------------------+
    | 偏移      | 大小      | 说明                      |
    +-----------+-----------+---------------------------+
    | 0         | 2 字节    | 随机前缀 (uint16)         |
    | 2         | 2 字节    | command_id (uint16 LE)    |
    | 4         | N 字节    | 命令负载                  |
    +-----------+-----------+---------------------------+

**请求帧** （客户端到服务器）：

- 字节 0-1 为随机值（由 ``os.urandom(2)`` 生成），提供最低限度的重放抵抗能力。
- 字节 2-3 是标识操作的 ``command_id`` 。
- 字节 4 及之后是序列化的命令负载（可以为空）。

**响应帧** （服务器到客户端）：

- 字节 0-1 包含一个双字节前缀，解析器目前会将其丢弃。2026-11-14 针对 build 6320
  进行的一次经过清理的只读抽样发现，在命令 0、3、6、9、20、41、44 和 51 的 10 个响应帧中，
  请求前缀与响应前缀均不匹配。一个 ``cmd=6`` 请求产生了两个响应帧。这是反驳回显假设的
  否定性证据，而不是适用于每个服务器构建的证明；客户端不依赖此前缀进行关联。
- 字节 2-3 是 ``command_id`` 。
- 字节 4 是 ``response_code`` （0 = 成功）。
- 字节 5 及之后是响应正文。

相同命令的请求会被串行化。如果可能已经发送，但由于超时、取消或发送失败而导致结果不确定，
则会隔离该连接，其他进行中的请求也会失败；后续请求不能复用该连接。纯主动推送命令绝不会
完成挂起请求 future，且共享命令可附加可选 ``response_predicate`` 进行结构校验。这些保护
可防范观察到的迟到响应序列与明显不匹配帧，但对于满足结构检查且共享同一命令 ID 的重复响应
或推送，仍无法做到完全区分。已超时或已取消交易在服务器端的结果仍然未知，在任何重试之前都
必须先核对。

密钥交换
--------

连接以 **使用初始硬编码 AES 密钥加密的引导阶段** 开始，此时尚未协商会话密钥。
初始加密器通过简单的字符移位算法解码 ``constants.py`` 中的
``INITIAL_KEY_OBFUSCATED`` 。

引导流程 (cmd=0)
~~~~~~~~~~~~~~~~

1. 客户端连接到 WebSocket 端点。
2. 客户端发送 ``CMD_BOOTSTRAP`` (cmd=0)，以 64 字节零令牌作为负载，并使用初始
   （硬编码）AES 密钥加密。
3. 服务器响应：

   - 成功时为 ``code=0`` 。
   - 响应正文布局::

       +--------+--------+---------------------------------+
       | 偏移   | 大小   | 说明                            |
       +--------+--------+---------------------------------+
       | 0      | 2      | 保留                            |
       | 2      | 64     | 会话令牌                        |
       | 66     | 16/24  | AES 会话密钥（128 或 192 位）   |
       +--------+--------+---------------------------------+

4. 客户端校验提取的会话密钥长度为 16、24 或 32 字节（否则抛出 ``MT5ConnectionError`` ），
   并用根据会话密钥初始化的新 ``AESCipher`` 替换初始加密器。所有后续消息均使用此会话密钥。

AES-CBC 加密
~~~~~~~~~~~~

所有帧（外层标头之后）均使用 AES-CBC 加密：

- **模式** ：CBC（密码分组链接）。
- **IV** ：始终为 16 个零字节（ ``b"\x00" * 16`` ）。
- **填充** ：PKCS7，块大小为 128 位。
- **密钥大小** ：128 位（16 字节）或 192 位（24 字节），由服务器在引导期间提供。

:class:`pymt5.crypto.AESCipher` 类封装了 ``cryptography`` 库的原语。

会话生命周期
------------

以下文本图展示了典型的连接状态转换::

    DISCONNECTED
        |
        v
    CONNECTING ---- WebSocket 已打开 ---->  （发送 bootstrap cmd=0）
        |
        | （接收 bootstrap 响应，提取会话密钥）
        v
      READY ---- （可选）cmd=29 init_session ---->
        |
        | cmd=28 login
        v
    AUTHENTICATED ---- 交易、数据订阅 ---->
        |
        | cmd=2 logout（或断开连接）
        v
    DISCONNECTED

传输状态机
~~~~~~~~~~

:class:`pymt5.transport.TransportState` 枚举跟踪以下状态：

- ``DISCONNECTED`` -- 没有活动连接。
- ``CONNECTING`` -- WebSocket 正在打开，并执行引导握手。
- ``READY`` -- 引导完成，会话密钥已交换，接受命令。
- ``CLOSING`` -- 已启动正常关闭。
- ``ERROR`` -- 意外断开连接或协议错误。

意外断开连接时，如果启用了 ``auto_reconnect`` ，客户端会进入采用指数退避和抖动的重连循环：
``min(base_delay * 2^(attempt-1) + random(0, base_delay), max_delay)`` 。

命令 ID 目录
------------

下表列出 ``pymt5/constants.py`` 中定义的所有命令 ID。标记为“推送”的命令是由服务器发起的通知。

.. list-table:: 命令 ID
   :header-rows: 1
   :widths: 10 30 60

   * - ID
     - 常量
     - 说明
   * - 0
     - ``CMD_BOOTSTRAP``
     - 引导握手：交换会话令牌和 AES 密钥。
   * - 2
     - ``CMD_LOGOUT``
     - 结束经过身份验证的会话。
   * - 3
     - ``CMD_GET_ACCOUNT``
     - 获取账户信息（余额、净值、保证金、杠杆）。
   * - 4
     - ``CMD_GET_POSITIONS_ORDERS``
     - 获取未平仓持仓和挂单。
   * - 5
     - ``CMD_GET_TRADE_HISTORY``
     - 获取历史成交。
   * - 6
     - ``CMD_GET_SYMBOLS``
     - 获取基础交易品种列表（名称、ID、小数位数、路径）。
   * - 7
     - ``CMD_SUBSCRIBE_TICKS``
     - 订阅指定交易品种 ID 的实时报价数据。
   * - 8
     - ``CMD_TICK_PUSH``
     - **推送** ：已订阅交易品种的实时报价数据。
   * - 9
     - ``CMD_GET_SYMBOL_GROUPS``
     - 获取交易品种组名称。
   * - 10
     - ``CMD_TRADE_UPDATE_PUSH``
     - **推送** ：交易状态变更（余额更新或订单事务）。
   * - 11
     - ``CMD_GET_RATES``
     - 获取指定交易品种和时间框架的 OHLCV K 线。
   * - 12
     - ``CMD_TRADE_REQUEST``
     - 提交交易请求（市价、挂单、修改、平仓）。
   * - 13
     - ``CMD_SYMBOL_UPDATE_PUSH``
     - **推送** ：交易品种属性变更（点差、交易时段等）。
   * - 14
     - ``CMD_ACCOUNT_UPDATE_PUSH``
     - **推送** ：账户余额/保证金/净值变更。
   * - 15
     - ``CMD_LOGIN_STATUS_PUSH``
     - **推送** ：登录状态变更（强制注销、会话过期）。
   * - 17
     - ``CMD_SYMBOL_DETAILS_PUSH``
     - **推送** ：扩展交易品种行情数据；布局因构建版本而异。
   * - 18
     - ``CMD_GET_FULL_SYMBOLS``
     - 获取完整的交易品种规格（合约详情）。
   * - 19
     - ``CMD_TRADE_RESULT_PUSH``
     - **推送** ：异步交易执行结果。
   * - 20
     - ``CMD_GET_SPREADS``
     - 获取交易品种的点差数据。
   * - 22
     - ``CMD_SUBSCRIBE_BOOK``
     - 订阅指定交易品种 ID 的订单簿（市场深度）。
   * - 23
     - ``CMD_BOOK_PUSH``
     - **推送** ：已订阅交易品种的订单簿更新。
   * - 24
     - ``CMD_CHANGE_PASSWORD``
     - 修改账户密码。
   * - 27
     - ``CMD_VERIFY_CODE``
     - 验证电子邮件/电话确认码。
   * - 28
     - ``CMD_LOGIN``
     - 使用登录名、密码和可选 OTP 进行身份验证。
   * - 29
     - ``CMD_INIT``
     - 初始化会话（cmd=28 之前的登录握手）。
   * - 30
     - ``CMD_OPEN_DEMO``
     - 开设模拟账户。
   * - 34
     - ``CMD_GET_SYMBOLS_GZIP``
     - 获取交易品种（gzip 压缩变体）。
   * - 39
     - ``CMD_OPEN_REAL``
     - 开设真实（实盘）交易账户。
   * - 40
     - ``CMD_SEND_VERIFY_CODES``
     - 请求发送电子邮件/电话验证码。
   * - 41
     - ``CMD_TRADER_PARAMS``
     - 获取交易者参数字符串。
   * - 42
     - ``CMD_NOTIFY``
     - 获取服务器通知。
   * - 43
     - ``CMD_OTP_SETUP``
     - 设置或管理 OTP（一次性密码 / 双因素身份验证）。
   * - 44
     - ``CMD_GET_CORPORATE_LINKS``
     - 获取公司链接（帮助、支持 URL）。
   * - 51
     - ``CMD_PING``
     - 心跳 / 保活 ping。

字段类型编码
------------

:class:`pymt5.protocol.SeriesCodec` 根据 ``PROP_*`` 类型常量序列化和反序列化字段。
所有多字节整数和浮点数均使用 **小端** 字节序。

.. list-table:: 字段类型
   :header-rows: 1
   :widths: 15 10 10 10 55

   * - 常量
     - ID
     - 大小
     - 字节序
     - 说明
   * - ``PROP_I8``
     - 1
     - 1 字节
     - 不适用
     - 有符号 8 位整数（ ``struct "<b"`` ）。
   * - ``PROP_I16``
     - 2
     - 2 字节
     - LE
     - 有符号 16 位整数（ ``struct "<h"`` ）。
   * - ``PROP_I32``
     - 3
     - 4 字节
     - LE
     - 有符号 32 位整数（ ``struct "<i"`` ）。
   * - ``PROP_U8``
     - 4
     - 1 字节
     - 不适用
     - 无符号 8 位整数（ ``struct "<B"`` ）。
   * - ``PROP_U16``
     - 5
     - 2 字节
     - LE
     - 无符号 16 位整数（ ``struct "<H"`` ）。
   * - ``PROP_U32``
     - 6
     - 4 字节
     - LE
     - 无符号 32 位整数（ ``struct "<I"`` ）。
   * - ``PROP_F32``
     - 7
     - 4 字节
     - LE
     - IEEE 754 单精度浮点数（ ``struct "<f"`` ）。
   * - ``PROP_F64``
     - 8
     - 8 字节
     - LE
     - IEEE 754 双精度浮点数（ ``struct "<d"`` ）。
   * - ``PROP_TIME``
     - 9
     - 8 字节
     - LE
     - Windows FILETIME（自 1601-01-01 起的 100ns 滴答数）。编解码器会将其与 Unix
       毫秒相互转换。
   * - ``PROP_STRING``
     - 10
     - N 字节
     - 不适用
     - 定长 ASCII 字符串（需要 ``propLength`` ）。以空字节填充，按 Latin-1 解码。
   * - ``PROP_FIXED_STRING``
     - 11
     - N 字节
     - LE
     - 定长 UTF-16LE 字符串（需要 ``propLength`` ）。以空字节填充，在第一对空字符处终止。
   * - ``PROP_BYTES``
     - 12
     - N 字节
     - 不适用
     - 原始字节缓冲区（固定大小字段需要 ``propLength`` ）。指定 ``propLength`` 时以空字节填充。
   * - ``PROP_I64``
     - 17
     - 8 字节
     - LE
     - 有符号 64 位整数（ ``int.from_bytes(..., signed=True)`` ）。
   * - ``PROP_U64``
     - 18
     - 8 字节
     - LE
     - 无符号 64 位整数（ ``int.from_bytes(..., signed=False)`` ）。

完整交易品种查询 (cmd=18)
-------------------------

在观察到的 build 6320 服务器上，请求由一个小端 ``uint32`` 计数和紧随其后、数量相同的
小端 ``uint32`` 交易品种 ID 组成。响应包含一个计数和随后的完整交易品种记录。公开的
``get_full_symbol_info(name)`` 会先解析名称，再请求一个 ID；它会拒绝非零代码、无效长度和
不匹配的记录。

交易品种详情通知 (cmd=17)
-------------------------

Build 6320 发送连续的 274 字节记录，不带计数前缀。最前面的字段依次为一个 ``uint32``
交易品种 ID、一个 ``int64`` 字段掩码、一个以秒为单位的 ``int32`` 时间戳、一个未知的
``uint32`` ，以及九个 ``float64`` 值：bid 最新/最高/最低、ask 最新/最高/最低，以及
last 最新/最高/最低。只有掩码位已设置的值才应解释为更新。这些价格是原始线路值；Web
客户端会按交易品种的小数位数对其进行缩放，小数位数上限为八位。解析器仅对观察到的
build 6320 使用此布局；未知构建保留旧版布局。未识别的字段不会标记为希腊字母。

报价推送通知 (cmd=8)
--------------------

通过 ``CMD_SUBSCRIBE_TICKS`` (cmd=7) 订阅交易品种后，服务器会以 ``CMD_TICK_PUSH`` (cmd=8)
推送通知的形式流式发送报价更新。

报价正文格式
~~~~~~~~~~~~

Build 6320 Web Terminal 解析器将正文视为连续的固定大小记录，不带计数前缀。当前客户端使用
相同的布局::

    +--------+--------+---------------------------+
    | 偏移   | 大小   | 说明                      |
    +--------+--------+---------------------------+
    | 0      | 50 * N | 报价记录                  |
    +--------+--------+---------------------------+

每条报价记录（50 字节）均遵循 ``TICK_SCHEMA`` 。此布局基于公开的 Web Terminal 实现和离线测试；
尚未使用实时报价样本验证它是否适用于模拟服务器或其他构建：

.. list-table:: 报价记录字段
   :header-rows: 1
   :widths: 5 15 15 65

   * - #
     - 字段
     - 类型
     - 说明
   * - 0
     - symbol_id
     - U32
     - 交易品种标识符。
   * - 1
     - tick_time
     - I32
     - 报价时间，Unix 秒。
   * - 2
     - fields
     - U32
     - 指示已填充哪些字段的位掩码。
   * - 3
     - bid
     - F64
     - 最佳买价。
   * - 4
     - ask
     - F64
     - 最佳卖价。
   * - 5
     - last
     - F64
     - 最新成交价。
   * - 6
     - tick_volume
     - I64
     - 报价交易量。
   * - 7
     - time_ms_delta
     - U32
     - 相对于 ``tick_time`` 的毫秒偏移量。
   * - 8
     - flags
     - U16
     - 报价标志（买价变更、卖价变更等）。

订单簿推送通知 (cmd=23)
-----------------------

通过 ``CMD_SUBSCRIBE_BOOK`` (cmd=22) 订阅交易品种后，服务器会以 ``CMD_BOOK_PUSH`` (cmd=23)
推送通知的形式流式发送订单簿更新。

订单簿正文格式
~~~~~~~~~~~~~~

响应正文包含一个或多个交易品种订单簿条目，每个条目均由一个标头以及随后的买价和卖价档位数组组成::

    +--------+--------+------------------------------------------+
    | 偏移   | 大小   | 说明                                     |
    +--------+--------+------------------------------------------+
    | 0      | 22     | 订单簿标头 (BOOK_HEADER_SCHEMA)          |
    | 22     | 16 * B | 买价档位（B = 标头中的 bid_count）       |
    | ...    | 16 * A | 卖价档位（A = 标头中的 ask_count）       |
    +--------+--------+------------------------------------------+

**订单簿标头** （22 字节）：

.. list-table:: 订单簿标头字段
   :header-rows: 1
   :widths: 5 15 10 70

   * - #
     - 字段
     - 类型
     - 说明
   * - 0
     - symbol_id
     - U32
     - 交易品种标识符。
   * - 1
     - field1
     - I32
     - 保留字段。
   * - 2
     - field2
     - I32
     - 保留字段。
   * - 3
     - bid_count
     - U32
     - 后续买价档位的数量。
   * - 4
     - ask_count
     - U32
     - 后续卖价档位的数量。
   * - 5
     - flags
     - U16
     - 订单簿更新标志。

**订单簿档位** （每个 16 字节）：

.. list-table:: 订单簿档位字段
   :header-rows: 1
   :widths: 5 15 10 70

   * - #
     - 字段
     - 类型
     - 说明
   * - 0
     - price
     - F64
     - 价格档位。
   * - 1
     - volume
     - I64
     - 此价格档位的交易量。

错误代码
--------

交易请求 (cmd=12) 在响应中返回一个指示成功或失败的 ``retcode`` 。下表列出所有
``TRADE_RETCODE_*`` 常量：

.. list-table:: 交易返回代码
   :header-rows: 1
   :widths: 10 25 65

   * - 代码
     - 常量
     - 说明
   * - 10004
     - ``TRADE_RETCODE_REQUOTE``
     - 重新报价。
   * - 10006
     - ``TRADE_RETCODE_REJECT``
     - 请求被拒绝。
   * - 10007
     - ``TRADE_RETCODE_CANCEL``
     - 请求被交易者取消。
   * - 10008
     - ``TRADE_RETCODE_PLACED``
     - 订单已下达。
   * - 10009
     - ``TRADE_RETCODE_DONE``
     - 请求已完成。
   * - 10010
     - ``TRADE_RETCODE_DONE_PARTIAL``
     - 请求已部分完成。
   * - 10011
     - ``TRADE_RETCODE_ERROR``
     - 请求处理错误。
   * - 10012
     - ``TRADE_RETCODE_TIMEOUT``
     - 请求因超时被取消。
   * - 10013
     - ``TRADE_RETCODE_INVALID``
     - 无效请求。
   * - 10014
     - ``TRADE_RETCODE_INVALID_VOLUME``
     - 无效交易量。
   * - 10015
     - ``TRADE_RETCODE_INVALID_PRICE``
     - 无效价格。
   * - 10016
     - ``TRADE_RETCODE_INVALID_STOPS``
     - 无效止损止盈。
   * - 10017
     - ``TRADE_RETCODE_TRADE_DISABLED``
     - 交易已禁用。
   * - 10018
     - ``TRADE_RETCODE_MARKET_CLOSED``
     - 市场已关闭。
   * - 10019
     - ``TRADE_RETCODE_NO_MONEY``
     - 资金不足。
   * - 10020
     - ``TRADE_RETCODE_PRICE_CHANGED``
     - 价格已变更。
   * - 10021
     - ``TRADE_RETCODE_PRICE_OFF``
     - 没有可用于处理请求的报价。
   * - 10022
     - ``TRADE_RETCODE_INVALID_EXPIRATION``
     - 无效的订单到期时间。
   * - 10023
     - ``TRADE_RETCODE_ORDER_CHANGED``
     - 订单状态已变更。
   * - 10024
     - ``TRADE_RETCODE_TOO_MANY_REQUESTS``
     - 请求过多。
   * - 10025
     - ``TRADE_RETCODE_NO_CHANGES``
     - 请求中没有变更。
   * - 10026
     - ``TRADE_RETCODE_SERVER_DISABLES_AT``
     - 服务器已禁用自动交易。
   * - 10027
     - ``TRADE_RETCODE_CLIENT_DISABLES_AT``
     - 客户端已禁用自动交易。
   * - 10028
     - ``TRADE_RETCODE_LOCKED``
     - 请求已锁定，等待处理。
   * - 10029
     - ``TRADE_RETCODE_FROZEN``
     - 订单/持仓已冻结。
   * - 10030
     - ``TRADE_RETCODE_INVALID_FILL``
     - 无效的订单成交类型。
   * - 10031
     - ``TRADE_RETCODE_CONNECTION``
     - 未连接到交易服务器。
   * - 10032
     - ``TRADE_RETCODE_ONLY_REAL``
     - 仅实盘账户允许执行此操作。
   * - 10033
     - ``TRADE_RETCODE_LIMIT_ORDERS``
     - 已达到挂单数量上限。
   * - 10034
     - ``TRADE_RETCODE_LIMIT_VOLUME``
     - 已达到交易品种的交易量上限。
   * - 10035
     - ``TRADE_RETCODE_INVALID_ORDER``
     - 无效或禁止的订单类型。
   * - 10036
     - ``TRADE_RETCODE_POSITION_CLOSED``
     - 持仓已平仓。

交易量编码
----------

MT5 使用整数交易量编码：线路值为 ``volume * 10000`` 。例如，0.01 手传输为 ``100`` ，
1.0 手传输为 ``10000`` 。pymt5 客户端在交易辅助方法中透明地处理此转换。

K 线格式 (cmd=11)
-----------------

观察到 ``CMD_GET_RATES`` (cmd=11) 返回的历史 OHLCV K 线采用以下每条 48 字节的布局。
某些服务器可能使用 56 字节扩展布局，其中额外包含一个 64 位 ``real_volume`` 字段。
当响应长度同时符合两种布局时，默认 ``auto`` 模式会引发 ``ProtocolError`` ，而不是猜测；
确定服务器的格式后，请在客户端上设置 ``rate_bar_format`` ，或向 K 线方法传入
``bar_format`` 。长度不能被所选记录大小整除时会被拒绝；长度检查无法检测恰好符合某种
受支持格式的截断。

请求的 ``from``/``to`` 字段和返回的 K 线 ``time`` 均为有符号 I32 Unix 秒。此线路布局
无法表示 2038-01-19 03:14:07 UTC 之后的日期。客户端保留观察到的布局，而不是静默拓宽
这些字段。

.. list-table:: K 线字段
   :header-rows: 1
   :widths: 5 15 10 70

   * - #
     - 字段
     - 类型
     - 说明
   * - 0
     - time
     - I32
     - K 线开盘时间（Unix 秒）。
   * - 1
     - open
     - F64
     - 开盘价。
   * - 2
     - high
     - F64
     - 最高价。
   * - 3
     - low
     - F64
     - 最低价。
   * - 4
     - close
     - F64
     - 收盘价。
   * - 5
     - tick_volume
     - I64
     - K 线周期内的报价交易量。
   * - 6
     - spread
     - I32
     - K 线收盘时的点差。
