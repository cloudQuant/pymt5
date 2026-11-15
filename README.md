# pymt5

**English** | [**简体中文**](README.zh-CN.md)

[![CI](https://github.com/cloudQuant/pymt5/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudQuant/pymt5/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mt5-web-client)](https://pypi.org/project/mt5-web-client/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![Documentation](https://img.shields.io/badge/docs-English%20%7C%20%E4%B8%AD%E6%96%87-blue)](https://cloudquant.github.io/pymt5/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Async Python client for MetaTrader 5 Web Terminal**, using a reverse-engineered,
encrypted WebSocket binary protocol.

[English documentation](https://cloudquant.github.io/pymt5/en/) ·
[中文文档](https://cloudquant.github.io/pymt5/zh-CN/) ·
[Examples](https://cloudquant.github.io/pymt5/en/examples.html) ·
[Issues](https://github.com/cloudQuant/pymt5/issues)

> **Unofficial and experimental:** pymt5 is not affiliated with or endorsed by
> MetaQuotes. Protocol behavior can change across brokers and server builds.
> Start with an authorized demo account and read the
> [security guide](https://cloudquant.github.io/pymt5/en/security.html) before
> connecting an application.

## Features

| Area | Capabilities |
| --- | --- |
| Session | Async context manager, bootstrap key exchange, login/logout, heartbeat, optional automatic reconnect |
| Account | Account state, positions, pending orders, deals, and trade history |
| Market data | Symbol metadata, historical bars, real-time ticks, and order book |
| Streaming | Raw and typed callbacks, tick/book caches, disconnect notifications |
| Trading | Market/pending order helpers, position modification/close, raw `trade_request()` |
| Compatibility | Selected MetaTrader5-style helpers, explicit tick/bar/trade wire-format controls |
| Operations | Exception hierarchy, logging, optional metrics, and command rate limiting |

The package name is **`mt5-web-client`**; the import name is **`pymt5`**.
Python **3.11+** is supported on Linux, macOS, and Windows. No local MetaTrader
terminal is required.

## Installation

```bash
python -m pip install mt5-web-client
```

Optional integrations:

```bash
python -m pip install "mt5-web-client[structlog]"
python -m pip install "mt5-web-client[pandas]"
```

For development:

```bash
git clone https://github.com/cloudQuant/pymt5.git
cd pymt5
python -m pip install -e ".[dev,docs]"
```

See the [installation guide](https://cloudquant.github.io/pymt5/en/installation.html)
for upgrades and verification.

## Quick start — read-only

Set credentials through your environment or secret manager. Do not hard-code or
commit them; pymt5 does not implicitly load `.env`.

```bash
export PYMT5_SERVER="wss://web.metatrader.app/terminal"
export PYMT5_LOGIN="your-demo-login"
export PYMT5_PASSWORD="your-demo-password"
```

The following example connects, loads symbols, and reads account state. It does
**not** place, modify, or cancel an order.

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

Continue with the [quick start](https://cloudquant.github.io/pymt5/en/quickstart.html)
for historical bars, managed tick subscriptions, and error handling. Runnable
scripts are listed in the [examples guide](https://cloudquant.github.io/pymt5/en/examples.html);
their read-only or mutating scope is labeled explicitly.

## Configuration that matters

| Setting | Default | Why it matters |
| --- | --- | --- |
| `timeout=30.0` | 30 seconds | A sent request with an uncertain outcome causes connection isolation, not a presumed server result |
| `auto_reconnect=False` | Disabled | Reconnects and logs in again after unexpected disconnects; does not retry interrupted trades |
| `rate_bar_format="auto"` | Strict auto-detection | Ambiguous/mismatched historical response lengths raise `ProtocolError` |
| `tick_price_mode="auto"` | Legacy heuristic | Prefer `scaled` or `normalized` after validating the target server/build |
| `tick_price_modes={...}` | No overrides | Case-insensitive per-symbol tick price modes |
| `trade_protocol="legacy"` | Legacy trade wire format | `build_6090` is an explicit, narrowly observed opt-in, not an automatic build selection |
| `rate_limit=0` | Disabled | Optional token-bucket command throttling |

Full settings and environment variables are in the
[configuration guide](https://cloudquant.github.io/pymt5/en/configuration.html).

## Trading and reliability — read before use

- Use an authorized demo account first. Broker/build compatibility is not
  established by offline tests or by a single demo observation.
- A trade timeout or cancellation leaves server execution **unknown**. Reconcile
  orders, deals, and positions before any retry; never assume the trade failed
  to execute. Automatic reconnection does not retry it.
- Same-command requests are serialized. After a timeout, cancellation, or send
  failure where sending may have occurred, the old connection is isolated so a
  late response cannot be assigned to a subsequent request on that connection.
  This does not identify every unsolicited push or duplicate response.
- Nonzero tick subscription/unsubscription responses raise `ProtocolError` and
  preserve the last confirmed local subscription set. A build 6320 demo sample
  rejected removal of all tick subscriptions; cleanup rejection is not success.
- Tick `auto` price normalization remains a compatibility heuristic. Validate
  raw and scaled values against the target server before using them in decisions.
- Ordinary `get_positions_and_orders()` responses also invoke cmd=4 callbacks;
  those callbacks do not distinguish query responses from unsolicited pushes.

See [Trading](https://cloudquant.github.io/pymt5/en/trading.html),
[Reliability](https://cloudquant.github.io/pymt5/en/reliability.html), and
[Protocol reference](https://cloudquant.github.io/pymt5/en/protocol_reference.html)
for details.

## Documentation

| English | 中文 | Contents |
| --- | --- | --- |
| [Documentation home](https://cloudquant.github.io/pymt5/en/) | [文档首页](https://cloudquant.github.io/pymt5/zh-CN/) | Task-based navigation and project boundaries |
| [Quick start](https://cloudquant.github.io/pymt5/en/quickstart.html) | [快速开始](https://cloudquant.github.io/pymt5/zh-CN/quickstart.html) | Safe first connection and core workflows |
| [User guides](https://cloudquant.github.io/pymt5/en/market_data.html) | [使用指南](https://cloudquant.github.io/pymt5/zh-CN/market_data.html) | Market data, trading, streaming, configuration |
| [API reference](https://cloudquant.github.io/pymt5/en/api.html) | [API 参考](https://cloudquant.github.io/pymt5/zh-CN/api.html) | Client, events, data classes, exceptions, constants |
| [Troubleshooting](https://cloudquant.github.io/pymt5/en/troubleshooting.html) | [故障排查](https://cloudquant.github.io/pymt5/zh-CN/troubleshooting.html) | Connection, login, rates, subscriptions, diagnostics |
| [Development](https://cloudquant.github.io/pymt5/en/development.html) | [开发指南](https://cloudquant.github.io/pymt5/zh-CN/development.html) | Tests, quality gates, bilingual docs, CI |
| [Changelog](https://cloudquant.github.io/pymt5/en/changelog.html) | [更新日志](https://cloudquant.github.io/pymt5/zh-CN/changelog.html) | Release history and compatibility notes |

Use the **English / 中文** switch on every documentation page. The site root
also provides a language selector. Read the Docs provides versioned
[English](https://pymt5.readthedocs.io/en/stable/) and
[中文](https://pymt5.readthedocs.io/zh-cn/stable/) documentation; GitHub Pages
remains available as a mirror.

## Development and verification

```bash
make check                       # lint, mypy, offline tests, docs, package checks
python docs/build_docs.py        # build /en/ and /zh-CN/
python docs/check_docs.py        # validate links and language switches
```

CI is configured for Python 3.11–3.13 on Linux, macOS, and Windows. Default
unit tests are offline. Live integration tests require explicit
`PYMT5_INTEGRATION=1` and authorized demo credentials; they are not implied by
an offline pass.

When reporting an issue, include the server build, minimal reproduction, and
sanitized exception output. Never include passwords, tokens, session IDs, CIDs,
or private account/position data.

## License

[MIT](LICENSE) © cloudQuant. MetaTrader and MetaQuotes are trademarks of their
respective owners; this project is independent and unofficial.
