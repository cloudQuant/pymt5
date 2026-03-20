"""
Example 07: All order types demonstration.

Demonstrates every trading operation:
  - buy_market / sell_market — market orders
  - buy_limit / sell_limit — limit pending orders
  - buy_stop / sell_stop — stop pending orders
  - buy_stop_limit / sell_stop_limit — stop-limit pending orders
  - modify_position_sltp — modify SL/TP
  - modify_pending_order — modify pending order
  - cancel_pending_order — cancel pending order
  - close_position — close with auto-detect direction
  - close_position_by — close by opposite position
  - trade_request — raw low-level trade request
"""
import asyncio
import logging
import os
import time

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import (
    MT5WebClient,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    ORDER_FILLING_FOK,
    TRADE_ACTION_DEAL,
    AuthenticationError,
    MT5ConnectionError,
    PyMT5Error,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example07")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
  try:
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        await client.login(login=LOGIN, password=PASSWORD)
        await client.load_symbols()
        log.info("Logged in, symbols loaded")

        symbol = "EURUSD"
        info = client.get_symbol_info(symbol)
        if not info:
            log.error(f"{symbol} not found")
            return
        log.info(f"{symbol}: id={info.symbol_id}, digits={info.digits}")

        now = int(time.time())
        bars = await client.get_rates(symbol, 1, now - 300, now)
        if not bars:
            log.error("No rate data available")
            return
        price = bars[-1]["close"]
        log.info(f"Current {symbol} price = {price:.{info.digits}f}")

        # --- 1. Market Buy ---
        log.info("=== 1. Market Buy ===")
        r = await client.buy_market(symbol, 0.01, deviation=30, comment="test-buy")
        log.info(f"  {r}")

        # --- 2. Market Sell ---
        log.info("=== 2. Market Sell ===")
        r = await client.sell_market(symbol, 0.01, deviation=30, comment="test-sell")
        log.info(f"  {r}")

        # --- 3. Buy Limit (below current price) ---
        log.info("=== 3. Buy Limit ===")
        limit_price = round(price - 0.0050, info.digits)
        r = await client.buy_limit(symbol, 0.01, price=limit_price, sl=limit_price - 0.005, tp=limit_price + 0.005, comment="test-bl")
        log.info(f"  price={limit_price}  {r}")

        # --- 4. Sell Limit (above current price) ---
        log.info("=== 4. Sell Limit ===")
        limit_price2 = round(price + 0.0050, info.digits)
        r = await client.sell_limit(symbol, 0.01, price=limit_price2, comment="test-sl")
        log.info(f"  price={limit_price2}  {r}")

        # --- 5. Buy Stop (above current price) ---
        log.info("=== 5. Buy Stop ===")
        stop_price = round(price + 0.0100, info.digits)
        r = await client.buy_stop(symbol, 0.01, price=stop_price, comment="test-bs")
        log.info(f"  price={stop_price}  {r}")

        # --- 6. Sell Stop (below current price) ---
        log.info("=== 6. Sell Stop ===")
        stop_price2 = round(price - 0.0100, info.digits)
        r = await client.sell_stop(symbol, 0.01, price=stop_price2, comment="test-ss")
        log.info(f"  price={stop_price2}  {r}")

        # --- 7. Buy Stop Limit ---
        log.info("=== 7. Buy Stop Limit ===")
        trigger = round(price + 0.0150, info.digits)
        limit = round(price + 0.0120, info.digits)
        r = await client.buy_stop_limit(symbol, 0.01, price=trigger, stop_limit_price=limit, comment="test-bsl")
        log.info(f"  trigger={trigger} limit={limit}  {r}")

        # --- 8. Sell Stop Limit ---
        log.info("=== 8. Sell Stop Limit ===")
        trigger2 = round(price - 0.0150, info.digits)
        limit2 = round(price - 0.0120, info.digits)
        r = await client.sell_stop_limit(symbol, 0.01, price=trigger2, stop_limit_price=limit2, comment="test-ssl")
        log.info(f"  trigger={trigger2} limit={limit2}  {r}")

        # --- 9. Raw trade_request (low-level) ---
        log.info("=== 9. Raw trade_request ===")
        r = await client.trade_request(
            trade_action=TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=MT5WebClient._volume_to_lots(0.01),
            digits=info.digits,
            trade_type=ORDER_TYPE_BUY,
            type_filling=ORDER_FILLING_FOK,
            deviation=30,
            comment="raw-test",
        )
        log.info(f"  {r}")

        # --- Show positions and orders ---
        await asyncio.sleep(0.5)
        data = await client.get_positions_and_orders()
        positions = data["positions"]
        orders = data["orders"]
        log.info(f"After trading: {len(positions)} positions, {len(orders)} pending orders")

        # --- 10. Modify SL/TP on first position ---
        if positions:
            pos = positions[0]
            pos_sym = pos["trade_symbol"]
            pos_info = client.get_symbol_info(pos_sym)
            if pos_info:
                log.info(f"=== 10. Modify SL/TP on position {pos['position_id']} ===")
                sl = round(pos["price_open"] * 0.97, pos_info.digits)
                tp = round(pos["price_open"] * 1.05, pos_info.digits)
                r = await client.modify_position_sltp(pos_sym, position_id=pos["position_id"], sl=sl, tp=tp)
                log.info(f"  SL={sl} TP={tp}  {r}")

        # --- 11. Cancel first pending order ---
        if orders:
            order = orders[0]
            log.info(f"=== 11. Cancel pending order {order['trade_order']} ===")
            r = await client.cancel_pending_order(order=order["trade_order"])
            log.info(f"  {r}")

        # --- 12. Close first position (auto-detect direction) ---
        if positions:
            pos = positions[0]
            pos_sym = pos["trade_symbol"]
            vol_lots = pos["trade_volume"] / 10**8
            log.info(f"=== 12. Close position {pos['position_id']} ({pos_sym}) ===")
            r = await client.close_position(pos_sym, position_id=pos["position_id"], volume=vol_lots)
            log.info(f"  {r}")

        # --- Final state ---
        await asyncio.sleep(0.5)
        final = await client.get_positions_and_orders()
        log.info(f"Final: {len(final['positions'])} positions, {len(final['orders'])} orders")

    log.info("Done")
  except MT5ConnectionError as e:
    log.error(f"Connection failed: {e}")
  except AuthenticationError as e:
    log.error(f"Login failed: {e}")
  except PyMT5Error as e:
    log.error(f"MT5 error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
