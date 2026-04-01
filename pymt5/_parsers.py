"""Standalone parsing and helper functions for pymt5.

These module-level functions handle binary protocol parsing, timestamp
coercion, and validation logic used by the client mixins.
"""

from __future__ import annotations

import struct
from datetime import datetime
from fnmatch import fnmatchcase

from pymt5._logging import get_logger
from pymt5.constants import (
    COPY_TICKS_ALL,
    COPY_TICKS_INFO,
    COPY_TICKS_TRADE,
    PERIOD_MAP,
    TRADE_ACTION_PENDING,
)
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.schemas import (
    ACCOUNT_WEB_COMMISSION_FIELD_NAMES,
    ACCOUNT_WEB_COMMISSION_SCHEMA,
    ACCOUNT_WEB_COMMISSION_TIER_FIELD_NAMES,
    ACCOUNT_WEB_COMMISSION_TIER_SCHEMA,
    ACCOUNT_WEB_LEVERAGE_RULE_FIELD_NAMES,
    ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA,
    ACCOUNT_WEB_LEVERAGE_TIER_FIELD_NAMES,
    ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA,
    ACCOUNT_WEB_MAIN_FIELD_NAMES,
    ACCOUNT_WEB_MAIN_SCHEMA,
    ACCOUNT_WEB_TRADE_SETTINGS_FIELD_NAMES,
    ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA,
    BOOK_HEADER_FIELD_NAMES,
    BOOK_HEADER_SCHEMA,
    BOOK_LEVEL_FIELD_NAMES,
    BOOK_LEVEL_SCHEMA,
    RATE_BAR_FIELD_NAMES,
    RATE_BAR_FIELD_NAMES_EXT,
    RATE_BAR_SCHEMA,
    RATE_BAR_SCHEMA_EXT,
    TICK_FIELD_NAMES,
    TICK_SCHEMA,
)
from pymt5.types import (
    OPEN_ACCOUNT_RESPONSE_SCHEMA,
    VERIFICATION_STATUS_SCHEMA,
    OpenAccountResult,
    Record,
    RecordList,
    SymbolInfo,
    VerificationStatus,
)

logger = get_logger("pymt5.client")

# Import constants needed for specific functions
PERIOD_MINUTES_MAP = {code: minutes for minutes, code in PERIOD_MAP.items()}

BUY_ORDER_TYPES: frozenset[int] = frozenset({0, 2, 4, 6})  # ORDER_TYPE_BUY, BUY_LIMIT, BUY_STOP, BUY_STOP_LIMIT
SELL_ORDER_TYPES: frozenset[int] = frozenset({1, 3, 5, 7})  # ORDER_TYPE_SELL, SELL_LIMIT, SELL_STOP, SELL_STOP_LIMIT


def _order_side(action: int) -> bool | None:
    if action in BUY_ORDER_TYPES:
        return True
    if action in SELL_ORDER_TYPES:
        return False
    return None


def _currencies_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    aliases = ({"RUB", "RUR"},)
    return any(left in group and right in group for group in aliases)


def _coerce_timestamp(value: int | float | datetime) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(value)


def _coerce_optional_timestamp(value: int | float | datetime | None) -> int:
    if value is None:
        return 0
    return _coerce_timestamp(value)


def _coerce_timestamp_ms(value: int | float | datetime) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, float):
        return int(value * 1000)
    return int(value) * 1000


def _coerce_timestamp_ms_end(value: int | float | datetime) -> int:
    if isinstance(value, datetime):
        return _coerce_timestamp_ms(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value) * 1000 + 999
    if isinstance(value, float) and value.is_integer():
        return int(value) * 1000 + 999
    return _coerce_timestamp_ms(value)


def _normalize_timeframe_minutes(timeframe: int) -> int:
    if timeframe in PERIOD_MINUTES_MAP:
        return PERIOD_MINUTES_MAP[timeframe]
    return int(timeframe)


def _history_lookback_seconds(period_minutes: int, bars: int) -> int:
    return max(60, period_minutes * 60) * max(16, bars * 4 + 8)


