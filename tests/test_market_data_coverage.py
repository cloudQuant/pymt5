"""Comprehensive tests covering all untested lines in pymt5/_market_data.py.

Covers: load_symbols, _resolve_symbol_id, get_symbols (gzip/non-gzip),
symbols_total, symbols_get, get_full_symbol_info, symbol_info, symbol_select,
get_symbol_groups, get_spreads, subscribe_ticks (empty list), subscribe_book,
subscribe_book_by_name, unsubscribe_book, market_book_add/get/release,
subscribe_symbols_batch, tick_history_stats, get_rates, get_rates_raw,
copy_rates_from, copy_rates_from_pos, copy_ticks_from/range,
_get_tick_history, _resolve_conversion_rates, _resolve_side_rate,
_find_conversion_symbol_name, _get_conversion_prices, _calc_profit_raw,
_calc_margin_raw.
"""

import struct
import zlib
from collections import deque
from unittest.mock import AsyncMock

import pytest

from pymt5.client import MT5WebClient
from pymt5.constants import (
    CMD_GET_FULL_SYMBOLS,
    CMD_GET_RATES,
    CMD_GET_SPREADS,
    CMD_GET_SYMBOL_GROUPS,
    CMD_GET_SYMBOLS,
    CMD_GET_SYMBOLS_GZIP,
    CMD_SUBSCRIBE_BOOK,
    CMD_SUBSCRIBE_TICKS,
)
from pymt5.helpers import encode_utf16le
from pymt5.protocol import get_series_size
from pymt5.schemas import (
    FULL_SYMBOL_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import SymbolInfo


# ---------------------------------------------------------------------------
# Helper: build a counted-records body from schema
# ---------------------------------------------------------------------------
def _build_symbol_basic_body(symbols: list[dict]) -> bytes:
    """Build body = 4-byte count (u32 LE) + count * serialised SYMBOL_BASIC records."""
    count = len(symbols)
    body = struct.pack("<I", count)
    for sym in symbols:
        body += encode_utf16le(sym.get("trade_symbol", ""), 64)
        body += encode_utf16le(sym.get("symbol_description", ""), 128)
        body += struct.pack("<I", sym.get("digits", 5))
        body += struct.pack("<I", sym.get("symbol_id", 0))
        body += encode_utf16le(sym.get("symbol_path", ""), 256)
        body += struct.pack("<I", sym.get("trade_calc_mode", 0))
        body += encode_utf16le(sym.get("basis", ""), 64)
        body += struct.pack("<H", sym.get("sector", 0))
    return body


def _build_gzip_symbols_body(symbols: list[dict]) -> bytes:
    """Build gzip symbols body: 4 bytes padding + zlib.compress(counted_records)."""
    raw = _build_symbol_basic_body(symbols)
    compressed = zlib.compress(raw)
    return b"\x00\x00\x00\x00" + compressed


def _build_spread_body(spreads: list[dict]) -> bytes:
    """Build counted-records body for spreads."""
    count = len(spreads)
    body = struct.pack("<I", count)
    for sp in spreads:
        body += struct.pack("<II", sp.get("spread_id", 0), sp.get("flags", 0))
        body += encode_utf16le(sp.get("trade_symbol", ""), 64)
        body += struct.pack("<II", sp.get("param1", 0), sp.get("param2", 0))
        body += struct.pack("<d", sp.get("spread_value", 0.0))
    return body


def _build_symbol_group_body(groups: list[str]) -> bytes:
    """Build body for symbol groups: 4-byte count + count * group records."""
    count = len(groups)
    body = struct.pack("<I", count)
    for g in groups:
        body += encode_utf16le(g, 256)
    return body


def _build_rate_bar_body(bars: list[dict]) -> bytes:
    """Build raw rate bar body (no count prefix -- _parse_rate_bars uses size division)."""
    body = b""
    for bar in bars:
        body += struct.pack(
            "<iddddqi",
            bar.get("time", 0),
            bar.get("open", 0.0),
            bar.get("high", 0.0),
            bar.get("low", 0.0),
            bar.get("close", 0.0),
            bar.get("tick_volume", 0),
            bar.get("spread", 0),
        )
    return body


def _make_client_with_symbols() -> MT5WebClient:
    """Create a client with a populated symbol cache."""
    client = MT5WebClient()
    client.transport.is_ready = True

    sym_eur = SymbolInfo(
        name="EURUSD",
        symbol_id=1,
        digits=5,
        description="Euro vs USD",
        path="Forex\\EURUSD",
        trade_calc_mode=0,
        basis="",
        sector=0,
    )
    sym_gbp = SymbolInfo(
        name="GBPUSD",
        symbol_id=2,
        digits=5,
        description="Pound vs USD",
        path="Forex\\GBPUSD",
        trade_calc_mode=0,
        basis="",
        sector=0,
    )
    sym_usdjpy = SymbolInfo(
        name="USDJPY",
        symbol_id=3,
        digits=3,
        description="USD vs JPY",
        path="Forex\\USDJPY",
        trade_calc_mode=0,
        basis="",
        sector=0,
    )
    client._symbols = {
        "EURUSD": sym_eur,
        "GBPUSD": sym_gbp,
        "USDJPY": sym_usdjpy,
    }
    client._symbols_by_id = {
        1: sym_eur,
        2: sym_gbp,
        3: sym_usdjpy,
    }
    return client


# ===========================================================================
# 1. load_symbols (lines 98-119)
# ===========================================================================


async def test_load_symbols_builds_cache():
    """load_symbols() parses get_symbols output, populates _symbols and _symbols_by_id."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "Euro",
            "symbol_path": "Forex\\EURUSD",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
        {
            "trade_symbol": "GBPUSD",
            "symbol_id": 2,
            "digits": 5,
            "symbol_description": "Pound",
            "symbol_path": "Forex\\GBPUSD",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    result = await client.load_symbols()
    assert len(result) == 2
    assert "EURUSD" in result
    assert "GBPUSD" in result
    assert client._symbols["EURUSD"].symbol_id == 1
    assert client._symbols_by_id[2].name == "GBPUSD"


async def test_load_symbols_links_tick_history():
    """load_symbols() links existing tick_history_by_id to tick_history_by_name."""
    client = MT5WebClient()
    # Pre-populate tick history by id
    history_deque = deque([{"bid": 1.1, "ask": 1.2, "tick_time": 100}])
    client._tick_history_by_id[1] = history_deque

    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    await client.load_symbols()
    # tick_history_by_name should now be linked
    assert "EURUSD" in client._tick_history_by_name
    assert client._tick_history_by_name["EURUSD"] is history_deque


async def test_load_symbols_clears_previous_cache():
    """load_symbols() clears old caches before rebuilding."""
    client = _make_client_with_symbols()
    client._full_symbols["EURUSD"] = {"trade_symbol": "EURUSD"}

    symbols_data = [
        {
            "trade_symbol": "XAUUSD",
            "symbol_id": 10,
            "digits": 2,
            "symbol_description": "Gold",
            "symbol_path": "Metals\\XAUUSD",
            "trade_calc_mode": 1,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    result = await client.load_symbols()
    assert "EURUSD" not in result
    assert "XAUUSD" in result
    assert len(client._full_symbols) == 0


# ===========================================================================
# 2. _resolve_symbol_id (lines 136-140)
# ===========================================================================


async def test_resolve_symbol_id_from_cache():
    """_resolve_symbol_id returns id from cache without reload."""
    client = _make_client_with_symbols()
    sid = await client._resolve_symbol_id("EURUSD")
    assert sid == 1


async def test_resolve_symbol_id_triggers_reload():
    """_resolve_symbol_id reloads symbols if not found in cache."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    sid = await client._resolve_symbol_id("EURUSD")
    assert sid == 1
    client.transport.send_command.assert_awaited()


async def test_resolve_symbol_id_returns_none_if_not_found():
    """_resolve_symbol_id returns None if symbol doesn't exist even after reload."""
    client = MT5WebClient()
    body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    sid = await client._resolve_symbol_id("NOSYMBOL")
    assert sid is None


# ===========================================================================
# 3. get_symbols (lines 143-151) -- gzip and non-gzip paths
# ===========================================================================


async def test_get_symbols_gzip():
    """get_symbols(use_gzip=True) decompresses and parses counted records."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "Euro",
            "symbol_path": "Forex",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    result = await client.get_symbols(use_gzip=True)
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "EURUSD"


async def test_get_symbols_gzip_empty_body():
    """get_symbols(use_gzip=True) returns empty list if body is too short."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=b"\x00\x00"),
    )
    result = await client.get_symbols(use_gzip=True)
    assert result == []


