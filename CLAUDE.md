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

- **`client.py`** (~1700 lines) — The public API surface. Contains `MT5WebClient` (async context manager), dataclasses (`TradeResult`, `SymbolInfo`, `AccountInfo`), all trading commands (buy/sell/modify/cancel), data retrieval (ticks, bars, history), and push handler registration (`on_tick()`, `on_trade_update()`, etc.).
- **`transport.py`** — Manages WebSocket connection, encrypts/decrypts messages via `AESCipher`, dispatches responses to pending command futures (FIFO queue per command ID), handles heartbeat pings and auto-reconnect with exponential backoff.
- **`protocol.py`** — `SeriesCodec` handles binary field serialization. Field types defined as `PROP_*` constants (i8/i16/i32/i64/u8/u16/u32/u64/f32/f64/str/bytes). Parses variable-length binary packets into dicts.
- **`schemas.py`** — Defines field layouts for all MT5 command requests/responses (ticks, bars, orders, positions, deals, book entries, corporate links). Each schema is a list of `(field_name, field_type)` tuples.
- **`constants.py`** — Command IDs (`CMD_*`), order types, trade actions, filling modes, return codes, and period constants.
- **`crypto.py`** — AES-CBC cipher initialization from obfuscated server key. Used by transport for all post-handshake communication.
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