def _matches_group_mask(symbol: str, group: str) -> bool:
    patterns = [item.strip() for item in group.split(",") if item.strip()]
    if not patterns:
        return True
    positive = [item for item in patterns if not item.startswith("!")]
    negative = [item[1:] for item in patterns if item.startswith("!")]
    included = any(fnmatchcase(symbol, pattern) for pattern in positive) if positive else True
    if not included:
        return False
    return not any(fnmatchcase(symbol, pattern) for pattern in negative)


def _parse_full_symbol_schedule(buffer: bytes) -> Record:
    sessions: Record = {"quote_sessions": [], "trade_sessions": []}
    if not buffer:
        return sessions
    offset = 0
    for key in ("quote_sessions", "trade_sessions"):
        for _ in range(7):
            day_sessions = []
            for _ in range(16):
                if offset + 4 > len(buffer):
                    break
                day_sessions.append(struct.unpack_from("<HH", buffer, offset))
                offset += 4
            sessions[key].append(day_sessions)
    return sessions


def _parse_full_symbol_subscription(buffer: bytes) -> Record:
    if not buffer or len(buffer) < 8:
        return {"delay": 0, "status": 0, "level": 0, "reserved": 0}
    delay, status, level, reserved = struct.unpack_from("<IBBH", buffer, 0)
    return {
        "delay": int(delay),
        "status": int(status),
        "level": int(level),
        "reserved": int(reserved),
    }


def _normalize_full_symbol_record(record: Record) -> Record:
    info = dict(record)
    trade_raw = info.get("trade", b"")
    if isinstance(trade_raw, (bytes, bytearray, memoryview)):
        trade_buffer = bytes(trade_raw)
        record_size = get_series_size(ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA)
        if len(trade_buffer) >= record_size:
            info["trade"] = _parse_account_trade_settings_record(trade_buffer, 0)
        else:
            info["trade"] = {}
    else:
        info["trade"] = dict(trade_raw or {})
    schedule_raw = info.get("schedule", b"")
    if isinstance(schedule_raw, (bytes, bytearray, memoryview)):
        info["schedule"] = _parse_full_symbol_schedule(bytes(schedule_raw))
    subscription_raw = info.get("subscription", b"")
    if isinstance(subscription_raw, (bytes, bytearray, memoryview)):
        info["subscription"] = _parse_full_symbol_subscription(bytes(subscription_raw))
    trade = info.get("trade", {})
    if isinstance(trade, dict):
        info["symbol_path"] = trade.get("symbol_path", info.get("symbol_path", ""))
        info["trade_mode"] = trade.get("trade_mode", info.get("trade_mode", 0))
        info["trade_stops_level"] = trade.get("trade_stops_level", info.get("trade_stops_level", 0))
        info["trade_freeze_level"] = trade.get("trade_freeze_level", info.get("trade_freeze_level", 0))
        info["volume_min"] = trade.get("volume_min", info.get("volume_min", 0.0))
        info["volume_max"] = trade.get("volume_max", info.get("volume_max", 0.0))
        info["volume_step"] = trade.get("volume_step", info.get("volume_step", 0.0))
        info["volume_limit"] = trade.get("volume_limit", info.get("volume_limit", 0.0))
        info["margin_initial"] = trade.get("margin_initial", info.get("margin_initial", 0.0))
        info["margin_maintenance"] = trade.get("margin_maintenance", info.get("margin_maintenance", 0.0))
        info["filling_mode"] = trade.get("trade_fill_flags", info.get("filling_mode", 0))
        info["expiration_mode"] = trade.get("trade_time_flags", info.get("expiration_mode", 0))
        info["order_mode"] = trade.get("trade_order_flags", info.get("order_mode", 0))
    info["trade_face_value"] = info.get("face_value", 0.0)
    info["trade_accrued_interest"] = info.get("accrued_interest", 0.0)
    return info


def _tick_matches_copy_flags(tick: Record, flags: int) -> bool:
    if flags == COPY_TICKS_ALL:
        return True
    has_quote = bool(float(tick.get("bid", 0.0) or 0.0) or float(tick.get("ask", 0.0) or 0.0))
    has_trade = bool(float(tick.get("last", 0.0) or 0.0) or int(tick.get("tick_volume", 0) or 0))
    if flags & COPY_TICKS_INFO and has_quote:
        return True
    return bool(flags & COPY_TICKS_TRADE and has_trade)