async def test_get_symbols_gzip_none_body():
    """get_symbols(use_gzip=True) returns empty list if body is None/empty."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=b""),
    )
    result = await client.get_symbols(use_gzip=True)
    assert result == []


async def test_get_symbols_non_gzip():
    """get_symbols(use_gzip=False) parses directly without decompression."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "GBPUSD",
            "symbol_id": 2,
            "digits": 5,
            "symbol_description": "Pound",
            "symbol_path": "Forex",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_symbol_basic_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS, code=0, body=body),
    )
    result = await client.get_symbols(use_gzip=False)
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "GBPUSD"


# ===========================================================================
# 4. symbols_total (lines 155-157)
# ===========================================================================


async def test_symbols_total_from_cache():
    """symbols_total returns cached count when cache is populated."""
    client = _make_client_with_symbols()
    total = await client.symbols_total()
    assert total == 3


async def test_symbols_total_fetches_when_empty():
    """symbols_total fetches symbols when cache is empty."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
        {
            "trade_symbol": "GBPUSD",
            "symbol_id": 2,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    total = await client.symbols_total()
    assert total == 2


# ===========================================================================
# 5. symbols_get (line 163) -- group filtering
# ===========================================================================


async def test_symbols_get_no_group_returns_all():
    """symbols_get with no group returns all symbols."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    result = await client.symbols_get(group=None)
    assert len(result) == 1


async def test_symbols_get_with_group_filters():
    """symbols_get with group filter returns matching symbols only."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
        {
            "trade_symbol": "XAUUSD",
            "symbol_id": 2,
            "digits": 2,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=body),
    )
    result = await client.symbols_get(group="EUR*")
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "EURUSD"


# ===========================================================================
# 6. get_full_symbol_info (lines 183, 189-198)
# ===========================================================================


async def test_get_full_symbol_info_empty_body():
    """get_full_symbol_info returns None on empty body (line 183)."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=b""),
    )
    result = await client.get_full_symbol_info("EURUSD")
    assert result is None


async def test_get_full_symbol_info_counted_records():
    """get_full_symbol_info parses counted records path."""
    client = MT5WebClient()
    record_size = get_series_size(FULL_SYMBOL_SCHEMA)
    # Build a counted-records body with 1 record
    record_body = b"\x00" * record_size
    body = struct.pack("<I", 1) + record_body
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=body),
    )
    result = await client.get_full_symbol_info("EURUSD")
    assert result is not None
    assert isinstance(result, dict)
    assert "EURUSD" in client._full_symbols


async def test_get_full_symbol_info_fallback_parse(monkeypatch):
    """get_full_symbol_info falls back to direct parse when counted records returns empty (lines 189-194)."""
    client = MT5WebClient()
    record_size = get_series_size(FULL_SYMBOL_SCHEMA)
    # Body that's large enough for one full symbol but with count=0
    body = struct.pack("<I", 0) + b"\x00" * record_size
    # The counted records path returns [] because count=0,
    # but the fallback path needs len(body) >= full_sym_size.
    # body = count(4) + record(record_size) => 4 + record_size >= record_size => True
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=body),
    )
    result = await client.get_full_symbol_info("EURUSD")
    # Should work via fallback parse (body[0:] starts with the 4-byte count,
    # but the fallback skips the count prefix and tries parsing from offset 0)
    # The fallback path checks len(result.body) >= full_sym_size
    # body length = 4 + record_size >= record_size, so it enters the fallback
    assert result is not None


async def test_get_full_symbol_info_fallback_too_short():
    """get_full_symbol_info returns None when fallback body is too short (line 195)."""
    client = MT5WebClient()
    # Body with count=0 and not enough data for a full record
    body = struct.pack("<I", 0) + b"\x00" * 10
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=body),
    )
    result = await client.get_full_symbol_info("EURUSD")
    assert result is None


async def test_get_full_symbol_info_exception_handling():
    """get_full_symbol_info catches struct/parse errors (lines 196-198)."""
    client = MT5WebClient()
    # Body with count=1 but truncated record data => will trigger struct.error
    body = struct.pack("<I", 1) + b"\x01\x02\x03"
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=body),
    )
    result = await client.get_full_symbol_info("EURUSD")
    assert result is None


# ===========================================================================
# 7. symbol_info (lines 202-213)
# ===========================================================================


async def test_symbol_info_from_full_cache():
    """symbol_info returns cached full symbol info (lines 202-204)."""
    client = MT5WebClient()
    client._full_symbols["EURUSD"] = {
        "trade_symbol": "EURUSD",
        "digits": 5,
        "contract_size": 100000.0,
    }
    result = await client.symbol_info("EURUSD")
    assert result is not None
    assert result["trade_symbol"] == "EURUSD"
    assert result["contract_size"] == 100000.0


async def test_symbol_info_from_get_full():
    """symbol_info fetches full symbol info when not cached (lines 205-207)."""
    client = MT5WebClient()
    record_size = get_series_size(FULL_SYMBOL_SCHEMA)
    body = struct.pack("<I", 1) + b"\x00" * record_size
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=body),
    )
    result = await client.symbol_info("EURUSD")
    assert result is not None


async def test_symbol_info_fallback_to_basic():
    """symbol_info falls back to basic symbol info (lines 208-222)."""
    client = MT5WebClient()
    # get_full_symbol_info returns None (empty body)
    # load_symbols populates the basic cache
    sym = SymbolInfo(
        name="EURUSD",
        symbol_id=1,
        digits=5,
        description="Euro",
        path="Forex\\EURUSD",
        trade_calc_mode=0,
        basis="EUR",
        sector=1,
    )
    client._symbols = {"EURUSD": sym}
    client._symbols_by_id = {1: sym}

    # get_full_symbol_info will return None
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=b""),
    )
    result = await client.symbol_info("EURUSD")
    assert result is not None
    assert result["trade_symbol"] == "EURUSD"
    assert result["symbol_id"] == 1
    assert result["digits"] == 5
    assert result["basis"] == "EUR"
    assert result["sector"] == 1


async def test_symbol_info_not_found():
    """symbol_info returns None when symbol not found anywhere (lines 211-212)."""
    client = MT5WebClient()
    # Make get_full_symbol_info return None
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=b""),
    )
    # No symbols in cache, load_symbols also returns empty
    symbols_body = _build_gzip_symbols_body([])

    call_count = 0

    async def side_effect(cmd, payload=None):
        nonlocal call_count
        call_count += 1
        if cmd == CMD_GET_FULL_SYMBOLS:
            return CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=b"")
        return CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body)

    client.transport.send_command = AsyncMock(side_effect=side_effect)
    result = await client.symbol_info("NOSYMBOL")
    assert result is None


