import struct
import zlib

from pymt5.client import MT5WebClient, _parse_counted_records, _parse_rate_bars
from pymt5.crypto import initial_cipher, initial_key_bytes
from pymt5.helpers import decode_utf16le, encode_utf16le
from pymt5.protocol import SeriesCodec, build_command, get_series_size, pack_outer, parse_response_frame
from pymt5.schemas import (
    DEAL_SCHEMA,
    ORDER_SCHEMA,
    POSITION_SCHEMA,
    RATE_BAR_SCHEMA,
    SYMBOL_BASIC_FIELD_NAMES,
    SYMBOL_BASIC_SCHEMA,
    TICK_SCHEMA,
)


def test_initial_key_length():
    key = initial_key_bytes()
    assert len(key) in (16, 24, 32)
    assert len(key) == 32


def test_bootstrap_packet_size_matches_browser_capture():
    cipher = initial_cipher()
    token = bytes(64)
    inner = build_command(0, token)
    assert len(inner) == 68
    encrypted = cipher.encrypt(inner)
    assert len(encrypted) == 80
    outer = pack_outer(encrypted)
    assert len(outer) == 88


def test_login_packet_size_matches_browser_capture():
    client = MT5WebClient()
    payload = client._build_login_payload(
        login=12345678,
        password="test-password",
        url="",
        session=0,
        otp="",
        version=0,
        cid=b"0123456789abcdef",
        lead_cookie_id=0,
        lead_affiliate_site="",
        utm_campaign="",
        utm_source="",
    )
    assert len(payload) == 912
    inner = build_command(28, payload)
    assert len(inner) == 916
    encrypted = initial_cipher().encrypt(inner)
    assert len(encrypted) == 928
    outer = pack_outer(encrypted)
    assert len(outer) == 936


def test_init_packet_size_matches_browser_capture():
    client = MT5WebClient()
    payload = client._build_init_payload(
        version=0,
        password="",
        otp="",
        cid=b"0123456789abcdef",
    )
    assert len(payload) == 744
    inner = build_command(29, payload)
    assert len(inner) == 748
    encrypted = initial_cipher().encrypt(inner)
    assert len(encrypted) == 752
    outer = pack_outer(encrypted)
    assert len(outer) == 760


def test_response_frame_parsing_offsets():
    raw = bytes([0xAA, 0xBB, 0x1C, 0x00, 0x05]) + b"hello"
    frame = parse_response_frame(raw)
    assert frame.command == 28
    assert frame.code == 5
    assert frame.body == b"hello"


def test_utf16le_encode_decode_roundtrip():
    text = "NyCh-i4r"
    encoded = encode_utf16le(text, 64)
    assert len(encoded) == 64
    assert encoded[:2] == b"N\x00"  # 'N' as UTF-16LE
    assert encoded[2:4] == b"y\x00"  # 'y' as UTF-16LE
    decoded = decode_utf16le(encoded)
    assert decoded == text


def test_utf16le_empty_string():
    encoded = encode_utf16le("", 64)
    assert encoded == bytes(64)
    assert decode_utf16le(encoded) == ""


def test_prop_time_roundtrip():
    from pymt5.constants import PROP_TIME

    ts_ms = 1710501234567
    payload = SeriesCodec.serialize([(PROP_TIME, ts_ms)])
    assert len(payload) == 8
    assert SeriesCodec.parse(payload, [{"propType": PROP_TIME}]) == [ts_ms]


def test_login_payload_password_is_utf16le():
    client = MT5WebClient()
    payload = client._build_login_payload(
        login=12345678,
        password="AB",
        url="",
        session=0,
        otp="",
        version=0,
        cid=b"0123456789abcdef",
        lead_cookie_id=0,
        lead_affiliate_site="",
        utm_campaign="",
        utm_source="",
    )
    # password field starts at offset 4 (after u32 version), length 64
    pw_field = payload[4:68]
    assert pw_field[0:2] == b"A\x00"  # 'A' in UTF-16LE
    assert pw_field[2:4] == b"B\x00"  # 'B' in UTF-16LE
    assert pw_field[4:] == bytes(60)   # rest is zero-padded


# ---- Schema size tests ----

