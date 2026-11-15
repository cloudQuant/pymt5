开发指南
========

本指南介绍源码检出、离线验证、双语文档以及自动交付流程。运行时行为和公开接口见
:doc:`api` 与 :doc:`protocol_reference`；用户可见的变化应记录在 :doc:`changelog`。

设置可编辑源码环境
------------------

要求 Python 3.11 或更高版本。CI 在 Linux、macOS 和 Windows 上测试 Python 3.11、
3.12 和 3.13。按照所用平台的常规方式创建并激活虚拟环境，然后在仓库根目录安装开发
和文档 extras：

.. code-block:: bash

   git clone https://github.com/cloudQuant/pymt5.git
   cd pymt5
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev,docs]"

可编辑安装会让 ``pymt5`` 导入指向源码检出目录。``dev`` extra 提供 pytest、
pytest-asyncio、coverage、Hypothesis、mypy、Ruff、build 和 twine；``docs`` 提供
Sphinx、Read the Docs 主题以及 autodoc 类型标注支持。发布包名仍为
``mt5-web-client``。

运行完整本地检查
----------------

在仓库根目录执行：

.. code-block:: bash

   make check

该目标依次运行：

1. ``lint``：对 ``pymt5/`` 和 ``tests/`` 执行 Ruff lint；
2. ``typecheck``：对 ``pymt5/`` 执行 mypy；
3. ``test``：显式加载 asyncio 插件，运行离线 pytest 套件；
4. ``docs``：执行 Sphinx 文档检查；
5. ``package-check``：先清理旧构建元数据和产物，再执行隔离 wheel/sdist 构建，
   最后用 twine 严格验证元数据。

使用该目标前应安装两个 extras。``make package-check`` 会在 ``dist/`` 及相关构建
目录中生成新的普通构建产物；应检查而不是提交这些生成输出。某个阶段失败时，先修复
并单独重跑该阶段，再重复完整检查。

单项质量检查命令
----------------

使用与 Makefile 和 workflow 相同的命令：

.. code-block:: bash

   ruff check pymt5/ tests/
   python -m mypy pymt5/ --ignore-missing-imports
   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/ -v --tb=short

对于不支持内联环境变量赋值的 shell，请在进程环境中设置
``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``，再运行相同的 ``python -m pytest`` 命令。
选择单个测试或文件：

.. code-block:: bash

   python -m pytest tests/test_protocol_smoke.py -v --tb=short
   python -m pytest tests/ -k test_name -v --tb=short

CI 覆盖率变体需要显式加载两个插件：

.. code-block:: bash

   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin -p pytest_cov.plugin tests/ -v --tb=short --cov=pymt5 --cov-report=term-missing

``make format`` 会执行 ``ruff format pymt5/ tests/`` 并重写文件。只在确需格式化
变更时使用，它不是只读检查。Ruff 配置目标为 Python 3.11，行宽 120 个字符。不要让
格式化或清理变更超出当前任务范围。

默认测试全部离线
----------------

默认套件使用本地 fake 和二进制夹具，不需要 MT5 服务器或凭据。标记为
``integration`` 的测试只有设置 ``PYMT5_INTEGRATION`` 后才会运行。默认 CI 不安装
在线凭据，也不运行在线服务器验收任务。

手动 integration 运行需要单独授权的模拟环境，以及 ``PYMT5_SERVER``、
``PYMT5_LOGIN`` 和 ``PYMT5_PASSWORD``。``PYMT5_TEST_SYMBOL`` 可以为 tick 测试
选择准确的服务器品种。30 秒内没有 tick 会导致跳过，因为市场可能闭市；这不代表
tick 解析验证通过。清理错误仍然重要，包括退订被拒绝。

不要在默认 CI 中启用在线测试，不要把真实凭据加入测试文件，也不要用在线交易代替
离线测试。任何手动在线运行前先阅读 :doc:`security`；除非某项变更状态的操作得到
明确授权，否则保持只读，并对所有输出脱敏。离线测试通过证明本地回归覆盖有效，并不
证明兼容所有券商或服务器 build。

构建英文和中文文档
------------------

安装 ``docs`` extra 后，在仓库根目录运行双语构建：

.. code-block:: bash

   python docs/build_docs.py

文档构建以 ``docs/`` 为英文源根目录，以 ``docs/zh_CN/`` 为简体中文源根目录。
生成的 HTML 位于：

- ``docs/_build/html/en/``：英文；
- ``docs/_build/html/zh-CN/``：简体中文。

这些是生成目录，不应手动编辑或提交。打开相应的 ``index.html``，检查导航、表格、
代码块、交叉引用和 API 渲染。在仓库根目录运行 ``make docs`` 可构建两种语言并检查
生成后的链接和语言切换，也可分别运行两个脚本：

