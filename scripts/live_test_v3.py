"""
Live test v3: correct flow WITHOUT cmd=29 init_session.
Flow: bootstrap → login → drain pushes → loadTypes(cmd=9) → loadSymbols(cmd=34) → loadOpened(cmd=4) → getRates(cmd=11)
"""
import asyncio
import logging
import os
import struct
import sys
import time
import zlib

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymt5.crypto import AESCipher, initial_cipher
from pymt5.protocol import build_command, pack_outer, parse_response_frame, unpack_outer, SeriesCodec, get_series_size
from pymt5.helpers import build_client_id
from pymt5.constants import CMD_BOOTSTRAP, CMD_LOGIN, CMD_PING
from pymt5.schemas import SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES, POSITION_SCHEMA, POSITION_FIELD_NAMES, ORDER_SCHEMA, ORDER_FIELD_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("v3")

WS_URI = "wss://web.metatrader.app/terminal"
LOGIN_ID = 5047785364
PASSWORD = "NyCh-i4r"


class RawSession:
    def __init__(self, ws, cipher):
        self.ws = ws
        self.cipher = cipher

    async def send_cmd(self, cmd, payload=b""):
        inner = build_command(cmd, payload)
        await self.ws.send(pack_outer(self.cipher.encrypt(inner)))

    async def recv_response(self, expected_cmd, timeout=10):
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
                log.info(f"  [push] cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
            except asyncio.TimeoutError:
                break
            except Exception as e:
                log.error(f"  recv error: {e}")
                break
        return None

    async def recv_any(self, timeout=5):
        """Receive the next response regardless of command."""
        try:
            raw = bytes(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            _, _, enc = unpack_outer(raw)
            dec = self.cipher.decrypt(enc)
            return parse_response_frame(dec)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            log.error(f"  recv error: {e}")
            return None

    async def drain_all(self, duration=2):
        """Read all messages for N seconds."""
        end = time.time() + duration
        frames = []
        while time.time() < end:
            remaining = max(0.1, end - time.time())
            try:
                raw = bytes(await asyncio.wait_for(self.ws.recv(), timeout=remaining))
                _, _, enc = unpack_outer(raw)
                dec = self.cipher.decrypt(enc)
                frame = parse_response_frame(dec)
                frames.append(frame)
                log.info(f"  [msg] cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
            except asyncio.TimeoutError:
                break
            except Exception as e:
                log.error(f"  drain error: {e}")
                break
        return frames


async def main():
    import websockets

    log.info("=== 1. Connect ===")
    ws = await asyncio.wait_for(
        websockets.connect(WS_URI, ping_interval=None, max_size=None, open_timeout=15,
                           additional_headers={"Origin": "https://web.metatrader.app"}),
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
    assert frame.code == 0
    cipher = AESCipher(frame.body[66:])
    session = RawSession(ws, cipher)

    # Login
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
    if not frame or frame.code != 0:
        log.error(f"Login failed: {frame}")
        await ws.close()
        return
    log.info(f"Login: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")

    # Drain post-login pushes
    log.info("=== 3. Drain pushes ===")
    frames = await session.drain_all(duration=2)
    log.info(f"Drained {len(frames)} messages")

    # loadTypes (cmd=9) - NO cmd=29!
    log.info("=== 4. loadTypes (cmd=9) ===")
    await session.send_cmd(9, b"")
    frame = await session.recv_response(9, timeout=10)
    if frame:
        log.info(f"loadTypes: cmd={frame.command}, code={frame.code}, body_len={len(frame.body)}")
        if frame.body and len(frame.body) >= 4:
            count = struct.unpack_from("<I", frame.body, 0)[0]
            log.info(f"  Symbol type groups: {count}")
    else:
        log.info("No loadTypes response")

    # Drain any pushes
    frames = await session.drain_all(duration=1)

    # loadSymbols (cmd=34, zlib compressed)
    log.info("=== 5. loadSymbols (cmd=34) ===")
    await session.send_cmd(34, b"")
    frame = await session.recv_response(34, timeout=15)
    symbols = []
    if frame and frame.body:
        log.info(f"cmd=34: code={frame.code}, body_len={len(frame.body)}")
        log.info(f"  Raw body hex: {frame.body.hex()}")
        if len(frame.body) > 4:
            compressed = bytes(frame.body[4:])
            try:
                raw_data = zlib.decompress(compressed)
            except Exception:
                import gzip
                raw_data = gzip.decompress(compressed)
            log.info(f"  Decompressed: {len(raw_data)} bytes")
            log.info(f"  Decompressed hex[:80]: {raw_data[:80].hex()}")
            if len(raw_data) >= 4:
                count = struct.unpack_from("<I", raw_data, 0)[0]
                log.info(f"  Symbol count in data: {count}")
                record_size = get_series_size(SYMBOL_BASIC_SCHEMA)
                log.info(f"  Expected record size: {record_size}, total expected: {4 + count * record_size}")
                offset = 4
                for i in range(min(count, 10)):
                    if offset + record_size > len(raw_data):
                        log.info(f"  Truncated at symbol {i}")
                        break
                    vals = SeriesCodec.parse(raw_data, SYMBOL_BASIC_SCHEMA, offset)
                    s = dict(zip(SYMBOL_BASIC_FIELD_NAMES, vals))
                    symbols.append(s)
                    log.info(f"  #{i}: {s.get('symbol', '?'):20s} id={s.get('id', '?'):>6} digits={s.get('digits', '?')}")
                    offset += record_size
                if count > 10:
                    log.info(f"  ... and {count - 10} more")
    else:
        log.info("No cmd=34 response")

    # Drain pushes
    frames = await session.drain_all(duration=1)

    # loadOpened (cmd=4)
    log.info("=== 6. loadOpened (cmd=4) ===")
    try:
        await session.send_cmd(4, b"")
        frame = await session.recv_response(4, timeout=10)
        if frame:
            log.info(f"cmd=4: code={frame.code}, body_len={len(frame.body)}")
            if frame.body and len(frame.body) >= 4:
                pos_count = struct.unpack_from("<I", frame.body, 0)[0]
                log.info(f"  Position count: {pos_count}")
        else:
            log.info("No cmd=4 response")
    except Exception as e:
        log.error(f"cmd=4 failed: {e}")

    # getRates (cmd=11)
    log.info("=== 7. getRates (cmd=11, EURUSD M1) ===")
    try:
        from pymt5.constants import PROP_FIXED_STRING, PROP_U16, PROP_I32
        now = int(time.time())
        rates_payload = SeriesCodec.serialize([
            (PROP_FIXED_STRING, "EURUSD", 64),
            (PROP_U16, 1),
            (PROP_I32, now - 3600),
            (PROP_I32, now),
        ])
        await session.send_cmd(11, rates_payload)
        frame = await session.recv_response(11, timeout=10)
        if frame:
            log.info(f"Rates: code={frame.code}, body_len={len(frame.body)}")
            if frame.body:
                log.info(f"  Body hex[:80]: {frame.body[:80].hex()}")
        else:
            log.info("No rates response")
    except Exception as e:
        log.error(f"getRates failed: {e}")

    await ws.close()
    log.info("=== ALL DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
