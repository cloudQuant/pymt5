Changelog
=========

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
