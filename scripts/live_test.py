"""
Live verification script for pymt5 against MetaQuotes-Demo.
Run: python scripts/live_test.py
"""
import asyncio
import logging
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymt5.constants import (
    CMD_BOOTSTRAP,
    CMD_LOGIN,
    CMD_PING,
    CMD_LOGOUT,
    HEADER_BYTE_LENGTH,
    VALID_COMMANDS,
)
from pymt5.crypto import AESCipher, initial_cipher, initial_key_bytes
from pymt5.helpers import build_client_id, obfuscation_decode
from pymt5.protocol import (
    SeriesCodec,
    build_command,
    pack_outer,
    parse_response_frame,
    unpack_outer,
)

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("live_test")

WS_URI = "wss://web.metatrader.app/terminal"
LOGIN = 5047785364
PASSWORD = "NyCh-i4r"


def hex_dump(data: bytes, limit: int = 128) -> str:
    return data[:limit].hex()


async def step1_raw_ws_connect():
    """Step 1: Just open WebSocket, send bootstrap, see what comes back."""
    import websockets

    log.info("=== Step 1: Raw WebSocket bootstrap ===")
    log.info(f"Connecting to {WS_URI}")

    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                WS_URI,
                ping_interval=None,
                max_size=None,
                additional_headers={
                    "Origin": "https://web.metatrader.app",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
            ),
            timeout=15,
        )
    except Exception as e:
        log.error(f"WebSocket connect failed: {e}")
        return None

    log.info("WebSocket connected!")

    cipher = initial_cipher()
    init_key = initial_key_bytes()
    log.info(f"Initial AES key ({len(init_key)} bytes): {init_key.hex()}")

    token = bytes(64)
    inner = build_command(CMD_BOOTSTRAP, token)
    log.info(f"Bootstrap inner ({len(inner)} bytes): {hex_dump(inner)}")

    encrypted = cipher.encrypt(inner)
    log.info(f"Bootstrap encrypted ({len(encrypted)} bytes): {hex_dump(encrypted)}")

    outer = pack_outer(encrypted)
    log.info(f"Bootstrap outer packet ({len(outer)} bytes): {hex_dump(outer)}")

    log.info("Sending bootstrap...")
    await ws.send(outer)

    log.info("Waiting for response...")
    try:
        response = await asyncio.wait_for(ws.recv(), timeout=15)
    except asyncio.TimeoutError:
        log.error("No response within 15s")
        await ws.close()
        return None

    if isinstance(response, str):
        log.error(f"Got text response (unexpected): {response[:200]}")
        await ws.close()
        return None

    raw = bytes(response)
    log.info(f"Got binary response ({len(raw)} bytes): {hex_dump(raw)}")

    body_len, version, encrypted_body = unpack_outer(raw)
    log.info(f"Outer: body_len={body_len}, version={version}, encrypted_body={len(encrypted_body)} bytes")

    try:
        decrypted = cipher.decrypt(encrypted_body)
        log.info(f"Decrypted ({len(decrypted)} bytes): {hex_dump(decrypted)}")
    except Exception as e:
        log.error(f"Decryption failed: {e}")
        log.info("Trying without decryption (maybe plaintext?)...")
        decrypted = encrypted_body
        log.info(f"Raw body ({len(decrypted)} bytes): {hex_dump(decrypted)}")

    frame = parse_response_frame(decrypted)
    log.info(f"Response frame: command={frame.command}, code={frame.code}, body_len={len(frame.body)}")

    if frame.code != 0:
        log.error(f"Bootstrap failed with code {frame.code}")
        await ws.close()
        return None

    if len(frame.body) < 66:
        log.error(f"Bootstrap response body too short: {len(frame.body)}")
        await ws.close()
        return None

    new_token = frame.body[2:66]
    new_key_material = frame.body[66:]
    log.info(f"New token ({len(new_token)} bytes): {new_token.hex()}")
    log.info(f"New key material ({len(new_key_material)} bytes): {hex_dump(new_key_material)}")

    return ws, new_token, new_key_material, cipher