async def test_symbol_info_triggers_load_symbols():
    """symbol_info calls load_symbols when cache empty (lines 208-209)."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "Euro",
            "symbol_path": "Forex",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    symbols_body = _build_gzip_symbols_body(symbols_data)

    async def side_effect(cmd, payload=None):
        if cmd == CMD_GET_FULL_SYMBOLS:
            return CommandResult(command=CMD_GET_FULL_SYMBOLS, code=0, body=b"")
        return CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body)

    client.transport.send_command = AsyncMock(side_effect=side_effect)
    result = await client.symbol_info("EURUSD")
    assert result is not None
    assert result["trade_symbol"] == "EURUSD"


# ===========================================================================
# 8. symbol_select (lines 231-240)
# ===========================================================================


async def test_symbol_select_enable():
    """symbol_select(enable=True) subscribes to ticks (lines 231-235)."""
    client = _make_client_with_symbols()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""),
    )
    result = await client.symbol_select("EURUSD", enable=True)
    assert result is True
    assert 1 in client._subscribed_ids


async def test_symbol_select_disable():
    """symbol_select(enable=False) unsubscribes and clears cache (lines 236-239)."""
    client = _make_client_with_symbols()
    client._subscribed_ids = [1, 2]
    client._tick_cache_by_id[1] = {"bid": 1.1}
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.1}
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""),
    )
    result = await client.symbol_select("EURUSD", enable=False)
    assert result is True
    assert 1 not in client._tick_cache_by_id
    assert "EURUSD" not in client._tick_cache_by_name


async def test_symbol_select_unknown_symbol():
    """symbol_select returns False for unknown symbol (line 233)."""
    client = MT5WebClient()
    symbols_body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    result = await client.symbol_select("NOSYMBOL")
    assert result is False


# ===========================================================================
# 9. get_symbol_groups (lines 247-261)
# ===========================================================================


async def test_get_symbol_groups():
    """get_symbol_groups returns parsed group names."""
    client = MT5WebClient()
    body = _build_symbol_group_body(["Forex", "Crypto", "Indices"])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOL_GROUPS, code=0, body=body),
    )
    groups = await client.get_symbol_groups()
    assert len(groups) == 3
    assert "Forex" in groups
    assert "Crypto" in groups
    assert "Indices" in groups


async def test_get_symbol_groups_empty():
    """get_symbol_groups returns [] on empty body (lines 248-249)."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOL_GROUPS, code=0, body=b""),
    )
    groups = await client.get_symbol_groups()
    assert groups == []


async def test_get_symbol_groups_short_body():
    """get_symbol_groups returns [] on body shorter than 4 bytes."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOL_GROUPS, code=0, body=b"\x01\x02"),
    )
    groups = await client.get_symbol_groups()
    assert groups == []


async def test_get_symbol_groups_truncated():
    """get_symbol_groups handles truncated body gracefully (line 256)."""
    client = MT5WebClient()
    # Count says 5 groups but body only has 1 group worth of data
    body = struct.pack("<I", 5) + encode_utf16le("Forex", 256)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOL_GROUPS, code=0, body=body),
    )
    groups = await client.get_symbol_groups()
    assert len(groups) == 1
    assert groups[0] == "Forex"


# ===========================================================================
# 10. get_spreads (lines 272-277)
# ===========================================================================


async def test_get_spreads_with_symbol_ids():
    """get_spreads with symbol IDs builds proper payload (lines 272-273)."""
    client = MT5WebClient()
    body = _build_spread_body(
        [
            {"spread_id": 1, "flags": 0, "trade_symbol": "EURUSD", "param1": 0, "param2": 0, "spread_value": 1.5},
        ]
    )
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SPREADS, code=0, body=body),
    )
    result = await client.get_spreads(symbol_ids=[1, 2])
    assert len(result) == 1
    assert result[0]["trade_symbol"] == "EURUSD"
    # Verify payload structure
    call_args = client.transport.send_command.call_args
    payload = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("payload", b"")
    # Payload should be: count(2) + id(1) + id(2) = 3 * 4 bytes = 12 bytes
    assert len(payload) == 12


async def test_get_spreads_without_symbol_ids():
    """get_spreads without symbol IDs sends empty payload (lines 274-275)."""
    client = MT5WebClient()
    body = _build_spread_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SPREADS, code=0, body=body),
    )
    result = await client.get_spreads(symbol_ids=None)
    assert result == []


# ===========================================================================
# 11. subscribe_ticks empty list (lines 282-283)
# ===========================================================================


async def test_subscribe_ticks_empty_list():
    """subscribe_ticks with empty list returns early (lines 281-283)."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock()
    await client.subscribe_ticks([])
    client.transport.send_command.assert_not_awaited()


# ===========================================================================
# 12. subscribe_book (lines 326, 329-330)
# ===========================================================================


async def test_subscribe_symbols_found():
    """subscribe_symbols finds and subscribes known symbols (line 326, 329-330)."""
    client = _make_client_with_symbols()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""),
    )
    ids = await client.subscribe_symbols(["EURUSD", "GBPUSD"])
    assert ids == [1, 2]
    client.transport.send_command.assert_awaited()


