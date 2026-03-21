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
python -m mypy pymt5/ --ignore-missing-imports
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

- **`client.py`** (~524 lines) — Core lifecycle: `MT5WebClient` class (async context manager), connect/close/login/logout, heartbeat, auto-reconnect with exponential backoff + jitter. Assembles functionality via five mixin classes.
- **`_push_handlers.py`** (~334 lines) — `_PushHandlersMixin`: push notification handler registration and dispatch (`on_tick()`, `on_trade_update()`, `on_book_update()`, etc.), tick/book caching.
- **`_account.py`** (~488 lines) — `_AccountMixin`: account info, terminal info, version, demo/real account opening, OTP, verification, notifications, corporate links.
- **`_market_data.py`** (~845 lines) — `_MarketDataMixin`: symbol management, tick/bar data retrieval, order book subscriptions, managed subscriptions (`subscribe_ticks_managed()`), currency conversion rate resolution, profit/margin calculations.
- **`_trading.py`** (~698 lines) — `_TradingMixin`: positions/orders CRUD, trade execution, order validation, trade request building.
- **`_order_helpers.py`** (~502 lines) — `_OrderHelpersMixin`: order convenience methods (buy_market, sell_limit, buy_stop, etc.), position close/modify, pending order management.
- **`_parsers.py`** (~518 lines) — Standalone parsing functions: binary protocol parsing, timestamp coercion, validation helpers.
- **`_validation.py`** (~33 lines) — Input validation functions: volume, price, symbol name, connection state.
- **`types.py`** (~306 lines) — Dataclasses (`TradeResult`, `SymbolInfo`, `AccountInfo`, etc.), TypedDicts (`TickRecord`, `BarRecord`, etc.), schemas, type aliases.
- **`exceptions.py`** (~89 lines) — Exception hierarchy: `PyMT5Error` base, `MT5ConnectionError` (with `server_uri`), `AuthenticationError`, `TradeError` (with `retcode`, `symbol`, `action`), `ProtocolError`, `SymbolNotFoundError`, `ValidationError`, `SessionError`, `MT5TimeoutError`.
- **`events.py`** (~63 lines) — Typed event dataclasses: `TickEvent`, `BookEvent`, `TradeResultEvent`, `AccountEvent` (frozen, slots).
- **`transport.py`** (~227 lines) — WebSocket lifecycle, `TransportState` enum state machine, encryption, command queue, FIFO dispatch, heartbeat pings, rate limiting, graceful shutdown, metrics hooks.
- **`protocol.py`** (~270 lines) — `SeriesCodec` binary field serialization/deserialization via dispatch table. Field types: `PROP_*` constants.
- **`schemas.py`** — Field layouts for all MT5 command requests/responses.
- **`constants.py`** — Command IDs (`CMD_*`), order types, trade actions, filling modes, return codes, calc mode sets.
- **`crypto.py`** — AES-CBC cipher initialization from obfuscated server key.
- **`helpers.py`** — UTF-16LE encoding, obfuscation routines, string padding, client ID generation.
- **`_rate_limiter.py`** (~42 lines) — `TokenBucketRateLimiter`: async token bucket for command throttling.
- **`_subscription.py`** (~62 lines) — `SubscriptionHandle`: async context manager for subscription lifecycle.
- **`_dataframe.py`** (~32 lines) — `to_dataframe()`: optional pandas DataFrame conversion.
- **`_metrics.py`** (~39 lines) — `MetricsCollector`: Protocol class for operational metrics.
- **`_logging.py`** (~25 lines) — `get_logger()`: structured logging wrapper (structlog with stdlib fallback).

### Key Patterns

- **Async context manager**: `async with MT5WebClient(server, login, password) as client:` handles connect/disconnect lifecycle.
- **Command/response correlation**: Transport maintains a dict of `asyncio.Future` queues keyed by command ID. `send_command()` enqueues a future; transport resolves it when the matching response arrives.
- **Push handlers**: Real-time data (ticks, trade updates, book updates) delivered via registered async callbacks. Transport routes push command IDs to client-level handlers.
- **Symbol cache**: `load_symbols()` fetches all symbols once, then lookups by name or ID are local. Required before trading or subscribing.
- **Volume encoding**: MT5 uses `volume * 10000` integer encoding (e.g., 0.01 lots = 100). The client handles this conversion.

### Transport State Machine

Transport uses a `TransportState` enum (`DISCONNECTED`, `CONNECTING`, `READY`, `CLOSING`, `ERROR`) to track connection lifecycle. The `is_ready` property provides backward-compatible boolean access.

### Rate Limiting

Optional token bucket rate limiter (`rate_limit` param, disabled by default). Limits command throughput to prevent server overload.

### Reconnection

Transport stores credentials and supports auto-reconnect with exponential backoff + jitter (`base_delay * 2^(attempt-1) + random(0, base_delay)`, capped at `max_reconnect_delay`). On reconnect, subscriptions are re-established automatically.

### Observability

- **Structured logging**: All modules use `get_logger()` from `_logging.py` (structlog if installed, stdlib fallback).
- **Metrics**: Optional `MetricsCollector` protocol for connect/send/receive/disconnect events.
- **Optional deps**: `pip install pymt5[structlog]` for structured logging, `pip install pymt5[pandas]` for DataFrame integration.

## Python Version

Requires Python >= 3.11. CI tests against 3.11, 3.12, and 3.13 on Linux, macOS, and Windows.
