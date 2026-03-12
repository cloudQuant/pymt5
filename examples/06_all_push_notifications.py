"""
Example 06: All push notification types.

Demonstrates every push handler:
  - on_tick (cmd=8) — real-time bid/ask ticks
  - on_trade_update (cmd=4) — combined position+order changes
  - on_position_update (cmd=4) — position changes only
  - on_order_update (cmd=4) — order changes only
  - on_trade_transaction (cmd=10) — order add/update/delete, balance updates
  - on_account_update (cmd=14) — account balance/equity/margin changes
  - on_symbol_update (cmd=13) — symbol specification changes
  - on_symbol_details (cmd=17) — extended quote data with greeks
  - on_trade_result (cmd=19) — async trade execution results
  - on_login_status (cmd=15) — login status changes
  - on_book_update (cmd=23) — order book / DOM updates
"""
import asyncio
import logging
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example06")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"

LISTEN_SECONDS = 15
counters = {}


def count(name):
    counters[name] = counters.get(name, 0) + 1
    return counters[name]


async def main():
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        await client.login(login=LOGIN, password=PASSWORD)
        await client.load_symbols()
        log.info("Logged in, symbols loaded")

        # --- 1. Tick push (cmd=8) ---
        def on_tick(ticks):
            n = count("tick")
            if n <= 5:
                for t in ticks:
                    name = t.get("symbol", f"id:{t['symbol_id']}")
                    log.info(f"  [TICK #{n}] {name} bid={t.get('bid', 0)} ask={t.get('ask', 0)}")

        client.on_tick(on_tick)

        # --- 2. Trade update (cmd=4, combined positions+orders) ---
        def on_trade(data):
            n = count("trade_update")
            log.info(f"  [TRADE_UPDATE #{n}] {len(data['positions'])} pos, {len(data['orders'])} orders")

        client.on_trade_update(on_trade)

        # --- 3. Trade transaction (cmd=10, order add/update/delete) ---
        def on_transaction(data):
            n = count("transaction")
            update_type = data.get("update_type")
            if update_type == 2:
                bi = data.get("balance_info", {})
                log.info(f"  [TRANSACTION #{n}] Balance update: balance={bi.get('balance', 0):.2f}")
            else:
                txn_type = data.get("transaction_type", -1)
                names = {0: "ADD", 1: "UPDATE", 2: "DELETE"}
                log.info(f"  [TRANSACTION #{n}] type={names.get(txn_type, txn_type)} "
                         f"flag_mask={data.get('flag_mask')}")

        client.on_trade_transaction(on_transaction)

        # --- 4. Account update (cmd=14) ---
        def on_acct(data):
            n = count("account")
            if "balance" in data:
                log.info(f"  [ACCOUNT #{n}] balance={data['balance']:.2f} equity={data.get('equity', 0):.2f} "
                         f"margin={data.get('margin', 0):.2f}")
            else:
                log.info(f"  [ACCOUNT #{n}] update ({len(data)} fields)")

        client.on_account_update(on_acct)

        # --- 5. Symbol update (cmd=13) ---
        def on_sym(result):
            n = count("symbol_update")
            if n <= 3:
                log.info(f"  [SYMBOL_UPDATE #{n}] body={len(result.body)} bytes")

        client.on_symbol_update(on_sym)

        # --- 6. Symbol details (cmd=17, extended with greeks) ---
        def on_details(details):
            n = count("details")
            if n <= 3:
                for d in details[:2]:
                    name = d.get("symbol", f"id:{d['symbol_id']}")
                    log.info(f"  [DETAILS #{n}] {name} bid={d.get('bid', 0)} "
                             f"delta={d.get('delta', 0):.4f} theta={d.get('theta', 0):.4f}")

        client.on_symbol_details(on_details)

        # --- 7. Trade result (cmd=19, async execution results) ---
        def on_result(data):
            n = count("trade_result")
            result_info = data.get("result", {})
            log.info(f"  [TRADE_RESULT #{n}] retcode={result_info.get('retcode', '?')} "
                     f"symbol={data.get('trade_symbol', '?')} price={result_info.get('price', 0)}")

        client.on_trade_result(on_result)

        # --- 8. Login status (cmd=15) ---
        def on_login(result):
            n = count("login_status")
            log.info(f"  [LOGIN_STATUS #{n}] code={result.code}")

        client.on_login_status(on_login)

        # --- 9. Book update (cmd=23) ---
        def on_book(entries):
            n = count("book")
            if n <= 3:
                for e in entries:
                    name = e.get("symbol", f"id:{e['symbol_id']}")
                    log.info(f"  [BOOK #{n}] {name} {len(e.get('bids', []))} bids, {len(e.get('asks', []))} asks")

        client.on_book_update(on_book)

        # --- Subscribe to ticks and order book ---
        watch = ["EURUSD", "GBPUSD", "XAUUSD"]
        await client.subscribe_symbols(watch)
        log.info(f"Subscribed to ticks: {watch}")

        try:
            await client.subscribe_book_by_name(watch)
            log.info(f"Subscribed to order book: {watch}")
        except Exception:
            log.info("Order book subscription not supported by server")

        # --- Listen ---
        log.info(f"Listening for {LISTEN_SECONDS} seconds for all push types...")
        await asyncio.sleep(LISTEN_SECONDS)

        # --- Summary ---
        log.info("--- Push notification summary ---")
        for name, cnt in sorted(counters.items()):
            log.info(f"  {name:20s}: {cnt}")

    log.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