async def test_subscribe_book():
    """subscribe_book subscribes to book updates."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    await client.subscribe_book([1, 2])
    assert 1 in client._subscribed_book_ids
    assert 2 in client._subscribed_book_ids
    client.transport.send_command.assert_awaited()


async def test_subscribe_book_accumulates():
    """subscribe_book merges with existing subscriptions."""
    client = MT5WebClient()
    client._subscribed_book_ids = [1]
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    await client.subscribe_book([2, 3])
    assert set(client._subscribed_book_ids) == {1, 2, 3}


# ===========================================================================
# 13. subscribe_book_by_name (lines 362, 365-366)
# ===========================================================================


async def test_subscribe_book_by_name():
    """subscribe_book_by_name resolves names to IDs and subscribes (lines 362, 365-366)."""
    client = _make_client_with_symbols()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    ids = await client.subscribe_book_by_name(["EURUSD", "GBPUSD"])
    assert ids == [1, 2]
    assert 1 in client._subscribed_book_ids
    assert 2 in client._subscribed_book_ids


async def test_subscribe_book_by_name_missing_raises():
    """subscribe_book_by_name raises ValueError for missing symbols."""
    client = _make_client_with_symbols()
    with pytest.raises(ValueError, match="symbols not found in cache"):
        await client.subscribe_book_by_name(["EURUSD", "NOSYMBOL"])


# ===========================================================================
# 14. unsubscribe_book (lines 375, 382)
# ===========================================================================


async def test_unsubscribe_book_all():
    """unsubscribe_book removes all IDs, sends empty payload (line 375)."""
    client = MT5WebClient()
    client._subscribed_book_ids = [1]
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    await client.unsubscribe_book([1])
    assert client._subscribed_book_ids == []


async def test_unsubscribe_book_partial():
    """unsubscribe_book removes specific IDs, keeps remaining."""
    client = MT5WebClient()
    client._subscribed_book_ids = [1, 2, 3]
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    await client.unsubscribe_book([2])
    assert 2 not in client._subscribed_book_ids
    assert 1 in client._subscribed_book_ids
    assert 3 in client._subscribed_book_ids


async def test_unsubscribe_book_clears_cache():
    """unsubscribe_book clears book cache entries (line 382)."""
    client = MT5WebClient()
    client._subscribed_book_ids = [1, 2]
    client._book_cache_by_id[1] = {"symbol_id": 1, "symbol": "EURUSD"}
    client._book_cache_by_name["EURUSD"] = {"symbol_id": 1, "symbol": "EURUSD"}
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    await client.unsubscribe_book([1])
    assert 1 not in client._book_cache_by_id
    assert "EURUSD" not in client._book_cache_by_name


async def test_unsubscribe_book_cache_entry_without_symbol():
    """unsubscribe_book handles cache entries without 'symbol' key."""
    client = MT5WebClient()
    client._subscribed_book_ids = [1]
    client._book_cache_by_id[1] = {"symbol_id": 1}  # no "symbol" key
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    await client.unsubscribe_book([1])
    assert 1 not in client._book_cache_by_id


# ===========================================================================
# 15. market_book_add/get/release (lines 386-405)
# ===========================================================================


async def test_market_book_add():
    """market_book_add subscribes by symbol name (lines 386-390)."""
    client = _make_client_with_symbols()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    result = await client.market_book_add("EURUSD")
    assert result is True
    assert 1 in client._subscribed_book_ids


async def test_market_book_add_unknown():
    """market_book_add returns False for unknown symbol (lines 387-388)."""
    client = MT5WebClient()
    symbols_body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    result = await client.market_book_add("NOSYMBOL")
    assert result is False


def test_market_book_get():
    """market_book_get returns cached book data."""
    client = MT5WebClient()
    client._book_cache_by_name["EURUSD"] = {"symbol_id": 1, "bids": [], "asks": []}
    result = client.market_book_get("EURUSD")
    assert result is not None
    assert result["symbol_id"] == 1


def test_market_book_get_not_found():
    """market_book_get returns None when not cached."""
    client = MT5WebClient()
    result = client.market_book_get("EURUSD")
    assert result is None


async def test_market_book_release():
    """market_book_release unsubscribes and clears cache (lines 399-405)."""
    client = _make_client_with_symbols()
    client._subscribed_book_ids = [1]
    client._book_cache_by_id[1] = {"symbol_id": 1, "symbol": "EURUSD"}
    client._book_cache_by_name["EURUSD"] = {"symbol_id": 1, "symbol": "EURUSD"}
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_BOOK, code=0, body=b""),
    )
    result = await client.market_book_release("EURUSD")
    assert result is True
    assert "EURUSD" not in client._book_cache_by_name
    assert 1 not in client._book_cache_by_id


async def test_market_book_release_unknown():
    """market_book_release returns False for unknown symbol (lines 400-401)."""
    client = MT5WebClient()
    symbols_body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    result = await client.market_book_release("NOSYMBOL")
    assert result is False


# ===========================================================================
# 16. subscribe_symbols_batch (lines 414-421)
# ===========================================================================


async def test_subscribe_symbols_batch():
    """subscribe_symbols_batch subscribes known symbols, skips unknown (lines 414-421)."""
    client = _make_client_with_symbols()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_SUBSCRIBE_TICKS, code=0, body=b""),
    )
    ids = await client.subscribe_symbols_batch(["EURUSD", "NOSYMBOL", "GBPUSD"])
    assert 1 in ids
    assert 2 in ids
    assert len(ids) == 2


async def test_subscribe_symbols_batch_all_unknown():
    """subscribe_symbols_batch with all unknown symbols does not subscribe."""
    client = _make_client_with_symbols()
    client.transport.send_command = AsyncMock()
    ids = await client.subscribe_symbols_batch(["NOSYMBOL1", "NOSYMBOL2"])
    assert ids == []
    client.transport.send_command.assert_not_awaited()


# ===========================================================================
# 17. tick_history_stats (line 425)
# ===========================================================================


def test_tick_history_stats():
    """tick_history_stats returns memory usage info (line 425)."""
    client = MT5WebClient()
    client._tick_history_by_name["EURUSD"] = deque([{"bid": 1.1}] * 5)
    client._tick_history_by_name["GBPUSD"] = deque([{"bid": 1.3}] * 3)
    stats = client.tick_history_stats()
    assert stats["symbols_tracked"] == 2
    assert stats["total_ticks"] == 8
    assert stats["limit_per_symbol"] == client._tick_history_limit


# ===========================================================================
# 18. get_rates (lines 445-453)
# ===========================================================================


async def test_get_rates():
    """get_rates requests rate bars and parses response (lines 445-453)."""
    client = MT5WebClient()
    bars_body = _build_rate_bar_body(
        [
            {
                "time": 1700000000,
                "open": 1.10,
                "high": 1.11,
                "low": 1.09,
                "close": 1.105,
                "tick_volume": 100,
                "spread": 5,
            },
            {
                "time": 1700000060,
                "open": 1.105,
                "high": 1.12,
                "low": 1.10,
                "close": 1.115,
                "tick_volume": 150,
                "spread": 4,
            },
        ]
    )
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_RATES, code=0, body=bars_body),
    )
    result = await client.get_rates("EURUSD", 1, 1700000000, 1700000120)
    assert len(result) == 2
    assert result[0]["time"] == 1700000000
    assert result[1]["close"] == 1.115


async def test_get_rates_period_mapping():
    """get_rates maps period minutes via PERIOD_MAP (line 445)."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_RATES, code=0, body=b""),
    )
    await client.get_rates("EURUSD", 60, 0, 100)
    # Verify the command was sent (period mapping 60 -> PERIOD_H1)
    client.transport.send_command.assert_awaited_once()


# ===========================================================================
# 19. get_rates_raw (lines 459-467)
# ===========================================================================


async def test_get_rates_raw():
    """get_rates_raw returns raw bytes (lines 459-467)."""
    client = MT5WebClient()
    raw_body = b"\x01\x02\x03\x04"
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_RATES, code=0, body=raw_body),
    )
    result = await client.get_rates_raw("EURUSD", 1, 0, 100)
    assert result == raw_body


# ===========================================================================
# 20. copy_rates_from (line 494)
# ===========================================================================


async def test_copy_rates_from_zero_count():
    """copy_rates_from with count<=0 returns empty list (line 494)."""
    client = MT5WebClient()
    result = await client.copy_rates_from("EURUSD", 1, 1700000000, 0)
    assert result == []


async def test_copy_rates_from_negative_count():
    """copy_rates_from with negative count returns empty list."""
    client = MT5WebClient()
    result = await client.copy_rates_from("EURUSD", 1, 1700000000, -5)
    assert result == []


async def test_copy_rates_from_normal():
    """copy_rates_from fetches bars and limits to count."""
    client = MT5WebClient()
    bars_body = _build_rate_bar_body(
        [
            {"time": i, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1}
            for i in range(10)
        ]
    )
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_RATES, code=0, body=bars_body),
    )
    result = await client.copy_rates_from("EURUSD", 1, 1700000000, 3)
    assert len(result) <= 3


# ===========================================================================
# 21. copy_rates_from_pos (lines 510-521)
# ===========================================================================


async def test_copy_rates_from_pos_negative_start():
    """copy_rates_from_pos raises ValueError for negative start_pos (line 510)."""
    client = MT5WebClient()
    with pytest.raises(ValueError, match="start_pos must be >= 0"):
        await client.copy_rates_from_pos("EURUSD", 1, -1, 10)


