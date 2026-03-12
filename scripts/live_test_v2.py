"""
Live test v2: proper flow with init_session, cmd=34 gzip symbols, drain pushes.
Flow: bootstrap → login → drain pushes → init_session → get_symbols(gzip) → get_positions → get_rates
"""
import asyncio
import gzip
import logging
import zlib
import os
import struct
import sys
import time

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymt5.crypto import AESCipher, initial_cipher
from pymt5.protocol import build_command, pack_outer, parse_response_frame, unpack_outer, SeriesCodec, get_series_size
from pymt5.helpers import build_client_id
from pymt5.constants import CMD_BOOTSTRAP, CMD_LOGIN, CMD_PING
from pymt5.schemas import SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES, POSITION_SCHEMA, POSITION_FIELD_NAMES, ORDER_SCHEMA, ORDER_FIELD_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("v2")

WS_URI = "wss://web.metatrader.app/terminal"
LOGIN_ID = 5047785364
PASSWORD = "NyCh-i4r"


class RawSession:
    """Low-level session that handles send/recv with push draining."""

    def __init__(self, ws, cipher):
        self.ws = ws
        self.cipher = cipher
        self._push_buffer = []

    async def send_cmd(self, cmd, payload=b""):
        inner = build_command(cmd, payload)
        await self.ws.send(pack_outer(self.cipher.encrypt(inner)))

    async def recv_response(self, expected_cmd, timeout=10):
        """Recv until we get expected_cmd, buffering pushes."""
        end = time.time() + timeout
        while time.time() < end:
            remaining = max(0.1, end - time.time())
            try:
                raw = bytes(await asyncio.wait_for(self.ws.recv(), timeout=remaining))
                _, _, enc = unpack_outer(raw)
                dec = self.cipher.decrypt(enc)
                frame = parse_response_frame(dec)
                if frame.command == expected_cmd:
                    return frame
                else:
                    log.info(f"  [push] cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
                    self._push_buffer.append(frame)
            except asyncio.TimeoutError:
                break
        return None

    async def drain_pushes(self, duration=2):
        """Drain all unsolicited pushes for N seconds."""
        end = time.time() + duration
        count = 0
        while time.time() < end:
            remaining = max(0.1, end - time.time())
            try:
                raw = bytes(await asyncio.wait_for(self.ws.recv(), timeout=remaining))
                _, _, enc = unpack_outer(raw)
                dec = self.cipher.decrypt(enc)
                frame = parse_response_frame(dec)
                count += 1
                log.info(f"  [drain] cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
            except asyncio.TimeoutError:
                break
        return count


def parse_symbols_from_body(body):
    """Parse symbol list from response body (with uint32 count prefix)."""
    if len(body) < 4:
        return []
    count = struct.unpack_from("<I", body, 0)[0]
    record_size = get_series_size(SYMBOL_BASIC_SCHEMA)
    symbols = []
    offset = 4
    for _ in range(count):
        if offset + record_size > len(body):
            break
        vals = SeriesCodec.parse(body, SYMBOL_BASIC_SCHEMA, offset)
        symbols.append(dict(zip(SYMBOL_BASIC_FIELD_NAMES, vals)))
        offset += record_size
    return symbols


async def main():
    import websockets

    # 1. Connect
    log.info("=== 1. Connect ===")
    ws = await asyncio.wait_for(
        websockets.connect(WS_URI, ping_interval=None, max_size=None,
                           open_timeout=15,
                           additional_headers={"Origin": "https://web.metatrader.app"}),
        timeout=15,
    )
    log.info("WS connected")

    # 2. Bootstrap
    cipher = initial_cipher()
    inner = build_command(CMD_BOOTSTRAP, bytes(64))
    await ws.send(pack_outer(cipher.encrypt(inner)))
    raw = bytes(await asyncio.wait_for(ws.recv(), timeout=10))
    _, _, enc = unpack_outer(raw)
    frame = parse_response_frame(cipher.decrypt(enc))
    log.info(f"Bootstrap: cmd={frame.command}, code={frame.code}")
    assert frame.code == 0
    cipher = AESCipher(frame.body[66:])
    session = RawSession(ws, cipher)

    # 3. Login
    log.info("=== 2. Login ===")
    from pymt5.client import MT5WebClient
    client = MT5WebClient()
    payload = client._build_login_payload(
        login=LOGIN_ID, password=PASSWORD, url="web.metatrader.app",
        session=0, otp="", version=0, cid=build_client_id(),
        lead_cookie_id=0, lead_affiliate_site="", utm_campaign="", utm_source="",
    )
    await session.send_cmd(CMD_LOGIN, payload)
    frame = await session.recv_response(CMD_LOGIN, timeout=10)
    if frame:
        log.info(f"Login: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
    else:
        log.error("Login response not received")
        await ws.close()
        return

    # 4. Drain post-login pushes (cmd=15 etc)
    log.info("=== 3. Drain pushes ===")
    n = await session.drain_pushes(duration=2)
    log.info(f"Drained {n} pushes")

    # 5. Init session (cmd=29)
    log.info("=== 4. Init Session ===")
    init_payload = client._build_init_payload(version=0, password="", otp="", cid=build_client_id())
    await session.send_cmd(29, init_payload)
    frame = await session.recv_response(29, timeout=10)
    if frame:
        log.info(f"Init: cmd={frame.command}, code={frame.code}")
    else:
        log.info("Init: no direct response (may be handled via push)")

    # 6. Get symbols via cmd=34 (gzip compressed)
    log.info("=== 5. Get Symbols (cmd=34, gzip) ===")
    await session.send_cmd(34, b"")
    frame = await session.recv_response(34, timeout=15)
    if frame and frame.body:
        log.info(f"Symbols gzip response: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
        try:
            # Response format: [4-byte something] + zlib/gzip compressed data
            if len(frame.body) > 4:
                compressed = bytes(frame.body[4:])
                # Try zlib first (header 78 xx), then gzip
                try:
                    raw_data = zlib.decompress(compressed)
                except zlib.error:
                    raw_data = gzip.decompress(compressed)
                log.info(f"Decompressed symbols: {len(raw_data)} bytes")
                symbols = parse_symbols_from_body(raw_data)
                log.info(f"Parsed {len(symbols)} symbols")
                for s in symbols[:5]:
                    log.info(f"  {s.get('symbol', '?'):20s} id={s.get('id', '?'):>6} digits={s.get('digits', '?')}")
                if len(symbols) > 5:
                    log.info(f"  ... and {len(symbols) - 5} more")
            else:
                log.info(f"Response body too short: {frame.body.hex()}")
        except Exception as e:
            log.error(f"Decompress failed: {e}")
            log.info(f"Raw body hex: {frame.body.hex()}")
    else:
        log.info("No symbols response")
        # Try cmd=6 as fallback
        log.info("Trying cmd=6 (uncompressed)...")
        await session.send_cmd(6, b"")
        frame = await session.recv_response(6, timeout=10)
        if frame:
            log.info(f"cmd=6 response: body_len={len(frame.body)}, preview={frame.body[:20].hex()}")

    # 7. Get positions and orders (cmd=4)
    log.info("=== 6. Get Positions & Orders (cmd=4) ===")
    await session.send_cmd(4, b"")
    frame = await session.recv_response(4, timeout=10)
    if frame:
        log.info(f"cmd=4 response: code={frame.code}, body_len={len(frame.body)}")
        body = frame.body
        if body and len(body) >= 4:
            pos_count = struct.unpack_from("<I", body, 0)[0]
            log.info(f"Position count: {pos_count}")
            pos_size = get_series_size(POSITION_SCHEMA)
            order_offset = 4 + pos_count * pos_size
            if order_offset + 4 <= len(body):
                order_count = struct.unpack_from("<I", body, order_offset)[0]
                log.info(f"Order count: {order_count}")
    else:
        log.info("No cmd=4 response")

    # 8. Get rates (cmd=11, EURUSD M1 last hour)
    log.info("=== 7. Get Rates (cmd=11) ===")
    from pymt5.constants import PROP_FIXED_STRING, PROP_U16, PROP_I32
    now = int(time.time())
    rates_payload = SeriesCodec.serialize([
        (PROP_FIXED_STRING, "EURUSD", 64),
        (PROP_U16, 1),  # M1
        (PROP_I32, now - 3600),
        (PROP_I32, now),
    ])
    await session.send_cmd(11, rates_payload)
    frame = await session.recv_response(11, timeout=10)
    if frame:
        log.info(f"Rates response: code={frame.code}, body_len={len(frame.body)}")
    else:
        log.info("No rates response")

    # 9. Subscribe ticks (cmd=7)
    log.info("=== 8. Subscribe Ticks (cmd=7) ===")
    if 'symbols' in dir() and symbols:
        sub_ids = [s["id"] for s in symbols[:3] if "id" in s]
    else:
        sub_ids = [1, 2, 3]
    tick_payload = struct.pack(f"<{len(sub_ids)+1}I", len(sub_ids), *sub_ids)
    await session.send_cmd(7, tick_payload)
    # Read tick pushes (cmd=8) for a few seconds
    log.info("Waiting for tick pushes (cmd=8)...")
    end = time.time() + 5
    tick_count = 0
    while time.time() < end:
        remaining = max(0.1, end - time.time())
        try:
            raw = bytes(await asyncio.wait_for(ws.recv(), timeout=remaining))
            _, _, enc_body = unpack_outer(raw)
            dec = cipher.decrypt(enc_body)
            f = parse_response_frame(dec)
            if f.command == 8:
                tick_count += 1
                if tick_count <= 3:
                    log.info(f"  TICK push: body_len={len(f.body)}")
            else:
                log.info(f"  [other] cmd={f.command}, body_len={len(f.body)}")
        except asyncio.TimeoutError:
            break
        except Exception as e:
            log.error(f"Recv error: {e}")
            break
    log.info(f"Received {tick_count} tick pushes")

    await ws.close()
    log.info("=== ALL DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