.. code-block:: bash

   python docs/build_docs.py
   python docs/check_docs.py

应从源头修复 Sphinx 警告，而不是隐藏警告。页面引用 Python 对象时，应根据
:doc:`api` 和当前代码核对签名；描述字节
或命令行为时，应与 :doc:`protocol_reference` 一致，并标明哪些观测是 build 特定的。

翻译一致性规则
--------------

英文与简体中文页面必须互为完整对应版本。每个新页面、章节、警告、表格行、命令和
行为限定条件都必须在同一次变更中同步更新两种语言。不得让其中一种语言成为占位页、
过时摘要或零散的机器翻译片段。

API 名称、类和方法名、环境变量、命令 ID、字面量配置值、文件路径以及 shell 命令应
保持一致，除非特定语言惯例另有要求。翻译正文、标题和说明性标签时，必须保留警告和
不确定性。尤其不能弱化有关交易结果未知、非官方协议支持、只读范围或未验证券商
兼容性的表述。

使用各语言树内的本地 ``:doc:`` 引用。源目录名 ``zh_CN`` 有意与发布路径 ``zh-CN``
不同。文档变更后检查两套输出；描述版本行为时查阅 :doc:`changelog`。

CI 与 GitHub Pages
------------------

CI workflow 分为测试、lint/类型检查、软件包构建和文档构建任务：

- 测试在 Ubuntu、macOS、Windows 上运行，覆盖 Python 3.11、3.12 和 3.13。
- Ruff 和 mypy 在 Ubuntu 上使用 Python 3.12。
- 构建软件包产物并用 twine 检查。
- Sphinx 文档构建把警告视为错误，并启用 ``--keep-going``。

默认矩阵不含在线 integration 凭据。因此，CI 全绿表示离线测试以及静态检查、软件包
和文档检查通过，并不是实盘券商认证。

GitHub Pages workflow 在推送到 ``main`` 或 ``master`` 时运行，也可手动触发。它构建
文档、配置 Pages、把 ``docs/_build/html`` 上传为 Pages artifact，并通过
``github-pages`` 环境部署。双语站点在 ``/en/`` 下提供英文，在 ``/zh-CN/`` 下提供
简体中文。部署权限限于读取仓库、写入 Pages 以及 OIDC 身份令牌。构建或上传失败时，
应先修复相应问题，再检查部署权限。

Read the Docs
-------------

Read the Docs 使用原生翻译项目模式： ``pymt5`` 是英文主项目，单独的简体中文项目作为其
translation 关联。两个项目使用同一仓库和同一份 ``.readthedocs.yaml``。构建根据
``READTHEDOCS_LANGUAGE`` 选择 ``docs/`` 或 ``docs/zh_CN/``，并把一种语言直接输出到
``$READTHEDOCS_OUTPUT/html``：

.. code-block:: bash

   python docs/build_docs.py --readthedocs

canonical URL 来自 ``READTHEDOCS_CANONICAL_URL``，不硬编码主机名。语言切换使用 Read the Docs
的语言/版本路径；GitHub Pages 仍使用合并的 ``/en/`` 和 ``/zh-CN/`` 产物。项目维护者必须在
Read the Docs 后台创建并关联中文 translation，仅靠仓库配置无法完成这一步。

发布流程
--------

推送 ``v*.*.*`` tag 后，首先检查 tag 是否与 ``project.version``、中英文更新日志和源码版本
回退值一致。测试、lint/类型检查、双语文档和软件包验证全部通过后才允许发布。软件包任务构建
wheel 和 sdist，执行严格的 twine 检查，在源码树之外的干净虚拟环境中安装 wheel，并上传这份
产物供后续任务使用。

PyPI 任务下载同一产物，通过受保护的 ``pypi`` environment 使用 Trusted Publishing；只有该任务
获得 OIDC 权限。GitHub Release 任务也下载同一产物并附加到 Release。不要从脏的本地工作区上传
发行包。PyPI 文件名不可变，每次发布都必须使用新的版本号和 tag。

贡献检查清单
------------

- 保持变更聚焦；除非变更明确要求，否则保留公开行为。
- 为修复添加离线回归覆盖，尤其是超时、取消、协议解析和连接代际场景。
- 提交前先运行相关测试，再运行 ``make check``。
- 同步更新英文和中文文档，并验证两套构建。
- 引用准确 API 和已观测协议证据；不要编造数字响应码含义，也不要声称完成并未执行的
  跨 build 验证。
- 按 :doc:`security` 要求，让凭据、在线账户数据、私有终端 bundle 和生成的分析摘录
  远离提交与报告。
- 用户可见的变化更新 :doc:`changelog`；运行诊断放入 :doc:`troubleshooting`，不要
  隐藏在开发说明中。