def _to_copy_tick_record(tick: Record) -> Record:
    return {
        "time": int(tick.get("tick_time", 0) or 0),
        "time_msc": int(tick.get("tick_time_ms", 0) or 0),
        "bid": float(tick.get("bid", 0.0) or 0.0),
        "ask": float(tick.get("ask", 0.0) or 0.0),
        "last": float(tick.get("last", 0.0) or 0.0),
        "volume": int(tick.get("tick_volume", 0) or 0),
        "volume_real": float(tick.get("tick_volume", 0) or 0),
        "flags": int(tick.get("flags", 0) or 0),
        "symbol": tick.get("symbol", ""),
    }


def _validate_requested_volume(symbol_info: Record, volume: float) -> str | None:
    volume_min = float(symbol_info.get("volume_min", 0.0) or 0.0)
    volume_max = float(symbol_info.get("volume_max", 0.0) or 0.0)
    volume_step = float(symbol_info.get("volume_step", 0.0) or 0.0)
    if volume_min > 0.0 and volume < volume_min:
        return f"volume {volume} is below symbol minimum {volume_min}"
    if volume_max > 0.0 and volume > volume_max:
        return f"volume {volume} is above symbol maximum {volume_max}"
    if volume_step > 0.0 and volume_min >= 0.0:
        steps = (volume - volume_min) / volume_step if volume >= volume_min else volume / volume_step
        if abs(steps - round(steps)) > 1e-9:
            return f"volume {volume} does not align with step {volume_step}"
    return None


def _validate_requested_stops(symbol_info: Record, request: Record, reference_price: float) -> str | None:
    point = float(symbol_info.get("point", 0.0) or 0.0)
    stops_level = float(symbol_info.get("trade_stops_level", 0.0) or 0.0)
    if point <= 0.0 or stops_level <= 0.0:
        return None
    min_distance = point * stops_level
    for label in ("sl", "tp"):
        value = float(request.get(label, 0.0) or 0.0)
        if value > 0.0 and abs(reference_price - value) < min_distance:
            return f"{label} is closer than symbol stop level"
    if int(request.get("action", 0) or 0) == TRADE_ACTION_PENDING:
        trigger = float(request.get("price_trigger", 0.0) or 0.0)
        if trigger > 0.0 and abs(reference_price - trigger) < min_distance:
            return "pending trigger is closer than symbol stop level"
    return None


def _normalize_tick_price_value(value: float | int | None, digits: int, symbol: str = "") -> float:
    price: float = float(value or 0.0)
    if digits <= 0 or price == 0.0:
        return price
    scale: float = float(10**digits)
    normalized_symbol = str(symbol or "").upper()
    base_symbol = normalized_symbol[:6]
    is_forex_like_pair = len(base_symbol) == 6 and base_symbol.isalpha()

    # Determine the magnitude threshold that separates "already normalized"
    # prices from "scaled integer" values sent by the protocol.
    threshold = scale
    if digits >= 4 and is_forex_like_pair and "JPY" not in base_symbol:
        threshold = scale / 10.0

    # For forex-like pairs with digits >= 3, use pure magnitude-based detection.
    # Proper forex prices are always far below the threshold (e.g. EURUSD ~1.1
    # vs threshold 10000; USDJPY ~150 vs threshold 1000), so this safely
    # handles non-integer scaled values like half-pips (87154.5 → 0.871545).
    if is_forex_like_pair and digits >= 3:
        if abs(price) >= threshold:
            return price / scale
        return price

    # For non-forex instruments (stocks, indices, commodities with low digits),
    # use the conservative integer-proximity check to avoid misidentifying
    # legitimate large prices (e.g. XAUUSD at 2000 with digits=2).
    rounded = round(price)
    if abs(price - rounded) > 1e-9:
        return price
    if abs(rounded) < threshold:
        return price
    return float(rounded) / scale


