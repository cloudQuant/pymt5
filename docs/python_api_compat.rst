Official Python API Compatibility
=================================

This page tracks ``pymt5`` coverage of the official
``MetaTrader5`` Python package API documented at
``mql5.com/en/docs/python_metatrader5``.

For the full per-function matrix, including signature and semantic differences,
see :doc:`python_api_detailed_comparison`.

Implemented With Confirmed Web Terminal Support
-----------------------------------------------

These helpers now map cleanly to existing Web Terminal commands or frontend
data paths:

- Session/account: ``initialize()``, ``shutdown()``, ``account_info()``
  via ``cmd=29`` and ``cmd=3``
- Terminal metadata: ``terminal_info()`` as a best-effort server/account
  view derived from ``cmd=3``
- Errors: ``last_error()`` for compatibility-layer failures
- Symbols: ``symbols_total()``, ``symbols_get()``, ``symbol_info()``
  via ``cmd=34`` / ``cmd=18``
- Bars: ``copy_rates_range()``, ``copy_rates_from()``,
  ``copy_rates_from_pos()`` via ``cmd=11``
- Trading state: ``positions_total()``, ``positions_get()``,
  ``orders_total()``, ``orders_get()``, ``history_orders_total()``,
  ``history_orders_get()``, ``history_deals_total()``,
  ``history_deals_get()`` via ``cmd=4`` / ``cmd=5``
- Trading request: ``order_send()`` via ``cmd=12``
- DOM: ``market_book_add()`` / ``market_book_release()`` via ``cmd=22``

Implemented As Cached Client-Side Views
---------------------------------------

These helpers are useful, but they do not come from dedicated snapshot
commands in the current Web Terminal protocol:

- ``symbol_info_tick()`` returns the latest cached tick from ``cmd=8`` pushes
- ``market_book_get()`` returns the latest cached DOM snapshot from ``cmd=23``
- ``copy_ticks_from()`` / ``copy_ticks_range()`` return cached tick history
  assembled from ``cmd=8`` pushes; they are not server-side history snapshots
- ``version()`` returns ``(500, build, release_date)`` from the ``cmd=3``
  build field plus locally observed public Web Terminal build-date metadata
- ``symbol_select()`` is a best-effort wrapper over tick subscriptions, not a
  true MarketWatch visibility API
- ``terminal_info()`` exposes only the subset the Web account config can prove;
  it does not emulate desktop filesystem/community fields
- ``order_calc_profit()`` and ``order_calc_margin()`` now use documented
  local formulas plus cached FX conversion quotes instead of dedicated
  ``sendCommand(...)`` calls
- ``order_check()`` is a local pre-flight validator that reuses symbol rules,
  cached prices, and local margin estimates instead of a dedicated server
  RPC

Current Gaps In The Local Formula Layer
---------------------------------------

The Web Terminal calculates profit and margin client-side for many symbols.
``pymt5`` now exposes that for common retail modes (forex, CFDs, major
exchange stocks/futures, bonds), but a few cases are still incomplete:

- Bond modes ``37`` / ``39`` now use ``face_value`` and
  ``accrued_interest`` from the current ``cmd=18`` schema
- Broker-specific leverage tiers and ``orderRate(...)`` rules are not yet
  modeled, so exchange-option / special-margin results remain best-effort

Still Unsupported Or Unconfirmed
--------------------------------

No additional official ``MetaTrader5`` helpers are currently left outside the
scoped compatibility layer. The remaining uncertainty is about fidelity, not
surface area: best-effort helpers such as ``version()``, ``terminal_info()``,
``copy_ticks_*()``, and ``order_check()`` are intentionally documented as
derived views rather than dedicated Web Terminal RPCs.

Notes
-----

``pymt5`` keeps its native dict/dataclass return types. The compatibility
helpers follow official function names where practical, but they do not try to
clone the desktop package's namedtuple layer byte-for-byte.
