"""
Protocol schemas extracted from the MT5 Web Terminal JavaScript source.

Each schema is a list of dicts with keys:
  - propType: int  (matches PROP_* constants)
  - propLength: int (optional, for fixed-size fields)
"""

from pymt5.constants import (
    PROP_BYTES,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I32,
    PROP_I64,
    PROP_U16,
    PROP_U32,
    PROP_U64,
)

# ---------------------------------------------------------------------------
# Tick (cmd 8 push / cmd 7 subscribe response)
# Parsed by Gh() in JS:
#   symbol_id, tick_time, fields, bid, ask, last, tick_volume, time_ms_delta, flags
# ---------------------------------------------------------------------------
TICK_SCHEMA = [
    {"propType": PROP_U32},      # 0: symbol_id
    {"propType": PROP_I32},      # 1: tick_time (seconds)
    {"propType": PROP_U32},      # 2: fields bitmask
    {"propType": PROP_F64},      # 3: bid
    {"propType": PROP_F64},      # 4: ask
    {"propType": PROP_F64},      # 5: last
    {"propType": PROP_I64},      # 6: tick_volume
    {"propType": PROP_U32},      # 7: time_ms_delta
    {"propType": PROP_U16},      # 8: flags
]

TICK_FIELD_NAMES = [
    "symbol_id", "tick_time", "fields", "bid", "ask",
    "last", "tick_volume", "time_ms_delta", "flags",
]

# ---------------------------------------------------------------------------
# Symbol (basic, from cmd 6 / cmd 34)
# Schema Bh in JS
# ---------------------------------------------------------------------------
SYMBOL_BASIC_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 0: trade_symbol
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 1: symbol_description
    {"propType": PROP_U32},                               # 2: digits
    {"propType": PROP_U32},                               # 3: symbol_id
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 4: symbol_path
    {"propType": PROP_U32},                               # 5: trade_calc_mode
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 6: basis
    {"propType": PROP_U16},                               # 7: sector
]

SYMBOL_BASIC_FIELD_NAMES = [
    "trade_symbol", "symbol_description", "digits", "symbol_id",
    "symbol_path", "trade_calc_mode", "basis", "sector",
]

# ---------------------------------------------------------------------------
# Position (from cmd 4, schema mu in JS)
# ---------------------------------------------------------------------------
POSITION_SCHEMA = [
    {"propType": PROP_I64},                               # 0: position_id
    {"propType": PROP_I64},                               # 1: trade_order
    {"propType": PROP_U32},                               # 2: time_create (sec)
    {"propType": PROP_U32},                               # 3: time_update (sec)
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 4: trade_symbol
    {"propType": PROP_U32},                               # 5: trade_action
    {"propType": PROP_F64},                               # 6: price_open
    {"propType": PROP_F64},                               # 7: price_close
    {"propType": PROP_F64},                               # 8: sl
    {"propType": PROP_F64},                               # 9: tp
    {"propType": PROP_U64},                               # 10: trade_volume
    {"propType": PROP_F64},                               # 11: profit
    {"propType": PROP_F64},                               # 12: rate_profit
    {"propType": PROP_F64},                               # 13: rate_margin
    {"propType": PROP_F64},                               # 14: commission
    {"propType": PROP_F64},                               # 15: storage (swap)
    {"propType": PROP_I64},                               # 16: expert
    {"propType": PROP_I64},                               # 17: expert_position_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 18: comment
    {"propType": PROP_F64},                               # 19: contract_size
    {"propType": PROP_U32},                               # 20: digits
    {"propType": PROP_U32},                               # 21: digits_currency
    {"propType": PROP_U32},                               # 22: trade_reason
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 23: external_id
    {"propType": PROP_I32},                               # 24: time_create_ms
    {"propType": PROP_I32},                               # 25: time_update_ms
]

POSITION_FIELD_NAMES = [
    "position_id", "trade_order", "time_create", "time_update",
    "trade_symbol", "trade_action", "price_open", "price_close",
    "sl", "tp", "trade_volume", "profit", "rate_profit", "rate_margin",
    "commission", "storage", "expert", "expert_position_id",
    "comment", "contract_size", "digits", "digits_currency",
    "trade_reason", "external_id", "time_create_ms", "time_update_ms",
]