def _parse_tick_batch(
    body: bytes | None,
    symbols_by_id: dict[int, SymbolInfo],
    tick_cache_by_id: dict[int, Record] | None = None,
) -> RecordList:
    """Parse a batch of ticks from a CMD_TICK_PUSH body.

    The MT5 protocol sends **partial tick updates**: the ``fields`` bitmask
    indicates which price fields actually changed in this tick.  Unchanged
    fields are transmitted as ``0.0``.  When *tick_cache_by_id* is provided,
    zero-valued bid/ask/last fields are filled from the most recent cached
    tick for the same symbol, so callers always see complete prices.
    """
    if not body:
        return []
    tick_size = get_series_size(TICK_SCHEMA)
    count = len(body) // tick_size
    ticks: RecordList = []
    for idx in range(count):
        vals = SeriesCodec.parse_at(body, TICK_SCHEMA, idx * tick_size)
        tick = dict(zip(TICK_FIELD_NAMES, vals))
        tick["tick_time_ms"] = tick["tick_time"] * 1000 + tick["time_ms_delta"]
        sym_info = symbols_by_id.get(int(tick["symbol_id"]))
        if sym_info:
            tick["symbol"] = sym_info.name
            digits = int(getattr(sym_info, "digits", 0) or 0)
            symbol_name = str(getattr(sym_info, "name", "") or "")
            tick["bid"] = _normalize_tick_price_value(tick.get("bid"), digits, symbol_name)
            tick["ask"] = _normalize_tick_price_value(tick.get("ask"), digits, symbol_name)
            tick["last"] = _normalize_tick_price_value(tick.get("last"), digits, symbol_name)

        # Merge with cached tick: carry forward non-zero prices for fields
        # that the server sent as 0.0 (meaning "unchanged").
        if tick_cache_by_id is not None:
            symbol_id = int(tick["symbol_id"])
            cached = tick_cache_by_id.get(symbol_id)
            if cached:
                for key in ("bid", "ask", "last"):
                    if float(tick.get(key, 0.0) or 0.0) == 0.0:
                        prev = float(cached.get(key, 0.0) or 0.0)
                        if prev != 0.0:
                            tick[key] = prev

        ticks.append(tick)
    return ticks


def _parse_book_entries(body: bytes | None, symbols_by_id: dict[int, SymbolInfo]) -> RecordList:
    if not body or len(body) < 4:
        return []
    header_size = get_series_size(BOOK_HEADER_SCHEMA)
    level_size = get_series_size(BOOK_LEVEL_SCHEMA)
    count = struct.unpack_from("<I", body, 0)[0]
    entries: RecordList = []
    offset = 4
    for _ in range(count):
        if offset + header_size > len(body):
            break
        header_vals = SeriesCodec.parse_at(body, BOOK_HEADER_SCHEMA, offset)
        header = dict(zip(BOOK_HEADER_FIELD_NAMES, header_vals))
        offset += header_size
        total_levels = int(header["bid_count"]) + int(header["ask_count"])
        levels: RecordList = []
        for _ in range(total_levels):
            if offset + level_size > len(body):
                break
            level_vals = SeriesCodec.parse_at(body, BOOK_LEVEL_SCHEMA, offset)
            levels.append(dict(zip(BOOK_LEVEL_FIELD_NAMES, level_vals)))
            offset += level_size
        entry: Record = {
            "symbol_id": header["symbol_id"],
            "bids": levels[: int(header["bid_count"])],
            "asks": levels[int(header["bid_count"]) :],
        }
        sym_info = symbols_by_id.get(int(header["symbol_id"]))
        if sym_info:
            entry["symbol"] = sym_info.name
        entries.append(entry)
    return entries


