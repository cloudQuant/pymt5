示例
====

仓库的 ``examples/`` 目录包含实际网络演示。它们不是离线示例，除非你有权使用目标
账户，否则绝不能针对该账户运行。请先阅读 :doc:`security`，在模拟账户上从只读
示例开始；不要为了测试连接是否正常而执行交易示例。

.. danger::

   示例 04 和 07 会提交交易，并可能立即修改、撤销或关闭持仓和订单。示例 08 会发送
   服务器通知并提交验证码。运行前必须审查每项操作。这些脚本没有 dry-run 外层保护。

准备凭据与环境
--------------

从 PyPI 安装软件包，或使用可编辑源码安装：

.. code-block:: bash

   python -m pip install mt5-web-client

编号示例文件从进程环境加载 ``SERVER``、``LOGIN`` 和 ``PASSWORD``：

.. code-block:: python

   import os

   SERVER = os.environ["PYMT5_SERVER"]
   LOGIN = int(os.environ["PYMT5_LOGIN"])
   PASSWORD = os.environ["PYMT5_PASSWORD"]

运行示例前必须设置全部三个变量，且 ``PYMT5_LOGIN`` 必须包含整数。示例不会隐式
加载 ``.env`` 文件，因此应通过 shell 导出变量，或通过经过批准的密钥管理系统提供。
绝不能把真实值复制到文档、提交、日志或问题报告中。基本异步生命周期见
:doc:`quickstart`；超时、心跳、重连、K线格式、tick 价格和节流选项见
:doc:`configuration`。

示例还把 ``NO_PROXY`` 和 ``no_proxy`` 设置为 ``*``，这会使进程绕过代理。如果
网络要求代理或安全政策禁止这种绕过，应删除或调整这些行。

示例目录
--------

下列标签描述的是实际改变状态的行为，而不只是文件名。“只读”表示脚本查询数据或
订阅数据流，不提交或修改交易；订阅仍会在服务器会话中创建临时状态。所有示例都
要求已获授权的实际登录。

.. list-table:: 当前示例
   :header-rows: 1
   :widths: 28 22 50

   * - 文件
     - 分类
     - 用途与副作用
   * - ``01_connect_and_account.py``
     - **只读**
     - 连接并登录；加载品种；查询账户详情、持仓、挂单、成交历史并 ping。脚本会打印
       账户和交易历史信息，因此日志属于敏感数据。
   * - ``02_market_data.py``
     - **只读**
     - 查询品种组、缓存品种信息、完整合约规格、点差和 M1/H1/D1 历史K线。服务器特定
       的K线布局可能需要显式配置。
   * - ``03_realtime_ticks.py``
     - **只读**
     - 加载品种，订阅四个硬编码品种，并显示 tick、账户、品种详情、登录状态和交易更新
       回调。脚本不提交交易；市场平静时可能没有 tick。
   * - ``04_trading.py``
     - **交易 / 改变状态**
     - 提交市价买入和卖出、买入限价及卖出止损请求，然后在已有持仓可用时修改其
       SL/TP。即使使用模拟账户，这也会改变真实服务器状态。
   * - ``05_order_book.py``
     - **只读**
     - 为两个硬编码品种订阅订单簿和 tick 流并打印更新。服务器可能不支持 DOM；被捕获
       的订阅错误不能证明支持情况或可用性。
   * - ``06_all_push_notifications.py``
     - **只读**
     - 注册可用的推送回调并订阅 tick/订单簿流。交易相关 handler 只观察收到的帧；
       脚本本身不下单。输出可能包含敏感账户更新。
   * - ``07_all_order_types.py``
     - **交易 / 改变状态**
     - 提交市价、限价、止损、止损限价和原始交易请求；修改 SL/TP；撤销挂单；在有持仓
       时平仓。它既可能操作刚创建的订单，也可能操作账户原有状态。
   * - ``08_misc_features.py``
     - **改变状态（非交易操作）**
     - 查询链接和 trader 参数，启用自动重连，发送服务器通知并提交占位验证码。密码
       修改和模拟开户仅展示用法而跳过执行。通知与验证码调用仍是实际变更，不是只读
       探针。

脚本包含硬编码的观察品种和固定监听时长。应检查每个准确品种是否存在于目标服务器
``load_symbols()`` 的输出中，以及市场是否开市。品种名称、后缀、DOM 支持、交易规则
和响应布局都可能随服务器和 build 而不同。

运行示例
--------

准备凭据并审查分类后，在仓库根目录一次只运行一个选定脚本：

.. code-block:: bash

   python examples/01_connect_and_account.py
   python examples/02_market_data.py
   python examples/03_realtime_ticks.py
   python examples/05_order_book.py
   python examples/06_all_push_notifications.py

一般形式为：

.. code-block:: bash

   python examples/<example-file>.py

从 01 或 02 开始。示例 03、05 和 06 订阅数据流后只等待其配置的时长。在该时段内
没有 tick 或订单簿更新，本身并不能证明客户端有故障。

不要把 04、07 或 08 放入通用的“运行全部示例”命令。如果某项改变状态的演示得到明确
授权，应先检查准确品种、交易量、价格、已有持仓/订单、验证码 payload 和通知内容；
使用已授权模拟账户，并且只运行对应文件。交易请求后出现非零响应、超时、取消或断线，
绝不能触发盲目重试。按照 :doc:`security` 和 :doc:`troubleshooting` 核对服务器状态。

只读线上 tick 诊断
------------------

``scripts/demo_tick_probe.py`` 是独立诊断工具，不属于八个编号示例。对于已授权模拟
账户，它记录原始 cmd=8 tick、缩放价格候选和附近 M1 K线，不会下单。该脚本已经读取
``PYMT5_SERVER``、``PYMT5_LOGIN`` 和 ``PYMT5_PASSWORD``：

.. code-block:: bash

   python scripts/demo_tick_probe.py --symbol EURUSD --seconds 90

使用服务器上准确且活跃的品种。安装 ``python-dotenv`` 后可选择传入
``--env-file .env``；只有已经确定K线布局后，才使用 ``--bar-format standard`` 或
``extended``。脚本输出脱敏 JSON，只有收到样本且退订和关闭都成功时才返回成功。
``no_sample`` 可能只是市场闭市；退订被拒绝仍是错误。报告用于辅助排查，不会自动
选择 tick 价格模式，也不会认证某个服务器 build。

常见调整
--------

- **连接或登录失败：** 核实地址、代理、bootstrap build 和脱敏登录响应码。不要猜测
  未记录代码的含义。
- **没有流数据：** 检查准确品种和交易时段；仅对于在线 integration 测试，可用
  ``PYMT5_TEST_SYMBOL`` 显式选择品种。
- **K线 ``ProtocolError``：** 识别 48 字节或 56 字节布局，不要选择恰好能消除错误的
  选项。
- **价格异常：** 先检查归一化前的线上数值，再选择全局或按品种的
  ``tick_price_mode``。
- **变更操作后超时：** 把结果视为未知而不是已拒绝；不要自动重新运行脚本。

诊断方法见 :doc:`troubleshooting`，方法签名见 :doc:`api`，已观测布局和关联限制见
:doc:`protocol_reference`，版本特定兼容性变化见 :doc:`changelog`。
