# pymt5

[![CI](https://github.com/cloudQuant/pymt5/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudQuant/pymt5/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Documentation](https://readthedocs.org/projects/pymt5/badge/?version=latest)](https://pymt5.readthedocs.io/en/latest/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](https://github.com/cloudQuant/pymt5)

Python client for the MT5 Web Terminal via reverse-engineered WebSocket binary protocol.

## Requirements

- **Python 3.11+** (3.11, 3.12, 3.13)
- **Platforms**: Linux, macOS, Windows
- **Dependencies**: `websockets`, `aiohttp`, `cryptography`

## Features

### Core Protocol
- WebSocket transport to `wss://web.metatrader.app/terminal`
- AES-CBC encryption with zero IV and PKCS7 padding
- Bootstrap handshake (cmd=0) with automatic key exchange
- Login (cmd=28) with UTF-16LE encoded fields
- `ping`, `logout`, `change_password`, `trader_params`
- `verify_code` (cmd=27) — two-factor authentication
- `open_demo` (cmd=30) — demo account creation
- `send_notification` (cmd=42) — server notifications

### Account Data
- **`get_account` (cmd=3)** — full account information: balance, credit, currency, leverage, name, trade_mode
- `get_account_summary()` — `AccountInfo` dataclass (uses cmd=3 + positions)
- `get_positions_and_orders` (cmd=4) — open positions and pending orders
- `get_positions()` — convenience: open positions only
- `get_orders()` — convenience: pending orders only
- `get_trade_history` (cmd=5) — closed deals and historical orders
- `get_deals()` — convenience: closed deals only

### Market Data
- `get_symbols` (cmd=34, zlib compressed) — full symbol list with metadata
- **`get_symbol_groups` (cmd=9)** — category tree (Forex, Crypto, Indexes, etc.)
- `get_full_symbol_info` (cmd=18) — detailed contract specs (tick size, margins, volumes)
- **`get_spreads` (cmd=20)** — spread data with proper schema parsing
- `get_rates` (cmd=11) — historical OHLCV bars (M1 to MN1), auto-detects extended format with `real_volume`
- `subscribe_ticks` (cmd=7) — real-time tick push (cmd=8) with callback
- `subscribe_symbols` — name-based tick subscription (resolves names via symbol cache)
- **`subscribe_book` (cmd=22)** — order book / depth-of-market subscription
- **`subscribe_book_by_name`** — name-based order book subscription

### Trading
- **Raw**: `trade_request` — full control over all trade fields, returns `TradeResult`
- **`TradeResult`** includes: `deal`, `order`, `volume`, `price`, `bid`, `ask`, `comment`, `request_id`
- **High-level helpers** (auto-resolve digits from symbol cache):
  - `buy_market` / `sell_market` — market orders with optional SL/TP
  - `buy_limit` / `sell_limit` — limit pending orders
  - `buy_stop` / `sell_stop` — stop pending orders
  - `buy_stop_limit` / `sell_stop_limit` — stop-limit pending orders
  - `close_position` — close by opposite market order (auto-detects BUY/SELL direction)
  - `close_position_by` — close by opposite position (hedge netting)
  - `modify_position_sltp` — modify SL/TP of open position
  - `modify_pending_order` — modify pending order price/SL/TP
  - `cancel_pending_order` — remove a pending order

### Symbol Cache & Info
- `load_symbols()` — fetch and cache all symbols for fast name→id/digits lookup
- `get_symbol_info(name)` — lookup `SymbolInfo` by name
- `get_symbol_id(name)` — lookup symbol ID by name
- `symbol_names` — list all cached symbol names

### Miscellaneous Commands
- **`get_corporate_links` (cmd=44)** — broker links (support, education, social)
- `send_raw_command` — send any command with raw payload

### Push Notifications
- `on_tick(callback)` — real-time tick pushes (cmd=8)
- `on_position_update(callback)` — position change pushes
- `on_order_update(callback)` — order change pushes
- `on_trade_update(callback)` — combined position+order push in single callback
- **`on_trade_transaction(callback)`** — order add/update/delete + balance updates (cmd=10)
- `on_symbol_update(callback)` — symbol change pushes (cmd=13)
- **`on_account_update(callback)`** — account balance/equity/margin pushes (cmd=14)
- `on_login_status(callback)` — login status pushes (cmd=15)
- **`on_symbol_details(callback)`** — extended quote data with options greeks (cmd=17)
- **`on_trade_result(callback)`** — async trade execution results (cmd=19)
- **`on_book_update(callback)`** — order book / DOM pushes (cmd=23)

### Reliability
- **Async context manager** (`async with MT5WebClient() as client:`)
- **Auto heartbeat** — periodic ping after login (configurable interval)
- **Auto reconnect** — optional reconnect on disconnect with exponential backoff
- **Disconnect callback** — `on_disconnect()` for custom handling
- **Python logging** — structured logging via `pymt5.client` and `pymt5.transport` loggers

### Constants & Enums
- `TradeResult` dataclass with `retcode`, `description`, `success`, `deal`, `order`, `volume`, `price` fields
- 33 MT5 return codes with human-readable descriptions
- Complete trade constants: `TRADE_ACTION_*` (including `CLOSE_BY`), `ORDER_TYPE_*` (including `STOP_LIMIT`), `ORDER_FILLING_*`, `ORDER_TIME_*`
- Position/deal enums: `POSITION_TYPE_*`, `DEAL_TYPE_*` (all 15 types), `DEAL_ENTRY_*`
- Order states: `ORDER_STATE_*` (all 7 states)
- Symbol trade modes: `SYMBOL_TRADE_MODE_*`
- Command IDs exported: `CMD_GET_ACCOUNT`, `CMD_GET_SYMBOL_GROUPS`, `CMD_TRADE_UPDATE_PUSH`, `CMD_ACCOUNT_UPDATE_PUSH`, `CMD_SYMBOL_DETAILS_PUSH`, `CMD_TRADE_RESULT_PUSH`, `CMD_SUBSCRIBE_BOOK`, `CMD_BOOK_PUSH`, `CMD_GET_CORPORATE_LINKS`

### Tests
- 104 offline unit tests: protocol, schemas, roundtrip parsing, trade constants, symbol cache, volume conversion, reconnect logic, AccountInfo, trade response parsing, full symbol schema, crypto roundtrip, push handler registration, extended rate bars, new schemas

Live-verified against MetaQuotes-Demo (2026-03-12): 6,104 symbols, 6 symbol groups, account info, positions, 60 M1 bars, deals, orders, tick push, all 9 order types, order book subscription, corporate links, notifications, verify code, trader params.

## Install

```bash
pip install pymt5
```

Or install from source:

```bash
git clone https://github.com/cloudQuant/pymt5.git
cd pymt5
pip install -e .
```

For development (tests + type checking):

```bash
pip install -e ".[dev]"
```

## Quick Start

```python
import asyncio
from pymt5 import MT5WebClient


async def main():
    async with MT5WebClient(auto_reconnect=True) as client:
        await client.login(login=12345678, password="your-password")

        # Load symbol cache
        await client.load_symbols()
        print(f"Loaded {len(client.symbol_names)} symbols")

        # Full account info (balance, equity, margin, leverage)
        acct = await client.get_account()
        print(f"Balance: {acct['balance']}, Currency: {acct['currency']}, Leverage: 1:{acct['leverage']}")

        # Symbol groups
        groups = await client.get_symbol_groups()
        print(f"Groups: {groups}")

        # Subscribe to ticks
        def on_ticks(ticks):
            for t in ticks:
                print(f"TICK {t.get('symbol', t['symbol_id'])}: bid={t['bid']} ask={t['ask']}")

        client.on_tick(on_ticks)
        await client.subscribe_symbols(["EURUSD", "GBPUSD"])

        # Register for all push notifications
        client.on_trade_update(lambda d: print(f"Positions: {len(d['positions'])}"))
        client.on_account_update(lambda d: print(f"Balance: {d.get('balance')}"))
        client.on_trade_transaction(lambda d: print(f"Transaction: type={d.get('update_type')}"))
        client.on_trade_result(lambda d: print(f"Trade result: {d.get('result', {})}"))

        # Place a market buy
        result = await client.buy_market("EURUSD", 0.01, sl=1.0800, tp=1.1200)
        print(f"Trade: {result}")

        await asyncio.sleep(10)


asyncio.run(main())
```

## Trading Examples

```python
from pymt5 import (
    MT5WebClient, ORDER_TYPE_BUY, ORDER_TYPE_SELL,
    TRADE_RETCODE_DONE, ORDER_FILLING_IOC,
)

# Market orders
result = await client.buy_market("XAUUSD", 0.1, sl=2300.0, tp=2400.0)
result = await client.sell_market("EURUSD", 0.05, deviation=30)

# Pending orders
result = await client.buy_limit("EURUSD", 0.1, price=1.0800, sl=1.0750, tp=1.0900)
result = await client.sell_stop("GBPUSD", 0.1, price=1.2500)

# Stop-limit orders (trigger at price, then place limit at stop_limit_price)
result = await client.buy_stop_limit("EURUSD", 0.1, price=1.1000,
                                      stop_limit_price=1.0950, sl=1.0900, tp=1.1100)
result = await client.sell_stop_limit("GBPUSD", 0.1, price=1.2400,
                                       stop_limit_price=1.2450)

# Close position (auto-detects BUY/SELL direction)
result = await client.close_position("EURUSD", position_id=123456, volume=0.1)

# Close by opposite position (hedge netting)
result = await client.close_position_by("EURUSD", position_id=123456,
                                         position_by=789012)

# Modify SL/TP
result = await client.modify_position_sltp("EURUSD", position_id=123456,
                                            sl=1.0850, tp=1.0950)

# Cancel pending order
result = await client.cancel_pending_order(order=789012)
```

## Push Notifications

```python
# Combined position+order updates in one callback
def on_trade_change(data):
    print(f"Positions: {len(data['positions'])}, Orders: {len(data['orders'])}")
client.on_trade_update(on_trade_change)

# Trade transaction (order add/update/delete, balance updates)
def on_transaction(data):
    if data.get('update_type') == 2:
        print(f"Balance update: {data['balance_info']}")
    else:
        print(f"Order transaction: type={data.get('transaction_type')}")
client.on_trade_transaction(on_transaction)

# Account balance/equity changes
client.on_account_update(lambda d: print(f"Balance: {d.get('balance')}"))

# Extended symbol data with options greeks
client.on_symbol_details(lambda d: print(f"Delta: {d[0].get('delta')}"))

# Async trade execution results
client.on_trade_result(lambda d: print(f"Retcode: {d.get('result', {}).get('retcode')}"))

# Order book (DOM) updates
client.on_book_update(lambda entries: print(f"Book: {len(entries)} symbols"))

# Login status changes (e.g. forced logout)
client.on_login_status(lambda r: print(f"Login status: code={r.code}"))
```

## Protocol Flow

1. `connect()` — opens WebSocket, sends bootstrap (cmd=0), receives new AES key
2. `login()` — sends credentials (cmd=28), receives session token, starts heartbeat
3. Data commands (get_account, get_symbols, get_rates, get_positions_and_orders, etc.)
4. `close()` — stops heartbeat, sends logout (cmd=2), closes WebSocket

**Important**: Do NOT call `init_session()` (cmd=29) after login — that command is for demo/real account creation, not session initialization.

## Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
# Loggers: pymt5.client, pymt5.transport
```

## Examples

Run any example from the project root:

```bash
pip install -e .
python examples/01_connect_and_account.py
```

| Example | Description |
|---------|-------------|
| `01_connect_and_account.py` | Login, full account info (cmd=3), positions/orders/deals, ping |
| `02_market_data.py` | Symbol groups (cmd=9), symbol cache, spreads (cmd=20), OHLCV rates |
| `03_realtime_ticks.py` | Tick subscription, account/symbol details/login status push handlers |
| `04_trading.py` | Market/limit/stop orders, modify SL/TP, position close |
| `05_order_book.py` | Order book (DOM) subscription (cmd=22/23) |
| `06_all_push_notifications.py` | All 9 push notification types in one example |
| `07_all_order_types.py` | All 9 order types + modify + cancel + close + raw trade_request |
| `08_misc_features.py` | Corporate links, notifications, trader params, verify code, reconnect |

## Command Coverage

| CMD | Name | Status |
|-----|------|--------|
| 0 | Bootstrap | Implemented |
| 2 | Logout | Implemented |
| 3 | Get Account | **NEW** — balance, credit, currency, leverage, name |
| 4 | Get Positions/Orders | Implemented |
| 5 | Get Trade History | Implemented |
| 6 | Get Symbols | Implemented |
| 7 | Subscribe Ticks | Implemented |
| 8 | Tick Push | Implemented |
| 9 | Get Symbol Groups | **NEW** — Forex, Metals, Indexes, etc. |
| 10 | Trade Update Push | **NEW** — order add/update/delete + balance updates |
| 11 | Get Rates | Implemented |
| 12 | Trade Request | Implemented |
| 13 | Symbol Update Push | Implemented |
| 14 | Account Update Push | **NEW** — real-time balance/equity changes |
| 15 | Login Status Push | Implemented |
| 17 | Symbol Details Push | **NEW** — extended quotes with options greeks |
| 18 | Get Full Symbols | Implemented |
| 19 | Trade Result Push | **NEW** — async trade execution results |
| 20 | Get Spreads | **IMPROVED** — proper schema parsing |
| 22 | Subscribe Book | **NEW** — order book / DOM subscription |
| 23 | Book Push | **NEW** — order book push with bid/ask levels |
| 24 | Change Password | Implemented |
| 27 | Verify Code | Implemented |
| 28 | Login | Implemented |
| 29 | Init Session | Implemented |
| 30 | Open Demo | Implemented |
| 34 | Get Symbols (gzip) | Implemented |
| 41 | Trader Params | Implemented |
| 42 | Notify | Implemented |
| 44 | Get Corporate Links | **NEW** — broker links |
| 51 | Ping | Implemented |

Commands 21, 25, 33, 37, 39, 40, 43, 50, 52, 100-112 are accepted by the server but have no usage in the Web Terminal JavaScript source. CMD 39 (Open Real Account) and 43 (OTP Setup) have schemas but require specialized registration/2FA flows.

## Documentation

Full API documentation is available at **[pymt5.readthedocs.io/en/latest](https://pymt5.readthedocs.io/en/latest/)**.

To build docs locally:

```bash
pip install -e ".[docs]"
cd docs && make html
```

## CI/CD

This project uses GitHub Actions for continuous integration, testing across:

| | Python 3.11 | Python 3.12 | Python 3.13 |
|---|---|---|---|
| **Linux** (ubuntu-latest) | ✅ | ✅ | ✅ |
| **macOS** (macos-latest) | ✅ | ✅ | ✅ |
| **Windows** (windows-latest) | ✅ | ✅ | ✅ |

The CI pipeline also runs mypy type checking and builds the Sphinx documentation.

## Notes

- This package is experimental and tracks the MT5 Web Terminal protocol as observed on 2026-03-12
- MetaQuotes may change the protocol at any time
- Volume encoding: MT5 uses integer volumes where `volume = lots × 10^precision`. The MetaQuotes demo server uses precision=8, so 1.0 lot = 100,000,000 and 0.01 lot = 1,000,000
- `get_account()` (cmd=3) returns balance, credit, currency, leverage, and name. Equity, margin, and profit are computed from positions
- The Symbol Details push (cmd=17) schema may produce approximate values — the exact byte layout for options greeks varies by server
- Order book (cmd=22/23) may not produce pushes on all servers — depends on broker configuration
- Some commands like `trader_params` (cmd=41) may cause the server to close the connection on rapid successive calls

## License

MIT License — see [LICENSE](LICENSE) for details.
