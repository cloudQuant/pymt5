"""
Live test v4: Use high-level MT5WebClient for end-to-end verification.
Also dumps raw rates bytes for format analysis.
"""
import asyncio
import logging
import os
import struct
import sys
import time

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymt5.client import MT5WebClient
from pymt5.protocol import get_series_size

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v4")

WS_URI = "wss://web.metatrader.app/terminal"
LOGIN_ID = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
    client = MT5WebClient(uri=WS_URI, timeout=15)

    # 1. Connect (bootstrap)
    log.info("=== 1. Connect ===")
    await client.connect()
    log.info("Connected + bootstrap OK")

    # 2. Login
    log.info("=== 2. Login ===")
    token_hex, session_id = await client.login(
        login=LOGIN_ID,
        password=PASSWORD,
        url="web.metatrader.app",
    )
    log.info(f"Login OK: session={session_id}, token={token_hex[:16]}...")

    # No sleep needed - transport recv_loop auto-discards unsolicited pushes (cmd=15)

    # 3. Get symbols (cmd=34, zlib)
    log.info("=== 3. Get Symbols ===")
    try:
        symbols = await client.get_symbols()
        log.info(f"Got {len(symbols)} symbols")
        for s in symbols[:5]:
            log.info(f"  {s['trade_symbol']:20s} id={s['symbol_id']:>6} digits={s['digits']} path={s.get('symbol_path','')[:40]}")
    except Exception as e:
        log.error(f"get_symbols failed: {e}", exc_info=True)
        symbols = []

    # 4. Get positions and orders (cmd=4)
    log.info("=== 4. Get Positions & Orders ===")
    try:
        data = await client.get_positions_and_orders()
        log.info(f"Positions: {len(data['positions'])}, Orders: {len(data['orders'])}")
        for p in data['positions'][:5]:
            log.info(f"  POS: {p['trade_symbol']} vol={p['trade_volume']} profit={p['profit']}")
        for o in data['orders'][:5]:
            log.info(f"  ORD: {o['trade_symbol']} type={o['order_type']} vol={o['volume_initial']}")
    except Exception as e:
        log.error(f"get_positions_and_orders failed: {e}", exc_info=True)

    # 5. Get rates parsed (cmd=11)
    log.info("=== 5. Get Rates (EURUSD M1) ===")
    try:
        now = int(time.time())
        bars = await client.get_rates("EURUSD", 1, now - 3600, now)
        log.info(f"Got {len(bars)} bars")
        for b in bars[:5]:
            t = time.strftime('%Y-%m-%d %H:%M', time.gmtime(b['time']))
            log.info(f"  {t}  O={b['open']:.5f} H={b['high']:.5f} L={b['low']:.5f} C={b['close']:.5f} vol={b['tick_volume']}")
        if bars:
            log.info(f"  ... last bar: {time.strftime('%Y-%m-%d %H:%M', time.gmtime(bars[-1]['time']))}")
    except Exception as e:
        log.error(f"get_rates failed: {e}", exc_info=True)

    # 6. Get trade history (cmd=5)
    log.info("=== 6. Get Trade History ===")
    try:
        now = int(time.time())
        history = await client.get_trade_history(from_ts=now - 86400*30, to_ts=now)
        log.info(f"Deals: {len(history['deals'])}, Orders: {len(history['orders'])}")
        for d in history['deals'][:3]:
            log.info(f"  DEAL: {d.get('trade_symbol','')} action={d.get('trade_action','')} profit={d.get('profit','')}")
    except Exception as e:
        log.error(f"get_trade_history failed: {e}", exc_info=True)

    # 7. Ping
    log.info("=== 7. Ping ===")
    try:
        await client.ping()
        log.info("Ping OK")
    except Exception as e:
        log.error(f"Ping failed: {e}", exc_info=True)

    await client.close()
    log.info("=== ALL DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
