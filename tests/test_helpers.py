"""Tests for pymt5.helpers: hex_to_bytes, bytes_to_hex, obfuscation_decode,
random_command_prefix, encode/decode utf16le, build_client_id, etc."""

from pymt5.helpers import (
    build_client_id,
    bytes_to_hex,
    decode_utf16le,
    encode_utf16le,
    hex_to_bytes,
    obfuscation_decode,
    pad_ascii,
    pad_bytes,
    random_command_prefix,
    strip_fixed_string,
    truncate_ascii,
)

# ---- hex_to_bytes / bytes_to_hex ----

def test_hex_to_bytes():
    assert hex_to_bytes("48656c6c6f") == b"Hello"


def test_bytes_to_hex():
    assert bytes_to_hex(b"Hello") == "48656c6c6f"


def test_hex_roundtrip():
    original = b"\x00\x01\xff\xab\xcd"
    assert hex_to_bytes(bytes_to_hex(original)) == original


def test_hex_empty():
    assert hex_to_bytes("") == b""
    assert bytes_to_hex(b"") == ""


# ---- obfuscation_decode ----

def test_obfuscation_decode_basic():
    # Each char's code is shifted down by 1, except 28→'&' and 23→'!'
    # 'B' (66) should decode from chr(67) = 'C'
    assert obfuscation_decode("C") == "B"


def test_obfuscation_decode_special_chars():
    assert obfuscation_decode(chr(28)) == "&"
    assert obfuscation_decode(chr(23)) == "!"


def test_obfuscation_decode_string():
    # "Ifmmp" shifted by -1 each = "Hello"
    assert obfuscation_decode("Ifmmp") == "Hello"


# ---- random_command_prefix ----

def test_random_command_prefix_length():
    prefix = random_command_prefix()
    assert len(prefix) == 2
    assert isinstance(prefix, bytes)


def test_random_command_prefix_randomness():
    # Two calls should (almost certainly) produce different results
    prefixes = {random_command_prefix() for _ in range(100)}
    assert len(prefixes) > 1


# ---- encode_utf16le / decode_utf16le ----

def test_encode_utf16le_basic():
    result = encode_utf16le("AB", 64)
    assert len(result) == 64
    assert result[0:2] == b"A\x00"
    assert result[2:4] == b"B\x00"
    assert result[4:] == bytes(60)


def test_encode_utf16le_empty():
    result = encode_utf16le("", 32)
    assert result == bytes(32)


def test_encode_utf16le_truncation():
    # String longer than buffer should be truncated
    result = encode_utf16le("A" * 100, 8)
    assert len(result) == 8


def test_decode_utf16le_basic():
    data = b"H\x00e\x00l\x00l\x00o\x00" + bytes(54)
    assert decode_utf16le(data) == "Hello"


def test_decode_utf16le_stops_at_null():
    data = b"A\x00B\x00\x00\x00C\x00"
    assert decode_utf16le(data) == "AB"


def test_decode_utf16le_empty():
    assert decode_utf16le(bytes(64)) == ""


def test_utf16le_roundtrip():
    text = "EURUSD"
    encoded = encode_utf16le(text, 64)
    decoded = decode_utf16le(encoded)
    assert decoded == text


def test_utf16le_roundtrip_unicode():
    text = "EUR/USD"
    encoded = encode_utf16le(text, 64)
    decoded = decode_utf16le(encoded)
    assert decoded == text


# ---- pad_ascii / truncate_ascii ----

def test_pad_ascii():
    result = pad_ascii("AB", 8)
    assert result == b"AB\x00\x00\x00\x00\x00\x00"
    assert len(result) == 8


def test_pad_ascii_empty():
    result = pad_ascii("", 4)
    assert result == bytes(4)


def test_truncate_ascii():
    result = truncate_ascii("Hello World", 5)
    assert result == b"Hello"


# ---- pad_bytes ----

def test_pad_bytes():
    result = pad_bytes(b"AB", 8)
    assert result == b"AB\x00\x00\x00\x00\x00\x00"


def test_pad_bytes_truncation():
    result = pad_bytes(b"ABCDEF", 4)
    assert result == b"ABCD"


# ---- strip_fixed_string ----

def test_strip_fixed_string():
    assert strip_fixed_string(b"hello\x00world") == "hello"


def test_strip_fixed_string_no_null():
    assert strip_fixed_string(b"hello") == "hello"


def test_strip_fixed_string_empty():
    assert strip_fixed_string(b"\x00") == ""


# ---- build_client_id ----

def test_build_client_id_length():
    cid = build_client_id()
    assert len(cid) == 16
    assert isinstance(cid, bytes)


def test_build_client_id_deterministic_with_uniq():
    cid1 = build_client_id(uniq="test123")
    cid2 = build_client_id(uniq="test123")
    assert cid1 == cid2


def test_build_client_id_different_params():
    cid1 = build_client_id(platform="python", uniq="a")
    cid2 = build_client_id(platform="darwin", uniq="a")
    assert cid1 != cid2


def test_build_client_id_random_without_uniq():
    # Without explicit uniq, should produce different IDs
    cids = {build_client_id() for _ in range(10)}
    assert len(cids) > 1
