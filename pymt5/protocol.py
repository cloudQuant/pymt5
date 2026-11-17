"""Binary framing and schema-driven codecs for the MT5 Web Terminal protocol.

Outer frames start with an eight-byte little-endian header containing a
32-bit body length and a 32-bit protocol version. Command bodies contain a
two-byte random prefix, a little-endian 16-bit command identifier, and an
optional payload. Response payloads additionally include a one-byte status
code. :class:`SeriesCodec` serializes fixed-layout field sequences without
embedding field names or types in the wire data.
"""

import logging
import os
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pymt5.constants import (
    HEADER_BYTE_LENGTH,
    OUTER_PROTOCOL_VERSION,
    PROP_BYTES,
    PROP_F32,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I8,
    PROP_I16,
    PROP_I32,
    PROP_I64,
    PROP_STRING,
    PROP_TIME,
    PROP_U8,
    PROP_U16,
    PROP_U32,
    PROP_U64,
)
from pymt5.exceptions import ProtocolError
from pymt5.helpers import (
    decode_utf16le,
    encode_utf16le,
    pad_ascii,
    pad_bytes,
    random_command_prefix,
    strip_fixed_string,
)

_DEBUG = bool(os.environ.get("PYMT5_DEBUG"))
_debug_logger = logging.getLogger("pymt5.protocol.debug")

# Timestamp validation bounds (unix milliseconds)
_TS_MIN_MS = 946_684_800_000  # year 2000
_TS_MAX_MS = 4_102_444_800_000  # year 2100


@dataclass(slots=True)
class ResponseFrame:
    """Decoded command-response metadata and its unparsed payload.

    Attributes:
        command: Little-endian 16-bit command identifier read at byte offset 2.
        code: One-byte response status read at byte offset 4.
        body: Bytes following the five-byte response prefix.

    """

    command: int
    code: int
    body: bytes


FILETIME_EPOCH_OFFSET_MS = 11_644_473_600_000
FILETIME_TICKS_PER_MS = 10_000


def _unix_ms_to_filetime(unix_ms: int | float) -> bytes:
    filetime = int(int(unix_ms) + FILETIME_EPOCH_OFFSET_MS) * FILETIME_TICKS_PER_MS
    return filetime.to_bytes(8, "little", signed=False)


def _filetime_to_unix_ms(buffer: bytes) -> int:
    filetime = int.from_bytes(buffer, "little", signed=False)
    return filetime // FILETIME_TICKS_PER_MS - FILETIME_EPOCH_OFFSET_MS


_SUPPORTED_OUTER_VERSIONS = frozenset({0, OUTER_PROTOCOL_VERSION})


def pack_outer(body: bytes) -> bytes:
    """Prepend the standard eight-byte outer-protocol header to a body.

    Args:
        body: Unframed command or response bytes.

    Returns:
        The body length and protocol version packed as two little-endian
        unsigned 32-bit integers, followed by ``body``.

    """
    return struct.pack("<II", len(body), OUTER_PROTOCOL_VERSION) + body


def unpack_outer(frame: bytes) -> tuple[int, int, bytes]:
    """Validate and split a complete outer-protocol frame.

    Args:
        frame: Complete frame, including its eight-byte header.

    Returns:
        A tuple of ``(declared_body_length, protocol_version, body)``.

    Raises:
        ProtocolError: If the header is incomplete, the protocol version is
            unsupported, or its declared body length differs from the number of
            bytes after the header.

    """
    if len(frame) < HEADER_BYTE_LENGTH:
        raise ProtocolError("frame too short")
    body_len, version = struct.unpack_from("<II", frame, 0)
    if version not in _SUPPORTED_OUTER_VERSIONS:
        raise ProtocolError(f"unsupported protocol version: {version}")
    body = frame[HEADER_BYTE_LENGTH:]
    if body_len != len(body):
        raise ProtocolError(f"frame length mismatch: header={body_len} actual={len(body)}")
    return body_len, version, body