def test_symbol_basic_schema_size():
    # JS: Bh = [FS64, FS128, U32, U32, FS256, U32, FS64, U16]
    # 64 + 128 + 4 + 4 + 256 + 4 + 64 + 2 = 526
    assert get_series_size(SYMBOL_BASIC_SCHEMA) == 526


def test_position_schema_size():
    # mu: [I64, I64, U32, U32, FS64, U32, F64*4, U64, F64*5, I64, I64, FS64, F64, U32*3, FS64, I32, I32]
    expected = 8 + 8 + 4 + 4 + 64 + 4 + 8*4 + 8 + 8*5 + 8 + 8 + 64 + 8 + 4*3 + 64 + 4 + 4
    assert get_series_size(POSITION_SCHEMA) == expected


def test_order_schema_size():
    # Ld: [I64, FS64, FS64, U32*7, F64*5, I64*2, U32, I64*2, FS64, F64, U32*2, F64*3, U32, I32*2]
    expected = (8 + 64 + 64 + 4*7 + 8*5 + 8*2 + 4 + 8*2 + 64 + 8 + 4*2 + 8*3 + 4 + 4*2)
    assert get_series_size(ORDER_SCHEMA) == expected


def test_tick_schema_size():
    # [U32, I32, U32, F64, F64, F64, I64, U32, U16]
    expected = 4 + 4 + 4 + 8 + 8 + 8 + 8 + 4 + 2
    assert get_series_size(TICK_SCHEMA) == expected


# ---- Roundtrip serialize/parse tests ----

def test_symbol_basic_roundtrip():
    from pymt5.constants import PROP_FIXED_STRING, PROP_U16, PROP_U32
    fields = [
        (PROP_FIXED_STRING, "EURUSD", 64),
        (PROP_FIXED_STRING, "Forex\\Major", 128),
        (PROP_U32, 5),       # digits
        (PROP_U32, 42),      # symbol_id
        (PROP_FIXED_STRING, "Euro vs US Dollar", 256),
        (PROP_U32, 0),       # trade_calc_mode
        (PROP_FIXED_STRING, "USD", 64),
        (PROP_U16, 1),       # sector
    ]
    data = SeriesCodec.serialize(fields)
    assert len(data) == 526
    vals = SeriesCodec.parse(data, SYMBOL_BASIC_SCHEMA)
    assert vals[0] == "EURUSD"
    assert vals[2] == 5
    assert vals[3] == 42


def test_parse_counted_records():
    from pymt5.constants import PROP_FIXED_STRING, PROP_U16, PROP_U32
    rec = SeriesCodec.serialize([
        (PROP_FIXED_STRING, "GBPUSD", 64),
        (PROP_FIXED_STRING, "", 128),
        (PROP_U32, 5),
        (PROP_U32, 99),
        (PROP_FIXED_STRING, "", 256),
        (PROP_U32, 0),
        (PROP_FIXED_STRING, "USD", 64),
        (PROP_U16, 0),
    ])
    body = struct.pack("<I", 1) + rec
    records = _parse_counted_records(body, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)
    assert len(records) == 1
    assert records[0]["trade_symbol"] == "GBPUSD"
    assert records[0]["symbol_id"] == 99
    assert records[0]["digits"] == 5


def test_parse_counted_records_empty():
    assert _parse_counted_records(b"", SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES) == []
    assert _parse_counted_records(b"\x00\x00\x00\x00", SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES) == []


def test_zlib_symbols_decompression():
    from pymt5.constants import PROP_FIXED_STRING, PROP_U16, PROP_U32
    rec = SeriesCodec.serialize([
        (PROP_FIXED_STRING, "USDJPY", 64),
        (PROP_FIXED_STRING, "", 128),
        (PROP_U32, 3),
        (PROP_U32, 7),
        (PROP_FIXED_STRING, "", 256),
        (PROP_U32, 0),
        (PROP_FIXED_STRING, "JPY", 64),
        (PROP_U16, 0),
    ])
    raw_data = struct.pack("<I", 1) + rec
    compressed = zlib.compress(raw_data)
    # Simulate cmd=34 response body: [4 bytes header] + compressed
    response_body = struct.pack("<I", len(raw_data)) + compressed
    # Parse like get_symbols does
    decompressed = zlib.decompress(bytes(response_body[4:]))
    records = _parse_counted_records(decompressed, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)
    assert len(records) == 1
    assert records[0]["trade_symbol"] == "USDJPY"
    assert records[0]["symbol_id"] == 7


