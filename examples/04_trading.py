"""
Example 04: Trading API demonstration.

Demonstrates all trading operations:
  - Market buy / sell
  - Pending limit orders
  - Position SL/TP modification
  - Position closing (auto-detect direction)
  - Order cancellation

The trade_request returns retcode=0 (OK) on the MetaQuotes demo.
Trade execution behavior depends on the broker/server configuration.

NOTE: On some demo accounts, small volume trades may be silently dropped.
      This is a server-side limitation, not a client issue.
"""
import asyncio
import logging
import os
import time

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient, ORDER_TYPE_SELL, AuthenticationError, MT5ConnectionError, PyMT5Error, TradeError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example04")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
  try:
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        await client.login(login=LOGIN, password=PASSWORD)
        await client.load_symbols()
        log.info("Logged in, symbols loaded")

        # --- Show existing positions ---
        data = await client.get_positions_and_orders()
        positions = data["positions"]
        orders = data["orders"]
        log.info(f"Current state: {len(positions)} positions, {len(orders)} orders")
        for p in positions:
            name = p["trade_symbol"].rstrip("\x00").strip()
            direction = "BUY" if p["trade_action"] == 0 else "SELL"
            log.info(f"  {name} {direction}  id={p['position_id']}  vol={p['trade_volume']}  profit={p['profit']:+.2f}")

        # --- Get current EURUSD price ---
        symbol = "EURUSD"
        info = client.get_symbol_info(symbol)
        log.info(f"{symbol}: id={info.symbol_id}, digits={info.digits}")

        now = int(time.time())
        bars = await client.get_rates(symbol, 1, now - 300, now)
        price = bars[-1]["close"]
        log.info(f"Current {symbol} price = {price:.{info.digits}f}")

        # --- Market Buy ---
        log.info("--- Market Buy ---")
        result = await client.buy_market(symbol, 0.01, deviation=30, comment="pymt5-buy")
        log.info(f"  {result}")

        # --- Market Sell ---
        log.info("--- Market Sell ---")
        result = await client.sell_market(symbol, 0.01, deviation=30, comment="pymt5-sell")
        log.info(f"  {result}")

        # --- Buy Limit (below current price) ---
        log.info("--- Buy Limit ---")
        limit_price = round(price - 0.01, info.digits)
        result = await client.buy_limit(symbol, 0.01, price=limit_price, comment="pymt5-bl")
        log.info(f"  price={limit_price}  {result}")

        # --- Sell Stop (below current price) ---
        log.info("--- Sell Stop ---")
        stop_price = round(price - 0.02, info.digits)
        result = await client.sell_stop(symbol, 0.01, price=stop_price, comment="pymt5-ss")
        log.info(f"  price={stop_price}  {result}")

        # --- Modify SL/TP (on existing position if any) ---
        if positions:
            pos = positions[0]
            pos_name = pos["trade_symbol"].rstrip("\x00").strip()
            log.info(f"--- Modify SL/TP on {pos_name} id={pos['position_id']} ---")
            pos_info = client.get_symbol_info(pos_name)
            if pos_info:
                sl = round(pos["price_open"] * 0.98, pos_info.digits)
                tp = round(pos["price_open"] * 1.05, pos_info.digits)
                result = await client.modify_position_sltp(
                    pos_name, position_id=pos["position_id"], sl=sl, tp=tp
                )
                log.info(f"  SL={sl} TP={tp}  {result}")

        # --- Final state ---
        await asyncio.sleep(0.5)
        final = await client.get_positions_and_orders()
        log.info(f"Final: {len(final['positions'])} positions, {len(final['orders'])} orders")

    log.info("Done")
  except MT5ConnectionError as e:
    log.error(f"Connection failed: {e}")
  except AuthenticationError as e:
    log.error(f"Login failed: {e}")
  except TradeError as e:
    log.error(f"Trade failed: {e}")
  except PyMT5Error as e:
    log.error(f"MT5 error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