def build_command(command: int, payload: bytes | None = None) -> bytes:
    """Build an unframed command body with a fresh random prefix.

    Args:
        command: Unsigned 16-bit command identifier, encoded little-endian.
        payload: Serialized command fields. ``None`` means an empty payload.

    Returns:
        Two random prefix bytes, the two-byte command identifier, and the
        payload concatenated in that order.

    """
    body = payload or b""
    return random_command_prefix() + struct.pack("<H", command) + body


def parse_response_frame(data: bytes) -> ResponseFrame:
    """Parse the fixed five-byte prefix of an unframed response body.

    Args:
        data: Response bytes beginning with a two-byte prefix, a little-endian
            16-bit command identifier, and a one-byte status code.

    Returns:
        A :class:`ResponseFrame` containing the command, status, and remaining
        bytes. The returned body is not otherwise interpreted.

    Raises:
        ProtocolError: If fewer than five bytes are available.

    """
    if len(data) < 5:
        raise ProtocolError("response frame too short")
    command = struct.unpack_from("<H", data, 2)[0]
    code = data[4]
    body = data[5:]
    return ResponseFrame(command=command, code=code, body=body)


def _field_type(field: Sequence[object] | Mapping[str, object]) -> int:
    if isinstance(field, Mapping):
        value = field["propType"]
        if not isinstance(value, int):
            raise ProtocolError("propType must be an int")
        return value
    value = field[0]
    if not isinstance(value, int):
        raise ProtocolError("propType must be an int")
    return value


def _field_value(field: Sequence[object] | Mapping[str, object]) -> Any:
    if isinstance(field, Mapping):
        return field.get("propValue")
    return field[1] if len(field) > 1 else None