# ---------------------------------------------------------------------------
# Order (from cmd 4 open-orders part, schema Ld in JS)
# ---------------------------------------------------------------------------
ORDER_SCHEMA = [
    {"propType": PROP_I64},                               # 0: trade_order
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 1: order_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 2: trade_symbol
    {"propType": PROP_U32},                               # 3: time_setup (sec)
    {"propType": PROP_U32},                               # 4: time_expiration (sec)
    {"propType": PROP_U32},                               # 5: time_done (sec)
    {"propType": PROP_U32},                               # 6: order_type
    {"propType": PROP_U32},                               # 7: type_filling
    {"propType": PROP_U32},                               # 8: type_time
    {"propType": PROP_U32},                               # 9: type_reason
    {"propType": PROP_F64},                               # 10: price_order
    {"propType": PROP_F64},                               # 11: price_trigger
    {"propType": PROP_F64},                               # 12: price_current
    {"propType": PROP_F64},                               # 13: price_sl
    {"propType": PROP_F64},                               # 14: price_tp
    {"propType": PROP_I64},                               # 15: volume_initial
    {"propType": PROP_I64},                               # 16: volume_current
    {"propType": PROP_U32},                               # 17: order_state
    {"propType": PROP_I64},                               # 18: expert
    {"propType": PROP_I64},                               # 19: position_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 20: comment
    {"propType": PROP_F64},                               # 21: contract_size
    {"propType": PROP_U32},                               # 22: digits
    {"propType": PROP_U32},                               # 23: digits_currency
    {"propType": PROP_F64},                               # 24: commission_daily
    {"propType": PROP_F64},                               # 25: commission_monthly
    {"propType": PROP_F64},                               # 26: margin_rate
    {"propType": PROP_U32},                               # 27: activation_mode
    {"propType": PROP_I32},                               # 28: time_setup_ms
    {"propType": PROP_I32},                               # 29: time_done_ms
]

ORDER_FIELD_NAMES = [
    "trade_order", "order_id", "trade_symbol", "time_setup",
    "time_expiration", "time_done", "order_type", "type_filling",
    "type_time", "type_reason", "price_order", "price_trigger",
    "price_current", "price_sl", "price_tp", "volume_initial",
    "volume_current", "order_state", "expert", "position_id",
    "comment", "contract_size", "digits", "digits_currency",
    "commission_daily", "commission_monthly", "margin_rate",
    "activation_mode", "time_setup_ms", "time_done_ms",
]

# ---------------------------------------------------------------------------
# Deal (from cmd 5 trade-history, schema Pd in JS)
# ---------------------------------------------------------------------------
DEAL_SCHEMA = [
    {"propType": PROP_I64},                               # 0: deal
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 1: deal_id
    {"propType": PROP_I64},                               # 2: trade_order
    {"propType": PROP_U32},                               # 3: time_create (sec)
    {"propType": PROP_U32},                               # 4: time_update (sec)
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 5: trade_symbol
    {"propType": PROP_U32},                               # 6: trade_action
    {"propType": PROP_U32},                               # 7: entry
    {"propType": PROP_F64},                               # 8: price_open
    {"propType": PROP_F64},                               # 9: price_close
    {"propType": PROP_F64},                               # 10: sl
    {"propType": PROP_F64},                               # 11: tp
    {"propType": PROP_U64},                               # 12: trade_volume
    {"propType": PROP_F64},                               # 13: profit
    {"propType": PROP_F64},                               # 14: rate_profit
    {"propType": PROP_F64},                               # 15: rate_margin
    {"propType": PROP_F64},                               # 16: commission
    {"propType": PROP_F64},                               # 17: storage (swap)
    {"propType": PROP_I64},                               # 18: expert
    {"propType": PROP_I64},                               # 19: position_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 20: comment
    {"propType": PROP_F64},                               # 21: contract_size
    {"propType": PROP_U32},                               # 22: digits
    {"propType": PROP_U32},                               # 23: digits_currency
    {"propType": PROP_U32},                               # 24: trade_reason
    {"propType": PROP_I32},                               # 25: time_create_ms
    {"propType": PROP_I32},                               # 26: time_update_ms
    {"propType": PROP_F64},                               # 27: commission_fee
]

DEAL_FIELD_NAMES = [
    "deal", "deal_id", "trade_order", "time_create", "time_update",
    "trade_symbol", "trade_action", "entry", "price_open", "price_close",
    "sl", "tp", "trade_volume", "profit", "rate_profit", "rate_margin",
    "commission", "storage", "expert", "position_id",
    "comment", "contract_size", "digits", "digits_currency",
    "trade_reason", "time_create_ms", "time_update_ms", "commission_fee",
]

