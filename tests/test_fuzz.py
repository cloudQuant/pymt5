"""Fuzz tests for protocol parser using hypothesis.

These tests verify that the protocol parser handles arbitrary/malformed
input gracefully without crashing.
"""

import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pymt5.constants import (
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
)
from pymt5.exceptions import ProtocolError, PyMT5Error
from pymt5.protocol import SeriesCodec, parse_response_frame, unpack_outer
from pymt5.schemas import TICK_SCHEMA

pytestmark = pytest.mark.fuzz

# ---------------------------------------------------------------------------
# Allowed exceptions: protocol parsing may raise these on malformed input.
# The important thing is it must NOT raise SystemExit, segfault, or other
# unrecoverable errors.
# ---------------------------------------------------------------------------

_SAFE_EXCEPTIONS = (
    ProtocolError,
    PyMT5Error,
    ValueError,
    struct.error,
    TypeError,
    IndexError,
    KeyError,
    OverflowError,
    NotImplementedError,
    UnicodeDecodeError,
)


# ---------------------------------------------------------------------------
# Fuzz: parse_response_frame
# ---------------------------------------------------------------------------


@given(data=st.binary(min_size=0, max_size=1024))
@settings(max_examples=500)
def test_parse_response_frame_does_not_crash(data: bytes):
    """Random bytes should either parse or raise ProtocolError, not crash."""
    try:
        frame = parse_response_frame(data)
        # If it succeeded, basic invariants should hold
        assert isinstance(frame.command, int)
        assert isinstance(frame.code, int)
        assert isinstance(frame.body, bytes)
    except _SAFE_EXCEPTIONS:
        pass  # Expected for malformed input


# ---------------------------------------------------------------------------
# Fuzz: unpack_outer
# ---------------------------------------------------------------------------


@given(data=st.binary(min_size=0, max_size=1024))
@settings(max_examples=500)
def test_unpack_outer_does_not_crash(data: bytes):
    """Random bytes should either parse or raise ProtocolError, not crash."""
    try:
        body_len, version, body = unpack_outer(data)
        assert isinstance(body_len, int)
        assert isinstance(version, int)
        assert isinstance(body, bytes)
        assert body_len == len(body)
    except _SAFE_EXCEPTIONS:
        pass  # Expected for malformed input


# ---------------------------------------------------------------------------
# Fuzz: SeriesCodec.parse with TICK_SCHEMA
# ---------------------------------------------------------------------------


@given(data=st.binary(min_size=0, max_size=1024))
@settings(max_examples=500)
def test_series_parse_tick_does_not_crash(data: bytes):
    """Random bytes parsed as TICK_SCHEMA should not crash."""
    try:
        values = SeriesCodec.parse(data, TICK_SCHEMA)
        assert isinstance(values, list)
        assert len(values) == len(TICK_SCHEMA)
    except _SAFE_EXCEPTIONS:
        pass  # Expected for malformed input


# ---------------------------------------------------------------------------
# Fuzz: serialize then parse roundtrip with valid random field values
# ---------------------------------------------------------------------------

# Strategy: generate random valid field values for SYMBOL_BASIC_SCHEMA
_roundtrip_schema_fields = st.tuples(
    # trade_symbol: short ASCII string (max 31 chars for 64-byte UTF-16LE)
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=0x20, max_codepoint=0x7E),
        min_size=0,
        max_size=31,
    ),
    # description: short ASCII string (max 63 chars for 128-byte UTF-16LE)
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=0x20, max_codepoint=0x7E),
        min_size=0,
        max_size=63,
    ),
    # digits: u32
    st.integers(min_value=0, max_value=20),
    # symbol_id: u32
    st.integers(min_value=0, max_value=2**32 - 1),
    # path: short ASCII string (max 127 chars for 256-byte UTF-16LE)
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=0x20, max_codepoint=0x7E),
        min_size=0,
        max_size=127,
    ),
    # trade_calc_mode: u32
    st.integers(min_value=0, max_value=100),
    # basis: short ASCII string (max 31 chars for 64-byte UTF-16LE)
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), min_codepoint=0x20, max_codepoint=0x7E),
        min_size=0,
        max_size=31,
    ),
    # sector: u16
    st.integers(min_value=0, max_value=2**16 - 1),
)

