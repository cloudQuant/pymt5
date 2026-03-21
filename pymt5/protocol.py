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
_TS_MIN_MS = 946_684_800_000   # year 2000
_TS_MAX_MS = 4_102_444_800_000  # year 2100


@dataclass(slots=True)
class ResponseFrame:
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


def pack_outer(body: bytes) -> bytes:
    return struct.pack("<II", len(body), OUTER_PROTOCOL_VERSION) + body


def unpack_outer(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < HEADER_BYTE_LENGTH:
        raise ProtocolError("frame too short")
    body_len, version = struct.unpack_from("<II", frame, 0)
    body = frame[HEADER_BYTE_LENGTH:]
    if body_len != len(body):
        raise ProtocolError(f"frame length mismatch: header={body_len} actual={len(body)}")
    return body_len, version, body


def build_command(command: int, payload: bytes | None = None) -> bytes:
    body = payload or b""
    return random_command_prefix() + struct.pack("<H", command) + body


def parse_response_frame(data: bytes) -> ResponseFrame:
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
    if isinstance(field, Mapping):
        value = field.get("propLength")
        if value is None:
            return None
        if not isinstance(value, int):
            raise ProtocolError("propLength must be an int")
        return value
    value = field[2] if len(field) > 2 else None
    if value is None:
        return None
    if not isinstance(value, int):
        raise ProtocolError("propLength must be an int")
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
    PROP_I8: int, PROP_U8: int, PROP_I16: int, PROP_U16: int,
    PROP_I32: int, PROP_U32: int, PROP_F32: float, PROP_F64: float,
}

# 8-byte integer types
_INT8_TYPES: dict[int, bool] = {
    PROP_I64: True,   # signed
    PROP_U64: False,  # unsigned
}

# Variable-length types that require propLength
_VARIABLE_TYPES = frozenset({PROP_FIXED_STRING, PROP_BYTES, PROP_STRING})

# All 8-byte types (for size calculation)
_EIGHT_BYTE_TYPES = frozenset({PROP_F64, PROP_TIME, PROP_I64, PROP_U64})


def get_series_size(schema: Iterable[Sequence[object] | Mapping[str, object]]) -> int:
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
    @staticmethod
    def serialize(fields: Sequence[Sequence[object] | Mapping[str, object]]) -> bytes:
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
                            i, ts, _TS_MIN_MS, _TS_MAX_MS,
                        )
            _debug_logger.debug("parse: %d fields, values=%s", len(values), values)

        return values

    @staticmethod
    def parse_at(
        buffer: bytes,
        schema: Sequence[Sequence[object] | Mapping[str, object]],
        offset: int = 0,
    ) -> list[Any]:
        return SeriesCodec.parse(buffer, schema, offset)
