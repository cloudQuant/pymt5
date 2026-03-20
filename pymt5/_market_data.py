"""Market data mixin for MT5WebClient.

Provides symbol management, tick/bar data retrieval, order book
subscriptions, and currency conversion rate resolution.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import zlib
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from pymt5._parsers import (
    _coerce_timestamp,
    _coerce_timestamp_ms,
    _coerce_timestamp_ms_end,
    _currencies_equal,
    _history_lookback_seconds,
    _matches_group_mask,
    _normalize_full_symbol_record,
    _normalize_timeframe_minutes,
    _parse_counted_records,
    _parse_rate_bars,
    _tick_matches_copy_flags,
    _to_copy_tick_record,
)
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

logger = logging.getLogger("pymt5.client")

FOREX_CALC_MODES = frozenset({0, 5})
FUTURES_CALC_MODES = frozenset({1, 33, 34})
CFD_CALC_MODES = frozenset({2, 3, 4, 32, 38})
OPTION_CALC_MODES = frozenset({35, 36})
BOND_CALC_MODES = frozenset({37, 39})
COLLATERAL_CALC_MODE = 64


class _MarketDataMixin:
    """Mixin providing market data methods for MT5WebClient."""

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

    if TYPE_CHECKING:

        def _fail_last_error(self, code: int, message: str) -> _T | None: ...
        def _clear_last_error(self) -> None: ...

    async def load_symbols(self, use_gzip: bool = True) -> dict[str, SymbolInfo]:
        """Load symbols and build internal cache for name->id lookup."""
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
            raise ValueError(f"symbols not found in cache (call load_symbols first): {missing}")
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
            raise ValueError(f"symbols not found in cache (call load_symbols first): {missing}")
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
            raise ValueError(f"start_pos must be >= 0, got {start_pos}")
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

    async def _resolve_conversion_rates(
        self,
        *,
        source: str,
        target: str,
        current_symbol: str,
        fallback_rate: float,
    ) -> tuple[float, float] | None:
        if not source or not target:
            return None
        if _currencies_equal(source, target):
            return 1.0, 1.0
        buy = await self._resolve_side_rate(
            source,
            target,
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell = await self._resolve_side_rate(
            source,
            target,
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        if buy > 0.0 and sell > 0.0:
            return buy, sell
        buy_usd = await self._resolve_side_rate(
            source,
            "USD",
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell_usd = await self._resolve_side_rate(
            source,
            "USD",
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        buy_target = await self._resolve_side_rate(
            "USD",
            target,
            prefer_ask_when_direct=True,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        sell_target = await self._resolve_side_rate(
            "USD",
            target,
            prefer_ask_when_direct=False,
            current_symbol=current_symbol,
            fallback_rate=fallback_rate,
        )
        if buy_usd > 0.0 and sell_usd > 0.0 and buy_target > 0.0 and sell_target > 0.0:
            return buy_usd * buy_target, sell_usd * sell_target
        return None

    async def _resolve_side_rate(
        self,
        source: str,
        target: str,
        *,
        prefer_ask_when_direct: bool,
        current_symbol: str,
        fallback_rate: float,
    ) -> float:
        direct = await self._find_conversion_symbol_name(source, target)
        if direct is not None:
            prices = self._get_conversion_prices(
                direct,
                current_symbol=current_symbol,
                fallback_rate=fallback_rate,
            )
            if prices is not None:
                bid, ask = prices
                return ask if prefer_ask_when_direct else bid
        inverse = await self._find_conversion_symbol_name(target, source)
        if inverse is not None:
            prices = self._get_conversion_prices(
                inverse,
                current_symbol=current_symbol,
                fallback_rate=fallback_rate,
            )
            if prices is not None:
                bid, ask = prices
                denominator = bid if prefer_ask_when_direct else ask
                if denominator > 0.0:
                    return 1.0 / denominator
        return 0.0

    async def _find_conversion_symbol_name(self, base: str, quote: str) -> str | None:
        if not base or not quote:
            return None
        if not self._symbols:
            try:
                await self.load_symbols()
            except (RuntimeError, struct.error, KeyError, ValueError) as exc:
                logger.debug("load_symbols() failed in _find_conversion_symbol_name: %s", exc)
                return None
        direct = f"{base}{quote}"
        if direct in self._symbols:
            return direct
        matches = sorted(name for name in self._symbols if name.startswith(base) and name.endswith(quote))
        if matches:
            return matches[0]
        return None

    def _get_conversion_prices(
        self,
        symbol_name: str,
        *,
        current_symbol: str,
        fallback_rate: float,
    ) -> tuple[float, float] | None:
        tick = self._tick_cache_by_name.get(symbol_name)
        bid = 0.0
        ask = 0.0
        if tick is not None:
            bid = float(tick.get("bid", 0.0) or 0.0)
            ask = float(tick.get("ask", 0.0) or 0.0)
        if symbol_name == current_symbol and fallback_rate > 0.0:
            if bid <= 0.0:
                bid = float(fallback_rate)
            if ask <= 0.0:
                ask = float(fallback_rate)
        if bid <= 0.0:
            bid = ask
        if ask <= 0.0:
            ask = bid
        if bid > 0.0 and ask > 0.0:
            return bid, ask
        return None

    def _calc_profit_raw(
        self,
        info: Record,
        is_buy: bool,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        mode = int(info.get("trade_calc_mode", 0) or 0)
        if mode in BOND_CALC_MODES:
            contract_size = float(info.get("contract_size", 0.0) or 0.0)
            face_value = float(info.get("face_value", 0.0) or 0.0)
            accrued_interest = float(info.get("accrued_interest", 0.0) or 0.0)
            if contract_size <= 0.0 or face_value <= 0.0:
                return self._fail_last_error(
                    -10,
                    f"trade_calc_mode={mode} requires contract_size and face_value for bond profit calculation",
                )
            direction = 1.0 if is_buy else -1.0
            open_value = (price_open / 100.0) * face_value
            close_value = (price_close / 100.0) * face_value + accrued_interest
            return direction * (close_value - open_value) * contract_size * float(volume)
        if mode == COLLATERAL_CALC_MODE:
            return 0.0
        direction = 1.0 if is_buy else -1.0
        contract_size = float(info.get("contract_size", 0.0) or 0.0)
        tick_size = float(info.get("tick_size", 0.0) or 0.0)
        tick_value = float(info.get("tick_value", 0.0) or 0.0)
        if mode in FOREX_CALC_MODES or mode in CFD_CALC_MODES:
            if contract_size <= 0.0:
                return self._fail_last_error(-11, "contract_size must be > 0 for profit calculation")
            return direction * (price_close - price_open) * contract_size * float(volume)
        if mode in {1, 33, 35, 36}:
            if tick_value <= 0.0:
                return self._fail_last_error(-12, "tick_value must be > 0 for futures/options profit calculation")
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return direction * (price_close - price_open) * float(volume) * point_value
        if mode == 34:
            if tick_value <= 0.0:
                return self._fail_last_error(-12, "tick_value must be > 0 for FORTS profit calculation")
            open_value = price_open * tick_value / tick_size if tick_size > 0.0 else price_open * tick_value
            close_value = price_close * tick_value / tick_size if tick_size > 0.0 else price_close * tick_value
            return direction * (close_value - open_value) * float(volume)
        if contract_size <= 0.0:
            return self._fail_last_error(-11, "contract_size must be > 0 for profit calculation")
        return direction * (price_close - price_open) * contract_size * float(volume)

    def _calc_margin_raw(
        self,
        info: Record,
        is_buy: bool,
        volume: float,
        price: float,
        leverage: int,
    ) -> float | None:
        del is_buy
        mode = int(info.get("trade_calc_mode", 0) or 0)
        contract_size = float(info.get("contract_size", 0.0) or 0.0)
        tick_size = float(info.get("tick_size", 0.0) or 0.0)
        tick_value = float(info.get("tick_value", 0.0) or 0.0)
        margin_initial = float(info.get("margin_initial", 0.0) or 0.0)
        lots = float(volume)
        units = lots * contract_size
        if mode in BOND_CALC_MODES:
            face_value = float(info.get("face_value", 0.0) or 0.0)
            if contract_size <= 0.0 or face_value <= 0.0:
                return self._fail_last_error(
                    -13,
                    f"trade_calc_mode={mode} requires contract_size and face_value for bond margin calculation",
                )
            return lots * contract_size * face_value * float(price) / 100.0
        if mode == COLLATERAL_CALC_MODE:
            return 0.0
        if mode == 5:
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            return units
        if mode == 0 or mode == 4:
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            if leverage <= 0:
                return self._fail_last_error(-15, "account leverage must be > 0 for leveraged margin calculation")
            base_margin = units / float(leverage)
            if mode == 4:
                base_margin *= float(price)
            return base_margin
        if mode in FUTURES_CALC_MODES:
            if margin_initial > 0.0:
                return lots * margin_initial
            if tick_value <= 0.0:
                return self._fail_last_error(
                    -16,
                    "margin_initial or tick_value is required for futures margin calculation",
                )
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return lots * float(price) * point_value
        if mode == 3:
            if contract_size <= 0.0 or tick_value <= 0.0:
                return self._fail_last_error(
                    -17,
                    "contract_size and tick_value are required for CFD index margin calculation",
                )
            point_value = tick_value / tick_size if tick_size > 0.0 else tick_value
            return units * float(price) * point_value
        if mode in {2, 32, 35, 36, 38}:
            if margin_initial > 0.0 and mode in {32, 35, 36, 38}:
                return lots * margin_initial
            if contract_size <= 0.0:
                return self._fail_last_error(-14, "contract_size must be > 0 for margin calculation")
            return units * float(price)
        return self._fail_last_error(-18, f"trade_calc_mode={mode} is not supported yet")