_ROUNDTRIP_PARSE_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},
    {"propType": PROP_FIXED_STRING, "propLength": 128},
    {"propType": PROP_U32},
    {"propType": PROP_U32},
    {"propType": PROP_FIXED_STRING, "propLength": 256},
    {"propType": PROP_U32},
    {"propType": PROP_FIXED_STRING, "propLength": 64},
    {"propType": PROP_U16},
]


@given(field_values=_roundtrip_schema_fields)
@settings(max_examples=200)
def test_serialize_parse_roundtrip(field_values):
    """Generate random valid field values for a schema, serialize then parse, verify roundtrip."""
    trade_symbol, description, digits, symbol_id, path, calc_mode, basis, sector = field_values

    fields = [
        (PROP_FIXED_STRING, trade_symbol, 64),
        (PROP_FIXED_STRING, description, 128),
        (PROP_U32, digits),
        (PROP_U32, symbol_id),
        (PROP_FIXED_STRING, path, 256),
        (PROP_U32, calc_mode),
        (PROP_FIXED_STRING, basis, 64),
        (PROP_U16, sector),
    ]

    serialized = SeriesCodec.serialize(fields)
    parsed = SeriesCodec.parse(serialized, _ROUNDTRIP_PARSE_SCHEMA)

    assert parsed[0] == trade_symbol
    assert parsed[1] == description
    assert parsed[2] == digits
    assert parsed[3] == symbol_id
    assert parsed[4] == path
    assert parsed[5] == calc_mode
    assert parsed[6] == basis
    assert parsed[7] == sector


# ---------------------------------------------------------------------------
# Fuzz: tick roundtrip with random numeric values
# ---------------------------------------------------------------------------

_tick_values = st.tuples(
    st.integers(min_value=0, max_value=2**32 - 1),  # symbol_id (U32)
    st.integers(min_value=-(2**31), max_value=2**31 - 1),  # tick_time (I32)
    st.integers(min_value=0, max_value=2**32 - 1),  # fields (U32)
    st.floats(min_value=-1e10, max_value=1e10, allow_nan=False, allow_infinity=False),  # bid
    st.floats(min_value=-1e10, max_value=1e10, allow_nan=False, allow_infinity=False),  # ask
    st.floats(min_value=-1e10, max_value=1e10, allow_nan=False, allow_infinity=False),  # last
    st.integers(min_value=-(2**63), max_value=2**63 - 1),  # tick_volume (I64)
    st.integers(min_value=0, max_value=2**32 - 1),  # time_ms_delta (U32)
    st.integers(min_value=0, max_value=2**16 - 1),  # flags (U16)
)


@given(vals=_tick_values)
@settings(max_examples=200)
def test_tick_serialize_parse_roundtrip(vals):
    """Serialize random tick values and verify roundtrip parse matches."""
    symbol_id, tick_time, fields, bid, ask, last, volume, ms_delta, flags = vals

    tick_fields = [
        (PROP_U32, symbol_id),
        (PROP_I32, tick_time),
        (PROP_U32, fields),
        (PROP_F64, bid),
        (PROP_F64, ask),
        (PROP_F64, last),
        (PROP_I64, volume),
        (PROP_U32, ms_delta),
        (PROP_U16, flags),
    ]

    serialized = SeriesCodec.serialize(tick_fields)
    parsed = SeriesCodec.parse(serialized, TICK_SCHEMA)

    assert parsed[0] == symbol_id
    assert parsed[1] == tick_time
    assert parsed[2] == fields
    assert abs(parsed[3] - bid) < 1e-10 or parsed[3] == bid
    assert abs(parsed[4] - ask) < 1e-10 or parsed[4] == ask
    assert abs(parsed[5] - last) < 1e-10 or parsed[5] == last
    assert parsed[6] == volume
    assert parsed[7] == ms_delta
    assert parsed[8] == flags
