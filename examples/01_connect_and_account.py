"""
Example 01: Connection, login, full account info, positions, orders, deals.

Demonstrates:
  - Async context manager (auto connect + close)
  - Login with credentials
  - get_account (cmd=3) — full balance/equity/margin/leverage
  - get_account_summary — AccountInfo dataclass
  - Loading symbols
  - Positions / orders / deals query
  - trader_params
  - Ping heartbeat
"""
import asyncio
import logging
import os
import time

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example01")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        # --- Login ---
        token, session = await client.login(login=LOGIN, password=PASSWORD)
        log.info(f"Logged in  session={session}")

        # --- Load Symbols ---
        symbols = await client.load_symbols()
        log.info(f"Loaded {len(symbols)} symbols")

        # --- Full Account Info (cmd=3) ---
        log.info("--- Full Account Info (cmd=3) ---")
        acct = await client.get_account()
        if acct:
            log.info(f"  Login       : {acct.get('login')}")
            log.info(f"  Name        : {acct.get('name')}")
            log.info(f"  Server      : {acct.get('server')}")
            log.info(f"  Company     : {acct.get('company')}")
            log.info(f"  Currency    : {acct.get('currency')}")
            log.info(f"  Leverage    : 1:{acct.get('leverage')}")
            log.info(f"  Balance     : {acct.get('balance', 0):.2f}")
            log.info(f"  Credit      : {acct.get('credit', 0):.2f}")
            log.info(f"  Profit      : {acct.get('profit', 0):+.2f}")
            log.info(f"  Equity      : {acct.get('equity', 0):.2f}")
            log.info(f"  Margin      : {acct.get('margin', 0):.2f}")
            log.info(f"  Margin Free : {acct.get('margin_free', 0):.2f}")
            log.info(f"  Margin Level: {acct.get('margin_level', 0):.2f}%")
            log.info(f"  Trade Allowed: {acct.get('trade_allowed')}")
        else:
            log.warning("  get_account returned empty (server may not support cmd=3)")

        # --- Account Summary (uses cmd=3 internally) ---
        summary = await client.get_account_summary()
        log.info(f"AccountInfo: balance={summary.balance:.2f} equity={summary.equity:.2f} "
                 f"margin={summary.margin:.2f} leverage={summary.leverage} "
                 f"positions={summary.positions_count} orders={summary.orders_count}")

        # --- Open Positions & Pending Orders ---
        data = await client.get_positions_and_orders()
        positions = data["positions"]
        orders = data["orders"]
        log.info(f"Open positions: {len(positions)}")
        for p in positions[:5]:
            symbol = p["trade_symbol"]
            vol = p["trade_volume"]
            profit = p["profit"]
            direction = "BUY" if p["trade_action"] == 0 else "SELL"
            log.info(f"  {symbol:12s} {direction:4s}  vol={vol}  profit={profit:+.2f}")
        log.info(f"Pending orders: {len(orders)}")
        for o in orders[:5]:
            log.info(f"  {o['trade_symbol']:12s} type={o['order_type']}  price={o['price_order']:.5f}")

        # --- Trade History (last 7 days) ---
        now = int(time.time())
        history = await client.get_trade_history(from_ts=now - 86400 * 7, to_ts=now)
        deals = history["deals"]
        hist_orders = history["orders"]
        log.info(f"Trade history (7 days): {len(deals)} deals, {len(hist_orders)} orders")
        for d in deals[:5]:
            log.info(f"  DEAL #{d['deal']}  {d['trade_symbol']:12s}  profit={d.get('profit', 0):+.2f}")

        # --- Ping ---
        await client.ping()
        log.info("Ping OK")

    log.info("Done — connection closed automatically")


if __name__ == "__main__":
    asyncio.run(main())
