"""
Example 02: Market data — symbol groups, symbols, spreads, rates (K-lines),
and full symbol specifications.

Demonstrates:
  - get_symbol_groups (cmd=9) — category tree (Forex, Crypto, etc.)
  - Loading symbol cache (6000+ symbols)
  - Looking up symbol info by name
  - get_full_symbol_info (cmd=18) — contract size, margins, etc.
  - get_spreads (cmd=20) — spread data with proper schema
  - Fetching OHLCV bars (K-lines) for multiple timeframes
"""
import asyncio
import logging
import os
import time

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient, PERIOD_M1, PERIOD_H1, PERIOD_D1, AuthenticationError, MT5ConnectionError, PyMT5Error

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example02")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
  try:
    async with MT5WebClient(uri=SERVER, timeout=30) as client:
        await client.login(login=LOGIN, password=PASSWORD)
        log.info("Logged in")

        # --- Symbol Groups (cmd=9) ---
        log.info("--- Symbol Groups (cmd=9) ---")
        groups = await client.get_symbol_groups()
        log.info(f"Got {len(groups)} symbol groups")
        for i, g in enumerate(groups[:20]):
            log.info(f"  [{i}] {g}")

        # --- Load Symbol Cache ---
        symbols = await client.load_symbols()
        log.info(f"Loaded {len(symbols)} symbols into cache")

        for name in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD"]:
            info = client.get_symbol_info(name)
            if info:
                log.info(f"  {name:10s}  id={info.symbol_id:>6}  digits={info.digits}  desc={info.description[:40]}")
            else:
                log.info(f"  {name:10s}  not found")

        all_names = client.symbol_names
        log.info(f"First 10 symbols: {all_names[:10]}")

        # --- Spreads (cmd=20) ---
        log.info("--- Spreads (cmd=20) ---")
        eurusd_id = client.get_symbol_id("EURUSD")
        gbpusd_id = client.get_symbol_id("GBPUSD")
        if eurusd_id and gbpusd_id:
            spreads = await client.get_spreads([eurusd_id, gbpusd_id])
            log.info(f"Got {len(spreads)} spread entries")
            for s in spreads[:10]:
                log.info(f"  {s.get('trade_symbol', '?'):12s}  spread_value={s.get('spread_value', 0):.2f}")
        else:
            spreads = await client.get_spreads()
            log.info(f"Got {len(spreads)} spread entries (all)")

        # --- Full Symbol Info (contract specs, cmd=18) ---
        log.info("--- Full Symbol Info ---")
        for name in ["EURUSD", "XAUUSD"]:
            spec = await client.get_full_symbol_info(name)
            if spec:
                log.info(f"  {name}:")
                log.info(f"    Contract size : {spec.get('contract_size')}")
                log.info(f"    Tick size     : {spec.get('tick_size')}")
                log.info(f"    Tick value    : {spec.get('tick_value')}")
                log.info(f"    Spread        : {spec.get('spread')}")
                log.info(f"    Volume min    : {spec.get('volume_min')}")
                log.info(f"    Volume max    : {spec.get('volume_max')}")
                log.info(f"    Base currency : {spec.get('currency_base')}")
            else:
                log.info(f"  {name}: full info not available")

        # --- OHLCV Rates (K-lines) ---
        now = int(time.time())

        log.info("--- EURUSD M1 Rates (last 30 minutes) ---")
        bars = await client.get_rates("EURUSD", PERIOD_M1, now - 1800, now)
        log.info(f"Got {len(bars)} M1 bars")
        for b in bars[-5:]:
            t = time.strftime("%H:%M", time.gmtime(b["time"]))
            log.info(f"  {t}  O={b['open']:.5f}  H={b['high']:.5f}  L={b['low']:.5f}  C={b['close']:.5f}  vol={b['tick_volume']}")

        log.info("--- EURUSD H1 Rates (last 24 hours) ---")
        bars_h1 = await client.get_rates("EURUSD", PERIOD_H1, now - 86400, now)
        log.info(f"Got {len(bars_h1)} H1 bars")
        for b in bars_h1[-5:]:
            t = time.strftime("%Y-%m-%d %H:%M", time.gmtime(b["time"]))
            log.info(f"  {t}  O={b['open']:.5f}  C={b['close']:.5f}  vol={b['tick_volume']}")

        log.info("--- XAUUSD D1 Rates (last 30 days) ---")
        bars_d1 = await client.get_rates("XAUUSD", PERIOD_D1, now - 86400 * 30, now)
        log.info(f"Got {len(bars_d1)} D1 bars")
        for b in bars_d1[-5:]:
            t = time.strftime("%Y-%m-%d", time.gmtime(b["time"]))
            log.info(f"  {t}  O={b['open']:.2f}  H={b['high']:.2f}  L={b['low']:.2f}  C={b['close']:.2f}")

    log.info("Done")
  except MT5ConnectionError as e:
    log.error(f"Connection failed: {e}")
  except AuthenticationError as e:
    log.error(f"Login failed: {e}")
  except PyMT5Error as e:
    log.error(f"MT5 error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
