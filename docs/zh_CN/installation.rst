安装
====

环境要求
--------

- Python 3.11、3.12 或 3.13
- Linux、macOS 或 Windows
- MT5 Web Terminal 地址，以及你有权用于在线连接测试的账户

运行时依赖为 ``websockets`` 和 ``cryptography``，不需要在本机安装
MetaTrader 终端。

从 PyPI 安装
------------

.. code-block:: bash

   python -m pip install mt5-web-client

发布包名与导入名有意保持不同：

.. code-block:: python

   from pymt5 import MT5WebClient

可选集成
--------

仅在需要时安装结构化日志或 pandas 支持：

.. code-block:: bash

   python -m pip install "mt5-web-client[structlog]"
   python -m pip install "mt5-web-client[pandas]"

开发环境安装
------------

.. code-block:: bash

   git clone https://github.com/cloudQuant/pymt5.git
   cd pymt5
   python -m pip install -e ".[dev,docs]"

验证离线安装：

.. code-block:: bash

   python -c "import pymt5; print(pymt5.MT5WebClient.__name__)"
   python -m pytest tests/ -q

默认测试套件全部离线运行。只有显式设置 ``PYMT5_INTEGRATION=1`` 才会启用
在线 integration 测试，详见 :doc:`development`。

升级与兼容性
------------

升级前请阅读 :doc:`changelog`。生产环境应固定已经验证的版本，并针对实际
使用的服务器 build 重新验证所有协议敏感流程。安装成功或离线测试通过并不
代表已经验证券商兼容性。

下一步
------

继续阅读 :doc:`quickstart`，并在启用订阅或交易前查看 :doc:`configuration`
和 :doc:`security`。
