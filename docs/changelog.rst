Changelog
=========

Unreleased
----------

- No unreleased changes yet.

v0.7.0 (2026-03-17)
-------------------

- Added frontend-aligned onboarding commands:
  ``request_opening_verification`` (cmd=27 structured flow),
  ``submit_opening_verification`` (cmd=40),
  ``open_demo_account`` (cmd=30), ``open_real_account`` (cmd=39)
- Added TOTP management helpers:
  ``enable_otp`` / ``disable_otp`` (cmd=43)
- Added account-opening result dataclasses and document payload support
- Added ``propType=9`` time encoding support to the protocol codec for
  real-account birth-date serialization
- Added experimental ``send_bootstrap_command_52()`` helper for the only
  currently observable reserved command with repeatable live behavior
- Added official ``MetaTrader5``-style compatibility helpers for session,
  symbols, rates, orders/positions/history, DOM, and ``order_send()``
- Added best-effort local-formula compatibility helpers:
  ``last_error()``, ``order_calc_profit()``, ``order_calc_margin()``
- Added cached tick-history compatibility helpers:
  ``copy_ticks_from()`` and ``copy_ticks_range()`` over observed ``cmd=8``
  pushes
- Added local ``order_check()`` compatibility pre-flight using symbol rules,
  cached prices, and local margin estimation
- Expanded ``cmd=3`` account parsing to expose server/company/timezone and
  rights metadata from the current frontend account header, plus trade
  settings, leverage rules, and commission tables
- Added a conservative ``terminal_info()`` compatibility helper derived from
  ``cmd=3`` account/server metadata
- Added a best-effort ``version()`` compatibility helper that returns
  ``(500, build, release_date)`` from ``cmd=3`` plus observed public
  Web Terminal build metadata
- Tightened ``mypy`` compatibility in ``client.py`` by making client-side
  error helpers and parsed schedule structures explicitly typed
- Added ``make check`` and ``make package-check`` targets to mirror the main
  local CI preflight steps, including offline ``build --no-isolation``
  package verification and ``twine check`` metadata validation
- Expanded ``cmd=18`` symbol parsing to the current frontend schema, including
  bond ``face_value`` / ``accrued_interest`` plus nested trade settings,
  schedule, and subscription sections
- Added best-effort bond profit/margin formulas for trade calc modes ``37``
  and ``39``
- Fixed ``cmd=22`` order-book subscription payloads to match the frontend's
  ``count + symbol_ids`` format
- Re-verified the public Web Terminal command surface against build 5687
  (built on 2026-03-15)

v0.5.0 (2026-03-12)
--------------------

- **New commands**: ``get_account`` (cmd=3), ``get_symbol_groups`` (cmd=9),
  ``get_spreads`` (cmd=20), ``subscribe_book`` (cmd=22),
  ``get_corporate_links`` (cmd=44)
- **New push handlers**: ``on_trade_transaction`` (cmd=10),
  ``on_account_update`` (cmd=14), ``on_symbol_details`` (cmd=17),
  ``on_trade_result`` (cmd=19), ``on_book_update`` (cmd=23)
- **Trading**: All 9 order types including stop-limit, close-by, modify, cancel
- **TradeResult** dataclass with retcode, description, deal, order, volume, price
- **AccountInfo** dataclass with balance, equity, margin, leverage
- **SymbolInfo** dataclass with name, symbol_id, digits, description
- **Symbol cache**: ``load_symbols()``, ``get_symbol_info()``, ``get_symbol_id()``
- **Auto reconnect** with exponential backoff and credential re-use
- **Auto heartbeat** with configurable interval
- 104 offline unit tests
- CI/CD with GitHub Actions (Python 3.11/3.12/3.13 × Linux/macOS/Windows)
- Sphinx documentation with ReadTheDocs integration

v0.1.0 (2026-03-01)
--------------------

- Initial release
- Bootstrap handshake and AES-CBC encryption
- Login, logout, ping
- Symbol list (plain and gzip-compressed)
- Tick subscription and push
- Historical OHLCV rates
- Position and order retrieval
- Trade history (deals)
- Basic trade request