# ---- Rate bar tests ----

def test_rate_bar_schema_size():
    # I32 + F64*4 + I64 + I32 = 4 + 32 + 8 + 4 = 48
    assert get_series_size(RATE_BAR_SCHEMA) == 48


def test_parse_rate_bars():
    # Build 2 fake bars using struct (matching the live-verified format)
    bar1 = struct.pack("<iddddqi", 1773293460, 1.15364, 1.15369, 1.15359, 1.15364, 57, 0)
    bar2 = struct.pack("<iddddqi", 1773293520, 1.15364, 1.15371, 1.15363, 1.15366, 46, 0)
    body = bar1 + bar2
    assert len(body) == 96  # 2 * 48
    bars = _parse_rate_bars(body)
    assert len(bars) == 2
    assert bars[0]["time"] == 1773293460
    assert abs(bars[0]["open"] - 1.15364) < 1e-10
    assert abs(bars[0]["high"] - 1.15369) < 1e-10
    assert abs(bars[0]["low"] - 1.15359) < 1e-10
    assert abs(bars[0]["close"] - 1.15364) < 1e-10
    assert bars[0]["tick_volume"] == 57
    assert bars[0]["spread"] == 0
    assert bars[1]["time"] == 1773293520
    assert bars[1]["tick_volume"] == 46


def test_parse_rate_bars_empty():
    assert _parse_rate_bars(b"") == []
    assert _parse_rate_bars(None) == []


def test_deal_schema_size():
    # Pd: I64 + FS64 + I64 + U32*2 + FS64 + U32*2 + F64*4 + U64 + F64*5 + I64*2 + FS64 + F64 + U32*3 + I32*2 + F64
    expected = (8 + 64 + 8 + 4*2 + 64 + 4*2 + 8*4 + 8 + 8*5 + 8*2 + 64 + 8 + 4*3 + 4*2 + 8)
    assert get_series_size(DEAL_SCHEMA) == expected


# ---- Error path tests (Phase 4.3) ----

def test_unpack_outer_short_frame():
    import pytest
    with pytest.raises(ValueError, match="frame too short"):
        from pymt5.protocol import unpack_outer
        unpack_outer(b"\x00\x01\x02")  # less than 8 bytes


def test_unpack_outer_length_mismatch():
    import pytest

    from pymt5.protocol import unpack_outer
    # header says body_len=100 but actual body is 4 bytes
    frame = struct.pack("<II", 100, 1) + b"\x00" * 4
    with pytest.raises(ValueError, match="frame length mismatch"):
        unpack_outer(frame)


def test_unpack_outer_empty_body():
    from pymt5.protocol import unpack_outer
    # header says body_len=0, body is empty
    frame = struct.pack("<II", 0, 1)
    body_len, version, body = unpack_outer(frame)
    assert body_len == 0
    assert body == b""


def test_parse_response_frame_short_data():
    import pytest
    with pytest.raises(ValueError, match="response frame too short"):
        parse_response_frame(b"\x00\x01\x02\x03")  # only 4 bytes, need 5


def test_parse_response_frame_exactly_5_bytes():
    frame = parse_response_frame(b"\xAA\xBB\x00\x00\x05")
    assert frame.command == 0
    assert frame.code == 5
    assert frame.body == b""


def test_series_codec_truncated_buffer():
    import pytest
    # TICK_SCHEMA needs 50 bytes, give it only 10
    with pytest.raises(ValueError, match="buffer too short"):
        SeriesCodec.parse(b"\x00" * 10, TICK_SCHEMA)


def test_series_codec_truncated_buffer_with_offset():
    import pytest
    # Buffer is 50 bytes but offset=45 leaves only 5 bytes
    with pytest.raises(ValueError, match="buffer too short"):
        SeriesCodec.parse(b"\x00" * 50, TICK_SCHEMA, offset=45)
