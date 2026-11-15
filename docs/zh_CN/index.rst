pymt5 中文文档
==============

**pymt5** 是一个面向 MetaTrader 5 Web Terminal 的异步 Python 客户端。
它通过逆向分析得到的加密 WebSocket 二进制协议连接服务器，提供账户查询、
行情数据、流式回调和交易辅助接口。

PyPI 发布包名为 ``mt5-web-client``，Python 导入名为 ``pymt5``。
运行环境要求 Python 3.11 或更高版本。

.. warning::

   本项目是非官方、实验性的客户端，与 MetaQuotes 没有隶属或背书关系。
   协议行为可能随券商、服务器 build、交易品种和交易时段而变化。请先使用
   已获授权的模拟账户验证所有必需流程；请求超时绝不能被当作交易未执行的证明。

按任务阅读
----------

.. list-table::
   :header-rows: 1
   :widths: 24 48 28

   * - 目标
     - 首先阅读
     - 继续阅读
   * - 安装并完成第一次只读连接
     - :doc:`installation` 和 :doc:`quickstart`
     - :doc:`configuration`
   * - 获取K线，订阅 tick 或订单簿
     - :doc:`market_data`
     - :doc:`push_notifications`
   * - 在已授权的模拟账户中使用交易接口
     - :doc:`trading`
     - :doc:`security` 和 :doc:`reliability`
   * - 处理断线、超时和服务器拒绝
     - :doc:`reliability`
     - :doc:`troubleshooting`
   * - 查询方法、命令或兼容接口
     - :doc:`api`
     - :doc:`protocol_reference` 和 :doc:`python_api_compat`
   * - 运行测试或改进文档
     - :doc:`development`
     - :doc:`changelog`

快速安装
--------

.. code-block:: bash

   python -m pip install mt5-web-client

第一次连接应只读取账户与交易品种信息，不应下单。完整的环境变量示例见
:doc:`quickstart`。

能力与边界
----------

- 异步 WebSocket 生命周期、bootstrap 密钥交换、登录、心跳和可选自动重连。
- 账户、持仓、订单、成交、交易品种、历史K线、tick 和订单簿 API。
- 原始与强类型推送回调、本地缓存、可选指标和结构化日志。
- 高层交易辅助方法以及更底层的 ``trade_request()`` API。
- 在 Web Terminal 有等价能力时提供 MetaTrader5 风格兼容接口；本地计算或
  基于缓存的接口会明确标注。
- tick 价格、历史K线布局和交易请求格式均有显式兼容配置。某个 build 上
  观察到的行为不构成对其他服务器的保证。

文档目录
--------

.. toctree::
   :maxdepth: 2
   :caption: 快速入门

   installation
   quickstart
   configuration
   examples

.. toctree::
   :maxdepth: 2
   :caption: 使用指南

   market_data
   trading
   push_notifications
   reliability
   security
   troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: 参考资料

   api
   protocol_reference
   command_coverage
   python_api_compat
   python_api_detailed_comparison

.. toctree::
   :maxdepth: 1
   :caption: 项目

   development
   author
   changelog

项目链接
--------

- `GitHub 仓库 <https://github.com/cloudQuant/pymt5>`_
- `问题跟踪 <https://github.com/cloudQuant/pymt5/issues>`_
- :ref:`genindex`
- :ref:`search`
