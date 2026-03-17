Detailed Comparison With Official MetaTrader5 Python API
========================================================

This page compares the official ``MetaTrader5`` Python package function set
published on `MQL5 Python Integration <https://www.mql5.com/en/docs/python_metatrader5>`_
with the current ``pymt5`` client as checked on 2026-03-17.

What Is Being Compared
----------------------

The official package talks to a locally installed desktop terminal. ``pymt5``
talks to the public MT5 Web Terminal over the reverse-engineered WebSocket
protocol. Because of that, the names now line up closely, but some semantics
do not:

- Official package: synchronous module-level functions such as
  ``mt5.initialize()`` and ``mt5.order_send()``
- ``pymt5``: async instance methods such as
  ``await client.initialize()`` and ``await client.order_send({...})``
- Official package: terminal-managed snapshots, namedtuples, and array-like
  return types
- ``pymt5``: dicts, lists, and dataclasses, with some views reconstructed from
  push streams or local formulas

Status Labels
-------------

- ``Direct``: backed by a confirmed Web Terminal command or response path
- ``Derived``: built from another command, cached push data, or local filtering
- ``Best-effort``: exposed publicly, but not backed by a dedicated Web Terminal
  RPC with fully equivalent semantics

Connection, Session, and Terminal Metadata
------------------------------------------

.. list-table::
   :header-rows: 1

   * - Official function
     - ``pymt5`` mapping
     - Status
     - Notes
   * - ``initialize()``
     - ``MT5WebClient.initialize()``
     - Direct
     - Maps to ``cmd=29`` session init. Unlike the desktop package, it does not
       launch a terminal executable or choose local data paths.
   * - ``login()``
     - ``MT5WebClient.login()``
     - Direct
     - Logs in through the Web Terminal protocol. ``pymt5`` returns
       ``(token, session)`` and also exposes Web-specific fields such as URL,
       OTP, and lead-tracking parameters.
   * - ``shutdown()``
     - ``MT5WebClient.shutdown()``
     - Direct
     - Closes the WebSocket session instead of detaching from a desktop
       terminal process.
   * - ``version()``
     - ``MT5WebClient.version()``
     - Best-effort
     - Returns ``(500, build, release_date)`` from ``cmd=3`` plus locally
       observed public build metadata. Unknown builds keep an empty date string.
   * - ``last_error()``
     - ``MT5WebClient.last_error()``
     - Best-effort
     - Tracks compatibility-layer failures in ``pymt5``. It is not a byte-for-
       byte clone of the desktop terminal's internal error buffer.
   * - ``account_info()``
     - ``MT5WebClient.account_info()``
     - Direct
     - Alias for ``get_account()`` over ``cmd=3``. Returns a dict rather than
       the desktop package's typed record.
   * - ``terminal_info()``
     - ``MT5WebClient.terminal_info()``
     - Best-effort
     - Exposes only the server/build/timezone/trade-right subset that the Web
       account config can prove. Desktop-only filesystem and community fields
       are intentionally omitted.

Symbols, MarketWatch, and DOM
-----------------------------

.. list-table::
   :header-rows: 1

   * - Official function
     - ``pymt5`` mapping
     - Status
     - Notes
   * - ``symbols_total()``
     - ``MT5WebClient.symbols_total()``
     - Direct
     - Counts cached symbols or fetches the Web symbol list directly.
   * - ``symbols_get()``
     - ``MT5WebClient.symbols_get()``
     - Direct
     - Backed by ``cmd=34``/``cmd=6`` with client-side wildcard group
       filtering.
   * - ``symbol_info()``
     - ``MT5WebClient.symbol_info()``
     - Direct
     - Uses ``cmd=18`` when available and falls back to the basic symbol cache.
   * - ``symbol_info_tick()``
     - ``MT5WebClient.symbol_info_tick()``
     - Derived
     - Returns the latest cached ``cmd=8`` tick push. No dedicated tick
       snapshot command has been confirmed in the current public frontend.
   * - ``symbol_select()``
     - ``MT5WebClient.symbol_select()``
     - Derived
     - Implemented as tick subscription management, not persistent desktop
       MarketWatch visibility.
   * - ``market_book_add()``
     - ``MT5WebClient.market_book_add()``
     - Direct
     - Uses ``cmd=22`` book subscription.
   * - ``market_book_get()``
     - ``MT5WebClient.market_book_get()``
     - Derived
     - Returns the last cached ``cmd=23`` DOM snapshot. It becomes useful only
       after a book stream has been observed.
   * - ``market_book_release()``
     - ``MT5WebClient.market_book_release()``
     - Direct
     - Removes the symbol from the current DOM subscription set.

Bars and Ticks
--------------