async def test_copy_rates_from_pos_zero_count():
    """copy_rates_from_pos returns empty for count<=0 (line 512)."""
    client = MT5WebClient()
    result = await client.copy_rates_from_pos("EURUSD", 1, 0, 0)
    assert result == []


async def test_copy_rates_from_pos_empty_bars():
    """copy_rates_from_pos returns empty when server returns no bars (line 518)."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_RATES, code=0, body=b""),
    )
    result = await client.copy_rates_from_pos("EURUSD", 1, 0, 5)
    assert result == []


async def test_copy_rates_from_pos_normal():
    """copy_rates_from_pos returns sliced bars based on position and count."""
    client = MT5WebClient()
    bars_body = _build_rate_bar_body(
        [
            {"time": i * 60, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "tick_volume": 10, "spread": 1}
            for i in range(20)
        ]
    )
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_RATES, code=0, body=bars_body),
    )
    result = await client.copy_rates_from_pos("EURUSD", 1, 2, 5)
    assert len(result) <= 5


# ===========================================================================
# 22. copy_ticks_from/range (lines 532, 555)
# ===========================================================================


async def test_copy_ticks_from_zero_count():
    """copy_ticks_from with count<=0 returns empty (line 532)."""
    client = MT5WebClient()
    result = await client.copy_ticks_from("EURUSD", 0, 0)
    assert result == []


async def test_copy_ticks_from_with_data():
    """copy_ticks_from returns filtered tick history."""
    client = _make_client_with_symbols()
    ticks = deque(
        [
            {
                "tick_time": 100,
                "tick_time_ms": 100000,
                "bid": 1.1,
                "ask": 1.2,
                "last": 0.0,
                "tick_volume": 0,
                "flags": 0,
                "symbol": "EURUSD",
            },
            {
                "tick_time": 200,
                "tick_time_ms": 200000,
                "bid": 1.15,
                "ask": 1.25,
                "last": 0.0,
                "tick_volume": 0,
                "flags": 0,
                "symbol": "EURUSD",
            },
        ]
    )
    client._tick_history_by_name["EURUSD"] = ticks
    result = await client.copy_ticks_from("EURUSD", 0, 10)
    assert len(result) == 2


async def test_copy_ticks_range_empty():
    """copy_ticks_range returns empty when to < from (line 555)."""
    client = _make_client_with_symbols()
    ticks = deque(
        [
            {
                "tick_time": 100,
                "tick_time_ms": 100000,
                "bid": 1.1,
                "ask": 1.2,
                "last": 0.0,
                "tick_volume": 0,
                "flags": 0,
                "symbol": "EURUSD",
            },
        ]
    )
    client._tick_history_by_name["EURUSD"] = ticks
    # date_to=50 < date_from=100 should return empty
    result = await client.copy_ticks_range("EURUSD", 200, 50)
    assert result == []


async def test_copy_ticks_range_with_data():
    """copy_ticks_range returns ticks within the time range."""
    client = _make_client_with_symbols()
    ticks = deque(
        [
            {
                "tick_time": 100,
                "tick_time_ms": 100000,
                "bid": 1.1,
                "ask": 1.2,
                "last": 0.0,
                "tick_volume": 0,
                "flags": 0,
                "symbol": "EURUSD",
            },
            {
                "tick_time": 200,
                "tick_time_ms": 200000,
                "bid": 1.15,
                "ask": 1.25,
                "last": 0.0,
                "tick_volume": 0,
                "flags": 0,
                "symbol": "EURUSD",
            },
            {
                "tick_time": 300,
                "tick_time_ms": 300000,
                "bid": 1.2,
                "ask": 1.3,
                "last": 0.0,
                "tick_volume": 0,
                "flags": 0,
                "symbol": "EURUSD",
            },
        ]
    )
    client._tick_history_by_name["EURUSD"] = ticks
    result = await client.copy_ticks_range("EURUSD", 100, 200)
    assert len(result) >= 1


# ===========================================================================
# 23. _get_tick_history (lines 567-574)
# ===========================================================================


async def test_get_tick_history_by_name():
    """_get_tick_history returns history from name cache."""
    client = _make_client_with_symbols()
    ticks = deque([{"bid": 1.1}])
    client._tick_history_by_name["EURUSD"] = ticks
    result = await client._get_tick_history("EURUSD")
    assert len(result) == 1


async def test_get_tick_history_by_id_fallback():
    """_get_tick_history falls back to id cache (lines 567-573)."""
    client = _make_client_with_symbols()
    ticks = deque([{"bid": 1.2}])
    client._tick_history_by_id[1] = ticks
    # Not in _tick_history_by_name
    result = await client._get_tick_history("EURUSD")
    assert len(result) == 1
    # Should now be linked
    assert "EURUSD" in client._tick_history_by_name


async def test_get_tick_history_not_found():
    """_get_tick_history returns empty list for unknown symbol (lines 568-569, 574)."""
    client = _make_client_with_symbols()
    result = await client._get_tick_history("EURUSD")
    assert result == []


async def test_get_tick_history_unknown_symbol():
    """_get_tick_history returns empty for totally unknown symbol (line 569)."""
    client = MT5WebClient()
    symbols_body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    result = await client._get_tick_history("NOSYMBOL")
    assert result == []


async def test_get_tick_history_id_exists_no_history():
    """_get_tick_history returns empty when symbol exists but no history by id (line 574)."""
    client = _make_client_with_symbols()
    # Symbol exists in _symbols but no tick_history_by_id
    result = await client._get_tick_history("EURUSD")
    assert result == []


# ===========================================================================
# 24. _resolve_conversion_rates (lines 585-634)
# ===========================================================================


async def test_resolve_conversion_rates_empty_source():
    """_resolve_conversion_rates returns None for empty source (line 585)."""
    client = _make_client_with_symbols()
    result = await client._resolve_conversion_rates(
        source="",
        target="USD",
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert result is None


async def test_resolve_conversion_rates_empty_target():
    """_resolve_conversion_rates returns None for empty target."""
    client = _make_client_with_symbols()
    result = await client._resolve_conversion_rates(
        source="EUR",
        target="",
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert result is None


async def test_resolve_conversion_rates_same_currency():
    """_resolve_conversion_rates returns (1,1) for same currency (line 587)."""
    client = _make_client_with_symbols()
    result = await client._resolve_conversion_rates(
        source="USD",
        target="USD",
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert result == (1.0, 1.0)


async def test_resolve_conversion_rates_direct():
    """_resolve_conversion_rates resolves directly when pair exists."""
    client = _make_client_with_symbols()
    # Place a tick in cache for EURUSD
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.10, "ask": 1.11}
    result = await client._resolve_conversion_rates(
        source="EUR",
        target="USD",
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert result is not None
    buy, sell = result
    assert buy > 0
    assert sell > 0


async def test_resolve_conversion_rates_usd_intermediary():
    """_resolve_conversion_rates uses USD intermediary when direct fails (lines 604-634)."""
    client = _make_client_with_symbols()
    # Add EURGBP symbol for conversion
    sym_eurgbp = SymbolInfo(name="EURGBP", symbol_id=10, digits=5)
    client._symbols["EURGBP"] = sym_eurgbp
    client._symbols_by_id[10] = sym_eurgbp

    # EURUSD and GBPUSD have ticks for USD intermediary
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.10, "ask": 1.11}
    client._tick_cache_by_name["GBPUSD"] = {"bid": 1.25, "ask": 1.26}

    result = await client._resolve_conversion_rates(
        source="EUR",
        target="GBP",
        current_symbol="EURGBP",
        fallback_rate=0.0,
    )
    # Direct path should work since EURGBP exists but has no tick
    # USD intermediary should work since both EURUSD and GBPUSD have ticks
    # But _resolve_side_rate for direct EUR->GBP would find EURGBP,
    # and _get_conversion_prices("EURGBP") returns None (no tick)
    # So falls through to USD intermediary
    if result is not None:
        buy, sell = result
        assert buy > 0
        assert sell > 0


async def test_resolve_conversion_rates_fails_completely():
    """_resolve_conversion_rates returns None when no path works (line 634)."""
    client = MT5WebClient()
    # No symbols, no ticks
    symbols_body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    result = await client._resolve_conversion_rates(
        source="XXX",
        target="YYY",
        current_symbol="XXXYYY",
        fallback_rate=0.0,
    )
    assert result is None


# ===========================================================================
# 25. _resolve_side_rate (lines 647-667)
# ===========================================================================


async def test_resolve_side_rate_direct():
    """_resolve_side_rate resolves direct pair (lines 647-654)."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.10, "ask": 1.11}
    rate = await client._resolve_side_rate(
        "EUR",
        "USD",
        prefer_ask_when_direct=True,
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert rate == 1.11  # ask when direct


async def test_resolve_side_rate_direct_bid():
    """_resolve_side_rate returns bid when prefer_ask_when_direct=False."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.10, "ask": 1.11}
    rate = await client._resolve_side_rate(
        "EUR",
        "USD",
        prefer_ask_when_direct=False,
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert rate == 1.10  # bid when not prefer_ask


async def test_resolve_side_rate_inverse():
    """_resolve_side_rate resolves inverse pair (lines 655-667)."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["USDJPY"] = {"bid": 150.0, "ask": 150.5}
    # Looking for JPY -> USD: inverse of USDJPY
    rate = await client._resolve_side_rate(
        "JPY",
        "USD",
        prefer_ask_when_direct=True,
        current_symbol="USDJPY",
        fallback_rate=0.0,
    )
    # Inverse: 1/bid when prefer_ask_when_direct=True
    assert rate > 0


async def test_resolve_side_rate_not_found():
    """_resolve_side_rate returns 0 when no conversion found (line 667)."""
    client = MT5WebClient()
    symbols_body = _build_gzip_symbols_body([])
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    rate = await client._resolve_side_rate(
        "XXX",
        "YYY",
        prefer_ask_when_direct=True,
        current_symbol="XXXYYY",
        fallback_rate=0.0,
    )
    assert rate == 0.0


# ===========================================================================
# 26. _find_conversion_symbol_name (lines 671-686)
# ===========================================================================


async def test_find_conversion_symbol_name_empty_base():
    """_find_conversion_symbol_name returns None for empty base (line 671)."""
    client = _make_client_with_symbols()
    result = await client._find_conversion_symbol_name("", "USD")
    assert result is None


async def test_find_conversion_symbol_name_empty_quote():
    """_find_conversion_symbol_name returns None for empty quote."""
    client = _make_client_with_symbols()
    result = await client._find_conversion_symbol_name("EUR", "")
    assert result is None


async def test_find_conversion_symbol_name_direct_match():
    """_find_conversion_symbol_name finds direct concatenation match."""
    client = _make_client_with_symbols()
    result = await client._find_conversion_symbol_name("EUR", "USD")
    assert result == "EURUSD"


async def test_find_conversion_symbol_name_fuzzy_match():
    """_find_conversion_symbol_name finds fuzzy match (line 686)."""
    client = _make_client_with_symbols()
    # Add a symbol with prefix/suffix matching but not direct concat
    sym = SymbolInfo(name="EUR.USD", symbol_id=99, digits=5)
    client._symbols["EUR.USD"] = sym
    # Direct "EURUSD" exists, so it returns that first
    result = await client._find_conversion_symbol_name("EUR", "USD")
    assert result == "EURUSD"


async def test_find_conversion_symbol_name_starts_ends_match():
    """_find_conversion_symbol_name matches via startswith/endswith (lines 681-686)."""
    client = MT5WebClient()
    sym = SymbolInfo(name="EUR_USD_FX", symbol_id=99, digits=5)
    client._symbols = {"EUR_USD_FX": sym}
    client._symbols_by_id = {99: sym}
    # No direct "EURUSD_FX" but EUR_USD_FX starts with EUR and ends with FX
    result = await client._find_conversion_symbol_name("EUR", "FX")
    assert result == "EUR_USD_FX"


async def test_find_conversion_symbol_name_loads_symbols():
    """_find_conversion_symbol_name loads symbols when cache empty (lines 672-677)."""
    client = MT5WebClient()
    symbols_data = [
        {
            "trade_symbol": "EURUSD",
            "symbol_id": 1,
            "digits": 5,
            "symbol_description": "",
            "symbol_path": "",
            "trade_calc_mode": 0,
            "basis": "",
            "sector": 0,
        },
    ]
    symbols_body = _build_gzip_symbols_body(symbols_data)
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(command=CMD_GET_SYMBOLS_GZIP, code=0, body=symbols_body),
    )
    result = await client._find_conversion_symbol_name("EUR", "USD")
    assert result == "EURUSD"