def _parse_f64_array(buffer: bytes, expected_count: int) -> list[float]:
    if not buffer:
        return []
    count = min(expected_count, len(buffer) // 8)
    if count <= 0:
        return []
    return list(struct.unpack(f"<{count}d", buffer[: count * 8]))


def _parse_account_trade_settings(body: bytes, offset: int) -> tuple[RecordList, int]:
    items: RecordList = []
    record_size = get_series_size(ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA)
    if len(body) < offset + 4:
        return items, offset
    count = struct.unpack_from("<I", body, offset)[0]
    if count <= 0:
        return items, offset
    offset += 4
    for _ in range(count):
        if offset + record_size > len(body):
            break
        items.append(_parse_account_trade_settings_record(body, offset))
        offset += record_size
    return items, offset


def _parse_account_trade_settings_record(body: bytes, offset: int) -> Record:
    values = SeriesCodec.parse_at(body, ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA, offset)
    item = dict(zip(ACCOUNT_WEB_TRADE_SETTINGS_FIELD_NAMES, values))
    item["margin_rates_initial"] = _parse_f64_array(item["margin_rates_initial"], 8)
    item["margin_rates_maintenance"] = _parse_f64_array(item["margin_rates_maintenance"], 8)
    item["swap_rates"] = _parse_f64_array(item["swap_rates"], 7)
    return item


def _parse_account_leverage_rules(body: bytes, offset: int) -> tuple[RecordList, int]:
    rules: RecordList = []
    rule_size = get_series_size(ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA)
    tier_size = get_series_size(ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA)
    if len(body) < offset + 4:
        return rules, offset
    count = struct.unpack_from("<i", body, offset)[0]
    offset += 4
    for _ in range(max(count, 0)):
        if offset + rule_size > len(body):
            break
        values = SeriesCodec.parse_at(body, ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA, offset)
        rule = dict(zip(ACCOUNT_WEB_LEVERAGE_RULE_FIELD_NAMES, values))
        offset += rule_size
        tiers: RecordList = []
        for _ in range(max(int(rule.get("tier_count", 0) or 0), 0)):
            if offset + tier_size > len(body):
                break
            tier_values = SeriesCodec.parse_at(body, ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA, offset)
            tiers.append(dict(zip(ACCOUNT_WEB_LEVERAGE_TIER_FIELD_NAMES, tier_values)))
            offset += tier_size
        rule["tiers"] = tiers
        rules.append(rule)
    return rules, offset


def _parse_account_commissions(body: bytes, offset: int) -> tuple[RecordList, int]:
    commissions: RecordList = []
    commission_size = get_series_size(ACCOUNT_WEB_COMMISSION_SCHEMA)
    tier_size = get_series_size(ACCOUNT_WEB_COMMISSION_TIER_SCHEMA)
    if len(body) < offset + 4:
        return commissions, offset
    count = struct.unpack_from("<I", body, offset)[0]
    offset += 4
    for _ in range(count):
        if offset + commission_size > len(body):
            break
        values = SeriesCodec.parse_at(body, ACCOUNT_WEB_COMMISSION_SCHEMA, offset)
        commission = dict(zip(ACCOUNT_WEB_COMMISSION_FIELD_NAMES, values))
        offset += commission_size
        tiers: RecordList = []
        if offset + 4 > len(body):
            commission["tiers"] = tiers
            commissions.append(commission)
            break
        tier_count = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        for _ in range(tier_count):
            if offset + tier_size > len(body):
                break
            tier_values = SeriesCodec.parse_at(body, ACCOUNT_WEB_COMMISSION_TIER_SCHEMA, offset)
            tiers.append(dict(zip(ACCOUNT_WEB_COMMISSION_TIER_FIELD_NAMES, tier_values)))
            offset += tier_size
        commission["tiers"] = tiers
        commissions.append(commission)
    return commissions, offset


def _parse_account_response(body: bytes) -> Record:
    """Parse the cmd=3 / cmd=14 account response."""
    min_size = get_series_size(ACCOUNT_WEB_MAIN_SCHEMA)
    if not body or len(body) < min_size:
        return {}
    try:
        values = SeriesCodec.parse(body, ACCOUNT_WEB_MAIN_SCHEMA)
        result = dict(zip(ACCOUNT_WEB_MAIN_FIELD_NAMES, values))
        result["auth_password_min"] = int(result.get("auth_password_min", 0) or 0) or 8
        offset = min_size
        trade_settings, offset = _parse_account_trade_settings(body, offset)
        result["trade_settings"] = trade_settings
        result["trade_flags"] = 0
        if len(body) >= offset + 4:
            result["trade_flags"] = struct.unpack_from("<i", body, offset)[0]
            offset += 4
        result["symbols_count"] = 0
        if len(body) >= offset + 4:
            result["symbols_count"] = struct.unpack_from("<i", body, offset)[0]
            offset += 4
        result["leverage_flags"] = 0
        if len(body) >= offset + 8:
            result["leverage_flags"] = struct.unpack_from("<Q", body, offset)[0]
            offset += 8
        leverage_rules, offset = _parse_account_leverage_rules(body, offset)
        commissions, _ = _parse_account_commissions(body, offset)
        result["leverage_rules"] = leverage_rules
        result["rules"] = leverage_rules
        result["commissions"] = commissions
        result["currency"] = result.get("account_currency", "")
        result["leverage"] = int(result.get("margin_leverage", 0) or 0)
        result["name"] = result.get("account_name", "")
        result["server"] = result.get("server_name", "")
        result["daylight_mode"] = int(result.get("daylightmode", 0) or 0)
        result["server_offset_time"] = int(result.get("timezone_shift", 0) or 0) + (
            int(result.get("daylight_mode", 0) or 0) * 3600
        )
        account_type_value = result.get("account_type")
        account_type = -1 if account_type_value is None else int(account_type_value)
        result["is_real"] = account_type == 0
        result["is_demo"] = account_type == 1
        result["is_contest"] = account_type == 2
        result["is_preliminary"] = account_type == 3
        result["trade_allowed"] = not bool(int(result.get("rights", 0) or 0) & 4)
        result["is_investor"] = bool(int(result.get("rights", 0) or 0) & 8)
        result["is_read_only"] = bool(int(result.get("rights", 0) or 0) & 512)
        result["is_reset_pass"] = bool(int(result.get("rights", 0) or 0) & 1024)
        result["is_trade_disabled"] = not result["trade_allowed"]
        result["is_hedged_margin"] = int(result.get("margin_mode", 0) or 0) == 2
        result["risk_warning"] = bool(int(result.get("permissions_flags", 0) or 0) & 16)
        result["profit"] = float(result.get("acc_profit", 0.0) or 0.0)
        result["equity"] = (
            float(result.get("balance", 0.0) or 0.0)
            + float(result.get("credit", 0.0) or 0.0)
            + float(result.get("profit", 0.0) or 0.0)
        )
    except (struct.error, KeyError, ValueError, TypeError, IndexError) as exc:
        logger.debug("account response parse failed: %s", exc)
        return {}
    return result


def _parse_verification_status(body: bytes | None) -> VerificationStatus:
    if not body or len(body) < 2:
        return VerificationStatus()
    email, phone = SeriesCodec.parse(body, VERIFICATION_STATUS_SCHEMA)
    return VerificationStatus(bool(email), bool(phone))


def _parse_open_account_result(body: bytes | None) -> OpenAccountResult:
    if not body or len(body) < get_series_size(OPEN_ACCOUNT_RESPONSE_SCHEMA):
        return OpenAccountResult(code=-1, login=0, password="", investor_password="")
    code, login, password, investor_password = SeriesCodec.parse(body, OPEN_ACCOUNT_RESPONSE_SCHEMA)
    return OpenAccountResult(
        code=int(code),
        login=int(login),
        password=str(password),
        investor_password=str(investor_password),
    )


def _parse_rate_bars(body: bytes | None) -> RecordList:
    if not body:
        return []
    bar_size_std = get_series_size(RATE_BAR_SCHEMA)
    bar_size_ext = get_series_size(RATE_BAR_SCHEMA_EXT)
    if len(body) % bar_size_ext == 0 and bar_size_ext != bar_size_std:
        schema, names, bar_size = RATE_BAR_SCHEMA_EXT, RATE_BAR_FIELD_NAMES_EXT, bar_size_ext
    else:
        schema, names, bar_size = RATE_BAR_SCHEMA, RATE_BAR_FIELD_NAMES, bar_size_std
    count = len(body) // bar_size
    bars = []
    offset = 0
    for _ in range(count):
        if offset + bar_size > len(body):
            break
        vals = SeriesCodec.parse_at(body, schema, offset)
        bars.append(dict(zip(names, vals)))
        offset += bar_size
    return bars


def _parse_counted_records(
    body: bytes,
    schema: list[dict[str, int]],
    field_names: list[str],
) -> RecordList:
    if not body or len(body) < 4:
        return []
    count = struct.unpack_from("<I", body, 0)[0]
    record_size = get_series_size(schema)
    offset = 4
    records = []
    for _ in range(count):
        if offset + record_size > len(body):
            break
        vals = SeriesCodec.parse_at(body, schema, offset)
        records.append(dict(zip(field_names, vals)))
        offset += record_size
    return records
