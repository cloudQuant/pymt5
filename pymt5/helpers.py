"""Encoding, padding, obfuscation, and identifier helpers for MT5 wire data.

Fixed-width string fields use either Latin-1 or UTF-16LE with NUL padding.
The module also implements the terminal's initial-key deobfuscation and the
truncated SHA-1 client identifier expected during connection setup.
"""

import hashlib
import os


def hex_to_bytes(value: str) -> bytes:
    """Decode hexadecimal text into bytes.

    Args:
        value: Hexadecimal digits in the format accepted by ``bytes.fromhex``;
            ASCII whitespace between bytes is permitted.

    Returns:
        The decoded bytes.

    Raises:
        ValueError: If ``value`` contains invalid hexadecimal input.

    """
    return bytes.fromhex(value)


def bytes_to_hex(value: bytes) -> str:
    """Encode bytes as lowercase hexadecimal text.

    Args:
        value: Bytes to encode.

    Returns:
        Two lowercase hexadecimal characters per input byte, with no separator.

    """
    return value.hex()


def obfuscation_decode(value: str) -> str:
    """Reverse the terminal's character-wise initial-key obfuscation.

    Each ordinary character is replaced by the preceding Unicode code point.
    Code points 28 and 23 are special cases mapping to ``&`` and ``!``
    respectively.

    Args:
        value: Obfuscated text from the terminal protocol configuration.

    Returns:
        The deobfuscated string, typically hexadecimal key material.

    """
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
    """Generate the random prefix used by each command body.

    Returns:
        Two bytes from ``os.urandom``.

    """
    return os.urandom(2)


def truncate_ascii(value: str, limit: int) -> bytes:
    """Encode a single-byte protocol string and truncate it to a byte limit.

    Despite the historical function name, the encoding is Latin-1 rather than
    seven-bit ASCII. Characters not representable in Latin-1 are discarded
    before slicing, so truncation cannot split a multibyte encoding.

    Args:
        value: Text to encode.
        limit: Slice limit applied to the encoded bytes.

    Returns:
        The Latin-1 encoding sliced with ``[:limit]``.

    """
    return value.encode("latin1", errors="ignore")[:limit]


def pad_ascii(value: str, size: int) -> bytes:
    """Create a NUL-padded, fixed-width Latin-1 protocol field.

    Args:
        value: Text to encode; unsupported Latin-1 characters are discarded.
        size: Field width in bytes. Longer encoded values are truncated.

    Returns:
        Exactly ``size`` bytes for a nonnegative size, padded on the right with
        NUL bytes when necessary.

    """
    raw = truncate_ascii(value, size)
    return raw.ljust(size, b"\x00")


def encode_utf16le(value: str, size: int) -> bytes:
    """Encode a string as a fixed-size UTF-16LE field (property type 11).

    Truncation occurs at a two-byte code-unit boundary. If the boundary would
    separate a valid surrogate pair, the high surrogate is removed as well.
    Unpaired surrogates in the input are preserved with ``surrogatepass``.

    Args:
        value: Text to encode.
        size: Total field width in bytes. A nonpositive size produces an empty
            result; an odd positive size leaves one final NUL padding byte.

    Returns:
        Exactly ``size`` bytes for a positive size, containing little-endian
        UTF-16 code units followed by NUL padding as needed.

    """
    if size <= 0:
        return b""
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    unit_size = size - (size % 2)
    truncated = encoded[:unit_size]
    if len(truncated) < len(encoded) and len(truncated) >= 2:
        last = int.from_bytes(truncated[-2:], "little")
        following = int.from_bytes(encoded[len(truncated) : len(truncated) + 2], "little")
        if 0xD800 <= last <= 0xDBFF and 0xDC00 <= following <= 0xDFFF:
            truncated = truncated[:-2]
    return truncated.ljust(size, b"\x00")


def decode_utf16le(data: bytes) -> str:
    """Decode a fixed-size UTF-16LE buffer up to its first NUL code unit.

    Valid surrogate pairs are combined. Unpaired surrogates are retained as
    individual code points for compatibility with legacy terminal data, and an
    incomplete final code unit is ignored.

    Args:
        data: Little-endian UTF-16 bytes, normally from a fixed-width field.

    Returns:
        The decoded text preceding the first two-byte NUL code unit.

    """
    result = []
    i = 0
    while i + 1 < len(data):
        code = int.from_bytes(data[i : i + 2], "little")
        if code == 0:
            break
        if 0xD800 <= code <= 0xDBFF and i + 3 < len(data):
            low = int.from_bytes(data[i + 2 : i + 4], "little")
            if 0xDC00 <= low <= 0xDFFF:
                result.append(chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00))
                i += 4
                continue
        # Preserve unpaired legacy surrogates rather than rejecting the buffer.
        result.append(chr(code))
        i += 2
    return "".join(result)


def pad_bytes(value: bytes, size: int) -> bytes:
    """Truncate or right-pad binary data to a fixed-width field.

    Args:
        value: Raw field contents.
        size: Required field width in bytes.

    Returns:
        Exactly ``size`` bytes for a nonnegative size, using NUL bytes for
        right padding.

    """
    return value[:size].ljust(size, b"\x00")


def strip_fixed_string(value: bytes) -> str:
    """Decode a NUL-terminated Latin-1 fixed-width string field.

    Args:
        value: Raw field bytes.

    Returns:
        Bytes before the first NUL decoded as Latin-1; bytes after that NUL are
        ignored.

    """
    return value.split(b"\x00", 1)[0].decode("latin1", errors="ignore")


def build_client_id(
    platform: str = "python",
    device_pixel_ratio: str = "1",
    language: str = "en-US",
    screen: str = "0x0",
    uniq: str | None = None,
) -> bytes:
    """Build the 16-byte client identifier used during terminal setup.

    The five input components are joined with semicolons, encoded as UTF-8,
    and hashed with SHA-1. Only the first 16 digest bytes are retained.

    Args:
        platform: Client platform label.
        device_pixel_ratio: Browser-compatible pixel ratio represented as text.
        language: Client language tag.
        screen: Screen dimensions in the terminal's textual format.
        uniq: Optional unique component. If omitted or empty, a decimal number
            generated from three random bytes is used.

    Returns:
        The first 16 bytes of the SHA-1 digest of the semicolon-joined source.

    """
    uniq_value = uniq or str(int.from_bytes(os.urandom(3), "big"))
    source = ";".join([platform, device_pixel_ratio, language, screen, uniq_value]).encode("utf-8")
    return hashlib.sha1(source).digest()[:16]