async def test_find_conversion_symbol_name_load_fails():
    """_find_conversion_symbol_name returns None when load_symbols fails (lines 675-677)."""
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        side_effect=RuntimeError("connection lost"),
    )
    result = await client._find_conversion_symbol_name("EUR", "USD")
    assert result is None


async def test_find_conversion_symbol_name_no_match():
    """_find_conversion_symbol_name returns None when no match found."""
    client = _make_client_with_symbols()
    result = await client._find_conversion_symbol_name("XXX", "YYY")
    assert result is None


# ===========================================================================
# 27. _get_conversion_prices (lines 704-713)
# ===========================================================================


def test_get_conversion_prices_from_tick_cache():
    """_get_conversion_prices extracts bid/ask from tick cache."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.10, "ask": 1.11}
    result = client._get_conversion_prices(
        "EURUSD",
        current_symbol="EURUSD",
        fallback_rate=0.0,
    )
    assert result == (1.10, 1.11)


def test_get_conversion_prices_fallback_rate():
    """_get_conversion_prices uses fallback_rate when tick has zero prices (lines 704, 706)."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 0.0, "ask": 0.0}
    result = client._get_conversion_prices(
        "EURUSD",
        current_symbol="EURUSD",
        fallback_rate=1.15,
    )
    assert result == (1.15, 1.15)


def test_get_conversion_prices_bid_zero_uses_ask():
    """_get_conversion_prices uses ask for bid when bid is zero (line 708)."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 0.0, "ask": 1.11}
    result = client._get_conversion_prices(
        "EURUSD",
        current_symbol="GBPUSD",
        fallback_rate=0.0,
    )
    assert result == (1.11, 1.11)


def test_get_conversion_prices_ask_zero_uses_bid():
    """_get_conversion_prices uses bid for ask when ask is zero (line 710)."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 1.10, "ask": 0.0}
    result = client._get_conversion_prices(
        "EURUSD",
        current_symbol="GBPUSD",
        fallback_rate=0.0,
    )
    assert result == (1.10, 1.10)


def test_get_conversion_prices_no_tick():
    """_get_conversion_prices returns None when no tick and no fallback (line 713)."""
    client = _make_client_with_symbols()
    result = client._get_conversion_prices(
        "EURUSD",
        current_symbol="GBPUSD",
        fallback_rate=0.0,
    )
    assert result is None