.. list-table::
   :header-rows: 1

   * - Official function
     - ``pymt5`` mapping
     - Status
     - Notes
   * - ``copy_rates_range()``
     - ``MT5WebClient.copy_rates_range()``
     - Direct
     - Thin wrapper over ``cmd=11`` bar history.
   * - ``copy_rates_from()``
     - ``MT5WebClient.copy_rates_from()``
     - Derived
     - Fetches a bar range through ``cmd=11`` and slices the trailing
       ``count`` locally.
   * - ``copy_rates_from_pos()``
     - ``MT5WebClient.copy_rates_from_pos()``
     - Derived
     - Reconstructs current-bar-relative access by requesting enough history
       and slicing in client code.
   * - ``copy_ticks_from()``
     - ``MT5WebClient.copy_ticks_from()``
     - Derived
     - Returns cached tick history assembled from observed ``cmd=8`` pushes.
       It is not a server-side historical tick query.
   * - ``copy_ticks_range()``
     - ``MT5WebClient.copy_ticks_range()``
     - Derived
     - Same cached-stream model as ``copy_ticks_from()``, with inclusive end-of-
       second handling for integer timestamps.

Orders, Positions, History, and Calculations
--------------------------------------------

.. list-table::
   :header-rows: 1

   * - Official function
     - ``pymt5`` mapping
     - Status
     - Notes
   * - ``orders_total()``
     - ``MT5WebClient.orders_total()``
     - Direct
     - Built on the current pending-order view from ``cmd=4``.
   * - ``orders_get()``
     - ``MT5WebClient.orders_get()``
     - Direct
     - Supports ``symbol``, ``group``, and ``ticket`` filters in client code
       after loading the ``cmd=4`` order state.
   * - ``positions_total()``
     - ``MT5WebClient.positions_total()``
     - Direct
     - Built on the current open-position view from ``cmd=4``.
   * - ``positions_get()``
     - ``MT5WebClient.positions_get()``
     - Direct
     - Supports ``symbol``, ``group``, and ``ticket`` filters in client code.
   * - ``history_orders_total()``
     - ``MT5WebClient.history_orders_total()``
     - Direct
     - Counts locally filtered historical orders from ``cmd=5``.
   * - ``history_orders_get()``
     - ``MT5WebClient.history_orders_get()``
     - Direct
     - Supports ``date_from``, ``date_to``, ``group``, ``ticket``, and
       ``position`` filters over ``cmd=5`` history.
   * - ``history_deals_total()``
     - ``MT5WebClient.history_deals_total()``
     - Direct
     - Counts locally filtered deals from ``cmd=5``.
   * - ``history_deals_get()``
     - ``MT5WebClient.history_deals_get()``
     - Direct
     - Supports ``date_from``, ``date_to``, ``group``, ``ticket``, and
       ``position`` filters over ``cmd=5`` deal history.
   * - ``order_send()``
     - ``MT5WebClient.order_send()``
     - Direct
     - Wraps ``cmd=12`` and normalizes official-style request dicts, including
       stop-limit mapping. Returns ``TradeResult`` instead of the desktop
       package's result record.
   * - ``order_check()``
     - ``MT5WebClient.order_check()``
     - Best-effort
     - Local pre-flight validator only. It checks symbol rules, stops,
       expiration, fill mode, and margin sufficiency without a dedicated server
       ``order_check`` RPC.
   * - ``order_calc_margin()``
     - ``MT5WebClient.order_calc_margin()``
     - Best-effort
     - Uses local formulas plus cached FX conversion quotes. Common forex/CFD/
       futures/stock/bond modes are covered; broker-specific margin tiers remain
       approximate.
   * - ``order_calc_profit()``
     - ``MT5WebClient.order_calc_profit()``
     - Best-effort
     - Same local-formula model as ``order_calc_margin()``. Bond modes
       ``37``/``39`` use ``face_value`` and ``accrued_interest`` from the
       expanded ``cmd=18`` schema.

No Official Equivalent In The Desktop Package
---------------------------------------------

``pymt5`` also exposes Web-Terminal-specific or protocol-level helpers that do
not have a direct official desktop-package counterpart:

- Account onboarding and verification:
  ``request_opening_verification()``, ``submit_opening_verification()``,
  ``open_demo_account()``, ``open_real_account()``
- OTP and verification helpers:
  ``verify_code()``, ``enable_otp()``, ``disable_otp()``
- Web-only broker/UI commands:
  ``trader_params()``, ``send_notification()``, ``get_corporate_links()``
- Protocol escape hatches:
  ``send_raw_command()``, ``send_bootstrap_command_52()``
- Async stream callbacks:
  ``on_tick()``, ``on_book_update()``, ``on_trade_result()``, and the other
  push-handler registrations
- High-level trade ergonomics:
  ``buy_market()``, ``sell_market()``, ``buy_limit()``, ``sell_limit()``,
  ``buy_stop()``, ``sell_stop()``, ``buy_stop_limit()``, ``sell_stop_limit()``,
  ``close_position()``, ``close_position_by()``, ``modify_position_sltp()``,
  ``modify_pending_order()``, ``cancel_pending_order()``

Practical Conclusion
--------------------

At the interface level, ``pymt5`` now covers the same official function names
that matter for trading, symbols, positions, orders, rates, ticks, history,
and terminal/account metadata. The real differences are semantic:

- the official package is desktop-terminal IPC, while ``pymt5`` is Web Terminal
  WebSocket transport
- some official snapshot APIs are modeled as cached views in ``pymt5``
- some terminal-side calculations are modeled as local best-effort formulas
- return shapes are Pythonic dict/list/dataclass structures rather than
  official namedtuple or array-oriented results

For a shorter status-only summary, see :doc:`python_api_compat`.
