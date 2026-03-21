"""Market data mixin for MT5WebClient.

Provides symbol management, tick/bar data retrieval, and order book
subscriptions. Currency conversion and profit/margin calculations
are in :mod:`pymt5._currency`.
"""

from __future__ import annotations

import asyncio
import struct
import time
import zlib
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from pymt5._currency import _CurrencyMixin
from pymt5._logging import get_logger
from pymt5._parsers import (
    _coerce_timestamp,
    _coerce_timestamp_ms,
    _coerce_timestamp_ms_end,
    _history_lookback_seconds,
    _matches_group_mask,
    _normalize_full_symbol_record,
    _normalize_timeframe_minutes,
    _parse_counted_records,
    _parse_rate_bars,
    _tick_matches_copy_flags,
    _to_copy_tick_record,
)
from pymt5._subscription import SubscriptionHandle
from pymt5._validation import validate_symbol_name
from pymt5.constants import (
    CMD_GET_FULL_SYMBOLS,
    CMD_GET_RATES,
    CMD_GET_SPREADS,
    CMD_GET_SYMBOL_GROUPS,
    CMD_GET_SYMBOLS,
    CMD_GET_SYMBOLS_GZIP,
    CMD_SUBSCRIBE_BOOK,
    CMD_SUBSCRIBE_TICKS,
    COPY_TICKS_ALL,
    PERIOD_MAP,
    PROP_FIXED_STRING,
    PROP_I32,
    PROP_U16,
)
from pymt5.exceptions import SymbolNotFoundError, ValidationError
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    FULL_SYMBOL_FIELD_NAMES,
    FULL_SYMBOL_SCHEMA,
    SPREAD_FIELD_NAMES,
    SPREAD_SCHEMA,
    SYMBOL_BASIC_FIELD_NAMES,
    SYMBOL_BASIC_SCHEMA,
    SYMBOL_GROUP_SCHEMA,
)
from pymt5.types import Record, RecordList, SymbolInfo

if TYPE_CHECKING:
    from collections import deque

    from pymt5.transport import MT5WebSocketTransport

_T = TypeVar("_T")

logger = get_logger("pymt5.client")