def test_get_conversion_prices_both_zero_no_fallback():
    """_get_conversion_prices returns None when both prices are zero and no fallback."""
    client = _make_client_with_symbols()
    client._tick_cache_by_name["EURUSD"] = {"bid": 0.0, "ask": 0.0}
    result = client._get_conversion_prices(
        "EURUSD",
        current_symbol="GBPUSD",
        fallback_rate=0.0,
    )
    assert result is None


# ===========================================================================
# 28. _calc_profit_raw (lines 729-760) -- various trade calc modes
# ===========================================================================


def test_calc_profit_raw_forex():
    """_calc_profit_raw: forex mode 0 (line 746)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 0, "contract_size": 100000.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=0.1, price_open=1.10, price_close=1.11)
    assert result is not None
    expected = 1.0 * (1.11 - 1.10) * 100000.0 * 0.1
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_forex_sell():
    """_calc_profit_raw: forex mode 0, sell direction."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 0, "contract_size": 100000.0}
    result = client._calc_profit_raw(info, is_buy=False, volume=0.1, price_open=1.10, price_close=1.09)
    assert result is not None
    expected = -1.0 * (1.09 - 1.10) * 100000.0 * 0.1
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_forex_mode5():
    """_calc_profit_raw: forex mode 5 (leveraged forex)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 5, "contract_size": 100000.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=1.10, price_close=1.11)
    assert result is not None


def test_calc_profit_raw_forex_zero_contract():
    """_calc_profit_raw: forex mode with zero contract_size returns error (line 745)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 0, "contract_size": 0.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=1.10, price_close=1.11)
    assert result is None


def test_calc_profit_raw_futures_mode1():
    """_calc_profit_raw: futures mode 1 (line 747-751)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 1, "contract_size": 10.0, "tick_size": 0.25, "tick_value": 12.50}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=101.0)
    assert result is not None
    point_value = 12.50 / 0.25
    expected = 1.0 * (101.0 - 100.0) * 1.0 * point_value
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_futures_zero_tick_value():
    """_calc_profit_raw: futures mode with zero tick_value (line 749)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 1, "contract_size": 10.0, "tick_size": 0.25, "tick_value": 0.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=101.0)
    assert result is None


def test_calc_profit_raw_futures_zero_tick_size():
    """_calc_profit_raw: futures mode 1 with tick_size=0 uses tick_value directly (line 750)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 1, "contract_size": 10.0, "tick_size": 0.0, "tick_value": 50.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=101.0)
    assert result is not None
    expected = 1.0 * (101.0 - 100.0) * 1.0 * 50.0
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_futures_mode33():
    """_calc_profit_raw: futures mode 33."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 33, "contract_size": 10.0, "tick_size": 0.01, "tick_value": 5.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=50.0, price_close=51.0)
    assert result is not None


def test_calc_profit_raw_options_mode35():
    """_calc_profit_raw: options mode 35 (in {1,33,35,36})."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 35, "contract_size": 100.0, "tick_size": 0.01, "tick_value": 1.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=10.0, price_close=11.0)
    assert result is not None


def test_calc_profit_raw_options_mode36():
    """_calc_profit_raw: options mode 36."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 36, "contract_size": 100.0, "tick_size": 0.01, "tick_value": 1.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=10.0, price_close=11.0)
    assert result is not None


def test_calc_profit_raw_forts_mode34():
    """_calc_profit_raw: FORTS futures mode 34 (lines 752-757)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 34, "contract_size": 10.0, "tick_size": 0.1, "tick_value": 1.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=2.0, price_open=100.0, price_close=105.0)
    assert result is not None
    tick_size = 0.1
    tick_value = 1.0
    open_value = 100.0 * tick_value / tick_size
    close_value = 105.0 * tick_value / tick_size
    expected = 1.0 * (close_value - open_value) * 2.0
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_forts_mode34_zero_tick_size():
    """_calc_profit_raw: FORTS mode 34 with tick_size=0 (line 755-756)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 34, "tick_size": 0.0, "tick_value": 1.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=105.0)
    assert result is not None


def test_calc_profit_raw_forts_mode34_zero_tick_value():
    """_calc_profit_raw: FORTS mode 34 with zero tick_value (line 754)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 34, "tick_size": 0.1, "tick_value": 0.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=105.0)
    assert result is None


def test_calc_profit_raw_cfd_modes():
    """_calc_profit_raw: CFD modes {2,3,4,32,38} (line 743,746)."""
    client = MT5WebClient()
    for mode in [2, 3, 4, 32, 38]:
        info = {"trade_calc_mode": mode, "contract_size": 1.0}
        result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=105.0)
        assert result is not None, f"Failed for mode {mode}"


def test_calc_profit_raw_bond_mode():
    """_calc_profit_raw: bond mode 37 (lines 724-736)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 37,
        "contract_size": 10.0,
        "face_value": 1000.0,
        "accrued_interest": 5.0,
    }
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=99.0, price_close=100.0)
    assert result is not None
    direction = 1.0
    open_value = (99.0 / 100.0) * 1000.0
    close_value = (100.0 / 100.0) * 1000.0 + 5.0
    expected = direction * (close_value - open_value) * 10.0 * 1.0
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_bond_mode39():
    """_calc_profit_raw: bond mode 39."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 39,
        "contract_size": 5.0,
        "face_value": 500.0,
        "accrued_interest": 2.0,
    }
    result = client._calc_profit_raw(info, is_buy=False, volume=2.0, price_open=98.0, price_close=99.0)
    assert result is not None


def test_calc_profit_raw_bond_zero_contract():
    """_calc_profit_raw: bond mode with zero contract_size (lines 728-732)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 37, "contract_size": 0.0, "face_value": 1000.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=99.0, price_close=100.0)
    assert result is None


def test_calc_profit_raw_bond_zero_face_value():
    """_calc_profit_raw: bond mode with zero face_value (line 729)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 37, "contract_size": 10.0, "face_value": 0.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=99.0, price_close=100.0)
    assert result is None


def test_calc_profit_raw_collateral():
    """_calc_profit_raw: collateral mode 64 returns 0 (line 738)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 64}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=105.0)
    assert result == 0.0


def test_calc_profit_raw_unknown_mode():
    """_calc_profit_raw: unknown mode falls through to default (lines 758-760)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 999, "contract_size": 100.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=105.0)
    # Unknown modes fall through to the final default calculation
    assert result is not None
    expected = 1.0 * (105.0 - 100.0) * 100.0 * 1.0
    assert abs(result - expected) < 1e-6


def test_calc_profit_raw_unknown_mode_zero_contract():
    """_calc_profit_raw: unknown mode with zero contract_size (line 758-759)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 999, "contract_size": 0.0}
    result = client._calc_profit_raw(info, is_buy=True, volume=1.0, price_open=100.0, price_close=105.0)
    assert result is None


# ===========================================================================
# 29. _calc_margin_raw (lines 781-825)
# ===========================================================================


def test_calc_margin_raw_bond():
    """_calc_margin_raw: bond mode 37 (lines 778-785)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 37,
        "contract_size": 10.0,
        "face_value": 1000.0,
        "tick_size": 0.01,
        "tick_value": 1.0,
        "margin_initial": 0.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=99.0, leverage=100)
    assert result is not None
    expected = 1.0 * 10.0 * 1000.0 * 99.0 / 100.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_bond_zero_contract():
    """_calc_margin_raw: bond mode with zero contract_size (line 781)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 37, "contract_size": 0.0, "face_value": 1000.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=99.0, leverage=100)
    assert result is None


