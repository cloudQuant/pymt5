"""
Live integration test using the high-level MT5WebClient.
Tests: connect, login, get_symbols, get_positions_and_orders, get_rates, subscribe_ticks.
Run: NO_PROXY="*" python scripts/live_test_client.py
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("NO_PROXY", "*")

from pymt5.client import MT5WebClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("live_client")

WS_URI = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
    client = MT5WebClient(uri=WS_URI, timeout=30.0)

    # 1. Connect (bootstrap)
    log.info("=== 1. Connecting ===")
    await client.connect()
    log.info("Connected (bootstrap OK)")

    # 2. Login
    log.info("=== 2. Login ===")
    token_hex, session_id = await client.login(
        login=LOGIN,
        password=PASSWORD,
        url="web.metatrader.app",
    )
    log.info(f"Login OK: session={session_id}, token={token_hex[:32]}...")

    # 3. Ping
    log.info("=== 3. Ping ===")
    await client.ping()
    log.info("Ping OK")

    # 4. Get symbols
    log.info("=== 4. Get Symbols ===")
    try:
        symbols = await client.get_symbols()
        log.info(f"Got {len(symbols)} symbols")
        for s in symbols[:5]:
            log.info(f"  {s.get('symbol', '?'):20s} id={s.get('id', '?'):>6} digits={s.get('digits', '?')} currency={s.get('currency', '?')}")
        if len(symbols) > 5:
            log.info(f"  ... and {len(symbols) - 5} more")
    except Exception as e:
        log.error(f"get_symbols failed: {e}")

    # 5. Get positions and orders
    log.info("=== 5. Get Positions & Orders ===")
    try:
        data = await client.get_positions_and_orders()
        log.info(f"Positions: {len(data['positions'])}, Orders: {len(data['orders'])}")
        for p in data["positions"][:3]:
            log.info(f"  POS: {p.get('trade_symbol', '?')} vol={p.get('trade_volume', '?')} profit={p.get('profit', '?')}")
        for o in data["orders"][:3]:
            log.info(f"  ORD: {o.get('trade_symbol', '?')} type={o.get('order_type', '?')} price={o.get('price_order', '?')}")
    except Exception as e:
        log.error(f"get_positions_and_orders failed: {e}")

    # 6. Get rates (EURUSD M1 last hour)
    log.info("=== 6. Get Rates ===")
    try:
        now = int(time.time())
        rates_body = await client.get_rates(
            symbol="EURUSD",
            period_minutes=1,
            from_ts=now - 3600,
            to_ts=now,
        )
        log.info(f"Rates response body: {len(rates_body)} bytes")
    except Exception as e:
        log.error(f"get_rates failed: {e}")

    # 7. Subscribe to ticks
    log.info("=== 7. Subscribe Ticks ===")
    tick_count = 0
    tick_event = asyncio.Event()

    def on_ticks(ticks):
        nonlocal tick_count
        for t in ticks:
            tick_count += 1
            if tick_count <= 3:
                log.info(f"  TICK: sym_id={t.get('symbol_id')} bid={t.get('bid')} ask={t.get('ask')} time_ms={t.get('tick_time_ms')}")
        if tick_count >= 1:
            tick_event.set()

    try:
        if symbols:
            sub_ids = [s["id"] for s in symbols[:5] if "id" in s]
            if sub_ids:
                client.on_tick(on_ticks)
                await client.subscribe_ticks(sub_ids)
                log.info(f"Subscribed to {len(sub_ids)} symbols, waiting for ticks (max 10s)...")
                try:
                    await asyncio.wait_for(tick_event.wait(), timeout=10)
                    log.info(f"Received {tick_count} ticks total")
                except asyncio.TimeoutError:
                    log.info(f"Timeout, received {tick_count} ticks")
    except Exception as e:
        log.error(f"subscribe_ticks failed: {e}")

    # 8. Get trade history (last 30 days)
    log.info("=== 8. Get Trade History ===")
    try:
        now = int(time.time())
        history = await client.get_trade_history(from_ts=now - 86400 * 30, to_ts=now)
        log.info(f"Deals: {len(history['deals'])}, Orders: {len(history['orders'])}")
        for d in history["deals"][:3]:
            log.info(f"  DEAL: {d.get('trade_symbol', '?')} action={d.get('trade_action', '?')} profit={d.get('profit', '?')}")
    except Exception as e:
        log.error(f"get_trade_history failed: {e}")

    # 9. Logout & close
    log.info("=== 9. Logout ===")
    try:
        await client.logout()
        log.info("Logout OK")
    except Exception:
        pass
    await client.close()
    log.info("=== ALL DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
