"""
Example 03: Real-time tick subscription with push notifications.

Demonstrates:
  - Loading symbol cache for name resolution
  - Subscribing to ticks by symbol name
  - Receiving real-time bid/ask push updates
  - on_trade_update — combined position+order push
  - on_account_update (cmd=14) — balance/equity changes
  - on_symbol_details (cmd=17) — extended quote data with greeks
  - on_login_status (cmd=15) — login status changes
"""
import asyncio
import logging
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example03")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"

WATCH_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
LISTEN_SECONDS = 10


async def main():
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        await client.login(login=LOGIN, password=PASSWORD)
        log.info("Logged in")

        await client.load_symbols()
        log.info(f"Symbol cache loaded ({len(client.symbol_names)} symbols)")

        tick_count = 0

        # --- Tick callback ---
        def on_tick(ticks):
            nonlocal tick_count
            for t in ticks:
                tick_count += 1
                name = t.get("symbol", f"id:{t['symbol_id']}")
                bid = t.get("bid", 0)
                ask = t.get("ask", 0)
                if tick_count <= 20:
                    log.info(f"  TICK #{tick_count:3d}  {name:10s}  bid={bid}  ask={ask}")

        client.on_tick(on_tick)

        # --- Trade update callback (position + order changes) ---
        def on_trade_change(data):
            log.info(f"  [PUSH] Trade update: {len(data['positions'])} positions, {len(data['orders'])} orders")

        client.on_trade_update(on_trade_change)

        # --- Account update callback (cmd=14) ---
        def on_acct(data):
            if "balance" in data:
                log.info(f"  [PUSH] Account: balance={data['balance']:.2f} equity={data.get('equity', 0):.2f}")
            else:
                log.info(f"  [PUSH] Account update: {len(data)} fields")

        client.on_account_update(on_acct)

        # --- Symbol details callback (cmd=17, extended quotes with greeks) ---
        def on_details(details):
            for d in details[:3]:
                name = d.get("symbol", f"id:{d['symbol_id']}")
                log.info(f"  [PUSH] Symbol details: {name} bid={d.get('bid', 0)} ask={d.get('ask', 0)} "
                         f"delta={d.get('delta', 0):.4f} gamma={d.get('gamma', 0):.4f}")

        client.on_symbol_details(on_details)

        # --- Login status callback (cmd=15) ---
        def on_login(result):
            log.info(f"  [PUSH] Login status: code={result.code}")

        client.on_login_status(on_login)

        # --- Subscribe by symbol names ---
        ids = await client.subscribe_symbols(WATCH_SYMBOLS)
        log.info(f"Subscribed to {WATCH_SYMBOLS} (ids={ids})")

        # --- Wait and collect ticks ---
        log.info(f"Listening for {LISTEN_SECONDS} seconds...")
        await asyncio.sleep(LISTEN_SECONDS)

        log.info(f"Received {tick_count} ticks total")

    log.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