# ---------------------------------------------------------------------------
# Full Symbol Info (from cmd 18, detailed contract specifications)
# These fields extend SYMBOL_BASIC_SCHEMA with trading parameters.
# ---------------------------------------------------------------------------
FULL_SYMBOL_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 0: trade_symbol
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 1: symbol_description
    {"propType": PROP_U32},                               # 2: digits
    {"propType": PROP_U32},                               # 3: symbol_id
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 4: symbol_path
    {"propType": PROP_U32},                               # 5: trade_calc_mode
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 6: basis
    {"propType": PROP_U16},                               # 7: sector
    {"propType": PROP_F64},                               # 8: contract_size
    {"propType": PROP_F64},                               # 9: tick_size
    {"propType": PROP_F64},                               # 10: tick_value
    {"propType": PROP_F64},                               # 11: point
    {"propType": PROP_F64},                               # 12: volume_min
    {"propType": PROP_F64},                               # 13: volume_max
    {"propType": PROP_F64},                               # 14: volume_step
    {"propType": PROP_U32},                               # 15: trade_mode
    {"propType": PROP_U32},                               # 16: trade_stops_level
    {"propType": PROP_U32},                               # 17: trade_freeze_level
    {"propType": PROP_U32},                               # 18: spread
    {"propType": PROP_U32},                               # 19: spread_float
    {"propType": PROP_F64},                               # 20: margin_initial
    {"propType": PROP_F64},                               # 21: margin_maintenance
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 22: currency_base
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 23: currency_profit
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # 24: currency_margin
    {"propType": PROP_U32},                               # 25: filling_mode
    {"propType": PROP_U32},                               # 26: expiration_mode
    {"propType": PROP_U32},                               # 27: order_mode
]

FULL_SYMBOL_FIELD_NAMES = [
    "trade_symbol", "symbol_description", "digits", "symbol_id",
    "symbol_path", "trade_calc_mode", "basis", "sector",
    "contract_size", "tick_size", "tick_value", "point",
    "volume_min", "volume_max", "volume_step",
    "trade_mode", "trade_stops_level", "trade_freeze_level",
    "spread", "spread_float",
    "margin_initial", "margin_maintenance",
    "currency_base", "currency_profit", "currency_margin",
    "filling_mode", "expiration_mode", "order_mode",
]

# ---------------------------------------------------------------------------
# Kline / Rates request (cmd 11)
# ---------------------------------------------------------------------------
RATES_REQUEST_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # symbol
    {"propType": PROP_U16},                               # period (mapped)
    {"propType": PROP_I32},                               # from (unix sec)
    {"propType": PROP_I32},                               # to (unix sec)
]

# ---------------------------------------------------------------------------
# Kline / Rate bar (response from cmd 11)
# Each bar is 48 bytes: I32 + F64*4 + I64 + I32
# Verified against live data: 2880 bytes / 48 = 60 M1 bars
# ---------------------------------------------------------------------------
RATE_BAR_SCHEMA = [
    {"propType": PROP_I32},      # 0: time (unix seconds)
    {"propType": PROP_F64},      # 1: open
    {"propType": PROP_F64},      # 2: high
    {"propType": PROP_F64},      # 3: low
    {"propType": PROP_F64},      # 4: close
    {"propType": PROP_I64},      # 5: tick_volume
    {"propType": PROP_I32},      # 6: spread
]

RATE_BAR_FIELD_NAMES = [
    "time", "open", "high", "low", "close", "tick_volume", "spread",
]

# ---------------------------------------------------------------------------
# Trade request (cmd 12)
# ---------------------------------------------------------------------------
TRADE_REQUEST_SCHEMA = [
    {"propType": PROP_U32},                               # action_id
    {"propType": PROP_U32},                               # trade_action
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # trade_symbol
    {"propType": PROP_U64},                               # trade_volume
    {"propType": PROP_U32},                               # digits
    {"propType": PROP_U64},                               # trade_order
    {"propType": PROP_U32},                               # trade_type
    {"propType": PROP_U32},                               # type_filling
    {"propType": PROP_U32},                               # type_time
    {"propType": PROP_U32},                               # type_flags
    {"propType": PROP_U32},                               # type_reason
    {"propType": PROP_F64},                               # price_order
    {"propType": PROP_F64},                               # price_trigger
    {"propType": PROP_F64},                               # price_sl
    {"propType": PROP_F64},                               # price_tp
    {"propType": PROP_U64},                               # deviation
    {"propType": PROP_FIXED_STRING, "propLength": 64},   # comment
    {"propType": PROP_U64},                               # position_id
    {"propType": PROP_U64},                               # position_by
    {"propType": PROP_U32},                               # time_expiration
]