class _MarketDataMixin(_CurrencyMixin):
    """Mixin providing market data methods for MT5WebClient.

    Inherits currency conversion and profit/margin calculations
    from :class:`_CurrencyMixin`.
    """

    # Attributes provided by MT5WebClient.__init__
    transport: MT5WebSocketTransport
    _symbols: dict[str, SymbolInfo]
    _symbols_by_id: dict[int, SymbolInfo]
    _full_symbols: dict[str, Record]
    _tick_cache_by_id: dict[int, Record]
    _tick_cache_by_name: dict[str, Record]
    _tick_history_limit: int
    _tick_history_by_id: dict[int, deque[Record]]
    _tick_history_by_name: dict[str, deque[Record]]
    _book_cache_by_id: dict[int, Record]
    _book_cache_by_name: dict[str, Record]
    _subscribed_ids: list[int]
    _subscribed_book_ids: list[int]
    _symbol_cache_ttl: float
    _symbols_loaded_at: float

    if TYPE_CHECKING:

        def _fail_last_error(self, code: int, message: str) -> _T | None: ...
        def _clear_last_error(self) -> None: ...

    def _is_symbol_cache_valid(self) -> bool:
        """Check whether the symbol cache is still valid.

        Returns ``True`` if the TTL is disabled (0) or if the cache has not
        yet expired.
        """
        if self._symbol_cache_ttl <= 0:
            return False
        if self._symbols_loaded_at <= 0:
            return False
        elapsed = time.monotonic() - self._symbols_loaded_at
        return elapsed < self._symbol_cache_ttl

    def invalidate_symbol_cache(self) -> None:
        """Force the symbol cache to be reloaded on the next ``load_symbols()`` call."""
        self._symbols_loaded_at = 0.0

    async def load_symbols(self, use_gzip: bool = True) -> dict[str, SymbolInfo]:
        """Load symbols and build internal cache for name->id lookup.

        If a ``symbol_cache_ttl`` was configured and the cache is still
        valid, the existing cache is returned without re-fetching from
        the server.
        """
        if self._symbols and self._is_symbol_cache_valid():
            logger.debug("symbol cache still valid, skipping reload")
            return dict(self._symbols)
        raw = await self.get_symbols(use_gzip=use_gzip)
        self._symbols.clear()
        self._symbols_by_id.clear()
        self._full_symbols.clear()
        for s in raw:
            info = SymbolInfo(
                name=s["trade_symbol"],
                symbol_id=s["symbol_id"],
                digits=s["digits"],
                description=s.get("symbol_description", ""),
                path=s.get("symbol_path", ""),
                trade_calc_mode=s.get("trade_calc_mode", 0),
                basis=s.get("basis", ""),
                sector=s.get("sector", 0),
            )
            self._symbols[info.name] = info
            self._symbols_by_id[info.symbol_id] = info
            history = self._tick_history_by_id.get(info.symbol_id)
            if history is not None:
                self._tick_history_by_name[info.name] = history
        self._symbols_loaded_at = time.monotonic()
        logger.info("symbol cache loaded: %d symbols", len(self._symbols))
        return dict(self._symbols)

    def get_symbol_info(self, name: str) -> SymbolInfo | None:
        """Look up cached symbol info by name."""
        return self._symbols.get(name)

    def get_symbol_id(self, name: str) -> int | None:
        """Look up cached symbol id by name."""
        info = self._symbols.get(name)
        return info.symbol_id if info else None

    @property
    def symbol_names(self) -> list[str]:
        """Return list of all cached symbol names."""
        return list(self._symbols.keys())

    async def _resolve_symbol_id(self, symbol: str) -> int | None:
        info = self._symbols.get(symbol)
        if info is None:
            await self.load_symbols()
            info = self._symbols.get(symbol)
        return info.symbol_id if info is not None else None

    async def get_symbols(self, use_gzip: bool = True) -> RecordList:
        if use_gzip:
            result = await self.transport.send_command(CMD_GET_SYMBOLS_GZIP)
            if result.body and len(result.body) > 4:
                compressed = bytes(result.body[4:])
                raw = await asyncio.to_thread(zlib.decompress, compressed)
                return _parse_counted_records(raw, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)
            return []
        result = await self.transport.send_command(CMD_GET_SYMBOLS)
        return _parse_counted_records(result.body, SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES)

    async def symbols_total(self, use_gzip: bool = True) -> int:
        """Official-style helper returning the number of known symbols."""
        if self._symbols:
            return len(self._symbols)
        return len(await self.get_symbols(use_gzip=use_gzip))

    async def symbols_get(self, group: str | None = None, use_gzip: bool = True) -> RecordList:
        """Official-style symbol list with optional wildcard group filtering."""
        symbols = await self.get_symbols(use_gzip=use_gzip)
        if not group:
            return symbols
        return [item for item in symbols if _matches_group_mask(item.get("trade_symbol", ""), group)]

    async def get_full_symbol_info(self, symbol: str) -> Record | None:
        """Get detailed symbol specification (cmd=18): contract_size, tick_size, tick_value,
        volume_min/max/step, margin_initial/maintenance, spread, currencies, etc.

        Args:
            symbol: Symbol name, e.g. "EURUSD"

        Returns:
            Dict with full symbol properties, or None if symbol not found.
        """
        validate_symbol_name(symbol)
        payload = SeriesCodec.serialize(
            [
                (PROP_FIXED_STRING, symbol[:32], 64),
            ]
        )
        try:
            result = await self.transport.send_command(CMD_GET_FULL_SYMBOLS, payload)
            if not result.body:
                return None
            records = _parse_counted_records(result.body, FULL_SYMBOL_SCHEMA, FULL_SYMBOL_FIELD_NAMES)
            if records:
                info = _normalize_full_symbol_record(records[0])
                self._full_symbols[symbol] = info
                return dict(info)
            full_sym_size = get_series_size(FULL_SYMBOL_SCHEMA)
            if len(result.body) >= full_sym_size:
                vals = SeriesCodec.parse(result.body, FULL_SYMBOL_SCHEMA)
                info = _normalize_full_symbol_record(dict(zip(FULL_SYMBOL_FIELD_NAMES, vals)))
                self._full_symbols[symbol] = info
                return dict(info)
            return None
        except (struct.error, KeyError, ValueError, TypeError, IndexError) as exc:
            logger.warning("get_full_symbol_info(%s) failed: %s", symbol, exc)
            return None

    async def symbol_info(self, symbol: str) -> Record | None:
        """Official-style symbol_info() wrapper backed by cmd=18."""
        cached = self._full_symbols.get(symbol)
        if cached is not None:
            return dict(cached)
        info = await self.get_full_symbol_info(symbol)
        if info is not None:
            return dict(info)
        if not self._symbols:
            await self.load_symbols()
        basic = self._symbols.get(symbol)
        if basic is None:
            return None
        return {
            "trade_symbol": basic.name,
            "symbol_id": basic.symbol_id,
            "digits": basic.digits,
            "symbol_description": basic.description,
            "symbol_path": basic.path,
            "trade_calc_mode": basic.trade_calc_mode,
            "basis": basic.basis,
            "sector": basic.sector,
        }

    def symbol_info_tick(self, symbol: str) -> Record | None:
        """Return the latest cached tick for a symbol if a tick stream has been seen."""
        tick = self._tick_cache_by_name.get(symbol)
        return dict(tick) if tick is not None else None

    async def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        """Best-effort official-style symbol selection via tick subscription."""
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return False
        if enable:
            await self.subscribe_ticks([symbol_id])
        else:
            await self.unsubscribe_ticks([symbol_id])
            self._tick_cache_by_id.pop(symbol_id, None)
            self._tick_cache_by_name.pop(symbol, None)
        return True

    async def get_symbol_groups(self) -> list[str]:
        """Get symbol type/group names (cmd=9), e.g. 'Forex', 'Crypto', 'Indices'.

        Returns list of group name strings.
        """
        result = await self.transport.send_command(CMD_GET_SYMBOL_GROUPS)
        if not result.body or len(result.body) < 4:
            return []
        count = struct.unpack_from("<I", result.body, 0)[0]
        group_size = get_series_size(SYMBOL_GROUP_SCHEMA)
        groups = []
        offset = 4
        for _ in range(count):
            if offset + group_size > len(result.body):
                break
            vals = SeriesCodec.parse_at(result.body, SYMBOL_GROUP_SCHEMA, offset)
            groups.append(vals[0])
            offset += group_size
        logger.info("loaded %d symbol groups", len(groups))
        return groups

    async def get_spreads(self, symbol_ids: list[int] | None = None) -> RecordList:
        """Request spread data (cmd=20).

        Args:
            symbol_ids: Optional list of symbol IDs to query. If None, sends empty payload.

        Returns:
            List of spread dicts with keys: spread_id, flags, trade_symbol, param1, param2, spread_value.
        """
        if symbol_ids:
            payload = struct.pack(f"<{len(symbol_ids) + 1}I", len(symbol_ids), *symbol_ids)
        else:
            payload = b""
        result = await self.transport.send_command(CMD_GET_SPREADS, payload)
        return _parse_counted_records(result.body, SPREAD_SCHEMA, SPREAD_FIELD_NAMES)

    async def subscribe_ticks(self, symbol_ids: list[int]) -> None:
        """Subscribe to tick updates for given symbol IDs."""
        if not symbol_ids:
            logger.debug("subscribe_ticks called with empty list, skipping")
            return
        # Accumulate rather than replace -- MT5 subscribe replaces server-side,
        # so we must always send the full set of desired subscriptions.
        existing = set(getattr(self, "_subscribed_ids", None) or [])
        merged = sorted(existing | set(symbol_ids))
        payload = struct.pack(f"<{len(merged) + 1}I", len(merged), *merged)
        await self.transport.send_command(CMD_SUBSCRIBE_TICKS, payload)
        self._subscribed_ids = merged
        logger.info(
            "subscribed to %d symbol ids for ticks (added %d new)", len(merged), len(set(symbol_ids) - existing)
        )

    async def unsubscribe_ticks(self, symbol_ids: list[int]) -> None:
        """Remove specific symbol IDs from tick subscriptions.

        If the resulting subscription set is empty, sends an empty subscription
        to the server to stop all tick pushes.
        """
        existing = set(self._subscribed_ids)
        to_remove = set(symbol_ids)
        remaining = sorted(existing - to_remove)
        if remaining:
            payload = struct.pack(f"<{len(remaining) + 1}I", len(remaining), *remaining)
        else:
            payload = struct.pack("<I", 0)
        await self.transport.send_command(CMD_SUBSCRIBE_TICKS, payload)
        removed_count = len(existing) - len(remaining)
        self._subscribed_ids = remaining
        logger.info("unsubscribed %d symbol ids from ticks (%d remaining)", removed_count, len(remaining))

    async def subscribe_symbols(self, symbol_names: list[str]) -> list[int]:
        """Subscribe to tick updates by symbol name (requires load_symbols first).

        Returns list of resolved symbol IDs.
        Raises ValueError if any symbol name is not found in cache.
        """
        ids = []
        missing = []
        for name in symbol_names:
            info = self._symbols.get(name)
            if info is None:
                missing.append(name)
            else:
                ids.append(info.symbol_id)
        if missing:
            raise SymbolNotFoundError(f"symbols not found in cache (call load_symbols first): {missing}")
        await self.subscribe_ticks(ids)
        return ids

    async def subscribe_book(self, symbol_ids: list[int]) -> None:
        """Subscribe to order book / depth-of-market for given symbols (cmd=22).

        After subscribing, the server pushes DOM updates via cmd=23.
        Register a handler with on_book_update() to receive them.
        """
        existing = set(self._subscribed_book_ids)
        merged = sorted(existing | set(symbol_ids))
        payload = struct.pack(f"<{len(merged) + 1}I", len(merged), *merged)
        await self.transport.send_command(CMD_SUBSCRIBE_BOOK, payload)
        self._subscribed_book_ids = merged
        logger.info(
            "subscribed to order book for %d symbols (added %d new)",
            len(merged),
            len(set(symbol_ids) - existing),
        )

    async def subscribe_book_by_name(self, symbol_names: list[str]) -> list[int]:
        """Subscribe to order book by symbol names (requires load_symbols first).

        Returns list of resolved symbol IDs.
        Raises ValueError if any symbol name is not found in cache.
        """
        ids = []
        missing = []
        for name in symbol_names:
            info = self._symbols.get(name)
            if info is None:
                missing.append(name)
            else:
                ids.append(info.symbol_id)
        if missing:
            raise SymbolNotFoundError(f"symbols not found in cache (call load_symbols first): {missing}")
        await self.subscribe_book(ids)
        return ids

    async def unsubscribe_book(self, symbol_ids: list[int]) -> None:
        """Remove specific symbol IDs from the current order-book subscription set."""
        existing = set(self._subscribed_book_ids)
        remaining = sorted(existing - set(symbol_ids))
        if remaining:
            payload = struct.pack(f"<{len(remaining) + 1}I", len(remaining), *remaining)
        else:
            payload = struct.pack("<I", 0)
        await self.transport.send_command(CMD_SUBSCRIBE_BOOK, payload)
        removed = existing - set(remaining)
        self._subscribed_book_ids = remaining
        for symbol_id in removed:
            entry = self._book_cache_by_id.pop(symbol_id, None)
            if entry and entry.get("symbol"):
                self._book_cache_by_name.pop(entry["symbol"], None)

    async def subscribe_ticks_managed(self, symbol_ids: list[int]) -> SubscriptionHandle:
        """Subscribe to tick updates and return a managed handle.

        The returned :class:`SubscriptionHandle` can be used as an async
        context manager to automatically unsubscribe on exit::

            async with client.subscribe_ticks_managed([123]) as sub:
                ...  # subscribed
            # automatically unsubscribed
        """
        await self.subscribe_ticks(symbol_ids)
        return SubscriptionHandle(symbol_ids, self.unsubscribe_ticks)

    async def subscribe_book_managed(self, symbol_ids: list[int]) -> SubscriptionHandle:
        """Subscribe to order book and return a managed handle.

        Works the same as :meth:`subscribe_ticks_managed` but for book data.
        """
        await self.subscribe_book(symbol_ids)
        return SubscriptionHandle(symbol_ids, self.unsubscribe_book)

    async def market_book_add(self, symbol: str) -> bool:
        """Official-style alias for subscribing to a symbol's DOM stream."""
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return False
        await self.subscribe_book([symbol_id])
        return True

    def market_book_get(self, symbol: str) -> Record | None:
        """Return the latest cached order-book snapshot for a symbol."""
        book = self._book_cache_by_name.get(symbol)
        return dict(book) if book is not None else None

    async def market_book_release(self, symbol: str) -> bool:
        """Official-style alias for removing a symbol from DOM subscriptions."""
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return False
        await self.unsubscribe_book([symbol_id])
        self._book_cache_by_name.pop(symbol, None)
        self._book_cache_by_id.pop(symbol_id, None)
        return True

    async def subscribe_symbols_batch(self, names: list[str]) -> list[int]:
        """Subscribe to multiple symbols' tick feeds by name in one call.

        Unlike subscribe_symbols(), this method silently skips unknown symbols
        instead of raising ValueError. Returns list of successfully subscribed
        symbol IDs.
        """
        ids = []
        for name in names:
            info = self._symbols.get(name)
            if info is not None:
                ids.append(info.symbol_id)
        if ids:
            await self.subscribe_ticks(ids)
        return ids

    def tick_history_stats(self) -> Record:
        """Return tick history memory usage stats."""
        return {
            "symbols_tracked": len(self._tick_history_by_name),
            "total_ticks": sum(len(d) for d in self._tick_history_by_name.values()),
            "limit_per_symbol": self._tick_history_limit,
        }

    async def get_rates(self, symbol: str, period_minutes: int, from_ts: int, to_ts: int) -> RecordList:
        """Get kline/rate bars.

        Args:
            symbol: Symbol name, e.g. "EURUSD"
            period_minutes: Bar period in minutes (1, 5, 15, 30, 60, 240, 1440, 10080, 43200)
            from_ts: Start time as UNIX seconds
            to_ts: End time as UNIX seconds

        Returns:
            List of bar dicts with keys: time, open, high, low, close, tick_volume, spread, real_volume
        """
        mapped = PERIOD_MAP.get(period_minutes, period_minutes)
        payload = SeriesCodec.serialize(
            [
                (PROP_FIXED_STRING, symbol[:32], 64),
                (PROP_U16, mapped),
                (PROP_I32, from_ts),
                (PROP_I32, to_ts),
            ]
        )
        result = await self.transport.send_command(CMD_GET_RATES, payload)
        return _parse_rate_bars(result.body)

    async def get_rates_raw(self, symbol: str, period_minutes: int, from_ts: int, to_ts: int) -> bytes:
        """Get raw rate bytes (for debugging)."""
        mapped = PERIOD_MAP.get(period_minutes, period_minutes)
        payload = SeriesCodec.serialize(
            [
                (PROP_FIXED_STRING, symbol[:32], 64),
                (PROP_U16, mapped),
                (PROP_I32, from_ts),
                (PROP_I32, to_ts),
            ]
        )
        result = await self.transport.send_command(CMD_GET_RATES, payload)
        return result.body

    async def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: int | float | datetime,
        date_to: int | float | datetime,
    ) -> RecordList:
        """Official-style range wrapper for cmd=11 bars."""
        period_minutes = _normalize_timeframe_minutes(timeframe)
        return await self.get_rates(
            symbol,
            period_minutes,
            _coerce_timestamp(date_from),
            _coerce_timestamp(date_to),
        )

    async def copy_rates_from(
        self,
        symbol: str,
        timeframe: int,
        date_from: int | float | datetime,
        count: int,
    ) -> RecordList:
        """Best-effort official-style wrapper returning bars up to date_from."""
        if count <= 0:
            return []
        period_minutes = _normalize_timeframe_minutes(timeframe)
        end_ts = _coerce_timestamp(date_from)
        lookback = _history_lookback_seconds(period_minutes, count)
        bars = await self.get_rates(symbol, period_minutes, max(0, end_ts - lookback), end_ts)
        return bars[-count:]

    async def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> RecordList:
        """Best-effort current-bar-relative wrapper built on cmd=11 history."""
        if start_pos < 0:
            raise ValidationError(f"start_pos must be >= 0, got {start_pos}")
        if count <= 0:
            return []
        period_minutes = _normalize_timeframe_minutes(timeframe)
        end_ts = int(datetime.now().timestamp())
        lookback = _history_lookback_seconds(period_minutes, start_pos + count)
        bars = await self.get_rates(symbol, period_minutes, max(0, end_ts - lookback), end_ts)
        if not bars:
            return []
        end_index = max(0, len(bars) - start_pos)
        start_index = max(0, end_index - count)
        return bars[start_index:end_index]

    async def copy_ticks_from(
        self,
        symbol: str,
        date_from: int | float | datetime,
        count: int,
        flags: int = COPY_TICKS_ALL,
    ) -> RecordList:
        """Best-effort cached tick-history view built from cmd=8 pushes."""
        if count <= 0:
            return []
        history = await self._get_tick_history(symbol)
        from_ms = _coerce_timestamp_ms(date_from)
        result = [
            _to_copy_tick_record(tick)
            for tick in history
            if int(tick.get("tick_time_ms", 0) or 0) >= from_ms and _tick_matches_copy_flags(tick, flags)
        ]
        return result[:count]

    async def copy_ticks_range(
        self,
        symbol: str,
        date_from: int | float | datetime,
        date_to: int | float | datetime,
        flags: int = COPY_TICKS_ALL,
    ) -> RecordList:
        """Best-effort cached tick-range view built from cmd=8 pushes."""
        history = await self._get_tick_history(symbol)
        from_ms = _coerce_timestamp_ms(date_from)
        to_ms = _coerce_timestamp_ms_end(date_to)
        if to_ms < from_ms:
            return []
        return [
            _to_copy_tick_record(tick)
            for tick in history
            if from_ms <= int(tick.get("tick_time_ms", 0) or 0) <= to_ms and _tick_matches_copy_flags(tick, flags)
        ]

    async def _get_tick_history(self, symbol: str) -> list[Record]:
        history = self._tick_history_by_name.get(symbol)
        if history is not None:
            return list(history)
        symbol_id = await self._resolve_symbol_id(symbol)
        if symbol_id is None:
            return []
        history = self._tick_history_by_id.get(symbol_id)
        if history is not None:
            self._tick_history_by_name[symbol] = history
            return list(history)
        return []

    # Currency conversion, profit, and margin methods are inherited
    # from _CurrencyMixin (see pymt5/_currency.py)