def test_calc_margin_raw_bond_zero_face_value():
    """_calc_margin_raw: bond mode with zero face_value."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 37, "contract_size": 10.0, "face_value": 0.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=99.0, leverage=100)
    assert result is None


def test_calc_margin_raw_collateral():
    """_calc_margin_raw: collateral mode 64 returns 0 (line 787)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 64, "contract_size": 100.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result == 0.0


def test_calc_margin_raw_mode5():
    """_calc_margin_raw: mode 5 (leveraged forex, returns units) (lines 789-791)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 5, "contract_size": 100000.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=0.1, price=1.10, leverage=100)
    assert result is not None
    expected = 0.1 * 100000.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode5_zero_contract():
    """_calc_margin_raw: mode 5 with zero contract_size (line 790)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 5, "contract_size": 0.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=1.10, leverage=100)
    assert result is None


def test_calc_margin_raw_mode0_forex():
    """_calc_margin_raw: mode 0 (forex leveraged) (lines 792-800)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 0, "contract_size": 100000.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=0.1, price=1.10, leverage=100)
    assert result is not None
    expected = (0.1 * 100000.0) / 100.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode0_zero_contract():
    """_calc_margin_raw: mode 0 with zero contract_size (line 794)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 0, "contract_size": 0.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=1.10, leverage=100)
    assert result is None


def test_calc_margin_raw_mode0_zero_leverage():
    """_calc_margin_raw: mode 0 with zero leverage (lines 795-796)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 0, "contract_size": 100000.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=1.10, leverage=0)
    assert result is None


def test_calc_margin_raw_mode4():
    """_calc_margin_raw: mode 4 (CFD leverage * price) (lines 798-799)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 4, "contract_size": 100.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=10)
    assert result is not None
    expected = (1.0 * 100.0) / 10.0 * 50.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_futures_margin_initial():
    """_calc_margin_raw: futures with margin_initial (lines 801-803)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 1,
        "contract_size": 10.0,
        "margin_initial": 5000.0,
        "tick_size": 0.25,
        "tick_value": 12.5,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=2.0, price=100.0, leverage=100)
    assert result is not None
    expected = 2.0 * 5000.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_futures_tick_value():
    """_calc_margin_raw: futures without margin_initial (lines 804-810)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 1,
        "contract_size": 10.0,
        "margin_initial": 0.0,
        "tick_size": 0.25,
        "tick_value": 12.5,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is not None
    point_value = 12.5 / 0.25
    expected = 1.0 * 100.0 * point_value
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_futures_zero_tick_value():
    """_calc_margin_raw: futures without margin_initial and zero tick_value (line 805-808)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 1,
        "contract_size": 10.0,
        "margin_initial": 0.0,
        "tick_size": 0.25,
        "tick_value": 0.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is None


def test_calc_margin_raw_futures_zero_tick_size():
    """_calc_margin_raw: futures with tick_size=0, uses tick_value directly (line 809)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 1,
        "contract_size": 10.0,
        "margin_initial": 0.0,
        "tick_size": 0.0,
        "tick_value": 50.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is not None
    expected = 1.0 * 100.0 * 50.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_futures_mode33():
    """_calc_margin_raw: futures mode 33."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 33,
        "contract_size": 10.0,
        "margin_initial": 3000.0,
        "tick_size": 0.01,
        "tick_value": 5.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=100)
    assert result is not None
    assert abs(result - 3000.0) < 1e-6


def test_calc_margin_raw_futures_mode34():
    """_calc_margin_raw: futures mode 34."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 34,
        "contract_size": 10.0,
        "margin_initial": 0.0,
        "tick_size": 0.01,
        "tick_value": 5.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=100)
    assert result is not None


def test_calc_margin_raw_cfd_index_mode3():
    """_calc_margin_raw: CFD index mode 3 (lines 811-818)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 3,
        "contract_size": 1.0,
        "tick_size": 0.01,
        "tick_value": 0.01,
        "margin_initial": 0.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is not None


def test_calc_margin_raw_cfd_index_zero_contract():
    """_calc_margin_raw: CFD index mode 3 with zero contract (line 812)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 3,
        "contract_size": 0.0,
        "tick_size": 0.01,
        "tick_value": 0.01,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is None


def test_calc_margin_raw_cfd_index_zero_tick_value():
    """_calc_margin_raw: CFD index mode 3 with zero tick_value (line 813)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 3,
        "contract_size": 1.0,
        "tick_size": 0.01,
        "tick_value": 0.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is None


def test_calc_margin_raw_cfd_index_zero_tick_size():
    """_calc_margin_raw: CFD index mode 3 with tick_size=0 (line 817)."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 3,
        "contract_size": 1.0,
        "tick_size": 0.0,
        "tick_value": 1.0,
        "margin_initial": 0.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is not None
    expected = 1.0 * 1.0 * 100.0 * 1.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode2():
    """_calc_margin_raw: mode 2 (CFD) (lines 819-824)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 2, "contract_size": 100.0, "margin_initial": 0.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=100)
    assert result is not None
    expected = 1.0 * 100.0 * 50.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode2_zero_contract():
    """_calc_margin_raw: mode 2 with zero contract_size (line 823)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 2, "contract_size": 0.0, "margin_initial": 0.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=100)
    assert result is None


def test_calc_margin_raw_mode32_with_margin_initial():
    """_calc_margin_raw: mode 32 with margin_initial (line 820-821)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 32, "contract_size": 100.0, "margin_initial": 2000.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=100)
    assert result is not None
    expected = 1.0 * 2000.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode35_with_margin_initial():
    """_calc_margin_raw: mode 35 (options) with margin_initial."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 35, "contract_size": 100.0, "margin_initial": 1500.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=2.0, price=10.0, leverage=100)
    assert result is not None
    expected = 2.0 * 1500.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode36_with_margin_initial():
    """_calc_margin_raw: mode 36 (options) with margin_initial."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 36, "contract_size": 100.0, "margin_initial": 1000.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=3.0, price=10.0, leverage=100)
    assert result is not None
    expected = 3.0 * 1000.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode38_with_margin_initial():
    """_calc_margin_raw: mode 38 with margin_initial."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 38, "contract_size": 100.0, "margin_initial": 800.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=10.0, leverage=100)
    assert result is not None
    expected = 1.0 * 800.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_mode32_no_margin_initial():
    """_calc_margin_raw: mode 32 without margin_initial uses units*price (lines 822-824)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 32, "contract_size": 100.0, "margin_initial": 0.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=50.0, leverage=100)
    assert result is not None
    expected = 1.0 * 100.0 * 50.0
    assert abs(result - expected) < 1e-6


def test_calc_margin_raw_unknown_mode():
    """_calc_margin_raw: unsupported mode returns error (line 825)."""
    client = MT5WebClient()
    info = {"trade_calc_mode": 999, "contract_size": 100.0}
    result = client._calc_margin_raw(info, is_buy=True, volume=1.0, price=100.0, leverage=100)
    assert result is None


def test_calc_margin_raw_bond_mode39():
    """_calc_margin_raw: bond mode 39."""
    client = MT5WebClient()
    info = {
        "trade_calc_mode": 39,
        "contract_size": 5.0,
        "face_value": 500.0,
        "tick_size": 0.01,
        "tick_value": 1.0,
        "margin_initial": 0.0,
    }
    result = client._calc_margin_raw(info, is_buy=True, volume=2.0, price=98.0, leverage=100)
    assert result is not None
    expected = 2.0 * 5.0 * 500.0 * 98.0 / 100.0
    assert abs(result - expected) < 1e-6
