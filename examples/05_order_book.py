"""
Example 05: Order Book / Depth of Market (Level-2 data).

Demonstrates:
  - subscribe_book (cmd=22) — subscribe to order book for symbols
  - subscribe_book_by_name — name-based subscription
  - on_book_update (cmd=23) — receive DOM push updates with bid/ask levels
"""
import asyncio
import logging
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example05")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"

BOOK_SYMBOLS = ["EURUSD", "GBPUSD"]
LISTEN_SECONDS = 15


async def main():
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        await client.login(login=LOGIN, password=PASSWORD)
        await client.load_symbols()
        log.info("Logged in, symbols loaded")

        book_updates = 0

        # --- Register order book callback ---
        def on_book(entries):
            nonlocal book_updates
            book_updates += 1
            for e in entries:
                name = e.get("symbol", f"id:{e['symbol_id']}")
                bids = e.get("bids", [])
                asks = e.get("asks", [])
                log.info(f"  [BOOK #{book_updates}] {name}: {len(bids)} bids, {len(asks)} asks")
                for b in bids[:3]:
                    log.info(f"    BID  price={b['price']}  vol={b['volume']}")
                for a in asks[:3]:
                    log.info(f"    ASK  price={a['price']}  vol={a['volume']}")

        client.on_book_update(on_book)

        # --- Subscribe to order book by name ---
        try:
            ids = await client.subscribe_book_by_name(BOOK_SYMBOLS)
            log.info(f"Subscribed to order book for {BOOK_SYMBOLS} (ids={ids})")
        except Exception as exc:
            log.warning(f"Order book subscription failed: {exc}")
            log.info("Some servers may not support DOM (cmd=22/23)")

        # --- Also subscribe to ticks for context ---
        def on_tick(ticks):
            for t in ticks[:1]:
                name = t.get("symbol", f"id:{t['symbol_id']}")
                log.info(f"  TICK {name}: bid={t.get('bid', 0)} ask={t.get('ask', 0)}")

        client.on_tick(on_tick)
        await client.subscribe_symbols(BOOK_SYMBOLS)

        log.info(f"Listening for {LISTEN_SECONDS} seconds...")
        await asyncio.sleep(LISTEN_SECONDS)

        log.info(f"Received {book_updates} book updates total")

    log.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
