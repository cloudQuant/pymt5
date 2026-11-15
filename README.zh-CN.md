# pymt5

[**English**](README.md) | **简体中文**

[![CI](https://github.com/cloudQuant/pymt5/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudQuant/pymt5/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mt5-web-client)](https://pypi.org/project/mt5-web-client/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![文档](https://img.shields.io/badge/docs-English%20%7C%20%E4%B8%AD%E6%96%87-blue)](https://cloudquant.github.io/pymt5/)
[![许可证：MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**面向 MetaTrader 5 Web Terminal 的异步 Python 客户端**，使用通过逆向分析
得到的加密 WebSocket 二进制协议。

[English documentation](https://cloudquant.github.io/pymt5/en/) ·
[中文文档](https://cloudquant.github.io/pymt5/zh-CN/) ·
[示例](https://cloudquant.github.io/pymt5/zh-CN/examples.html) ·
[问题反馈](https://github.com/cloudQuant/pymt5/issues)

> **非官方、实验性项目：** pymt5 与 MetaQuotes 没有隶属或背书关系。协议行为
> 可能随券商和服务器 build 改变。请先使用已获授权的模拟账户，并在接入应用前
> 阅读[安全指南](https://cloudquant.github.io/pymt5/zh-CN/security.html)。

## 功能概览

| 领域 | 能力 |
| --- | --- |
| 会话 | 异步上下文管理器、bootstrap 密钥交换、登录/登出、心跳、可选自动重连 |
| 账户 | 账户状态、持仓、挂单、成交和交易历史 |
| 行情 | 品种元数据、历史K线、实时 tick 和订单簿 |
| 流式数据 | 原始与强类型回调、tick/订单簿缓存、断线通知 |
| 交易 | 市价/挂单辅助方法、持仓修改/平仓、底层 `trade_request()` |
| 兼容 | 部分 MetaTrader5 风格接口，显式 tick/K线/交易线格式配置 |
| 运维 | 异常层级、日志、可选指标和命令限流 |

PyPI 发布包名为 **`mt5-web-client`**，Python 导入名为 **`pymt5`**。
支持 Linux、macOS 和 Windows 上的 Python **3.11+**，不需要安装本地
MetaTrader 终端。

## 安装

```bash
python -m pip install mt5-web-client
```

可选集成：

```bash
python -m pip install "mt5-web-client[structlog]"
python -m pip install "mt5-web-client[pandas]"
```

开发环境：

```bash
git clone https://github.com/cloudQuant/pymt5.git
cd pymt5
python -m pip install -e ".[dev,docs]"
```

升级与安装验证见[安装指南](https://cloudquant.github.io/pymt5/zh-CN/installation.html)。

## 快速开始——只读

通过环境变量或密钥管理器配置凭据。不要硬编码或提交凭据；pymt5 不会自动
加载 `.env`。

```bash
export PYMT5_SERVER="wss://web.metatrader.app/terminal"
export PYMT5_LOGIN="your-demo-login"
export PYMT5_PASSWORD="your-demo-password"
```

以下示例只连接服务器、加载品种并读取账户状态，**不会**下单、改单或撤单。

```python
import asyncio
import os

from pymt5 import MT5WebClient


async def main() -> None:
    client = MT5WebClient(
        uri=os.environ["PYMT5_SERVER"],
        auto_reconnect=True,
    )
    async with client:
        await client.login(
            login=int(os.environ["PYMT5_LOGIN"]),
            password=os.environ["PYMT5_PASSWORD"],
        )

        symbols = await client.load_symbols()  # dict[str, SymbolInfo]
        account = await client.get_account()

        print(f"Loaded {len(symbols)} symbols")
        print(f"Account currency: {account.get('currency', 'unknown')}")


asyncio.run(main())
```

历史K线、托管 tick 订阅和错误处理见[快速开始](https://cloudquant.github.io/pymt5/zh-CN/quickstart.html)。
可运行脚本见[示例指南](https://cloudquant.github.io/pymt5/zh-CN/examples.html)，
其中明确标注了只读或写操作范围。

## 关键配置

| 设置 | 默认值 | 注意事项 |
| --- | --- | --- |
| `timeout=30.0` | 30 秒 | 已发送请求结果不确定时会隔离连接，不能据此推断服务端结果 |
| `auto_reconnect=False` | 关闭 | 意外断线后重新连接并登录，但不会重试被中断的交易 |
| `rate_bar_format="auto"` | 严格自动识别 | 历史响应长度有歧义或不匹配时抛出 `ProtocolError` |
| `tick_price_mode="auto"` | 旧版启发式 | 验证目标服务器/build 后，优先显式选择 `scaled` 或 `normalized` |
| `tick_price_modes={...}` | 无覆盖 | 按品种设置 tick 价格模式，键不区分大小写 |
| `trade_protocol="legacy"` | 旧版交易线格式 | `build_6090` 是基于有限观察的显式选项，不会根据 build 自动选择 |
| `rate_limit=0` | 关闭 | 可选令牌桶命令限流 |

完整设置与环境变量见[配置指南](https://cloudquant.github.io/pymt5/zh-CN/configuration.html)。

## 交易与可靠性——使用前必读

- 先使用已获授权的模拟账户。离线测试或一次 demo 观察不能证明券商/build
  兼容性。
- 交易超时或取消后，服务端执行结果**未知**。重试前必须核对订单、成交和
  持仓，不能假设交易没有执行；自动重连不会重试该交易。
- 同 command 请求会串行发送。超时、取消或发送失败且可能已经发送时，旧连接
  会被隔离，避免迟到响应被分给该连接上的后续请求；但这不能识别所有主动
  push 或重复响应。
- tick 订阅/退订的非零响应会抛出 `ProtocolError`，并保留最后一次确认的
  本地订阅集合。build 6320 的 demo 样本曾拒绝全部 tick 退订；清理被拒绝
  不等于成功。
- tick 的 `auto` 价格归一化仍是兼容启发式。用于决策前，应对目标服务器的
  原始值和缩放值进行验证。
- 普通 `get_positions_and_orders()` 响应也会调用 cmd=4 回调；回调无法区分
  查询响应和服务器主动 push。

详见[交易指南](https://cloudquant.github.io/pymt5/zh-CN/trading.html)、
[可靠性指南](https://cloudquant.github.io/pymt5/zh-CN/reliability.html)和
[协议参考](https://cloudquant.github.io/pymt5/zh-CN/protocol_reference.html)。

## 在线文档

| English | 中文 | 内容 |
| --- | --- | --- |
| [Documentation home](https://cloudquant.github.io/pymt5/en/) | [文档首页](https://cloudquant.github.io/pymt5/zh-CN/) | 按任务组织的导航与项目边界 |
| [Quick start](https://cloudquant.github.io/pymt5/en/quickstart.html) | [快速开始](https://cloudquant.github.io/pymt5/zh-CN/quickstart.html) | 安全的首次连接与核心流程 |
| [User guides](https://cloudquant.github.io/pymt5/en/market_data.html) | [使用指南](https://cloudquant.github.io/pymt5/zh-CN/market_data.html) | 行情、交易、流式数据和配置 |
| [API reference](https://cloudquant.github.io/pymt5/en/api.html) | [API 参考](https://cloudquant.github.io/pymt5/zh-CN/api.html) | 客户端、事件、数据类、异常和常量 |
| [Troubleshooting](https://cloudquant.github.io/pymt5/en/troubleshooting.html) | [故障排查](https://cloudquant.github.io/pymt5/zh-CN/troubleshooting.html) | 连接、登录、K线、订阅和诊断 |
| [Development](https://cloudquant.github.io/pymt5/en/development.html) | [开发指南](https://cloudquant.github.io/pymt5/zh-CN/development.html) | 测试、质量门禁、双语文档和 CI |
| [Changelog](https://cloudquant.github.io/pymt5/en/changelog.html) | [更新日志](https://cloudquant.github.io/pymt5/zh-CN/changelog.html) | 发布历史与兼容性说明 |

每个文档页面都有 **English / 中文** 切换链接，站点根路径也提供语言选择。
Read the Docs 提供带版本管理的 [English](https://pymt5.readthedocs.io/en/stable/) 和
[中文文档](https://pymt5.readthedocs.io/zh-cn/stable/)；GitHub Pages 继续作为镜像站点。

## 开发与验证

```bash
make check                       # lint、mypy、离线测试、文档和包检查
python docs/build_docs.py        # 构建 /en/ 和 /zh-CN/
python docs/check_docs.py        # 检查链接和语言切换
```

CI 配置了 Linux、macOS、Windows 上的 Python 3.11–3.13 矩阵。默认单元测试
全部离线运行。在线 integration 测试需要显式设置 `PYMT5_INTEGRATION=1` 和
已授权的 demo 凭据；离线测试通过不代表在线验收通过。

反馈问题时，请提供服务器 build、最小复现和脱敏异常信息。绝不要包含密码、
token、session ID、CID 或私有账户/持仓数据。

## 许可证

[MIT](LICENSE) © cloudQuant。MetaTrader 和 MetaQuotes 是其各自所有者的商标；
本项目为独立的非官方实现。
