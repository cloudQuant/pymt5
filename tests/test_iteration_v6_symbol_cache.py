"""Focused tests for bounded negative lookups and full-symbol cache expiry."""

from unittest.mock import AsyncMock

import pytest

from pymt5._market_data import _MarketDataMixin
from pymt5.types import Record


def _symbol(name: str, symbol_id: int) -> Record:
    return {
        "trade_symbol": name,
        "symbol_id": symbol_id,
        "digits": 5,
        "symbol_description": "",
        "symbol_path": "",
        "trade_calc_mode": 0,
        "basis": "",
        "sector": 0,
    }


class _SymbolCacheClient(_MarketDataMixin):
    def __init__(self, responses: list[list[Record]], ttl: float) -> None:
        self._symbols = {}
        self._symbols_by_id = {}
        self._full_symbols = {}
        self._tick_history_by_id = {}
        self._tick_history_by_name = {}
        self._symbol_cache_ttl = ttl
        self._symbols_loaded_at = 0.0
        self.get_symbols = AsyncMock(side_effect=responses)


def _clock(monkeypatch: pytest.MonkeyPatch, now: list[float]) -> None:
    monkeypatch.setattr("pymt5._market_data.time.monotonic", lambda: now[0])


async def test_repeated_unknown_symbol_misses_use_negative_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([[], []], ttl=60.0)

    assert await client._resolve_symbol_id("UNKNOWN") is None
    now[0] = 104.9
    assert await client._resolve_symbol_id("UNKNOWN") is None
    assert client.get_symbols.await_count == 1

    now[0] = 105.0
    assert await client._resolve_symbol_id("UNKNOWN") is None
    assert client.get_symbols.await_count == 2


async def test_unknown_symbol_can_resolve_after_negative_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([[], [_symbol("NEW", 7)]], ttl=60.0)

    assert await client._resolve_symbol_id("NEW") is None
    now[0] = 106.0
    assert await client._resolve_symbol_id("NEW") == 7
    assert client.get_symbols.await_count == 2
    assert "NEW" not in client._unknown_symbol_misses


async def test_successful_cached_lookup_does_not_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([[_symbol("EURUSD", 1)]], ttl=60.0)

    assert await client._resolve_symbol_id("EURUSD") == 1
    now[0] = 1000.0
    assert await client._resolve_symbol_id("EURUSD") == 1
    assert client.get_symbols.await_count == 1


async def test_full_symbol_cache_expires_at_configured_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([[_symbol("EURUSD", 1)], [_symbol("EURUSD", 2)]], ttl=10.0)

    assert (await client.load_symbols())["EURUSD"].symbol_id == 1
    now[0] = 109.9
    assert (await client.load_symbols())["EURUSD"].symbol_id == 1
    assert client.get_symbols.await_count == 1

    now[0] = 110.0
    assert (await client.load_symbols())["EURUSD"].symbol_id == 2
    assert client.get_symbols.await_count == 2


async def test_zero_basic_symbol_ttl_preserves_always_reload_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([[_symbol("EURUSD", 1)], [_symbol("EURUSD", 2)]], ttl=0.0)

    assert (await client.load_symbols())["EURUSD"].symbol_id == 1
    now[0] = 100_000.0
    assert (await client.load_symbols())["EURUSD"].symbol_id == 2
    assert client.get_symbols.await_count == 2


async def test_cmd18_full_symbol_cache_expires_at_configured_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([], ttl=10.0)
    client._full_symbols_loaded_at = {}

    async def fetch(symbol: str) -> Record:
        info = {"trade_symbol": symbol, "contract_size": float(len(client._full_symbols) + 1)}
        return client._cache_full_symbol(symbol, info)

    client.get_full_symbol_info = AsyncMock(side_effect=fetch)
    first = await client.symbol_info("EURUSD")
    now[0] = 109.9
    cached = await client.symbol_info("EURUSD")
    assert cached == first
    assert client.get_full_symbol_info.await_count == 1

    client._full_symbols["EURUSD"]["contract_size"] = 99.0
    now[0] = 110.0
    refreshed = await client.symbol_info("EURUSD")
    assert refreshed["contract_size"] == 1.0
    assert client.get_full_symbol_info.await_count == 2


async def test_zero_cmd18_full_symbol_ttl_preserves_cached_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([], ttl=0.0)
    client._full_symbols["EURUSD"] = {"trade_symbol": "EURUSD", "contract_size": 100_000.0}
    client.get_full_symbol_info = AsyncMock()

    now[0] = 100_000.0
    result = await client.symbol_info("EURUSD")
    assert result["contract_size"] == 100_000.0
    client.get_full_symbol_info.assert_not_awaited()


async def test_negative_cache_has_a_bounded_number_of_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    monkeypatch.setattr("pymt5._market_data._UNKNOWN_SYMBOL_CACHE_LIMIT", 2)
    client = _SymbolCacheClient([[], [], []], ttl=60.0)

    for name in ("FIRST", "SECOND", "THIRD"):
        assert await client._resolve_symbol_id(name) is None

    assert set(client._unknown_symbol_misses) == {"SECOND", "THIRD"}


async def test_invalidation_clears_negative_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    client = _SymbolCacheClient([[], [_symbol("NEW", 7)]], ttl=60.0)

    assert await client._resolve_symbol_id("NEW") is None
    client.invalidate_symbol_cache()
    assert await client._resolve_symbol_id("NEW") == 7
    assert client.get_symbols.await_count == 2
