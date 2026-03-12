# pymt5

Python client for the MT5 Web Terminal via reverse-engineered WebSocket binary protocol.

## Features

### Core Protocol
- WebSocket transport to `wss://web.metatrader.app/terminal`
- AES-CBC encryption with zero IV and PKCS7 padding
- Bootstrap handshake (cmd=0) with automatic key exchange
- Login (cmd=28) with UTF-16LE encoded fields
- `ping`, `logout`, `change_password`, `trader_params`

### Market Data
- `get_symbols` (cmd=34, zlib compressed) — full symbol list with metadata
- `get_rates` (cmd=11) — historical OHLCV bars (M1 to MN1)
- `subscribe_ticks` (cmd=7) — real-time tick push (cmd=8) with callback
- `subscribe_symbols` — name-based tick subscription (resolves names via symbol cache)
- Tick push automatically resolves `symbol_id` → `symbol` name via cache

### Account Data
- `get_positions_and_orders` (cmd=4) — open positions and pending orders
- `get_positions()` — convenience: open positions only
- `get_orders()` — convenience: pending orders only
- `get_trade_history` (cmd=5) — closed deals and historical orders
- `get_deals()` — convenience: closed deals only
- `get_account_summary()` — compute `AccountInfo` from positions (profit, counts)

### Trading
- **Raw**: `trade_request` — full control over all trade fields, returns `TradeResult`
- **`TradeResult`** now includes: `deal`, `order`, `volume`, `price`, `bid`, `ask`, `comment`, `request_id`
- **High-level helpers** (auto-resolve digits from symbol cache):
  - `buy_market` / `sell_market` — market orders with optional SL/TP
  - `buy_limit` / `sell_limit` — limit pending orders
  - `buy_stop` / `sell_stop` — stop pending orders
  - `close_position` — close by opposite market order
  - `modify_position_sltp` — modify SL/TP of open position
  - `modify_pending_order` — modify pending order price/SL/TP
  - `cancel_pending_order` — remove a pending order

### Symbol Cache & Info
- `load_symbols()` — fetch and cache all symbols for fast name→id/digits lookup
- `get_symbol_info(name)` — lookup `SymbolInfo` by name
- `get_symbol_id(name)` — lookup symbol ID by name
- `symbol_names` — list all cached symbol names
- `get_full_symbol_info(symbol)` (cmd=18) — detailed specs: contract_size, tick_size, tick_value, margins, volume limits, currencies

### Push Notifications
- `on_position_update(callback)` — register for real-time position change pushes
- `on_order_update(callback)` — register for real-time order change pushes

### Reliability
- **Async context manager** (`async with MT5WebClient() as client:`)
- **Auto heartbeat** — periodic ping after login (configurable interval)
- **Auto reconnect** — optional reconnect on disconnect with exponential backoff
- **Disconnect callback** — `on_disconnect()` for custom handling
- **Python logging** — structured logging via `pymt5.client` and `pymt5.transport` loggers

### Trade Result Codes
- `TradeResult` dataclass with `retcode`, `description`, `success`, `deal`, `order`, `volume`, `price` fields
- 33 MT5 return codes with human-readable descriptions
- All trade constants exported: `TRADE_ACTION_*`, `ORDER_TYPE_*`, `ORDER_FILLING_*`, `ORDER_TIME_*`

### Tests
- 52 offline unit tests: protocol, schemas, roundtrip parsing, trade constants, symbol cache, volume conversion, reconnect logic, AccountInfo, trade response parsing, full symbol schema

Live-verified against MetaQuotes-Demo (2026-03-12): 6,104 symbols, positions, 60 M1 bars, deals, orders, tick push, ping.

## Install

```bash
pip install -e .
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

        # Get EURUSD info
        info = client.get_symbol_info("EURUSD")
        print(f"EURUSD: id={info.symbol_id}, digits={info.digits}")

        # Detailed symbol specs (contract size, tick size, margins, etc.)
        full = await client.get_full_symbol_info("EURUSD")
        if full:
            print(f"Contract: {full['contract_size']}, Tick: {full['tick_size']}")

        # Subscribe to ticks by name
        def on_ticks(ticks):
            for t in ticks:
                print(f"TICK {t.get('symbol', t['symbol_id'])}: bid={t['bid']} ask={t['ask']}")

        client.on_tick(on_ticks)
        await client.subscribe_symbols(["EURUSD", "GBPUSD"])

        # Register for position/order push notifications
        client.on_position_update(lambda pos: print(f"Position update: {len(pos)} positions"))
        client.on_order_update(lambda ords: print(f"Order update: {len(ords)} orders"))

        # Place a market buy — TradeResult now includes deal/order ticket
        result = await client.buy_market("EURUSD", 0.01, sl=1.0800, tp=1.1200)
        print(f"Trade: {result}")  # TradeResult(retcode=10009, success=True, deal=..., order=...)

        # Convenience queries
        positions = await client.get_positions()
        orders = await client.get_orders()
        deals = await client.get_deals()
        print(f"Positions: {len(positions)}, Orders: {len(orders)}, Deals: {len(deals)}")

        # Account summary
        account = await client.get_account_summary()
        print(f"Floating P/L: {account.profit}")

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

# Close position
result = await client.close_position("EURUSD", position_id=123456, volume=0.1,
                                      order_type=ORDER_TYPE_SELL)

# Modify SL/TP
result = await client.modify_position_sltp("EURUSD", position_id=123456,
                                            sl=1.0850, tp=1.0950)

# Cancel pending order
result = await client.cancel_pending_order(order=789012)

# Check result
if result.success:
    print(f"OK: {result.description}")
else:
    print(f"FAILED: {result.retcode} - {result.description}")
```

## Protocol Flow

1. `connect()` — opens WebSocket, sends bootstrap (cmd=0), receives new AES key
2. `login()` — sends credentials (cmd=28), receives session token, starts heartbeat
3. Data commands (get_symbols, get_rates, get_positions_and_orders, etc.)
4. `close()` — stops heartbeat, sends logout (cmd=2), closes WebSocket

**Important**: Do NOT call `init_session()` (cmd=29) after login — that command is for demo/real account creation, not session initialization.

## Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
# Loggers: pymt5.client, pymt5.transport
```

## Notes

- This package is experimental and tracks the MT5 Web Terminal protocol as observed on 2026-03-12
- MetaQuotes may change the protocol at any time
- Volume encoding: MT5 uses integer volumes where `volume = lots × 10^volume_precision` (typically precision=2, so 0.01 lot = 1)
