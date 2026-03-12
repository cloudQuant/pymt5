import struct
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from pymt5.constants import (
    HEADER_BYTE_LENGTH,
    OUTER_PROTOCOL_VERSION,
    PROP_BYTES,
    PROP_F32,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I16,
    PROP_I32,
    PROP_I64,
    PROP_I8,
    PROP_STRING,
    PROP_U16,
    PROP_U32,
    PROP_U64,
    PROP_U8,
)
from pymt5.helpers import decode_utf16le, encode_utf16le, pad_ascii, pad_bytes, random_command_prefix, strip_fixed_string


@dataclass(slots=True)
class ResponseFrame:
    command: int
    code: int
    body: bytes


def pack_outer(body: bytes) -> bytes:
    return struct.pack("<II", len(body), OUTER_PROTOCOL_VERSION) + body


def unpack_outer(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < HEADER_BYTE_LENGTH:
        raise ValueError("frame too short")
    body_len, version = struct.unpack_from("<II", frame, 0)
    body = frame[HEADER_BYTE_LENGTH:]
    if body_len != len(body):
        raise ValueError(f"frame length mismatch: header={body_len} actual={len(body)}")
    return body_len, version, body


def build_command(command: int, payload: bytes | None = None) -> bytes:
    body = payload or b""
    return random_command_prefix() + struct.pack("<H", command) + body


def parse_response_frame(data: bytes) -> ResponseFrame:
    if len(data) < 5:
        raise ValueError("response frame too short")
    command = struct.unpack_from("<H", data, 2)[0]
    code = data[4]
    body = data[5:]
    return ResponseFrame(command=command, code=code, body=body)


def _field_type(field: Sequence | Mapping) -> int:
    if isinstance(field, Mapping):
        return int(field["propType"])
    return int(field[0])


def _field_value(field: Sequence | Mapping):
    if isinstance(field, Mapping):
        return field.get("propValue")
    return field[1] if len(field) > 1 else None


def _field_length(field: Sequence | Mapping) -> int | None:
    if isinstance(field, Mapping):
        return field.get("propLength")
    return field[2] if len(field) > 2 else None


def get_series_size(schema: Iterable[Sequence | Mapping]) -> int:
    size = 0
    for field in schema:
        prop_type = _field_type(field)
        prop_length = _field_length(field)
        if prop_type in (PROP_I8, PROP_U8):
            size += 1
        elif prop_type in (PROP_I16, PROP_U16):
            size += 2
        elif prop_type in (PROP_I32, PROP_U32, PROP_F32):
            size += 4
        elif prop_type in (PROP_F64, PROP_I64, PROP_U64):
            size += 8
        elif prop_type in (PROP_FIXED_STRING, PROP_BYTES, PROP_STRING):
            if prop_length is None:
                raise ValueError(f"propLength required for propType={prop_type}")
            size += prop_length
        else:
            raise NotImplementedError(f"unsupported propType={prop_type}")
    return size


class SeriesCodec:
    @staticmethod
    def serialize(fields: Sequence[Sequence | Mapping]) -> bytes:
        chunks: list[bytes] = []
        for field in fields:
            prop_type = _field_type(field)
            value = _field_value(field)
            prop_length = _field_length(field)
            if prop_type == PROP_I8:
                chunks.append(struct.pack("<b", int(value)))
            elif prop_type == PROP_I16:
                chunks.append(struct.pack("<h", int(value)))
            elif prop_type == PROP_I32:
                chunks.append(struct.pack("<i", int(value)))
            elif prop_type == PROP_U8:
                chunks.append(struct.pack("<B", int(value)))
            elif prop_type == PROP_U16:
                chunks.append(struct.pack("<H", int(value)))
            elif prop_type == PROP_U32:
                chunks.append(struct.pack("<I", int(value)))
            elif prop_type == PROP_F32:
                chunks.append(struct.pack("<f", float(value)))
            elif prop_type == PROP_F64:
                chunks.append(struct.pack("<d", float(value)))
            elif prop_type == PROP_I64:
                chunks.append(int(value).to_bytes(8, "little", signed=True))
            elif prop_type == PROP_U64:
                chunks.append(int(value).to_bytes(8, "little", signed=False))
            elif prop_type == PROP_FIXED_STRING:
                if prop_length is None:
                    raise ValueError("fixed string requires propLength")
                chunks.append(encode_utf16le(str(value or ""), prop_length))
            elif prop_type == PROP_BYTES:
                raw = bytes(value or b"")
                if prop_length is None:
                    chunks.append(raw)
                else:
                    chunks.append(pad_bytes(raw, prop_length))
            elif prop_type == PROP_STRING:
                if prop_length is None:
                    raise ValueError("string requires propLength in experimental codec")
                chunks.append(pad_ascii(str(value or ""), prop_length))
            else:
                raise NotImplementedError(f"unsupported propType={prop_type}")
        return b"".join(chunks)

    @staticmethod
    def parse(buffer: bytes, schema: Sequence[Sequence | Mapping], offset: int = 0) -> list:
        values: list = []
        cursor = offset
        for field in schema:
            prop_type = _field_type(field)
            prop_length = _field_length(field)
            if prop_type == PROP_I8:
                values.append(struct.unpack_from("<b", buffer, cursor)[0])
                cursor += 1
            elif prop_type == PROP_I16:
                values.append(struct.unpack_from("<h", buffer, cursor)[0])
                cursor += 2
            elif prop_type == PROP_I32:
                values.append(struct.unpack_from("<i", buffer, cursor)[0])
                cursor += 4
            elif prop_type == PROP_U8:
                values.append(struct.unpack_from("<B", buffer, cursor)[0])
                cursor += 1
            elif prop_type == PROP_U16:
                values.append(struct.unpack_from("<H", buffer, cursor)[0])
                cursor += 2
            elif prop_type == PROP_U32:
                values.append(struct.unpack_from("<I", buffer, cursor)[0])
                cursor += 4
            elif prop_type == PROP_F32:
                values.append(struct.unpack_from("<f", buffer, cursor)[0])
                cursor += 4
            elif prop_type == PROP_F64:
                values.append(struct.unpack_from("<d", buffer, cursor)[0])
                cursor += 8
            elif prop_type == PROP_I64:
                values.append(int.from_bytes(buffer[cursor:cursor + 8], "little", signed=True))
                cursor += 8
            elif prop_type == PROP_U64:
                values.append(int.from_bytes(buffer[cursor:cursor + 8], "little", signed=False))
                cursor += 8
            elif prop_type == PROP_FIXED_STRING:
                if prop_length is None:
                    raise ValueError("fixed string requires propLength")
                values.append(decode_utf16le(buffer[cursor:cursor + prop_length]))
                cursor += prop_length
            elif prop_type == PROP_BYTES:
                if prop_length is None:
                    raise ValueError("bytes requires propLength in experimental codec")
                values.append(buffer[cursor:cursor + prop_length])
                cursor += prop_length
            elif prop_type == PROP_STRING:
                if prop_length is None:
                    raise ValueError("string requires propLength in experimental codec")
                values.append(strip_fixed_string(buffer[cursor:cursor + prop_length]))
                cursor += prop_length
            else:
                raise NotImplementedError(f"unsupported propType={prop_type}")
        return values

    @staticmethod
    def parse_at(buffer: bytes, schema: Sequence[Sequence | Mapping], offset: int = 0) -> list:
        return SeriesCodec.parse(buffer, schema, offset)
