"""Performance benchmarks for pymt5 core operations.

Run with: pytest tests/test_benchmarks.py -v

These are NOT pytest-benchmark tests (to avoid adding a dependency).
Instead they use simple timing to verify operations are reasonably fast.
"""

import struct
import time

import pytest

from pymt5.constants import (
    OUTER_PROTOCOL_VERSION,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
)
from pymt5.crypto import initial_cipher
from pymt5.protocol import SeriesCodec, pack_outer, unpack_outer
from pymt5.schemas import TICK_FIELD_NAMES, TICK_SCHEMA
from pymt5.types import SymbolInfo

pytestmark = pytest.mark.benchmark


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A typical schema used for benchmarks (matches SYMBOL_BASIC_SCHEMA layout)
_BENCH_SCHEMA_FIELDS = [
    (PROP_FIXED_STRING, "EURUSD", 64),
    (PROP_FIXED_STRING, "Euro vs US Dollar", 128),
    (PROP_U32, 5),
    (PROP_U32, 42),
    (PROP_FIXED_STRING, "Forex\\Majors", 256),
    (PROP_U32, 0),
    (PROP_FIXED_STRING, "USD", 64),
    (PROP_U16, 1),
]

_BENCH_PARSE_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},
    {"propType": PROP_FIXED_STRING, "propLength": 128},
    {"propType": PROP_U32},
    {"propType": PROP_U32},
    {"propType": PROP_FIXED_STRING, "propLength": 256},
    {"propType": PROP_U32},
    {"propType": PROP_FIXED_STRING, "propLength": 64},
    {"propType": PROP_U16},
]

# Pre-serialized buffer for parse benchmarks
_BENCH_BUFFER = SeriesCodec.serialize(_BENCH_SCHEMA_FIELDS)


def _build_tick_buffer() -> bytes:
    """Build a realistic tick buffer for parsing benchmarks."""
    fields = [
        (PROP_U32, 1),  # symbol_id
        (PROP_I32, 1710501234),  # tick_time
        (PROP_U32, 0xFF),  # fields
        (PROP_F64, 1.08765),  # bid
        (PROP_F64, 1.08770),  # ask
        (PROP_F64, 0.0),  # last
        (PROP_I64, 100),  # tick_volume
        (PROP_U32, 500),  # time_ms_delta
        (PROP_U16, 0),  # flags
    ]
    return SeriesCodec.serialize(fields)


_TICK_BUFFER = _build_tick_buffer()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_serialize_throughput():
    """Serialize a typical schema 10000 times, assert < 2s."""
    iterations = 10_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        SeriesCodec.serialize(_BENCH_SCHEMA_FIELDS)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"serialize {iterations} iterations took {elapsed:.3f}s (limit: 2.0s)"


def test_parse_throughput():
    """Parse a typical buffer 10000 times, assert < 2s."""
    iterations = 10_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        SeriesCodec.parse(_BENCH_BUFFER, _BENCH_PARSE_SCHEMA)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"parse {iterations} iterations took {elapsed:.3f}s (limit: 2.0s)"


def test_pack_outer_throughput():
    """Pack outer 100000 times, assert < 1s."""
    iterations = 100_000
    body = b"\x00" * 128
    t0 = time.perf_counter()
    for _ in range(iterations):
        pack_outer(body)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"pack_outer {iterations} iterations took {elapsed:.3f}s (limit: 1.0s)"


def test_unpack_outer_throughput():
    """Unpack outer 100000 times, assert < 1s."""
    iterations = 100_000
    body = b"\x00" * 128
    frame = struct.pack("<II", len(body), OUTER_PROTOCOL_VERSION) + body
    t0 = time.perf_counter()
    for _ in range(iterations):
        unpack_outer(frame)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"unpack_outer {iterations} iterations took {elapsed:.3f}s (limit: 1.0s)"


def test_aes_encrypt_decrypt_throughput():
    """Encrypt+decrypt 10000 times, assert < 2s."""
    iterations = 10_000
    cipher = initial_cipher()
    plaintext = b"A" * 64  # typical command-sized payload
    t0 = time.perf_counter()
    for _ in range(iterations):
        encrypted = cipher.encrypt(plaintext)
        cipher.decrypt(encrypted)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"AES encrypt+decrypt {iterations} iterations took {elapsed:.3f}s (limit: 2.0s)"


def test_symbol_cache_lookup_throughput():
    """Lookup from a 1000-symbol dict 100000 times, assert < 1s."""
    iterations = 100_000
    # Build a 1000-symbol cache (dict keyed by name)
    cache: dict[str, SymbolInfo] = {}
    for i in range(1000):
        name = f"SYM{i:04d}"
        cache[name] = SymbolInfo(name=name, symbol_id=i, digits=5, trade_calc_mode=0, description="", path="")

    # Target key to look up (in the middle of the dict)
    target = "SYM0500"
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = cache[target]
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"symbol cache lookup {iterations} iterations took {elapsed:.3f}s (limit: 1.0s)"


def test_tick_parse_throughput():
    """Parse a tick buffer 10000 times, assert < 2s."""
    iterations = 10_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        vals = SeriesCodec.parse(_TICK_BUFFER, TICK_SCHEMA)
        dict(zip(TICK_FIELD_NAMES, vals))
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"tick parse {iterations} iterations took {elapsed:.3f}s (limit: 2.0s)"
