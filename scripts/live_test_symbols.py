"""
Focused live test: after login, send cmd=6 (get_symbols) and log ALL
responses that arrive to discover if the server uses a different command ID.
"""
import asyncio
import logging
import os
import sys
import struct

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymt5.crypto import AESCipher, initial_cipher
from pymt5.protocol import build_command, pack_outer, parse_response_frame, unpack_outer
from pymt5.helpers import build_client_id
from pymt5.constants import CMD_BOOTSTRAP, CMD_LOGIN, CMD_PING

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("test")

WS_URI = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


async def main():
    import websockets

    log.info("Connecting...")
    ws = await asyncio.wait_for(
        websockets.connect(
            WS_URI,
            ping_interval=None,
            max_size=None,
            open_timeout=15,
            additional_headers={"Origin": "https://web.metatrader.app"},
        ),
        timeout=15,
    )
    log.info("WS connected")

    # Bootstrap
    cipher = initial_cipher()
    inner = build_command(CMD_BOOTSTRAP, bytes(64))
    await ws.send(pack_outer(cipher.encrypt(inner)))
    raw = bytes(await asyncio.wait_for(ws.recv(), timeout=10))
    _, _, enc = unpack_outer(raw)
    frame = parse_response_frame(cipher.decrypt(enc))
    log.info(f"Bootstrap: cmd={frame.command}, code={frame.code}")
    cipher = AESCipher(frame.body[66:])

    # Login
    from pymt5.client import MT5WebClient
    client = MT5WebClient()
    payload = client._build_login_payload(
        login=LOGIN, password=PASSWORD, url="web.metatrader.app",
        session=0, otp="", version=0, cid=build_client_id(),
        lead_cookie_id=0, lead_affiliate_site="", utm_campaign="", utm_source="",
    )
    inner = build_command(CMD_LOGIN, payload)
    await ws.send(pack_outer(cipher.encrypt(inner)))
    raw = bytes(await asyncio.wait_for(ws.recv(), timeout=10))
    _, _, enc = unpack_outer(raw)
    frame = parse_response_frame(cipher.decrypt(enc))
    log.info(f"Login: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")

    if frame.code != 0:
        log.error("Login failed")
        await ws.close()
        return

    # Ping
    inner = build_command(CMD_PING)
    await ws.send(pack_outer(cipher.encrypt(inner)))
    raw = bytes(await asyncio.wait_for(ws.recv(), timeout=10))
    _, _, enc = unpack_outer(raw)
    frame = parse_response_frame(cipher.decrypt(enc))
    log.info(f"Ping: cmd={frame.command}, code={frame.code}")

    # Init session (cmd=29) — required before data commands
    log.info("Sending cmd=29 (init_session)...")
    init_payload = client._build_init_payload(version=0, password="", otp="", cid=build_client_id())
    inner = build_command(29, init_payload)
    await ws.send(pack_outer(cipher.encrypt(inner)))

    # Read init response(s)
    import time
    end_time = time.time() + 8
    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        try:
            raw = bytes(await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5)))
            _, _, enc = unpack_outer(raw)
            dec = cipher.decrypt(enc)
            frame = parse_response_frame(dec)
            log.info(f"  Init resp: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
            if frame.command == 29:
                log.info("  Init session acknowledged")
                break
        except asyncio.TimeoutError:
            log.info("No init response (timeout)")
            break

    # Send get_symbols (cmd=6)
    log.info("Sending cmd=6 (get_symbols)...")
    inner = build_command(6, b"")
    await ws.send(pack_outer(cipher.encrypt(inner)))

    # Read ALL responses for 10 seconds
    end_time = time.time() + 10
    resp_count = 0
    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        try:
            raw = bytes(await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5)))
            _, _, enc = unpack_outer(raw)
            dec = cipher.decrypt(enc)
            frame = parse_response_frame(dec)
            resp_count += 1
            body_preview = frame.body[:40].hex() if frame.body else "(empty)"
            log.info(f"  Resp #{resp_count}: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}, preview={body_preview}")
        except asyncio.TimeoutError:
            log.info("No more responses (timeout)")
            break
        except Exception as e:
            log.error(f"Error reading response: {e}")
            break

    log.info(f"Total responses after cmd=6: {resp_count}")

    # Also try cmd=4 (positions+orders)
    log.info("Sending cmd=4 (get_positions_and_orders)...")
    inner = build_command(4, b"")
    await ws.send(pack_outer(cipher.encrypt(inner)))

    end_time = time.time() + 8
    resp_count = 0
    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        try:
            raw = bytes(await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5)))
            _, _, enc = unpack_outer(raw)
            dec = cipher.decrypt(enc)
            frame = parse_response_frame(dec)
            resp_count += 1
            body_preview = frame.body[:40].hex() if frame.body else "(empty)"
            log.info(f"  Resp #{resp_count}: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}, preview={body_preview}")
        except asyncio.TimeoutError:
            log.info("No more responses (timeout)")
            break
        except Exception as e:
            log.error(f"Error reading response: {e}")
            break

    log.info(f"Total responses after cmd=4: {resp_count}")

    await ws.close()
    log.info("DONE")


if __name__ == "__main__":
    asyncio.run(main())
