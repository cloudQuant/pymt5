"""
Example 08: Miscellaneous features — corporate links, notifications,
password change, trader params, verify code, reconnect.

Demonstrates:
  - get_corporate_links (cmd=44) — broker links
  - send_notification (cmd=42) — push notification to server
  - trader_params (cmd=41) — server/currency info
  - change_password (cmd=24) — password change
  - verify_code (cmd=27) — verification code
  - Auto-reconnect configuration
  - Disconnect callback
"""
import asyncio
import logging
import os

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from pymt5 import MT5WebClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("example08")

SERVER = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
    # Create client with auto-reconnect and disconnect callback
    client = MT5WebClient(
        uri=SERVER,
        timeout=30,
        auto_reconnect=True,
        max_reconnect_attempts=3,
        reconnect_delay=2.0,
    )
    client.on_disconnect(lambda: log.warning("Disconnected from server!"))

    try:
        await client.connect()
        await client.login(login=LOGIN, password=PASSWORD)
        await client.load_symbols()
        log.info(f"Logged in, {len(client.symbol_names)} symbols loaded")

        # --- Corporate Links (cmd=44) ---
        log.info("=== Corporate Links (cmd=44) ===")
        try:
            links = await client.get_corporate_links()
            log.info(f"Got {len(links)} corporate links")
            for link in links[:10]:
                log.info(f"  type={link.get('link_type')}  url={link.get('url', '')[:60]}")
                log.info(f"    label={link.get('label', '')}")
        except Exception as exc:
            log.info(f"Corporate links not available: {exc}")

        # --- Trader Params (cmd=41) ---
        log.info("=== Trader Params (cmd=41) ===")
        try:
            first, second = await client.trader_params()
            log.info(f"  param1: {first}")
            log.info(f"  param2: {second}")
        except Exception as exc:
            log.info(f"Trader params failed (some servers disconnect): {exc}")

        # --- Send Notification (cmd=42) ---
        log.info("=== Send Notification (cmd=42) ===")
        try:
            result = await client.send_notification("Hello from pymt5!")
            log.info(f"  Notification sent: code={result.code} body={len(result.body)} bytes")
        except Exception as exc:
            log.info(f"Notification failed: {exc}")

        # --- Verify Code (cmd=27) ---
        log.info("=== Verify Code (cmd=27) ===")
        try:
            result = await client.verify_code("000000")
            log.info(f"  Verify result: code={result.code}")
        except Exception as exc:
            log.info(f"Verify code failed: {exc}")

        # --- Change Password (cmd=24) ---
        # NOTE: This would actually change the password! Only showing the API.
        log.info("=== Change Password (cmd=24) ===")
        log.info("  Skipped (would change actual password)")
        log.info("  Usage: result = await client.change_password('new_pass', 'old_pass')")

        # --- Open Demo (cmd=30) ---
        log.info("=== Open Demo (cmd=30) ===")
        log.info("  Skipped (requires specific server setup)")
        log.info("  Usage: result = await client.open_demo(password='pass')")

        # --- Send Raw Command ---
        log.info("=== Raw Command (ping as example) ===")
        result = await client.send_raw_command(51)
        log.info(f"  Raw ping result: code={result.code}")

        # --- Connection State ---
        log.info(f"is_connected: {client.is_connected}")

    finally:
        await client.close()
        log.info("Closed")


if __name__ == "__main__":
    asyncio.run(main())