async def step2_login(ws, token, key_material, old_cipher):
    """Step 2: Login with credentials."""
    log.info("\n=== Step 2: Login ===")

    if len(key_material) not in (16, 24, 32):
        log.warning(f"Key material length is {len(key_material)}, trying first 32/16 bytes")
        if len(key_material) >= 32:
            key_material = key_material[:32]
        elif len(key_material) >= 16:
            key_material = key_material[:16]
        else:
            log.error(f"Key material too short: {len(key_material)}")
            return False

    new_cipher = AESCipher(key_material)
    log.info(f"New cipher initialized with {len(key_material)}-byte key")

    from pymt5.client import MT5WebClient
    client = MT5WebClient()
    cid = build_client_id()
    payload = client._build_login_payload(
        login=LOGIN,
        password=PASSWORD,
        url="web.metatrader.app",
        session=0,
        otp="",
        version=0,
        cid=cid,
        lead_cookie_id=0,
        lead_affiliate_site="",
        utm_campaign="",
        utm_source="",
    )
    log.info(f"Login payload ({len(payload)} bytes): {hex_dump(payload, 64)}...")

    inner = build_command(CMD_LOGIN, payload)
    log.info(f"Login inner ({len(inner)} bytes)")

    encrypted = new_cipher.encrypt(inner)
    log.info(f"Login encrypted ({len(encrypted)} bytes)")

    outer = pack_outer(encrypted)
    log.info(f"Login outer packet ({len(outer)} bytes)")

    log.info("Sending login...")
    await ws.send(outer)

    log.info("Waiting for login response...")
    try:
        response = await asyncio.wait_for(ws.recv(), timeout=15)
    except asyncio.TimeoutError:
        log.error("No login response within 15s")
        return False

    raw = bytes(response)
    log.info(f"Got login response ({len(raw)} bytes): {hex_dump(raw)}")

    _, _, encrypted_body = unpack_outer(raw)

    try:
        decrypted = new_cipher.decrypt(encrypted_body)
        log.info(f"Login decrypted ({len(decrypted)} bytes): {hex_dump(decrypted)}")
    except Exception as e:
        log.error(f"Login decryption with new key failed: {e}")
        try:
            decrypted = old_cipher.decrypt(encrypted_body)
            log.info(f"Login decrypted with OLD key ({len(decrypted)} bytes): {hex_dump(decrypted)}")
        except Exception as e2:
            log.error(f"Login decryption with old key also failed: {e2}")
            return False

    frame = parse_response_frame(decrypted)
    log.info(f"Login response: command={frame.command}, code={frame.code}, body_len={len(frame.body)}")

    if frame.code != 0:
        log.error(f"Login failed with code {frame.code}")
        return False

    log.info("LOGIN SUCCESS!")
    log.info(f"Login response body: {hex_dump(frame.body, 200)}")
    return True


async def step_ping(ws, key_material, old_cipher):
    """Test: send a ping to verify new key works."""
    log.info("\n=== Step 1b: Ping with new key ===")

    if len(key_material) not in (16, 24, 32):
        if len(key_material) >= 32:
            key_material = key_material[:32]
        elif len(key_material) >= 16:
            key_material = key_material[:16]

    new_cipher = AESCipher(key_material)

    # Build ping command (cmd=51, no payload)
    inner = build_command(CMD_PING)
    log.info(f"Ping inner ({len(inner)} bytes): {hex_dump(inner)}")

    encrypted = new_cipher.encrypt(inner)
    outer = pack_outer(encrypted)
    log.info(f"Ping outer ({len(outer)} bytes): {hex_dump(outer)}")

    await ws.send(outer)

    try:
        response = await asyncio.wait_for(ws.recv(), timeout=10)
        raw = bytes(response)
        log.info(f"Ping response ({len(raw)} bytes): {hex_dump(raw)}")

        _, _, enc_body = unpack_outer(raw)
        decrypted = new_cipher.decrypt(enc_body)
        frame = parse_response_frame(decrypted)
        log.info(f"Ping result: cmd={frame.command}, code={frame.code}")
        return True
    except Exception as e:
        log.error(f"Ping failed: {e}")
        # Try with old key
        try:
            _, _, enc_body = unpack_outer(raw)
            decrypted = old_cipher.decrypt(enc_body)
            frame = parse_response_frame(decrypted)
            log.info(f"Ping with OLD key: cmd={frame.command}, code={frame.code}")
        except Exception:
            pass
        return False


async def main():
    result = await step1_raw_ws_connect()
    if result is None:
        log.error("Bootstrap failed, aborting")
        return

    ws, token, key_material, cipher = result
    log.info("Bootstrap succeeded!")

    # First test: ping with new key
    ping_ok = await step_ping(ws, key_material, cipher)
    if not ping_ok:
        log.error("Ping with new key failed - key derivation issue")
        await ws.close()
        return

    login_ok = await step2_login(ws, token, key_material, cipher)
    if login_ok:
        log.info("Full handshake + login verified!")
    else:
        log.warning("Login failed - need to debug protocol further")

    await ws.close()
    log.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
