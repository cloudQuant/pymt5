import hashlib
import os


def hex_to_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


def bytes_to_hex(value: bytes) -> str:
    return value.hex()


def obfuscation_decode(value: str) -> str:
    chars: list[str] = []
    for char in value:
        code = ord(char)
        if code == 28:
            chars.append("&")
        elif code == 23:
            chars.append("!")
        else:
            chars.append(chr(code - 1))
    return "".join(chars)


def random_command_prefix() -> bytes:
    return os.urandom(2)


def truncate_ascii(value: str, limit: int) -> bytes:
    return value.encode("latin1", errors="ignore")[:limit]


def pad_ascii(value: str, size: int) -> bytes:
    raw = truncate_ascii(value, size)
    return raw.ljust(size, b"\x00")


def encode_utf16le(value: str, size: int) -> bytes:
    """Encode a string as UTF-16LE into a fixed-size buffer (type 11)."""
    encoded = value.encode("utf-16-le")
    if len(encoded) > size:
        encoded = encoded[:size]
    return encoded.ljust(size, b"\x00")


def decode_utf16le(data: bytes) -> str:
    """Decode a UTF-16LE fixed-size buffer, stopping at first null char."""
    result = []
    for i in range(0, len(data) - 1, 2):
        code = int.from_bytes(data[i:i+2], "little")
        if code == 0:
            break
        result.append(chr(code))
    return "".join(result)


def pad_bytes(value: bytes, size: int) -> bytes:
    return value[:size].ljust(size, b"\x00")


def strip_fixed_string(value: bytes) -> str:
    return value.split(b"\x00", 1)[0].decode("latin1", errors="ignore")


def build_client_id(
    platform: str = "python",
    device_pixel_ratio: str = "1",
    language: str = "en-US",
    screen: str = "0x0",
    uniq: str | None = None,
) -> bytes:
    uniq_value = uniq or str(int.from_bytes(os.urandom(3), "big"))
    source = ";".join([platform, device_pixel_ratio, language, screen, uniq_value]).encode("utf-8")
    return hashlib.sha1(source).digest()[:16]
