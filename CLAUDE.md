# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pymt5 is a Python async client for MetaTrader 5 Web Terminal via a reverse-engineered WebSocket binary protocol. It provides trading operations (place/modify/cancel orders), market data subscriptions (ticks, bars, order book), and account management over an encrypted binary WebSocket connection.

## Commands

### Install
```bash
pip install -e ".[dev]"       # development (includes pytest, mypy)
pip install -e ".[docs]"      # documentation (includes sphinx)
```

### Tests
```bash
python -m pytest tests/ -v --tb=short          # all tests
python -m pytest tests/test_protocol_smoke.py   # protocol-level tests only
python -m pytest tests/test_client_features.py  # client feature tests only
python -m pytest tests/ -k "test_name"          # single test by name
```

All tests are offline unit tests (no live MT5 server required). pytest-asyncio is configured with `asyncio_mode = "auto"`.

### Type Checking
```bash
python -m mypy pymt5/ --ignore-missing-imports --no-strict-optional
```

### Build Documentation
```bash
cd docs && python -m sphinx . _build/html -W --keep-going
```

## Architecture

### Layer Stack

```
MT5WebClient (pymt5/client.py)        — High-level async API, command builders, response parsers
    ↓
MT5WebSocketTransport (pymt5/transport.py) — WebSocket lifecycle, encryption, command queue
    ↓
AESCipher (pymt5/crypto.py)           — AES-CBC encryption with zero IV, PKCS7 padding
    ↓
SeriesCodec (pymt5/protocol.py)       — Binary serialization/deserialization of struct fields
    ↓
websockets library                     — Raw WebSocket I/O
```

### Key Modules

- **`client.py`** (~480 lines) — Core lifecycle: `MT5WebClient` class (async context manager), connect/close/login/logout, heartbeat, auto-reconnect. Assembles functionality via four mixin classes.
- **`_push_handlers.py`** (~315 lines) — `_PushHandlersMixin`: push notification handler registration and dispatch (`on_tick()`, `on_trade_update()`, `on_book_update()`, etc.), tick/book caching.
- **`_account.py`** (~470 lines) — `_AccountMixin`: account info, terminal info, version, demo/real account opening, OTP, verification, notifications, corporate links.
- **`_market_data.py`** (~820 lines) — `_MarketDataMixin`: symbol management, tick/bar data retrieval, order book subscriptions, currency conversion rate resolution, profit/margin calculations.
- **`_trading.py`** (~1050 lines) — `_TradingMixin`: positions/orders CRUD, trade execution, all order type helpers (buy_market, sell_limit, etc.), order validation.
- **`_parsers.py`** (~520 lines) — Standalone parsing functions: binary protocol parsing, timestamp coercion, validation helpers.
- **`_validation.py`** (~35 lines) — Input validation functions: volume, price, symbol name, connection state.
- **`types.py`** (~295 lines) — Dataclasses (`TradeResult`, `SymbolInfo`, `AccountInfo`, etc.), TypedDicts (`TickRecord`, `BarRecord`, etc.), schemas, type aliases.
- **`exceptions.py`** (~55 lines) — Exception hierarchy: `PyMT5Error` base, `MT5ConnectionError`, `AuthenticationError`, `TradeError`, `ProtocolError`, `SymbolNotFoundError`, `ValidationError`, `SessionError`, `MT5TimeoutError`.
- **`transport.py`** — WebSocket lifecycle, encryption, command queue, FIFO dispatch, heartbeat pings.
- **`protocol.py`** — `SeriesCodec` binary field serialization/deserialization. Field types: `PROP_*` constants.
- **`schemas.py`** — Field layouts for all MT5 command requests/responses.
- **`constants.py`** — Command IDs (`CMD_*`), order types, trade actions, filling modes, return codes.
- **`crypto.py`** — AES-CBC cipher initialization from obfuscated server key.
- **`helpers.py`** — UTF-16LE encoding, obfuscation routines, string padding, client ID generation.

### Key Patterns

- **Async context manager**: `async with MT5WebClient(server, login, password) as client:` handles connect/disconnect lifecycle.
- **Command/response correlation**: Transport maintains a dict of `asyncio.Future` queues keyed by command ID. `send_command()` enqueues a future; transport resolves it when the matching response arrives.
- **Push handlers**: Real-time data (ticks, trade updates, book updates) delivered via registered async callbacks. Transport routes push command IDs to client-level handlers.
- **Symbol cache**: `load_symbols()` fetches all symbols once, then lookups by name or ID are local. Required before trading or subscribing.
- **Volume encoding**: MT5 uses `volume * 10000` integer encoding (e.g., 0.01 lots = 100). The client handles this conversion.

### Reconnection

Transport stores credentials and supports auto-reconnect with exponential backoff (3s × attempt, configurable max attempts). On reconnect, subscriptions are re-established automatically.

## Python Version

Requires Python >= 3.11. CI tests against 3.11, 3.12, and 3.13 on Linux, macOS, and Windows.