def _field_length(field: Sequence[object] | Mapping[str, object]) -> int | None:
    value = field.get("propLength") if isinstance(field, Mapping) else (field[2] if len(field) > 2 else None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("propLength must be an int")
    if value < 0:
        raise ProtocolError(f"propLength must be non-negative, got {value}")
    return value


# ---------------------------------------------------------------------------
# Dispatch tables for fixed-size types
# Maps prop_type → (struct_format, byte_size)
# ---------------------------------------------------------------------------
_FIXED_PACK: dict[int, tuple[str, int]] = {
    PROP_I8: ("<b", 1),
    PROP_U8: ("<B", 1),
    PROP_I16: ("<h", 2),
    PROP_U16: ("<H", 2),
    PROP_I32: ("<i", 4),
    PROP_U32: ("<I", 4),
    PROP_F32: ("<f", 4),
    PROP_F64: ("<d", 8),
}

# Fixed-size types that use struct.pack with a type coercion
_PACK_COERCE: dict[int, type] = {
    PROP_I8: int,
    PROP_U8: int,
    PROP_I16: int,
    PROP_U16: int,
    PROP_I32: int,
    PROP_U32: int,
    PROP_F32: float,
    PROP_F64: float,
}

# 8-byte integer types
_INT8_TYPES: dict[int, bool] = {
    PROP_I64: True,  # signed
    PROP_U64: False,  # unsigned
}

# Variable-length types that require propLength
_VARIABLE_TYPES = frozenset({PROP_FIXED_STRING, PROP_BYTES, PROP_STRING})

# All 8-byte types (for size calculation)
_EIGHT_BYTE_TYPES = frozenset({PROP_F64, PROP_TIME, PROP_I64, PROP_U64})


def get_series_size(schema: Iterable[Sequence[object] | Mapping[str, object]]) -> int:
    """Calculate the number of wire bytes occupied by a field schema.

    Fixed-width numeric fields contribute their native encoded size. Time and
    64-bit integer fields contribute eight bytes. String and byte fields use
    their declared ``propLength``; no length prefix or field metadata is added.

    Args:
        schema: Ordered field specifications. A specification may be a mapping
            with ``propType`` and optional ``propLength`` keys, or a sequence
            whose items at indexes 0 and 2 provide those values.

    Returns:
        Total encoded size in bytes.

    Raises:
        ProtocolError: If a type or length tag is not an integer, or a
            variable-width field lacks ``propLength``.
        NotImplementedError: If the schema contains an unsupported property
            type.

    """
    size = 0
    for field in schema:
        prop_type = _field_type(field)
        prop_length = _field_length(field)
        entry = _FIXED_PACK.get(prop_type)
        if entry is not None:
            size += entry[1]
        elif prop_type in _EIGHT_BYTE_TYPES:
            size += 8
        elif prop_type in _VARIABLE_TYPES:
            if prop_length is None:
                raise ProtocolError(f"propLength required for propType={prop_type}")
            size += prop_length
        else:
            raise NotImplementedError(f"unsupported propType={prop_type}")
    return size


class SeriesCodec:
    """Serialize and parse ordered fields according to an external schema.

    Sequence specifications use ``(propType, propValue, propLength)`` for
    serialization and ``(propType, ..., propLength)`` for parsing. Mapping
    specifications use the keys ``propType``, ``propValue``, and
    ``propLength``. Numeric values are little-endian; ``PROP_TIME`` converts
    between Unix milliseconds and an eight-byte Windows FILETIME count of
    100-nanosecond ticks since 1601-01-01 UTC. Fixed strings are UTF-16LE,
    while ``PROP_STRING`` uses fixed-width Latin-1 and ``PROP_BYTES`` contains
    raw bytes.
    """

    @staticmethod
    def serialize(fields: Sequence[Sequence[object] | Mapping[str, object]]) -> bytes:
        """Encode field values consecutively without per-field metadata.

        Integer fields are coerced to ``int`` and floating-point fields to
        ``float``. ``PROP_TIME`` treats its value as Unix milliseconds. A
        ``PROP_BYTES`` field without ``propLength`` emits its raw bytes;
        fixed-string and ``PROP_STRING`` fields require a declared length.

        Args:
            fields: Ordered schema entries containing the property type, value,
                and, for fixed-width variable data, its byte length.

        Returns:
            The concatenated little-endian field representation.

        Raises:
            ProtocolError: If a required ``propLength`` is missing or a type or
                length tag is invalid.
            NotImplementedError: If a property type is unsupported.

        """
        chunks: list[bytes] = []
        for field in fields:
            prop_type = _field_type(field)
            value = _field_value(field)
            prop_length = _field_length(field)

            # Fast path: fixed-size struct types
            entry = _FIXED_PACK.get(prop_type)
            if entry is not None:
                coerce = _PACK_COERCE[prop_type]
                chunks.append(struct.pack(entry[0], coerce(value)))
                continue

            # 8-byte special types
            if prop_type == PROP_TIME:
                chunks.append(_unix_ms_to_filetime(float(value or 0)))
            elif prop_type in _INT8_TYPES:
                signed = _INT8_TYPES[prop_type]
                chunks.append(int(value).to_bytes(8, "little", signed=signed))
            elif prop_type == PROP_FIXED_STRING:
                if prop_length is None:
                    raise ProtocolError("fixed string requires propLength")
                chunks.append(encode_utf16le(str(value or ""), prop_length))
            elif prop_type == PROP_BYTES:
                raw = bytes(value or b"")
                if prop_length is None:
                    chunks.append(raw)
                else:
                    chunks.append(pad_bytes(raw, prop_length))
            elif prop_type == PROP_STRING:
                if prop_length is None:
                    raise ProtocolError("string requires propLength in experimental codec")
                chunks.append(pad_ascii(str(value or ""), prop_length))
            else:
                raise NotImplementedError(f"unsupported propType={prop_type}")
        return b"".join(chunks)

    @staticmethod
    def parse(
        buffer: bytes,
        schema: Sequence[Sequence[object] | Mapping[str, object]],
        offset: int = 0,
    ) -> list[Any]:
        """Decode one schema-defined record from a byte buffer.

        Only the bytes required by ``schema`` are consumed; trailing bytes are
        left unexamined. ``PROP_TIME`` values are returned as Unix milliseconds,
        fixed strings as text, and byte fields as raw ``bytes``.

        Args:
            buffer: Source bytes containing the encoded record.
            schema: Ordered field specifications. Their value entries, if any,
                are ignored during parsing.
            offset: Zero-based byte position at which the record starts.

        Returns:
            Decoded values in schema order.

        Raises:
            ProtocolError: If ``offset`` is out of range, the buffer does not
                contain the complete record, a required field length is missing,
                or a type or length tag is invalid.
            NotImplementedError: If a property type is unsupported.

        """
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > len(buffer):
            raise ProtocolError(f"invalid offset {offset!r} for buffer of length {len(buffer)}")
        required = get_series_size(schema)
        available = len(buffer) - offset
        if available < required:
            raise ProtocolError(
                f"buffer too short: need {required} bytes from offset {offset}, but only {available} bytes available"
            )
        values: list[Any] = []
        cursor = offset
        for field in schema:
            prop_type = _field_type(field)
            prop_length = _field_length(field)

            # Fast path: fixed-size struct types
            entry = _FIXED_PACK.get(prop_type)
            if entry is not None:
                values.append(struct.unpack_from(entry[0], buffer, cursor)[0])
                cursor += entry[1]
                continue

            # 8-byte special types
            if prop_type == PROP_TIME:
                values.append(_filetime_to_unix_ms(buffer[cursor : cursor + 8]))
                cursor += 8
            elif prop_type in _INT8_TYPES:
                signed = _INT8_TYPES[prop_type]
                values.append(int.from_bytes(buffer[cursor : cursor + 8], "little", signed=signed))
                cursor += 8
            elif prop_type == PROP_FIXED_STRING:
                if prop_length is None:
                    raise ProtocolError("fixed string requires propLength")
                values.append(decode_utf16le(buffer[cursor : cursor + prop_length]))
                cursor += prop_length
            elif prop_type == PROP_BYTES:
                if prop_length is None:
                    raise ProtocolError("bytes requires propLength in experimental codec")
                values.append(buffer[cursor : cursor + prop_length])
                cursor += prop_length
            elif prop_type == PROP_STRING:
                if prop_length is None:
                    raise ProtocolError("string requires propLength in experimental codec")
                values.append(strip_fixed_string(buffer[cursor : cursor + prop_length]))
                cursor += prop_length
            else:
                raise NotImplementedError(f"unsupported propType={prop_type}")

        if _DEBUG:
            expected_end = offset + required
            if cursor != expected_end:
                _debug_logger.warning(
                    "parse: cursor=%d != expected_end=%d (%d unparsed trailing bytes)",
                    cursor,
                    expected_end,
                    cursor - expected_end,
                )
            # Validate timestamps are in a reasonable range
            for i, fld in enumerate(schema):
                ft = _field_type(fld)
                if ft == PROP_TIME:
                    ts = values[i]
                    if ts != 0 and not (_TS_MIN_MS <= ts <= _TS_MAX_MS):
                        _debug_logger.warning(
                            "parse: field %d timestamp %d out of range [%d, %d]",
                            i,
                            ts,
                            _TS_MIN_MS,
                            _TS_MAX_MS,
                        )
            _debug_logger.debug("parse: %d fields, values=%s", len(values), values)

        return values

    @staticmethod
    def parse_at(
        buffer: bytes,
        schema: Sequence[Sequence[object] | Mapping[str, object]],
        offset: int = 0,
    ) -> list[Any]:
        """Decode a record at an explicit offset.

        This is a compatibility alias for :meth:`SeriesCodec.parse`.

        Args:
            buffer: Source bytes containing the encoded record.
            schema: Ordered field specifications.
            offset: Zero-based byte position at which the record starts.

        Returns:
            Decoded values in schema order.

        Raises:
            ProtocolError: If the record is incomplete or its schema is invalid.
            NotImplementedError: If a property type is unsupported.

        """
        return SeriesCodec.parse(buffer, schema, offset)
