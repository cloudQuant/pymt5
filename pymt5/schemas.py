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
    PROP_I8,
    PROP_I32,
    PROP_I64,
    PROP_U8,
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
    {"propType": PROP_U32},  # 0: symbol_id
    {"propType": PROP_I32},  # 1: tick_time (seconds)
    {"propType": PROP_U32},  # 2: fields bitmask
    {"propType": PROP_F64},  # 3: bid
    {"propType": PROP_F64},  # 4: ask
    {"propType": PROP_F64},  # 5: last
    {"propType": PROP_I64},  # 6: tick_volume
    {"propType": PROP_U32},  # 7: time_ms_delta
    {"propType": PROP_U16},  # 8: flags
]

TICK_FIELD_NAMES = [
    "symbol_id",
    "tick_time",
    "fields",
    "bid",
    "ask",
    "last",
    "tick_volume",
    "time_ms_delta",
    "flags",
]

# ---------------------------------------------------------------------------
# Symbol (basic, from cmd 6 / cmd 34)
# Schema Bh in JS
# ---------------------------------------------------------------------------
SYMBOL_BASIC_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 0: trade_symbol
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 1: symbol_description
    {"propType": PROP_U32},  # 2: digits
    {"propType": PROP_U32},  # 3: symbol_id
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 4: symbol_path
    {"propType": PROP_U32},  # 5: trade_calc_mode
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 6: basis
    {"propType": PROP_U16},  # 7: sector
]

SYMBOL_BASIC_FIELD_NAMES = [
    "trade_symbol",
    "symbol_description",
    "digits",
    "symbol_id",
    "symbol_path",
    "trade_calc_mode",
    "basis",
    "sector",
]

# ---------------------------------------------------------------------------
# Position (from cmd 4, schema mu in JS)
# ---------------------------------------------------------------------------
POSITION_SCHEMA = [
    {"propType": PROP_I64},  # 0: position_id
    {"propType": PROP_I64},  # 1: trade_order
    {"propType": PROP_U32},  # 2: time_create (sec)
    {"propType": PROP_U32},  # 3: time_update (sec)
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 4: trade_symbol
    {"propType": PROP_U32},  # 5: trade_action
    {"propType": PROP_F64},  # 6: price_open
    {"propType": PROP_F64},  # 7: price_close
    {"propType": PROP_F64},  # 8: sl
    {"propType": PROP_F64},  # 9: tp
    {"propType": PROP_U64},  # 10: trade_volume
    {"propType": PROP_F64},  # 11: profit
    {"propType": PROP_F64},  # 12: rate_profit
    {"propType": PROP_F64},  # 13: rate_margin
    {"propType": PROP_F64},  # 14: commission
    {"propType": PROP_F64},  # 15: storage (swap)
    {"propType": PROP_I64},  # 16: expert
    {"propType": PROP_I64},  # 17: expert_position_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 18: comment
    {"propType": PROP_F64},  # 19: contract_size
    {"propType": PROP_U32},  # 20: digits
    {"propType": PROP_U32},  # 21: digits_currency
    {"propType": PROP_U32},  # 22: trade_reason
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 23: external_id
    {"propType": PROP_I32},  # 24: time_create_ms
    {"propType": PROP_I32},  # 25: time_update_ms
]

POSITION_FIELD_NAMES = [
    "position_id",
    "trade_order",
    "time_create",
    "time_update",
    "trade_symbol",
    "trade_action",
    "price_open",
    "price_close",
    "sl",
    "tp",
    "trade_volume",
    "profit",
    "rate_profit",
    "rate_margin",
    "commission",
    "storage",
    "expert",
    "expert_position_id",
    "comment",
    "contract_size",
    "digits",
    "digits_currency",
    "trade_reason",
    "external_id",
    "time_create_ms",
    "time_update_ms",
]

# ---------------------------------------------------------------------------
# Order (from cmd 4 open-orders part, schema Ld in JS)
# ---------------------------------------------------------------------------
ORDER_SCHEMA = [
    {"propType": PROP_I64},  # 0: trade_order
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 1: order_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 2: trade_symbol
    {"propType": PROP_U32},  # 3: time_setup (sec)
    {"propType": PROP_U32},  # 4: time_expiration (sec)
    {"propType": PROP_U32},  # 5: time_done (sec)
    {"propType": PROP_U32},  # 6: order_type
    {"propType": PROP_U32},  # 7: type_filling
    {"propType": PROP_U32},  # 8: type_time
    {"propType": PROP_U32},  # 9: type_reason
    {"propType": PROP_F64},  # 10: price_order
    {"propType": PROP_F64},  # 11: price_trigger
    {"propType": PROP_F64},  # 12: price_current
    {"propType": PROP_F64},  # 13: price_sl
    {"propType": PROP_F64},  # 14: price_tp
    {"propType": PROP_I64},  # 15: volume_initial
    {"propType": PROP_I64},  # 16: volume_current
    {"propType": PROP_U32},  # 17: order_state
    {"propType": PROP_I64},  # 18: expert
    {"propType": PROP_I64},  # 19: position_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 20: comment
    {"propType": PROP_F64},  # 21: contract_size
    {"propType": PROP_U32},  # 22: digits
    {"propType": PROP_U32},  # 23: digits_currency
    {"propType": PROP_F64},  # 24: commission_daily
    {"propType": PROP_F64},  # 25: commission_monthly
    {"propType": PROP_F64},  # 26: margin_rate
    {"propType": PROP_U32},  # 27: activation_mode
    {"propType": PROP_I32},  # 28: time_setup_ms
    {"propType": PROP_I32},  # 29: time_done_ms
]

ORDER_FIELD_NAMES = [
    "trade_order",
    "order_id",
    "trade_symbol",
    "time_setup",
    "time_expiration",
    "time_done",
    "order_type",
    "type_filling",
    "type_time",
    "type_reason",
    "price_order",
    "price_trigger",
    "price_current",
    "price_sl",
    "price_tp",
    "volume_initial",
    "volume_current",
    "order_state",
    "expert",
    "position_id",
    "comment",
    "contract_size",
    "digits",
    "digits_currency",
    "commission_daily",
    "commission_monthly",
    "margin_rate",
    "activation_mode",
    "time_setup_ms",
    "time_done_ms",
]

# ---------------------------------------------------------------------------
# Deal (from cmd 5 trade-history, schema Pd in JS)
# ---------------------------------------------------------------------------
DEAL_SCHEMA = [
    {"propType": PROP_I64},  # 0: deal
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 1: deal_id
    {"propType": PROP_I64},  # 2: trade_order
    {"propType": PROP_U32},  # 3: time_create (sec)
    {"propType": PROP_U32},  # 4: time_update (sec)
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 5: trade_symbol
    {"propType": PROP_U32},  # 6: trade_action
    {"propType": PROP_U32},  # 7: entry
    {"propType": PROP_F64},  # 8: price_open
    {"propType": PROP_F64},  # 9: price_close
    {"propType": PROP_F64},  # 10: sl
    {"propType": PROP_F64},  # 11: tp
    {"propType": PROP_U64},  # 12: trade_volume
    {"propType": PROP_F64},  # 13: profit
    {"propType": PROP_F64},  # 14: rate_profit
    {"propType": PROP_F64},  # 15: rate_margin
    {"propType": PROP_F64},  # 16: commission
    {"propType": PROP_F64},  # 17: storage (swap)
    {"propType": PROP_I64},  # 18: expert
    {"propType": PROP_I64},  # 19: position_id
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 20: comment
    {"propType": PROP_F64},  # 21: contract_size
    {"propType": PROP_U32},  # 22: digits
    {"propType": PROP_U32},  # 23: digits_currency
    {"propType": PROP_U32},  # 24: trade_reason
    {"propType": PROP_I32},  # 25: time_create_ms
    {"propType": PROP_I32},  # 26: time_update_ms
    {"propType": PROP_F64},  # 27: commission_fee
]

DEAL_FIELD_NAMES = [
    "deal",
    "deal_id",
    "trade_order",
    "time_create",
    "time_update",
    "trade_symbol",
    "trade_action",
    "entry",
    "price_open",
    "price_close",
    "sl",
    "tp",
    "trade_volume",
    "profit",
    "rate_profit",
    "rate_margin",
    "commission",
    "storage",
    "expert",
    "position_id",
    "comment",
    "contract_size",
    "digits",
    "digits_currency",
    "trade_reason",
    "time_create_ms",
    "time_update_ms",
    "commission_fee",
]

# ---------------------------------------------------------------------------
# Full Symbol Info (from cmd 18, detailed contract specifications)
# Schema $h / Dh in the current Web Terminal bundle.
#
# The last three fields are nested binary sections:
# - trade config: schema ll (same layout as account trade settings)
# - schedule: 7 * 16 quote sessions + 7 * 16 trade sessions, each session u16/u16
# - subscription: {delay:u32, status:u8, level:u8, reserved:u16}
# ---------------------------------------------------------------------------
FULL_SYMBOL_TRADE_CONFIG_LENGTH = 628
FULL_SYMBOL_SCHEDULE_LENGTH = 896
FULL_SYMBOL_SUBSCRIPTION_LENGTH = 8

FULL_SYMBOL_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 0: trade_symbol
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 1: isin
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 2: symbol_description
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 3: international
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 4: basis
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 5: symbol_source
    {"propType": PROP_FIXED_STRING, "propLength": 512},  # 6: symbol_page
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 7: category
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 8: trade_exchange
    {"propType": PROP_FIXED_STRING, "propLength": 16},  # 9: cfi
    {"propType": PROP_U16},  # 10: sector
    {"propType": PROP_U16},  # 11: industry
    {"propType": PROP_FIXED_STRING, "propLength": 8},  # 12: country
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 13: currency_base
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 14: currency_profit
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 15: currency_margin
    {"propType": PROP_U32},  # 16: currency_base_digits
    {"propType": PROP_U32},  # 17: currency_profit_digits
    {"propType": PROP_U32},  # 18: currency_margin_digits
    {"propType": PROP_U32},  # 19: symbol_color
    {"propType": PROP_U32},  # 20: color_background
    {"propType": PROP_U32},  # 21: digits
    {"propType": PROP_F64},  # 22: point
    {"propType": PROP_F64},  # 23: symbol_multiply
    {"propType": PROP_U32},  # 24: symbol_id
    {"propType": PROP_U32},  # 25: ticks_flags
    {"propType": PROP_U32},  # 26: ticks_bookdepth
    {"propType": PROP_U32},  # 27: ticks_chart_mode
    {"propType": PROP_U32},  # 28: trade_flags
    {"propType": PROP_I32},  # 29: spread
    {"propType": PROP_I32},  # 30: spread_balance
    {"propType": PROP_F64},  # 31: tick_value
    {"propType": PROP_F64},  # 32: tick_size
    {"propType": PROP_F64},  # 33: contract_size
    {"propType": PROP_U32},  # 34: trade_gtc_mode
    {"propType": PROP_U32},  # 35: trade_calc_mode
    {"propType": PROP_F64},  # 36: trade_price_settle
    {"propType": PROP_F64},  # 37: trade_price_limit_min
    {"propType": PROP_F64},  # 38: trade_price_limit_max
    {"propType": PROP_F64},  # 39: trade_price_strike
    {"propType": PROP_U32},  # 40: trade_option_mode
    {"propType": PROP_F64},  # 41: face_value
    {"propType": PROP_F64},  # 42: accrued_interest
    {"propType": PROP_U32},  # 43: time_flags
    {"propType": PROP_I32},  # 44: time_start
    {"propType": PROP_I32},  # 45: time_expiration
    {"propType": PROP_BYTES, "propLength": FULL_SYMBOL_TRADE_CONFIG_LENGTH},  # 46: trade
    {"propType": PROP_BYTES, "propLength": FULL_SYMBOL_SCHEDULE_LENGTH},  # 47: schedule
    {"propType": PROP_BYTES, "propLength": FULL_SYMBOL_SUBSCRIPTION_LENGTH},  # 48: subscription
]

FULL_SYMBOL_FIELD_NAMES = [
    "trade_symbol",
    "isin",
    "symbol_description",
    "international",
    "basis",
    "symbol_source",
    "symbol_page",
    "category",
    "trade_exchange",
    "cfi",
    "sector",
    "industry",
    "country",
    "currency_base",
    "currency_profit",
    "currency_margin",
    "currency_base_digits",
    "currency_profit_digits",
    "currency_margin_digits",
    "symbol_color",
    "color_background",
    "digits",
    "point",
    "symbol_multiply",
    "symbol_id",
    "ticks_flags",
    "ticks_bookdepth",
    "ticks_chart_mode",
    "trade_flags",
    "spread",
    "spread_balance",
    "tick_value",
    "tick_size",
    "contract_size",
    "trade_gtc_mode",
    "trade_calc_mode",
    "trade_price_settle",
    "trade_price_limit_min",
    "trade_price_limit_max",
    "trade_price_strike",
    "trade_option_mode",
    "face_value",
    "accrued_interest",
    "time_flags",
    "time_start",
    "time_expiration",
    "trade",
    "schedule",
    "subscription",
]

# ---------------------------------------------------------------------------
# Kline / Rates request (cmd 11)
# ---------------------------------------------------------------------------
RATES_REQUEST_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # symbol
    {"propType": PROP_U16},  # period (mapped)
    {"propType": PROP_I32},  # from (unix sec)
    {"propType": PROP_I32},  # to (unix sec)
]

# ---------------------------------------------------------------------------
# Kline / Rate bar (response from cmd 11)
# Each bar is 48 bytes: I32 + F64*4 + I64 + I32
# Verified against live data: 2880 bytes / 48 = 60 M1 bars
# ---------------------------------------------------------------------------
RATE_BAR_SCHEMA = [
    {"propType": PROP_I32},  # 0: time (unix seconds)
    {"propType": PROP_F64},  # 1: open
    {"propType": PROP_F64},  # 2: high
    {"propType": PROP_F64},  # 3: low
    {"propType": PROP_F64},  # 4: close
    {"propType": PROP_I64},  # 5: tick_volume
    {"propType": PROP_I32},  # 6: spread
]

RATE_BAR_FIELD_NAMES = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
]

# Extended rate bar with real_volume — some servers may include this.
# Standard MT5 Web Terminal (2026-03) sends 48-byte bars without real_volume.
RATE_BAR_SCHEMA_EXT = RATE_BAR_SCHEMA + [
    {"propType": PROP_I64},  # 7: real_volume
]

RATE_BAR_FIELD_NAMES_EXT = RATE_BAR_FIELD_NAMES + ["real_volume"]

# ---------------------------------------------------------------------------
# Trade request (cmd 12)
# ---------------------------------------------------------------------------
TRADE_REQUEST_SCHEMA = [
    {"propType": PROP_U32},  # action_id
    {"propType": PROP_U32},  # trade_action
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # trade_symbol
    {"propType": PROP_U64},  # trade_volume
    {"propType": PROP_U32},  # digits
    {"propType": PROP_U64},  # trade_order
    {"propType": PROP_U32},  # trade_type
    {"propType": PROP_U32},  # type_filling
    {"propType": PROP_U32},  # type_time
    {"propType": PROP_U32},  # type_flags
    {"propType": PROP_U32},  # type_reason
    {"propType": PROP_F64},  # price_order
    {"propType": PROP_F64},  # price_trigger
    {"propType": PROP_F64},  # price_sl
    {"propType": PROP_F64},  # price_tp
    {"propType": PROP_U64},  # deviation
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # comment
    {"propType": PROP_U64},  # position_id
    {"propType": PROP_U64},  # position_by
    {"propType": PROP_U32},  # time_expiration
]

# ---------------------------------------------------------------------------
# Symbol Group (cmd 9 response)
# Each group is a single fixed-length UTF-16LE string (256 bytes)
# ---------------------------------------------------------------------------
SYMBOL_GROUP_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 0: group_name
]

SYMBOL_GROUP_FIELD_NAMES = ["group_name"]

# ---------------------------------------------------------------------------
# Spread (cmd 20 response)
# Schema Vl in JS
# ---------------------------------------------------------------------------
SPREAD_SCHEMA = [
    {"propType": PROP_U32},  # 0: spread_id
    {"propType": PROP_U32},  # 1: flags
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 2: trade_symbol
    {"propType": PROP_U32},  # 3: param1
    {"propType": PROP_U32},  # 4: param2
    {"propType": PROP_F64},  # 5: spread_value
]

SPREAD_FIELD_NAMES = [
    "spread_id",
    "flags",
    "trade_symbol",
    "param1",
    "param2",
    "spread_value",
]

# ---------------------------------------------------------------------------
# Order Book Entry (cmd 22/23, schema Nu/Pu in JS)
# Header per symbol group in the DOM push
# ---------------------------------------------------------------------------
BOOK_HEADER_SCHEMA = [
    {"propType": PROP_U32},  # 0: symbol_id
    {"propType": PROP_I32},  # 1: field1
    {"propType": PROP_I32},  # 2: field2
    {"propType": PROP_U32},  # 3: bid_count
    {"propType": PROP_U32},  # 4: ask_count
    {"propType": PROP_U16},  # 5: flags
]

BOOK_HEADER_FIELD_NAMES = [
    "symbol_id",
    "field1",
    "field2",
    "bid_count",
    "ask_count",
    "flags",
]

BOOK_LEVEL_SCHEMA = [
    {"propType": PROP_F64},  # 0: price
    {"propType": PROP_I64},  # 1: volume
]

BOOK_LEVEL_FIELD_NAMES = ["price", "volume"]

# ---------------------------------------------------------------------------
# Trade Update Push (cmd 10, order transaction format)
# Schema Zu in JS — used when type != 2
# ---------------------------------------------------------------------------
TRADE_TRANSACTION_SCHEMA = [
    {"propType": PROP_U32},  # 0: flag_mask (0=open orders, 1=history orders, 2=deal)
    {"propType": PROP_U32},  # 1: transaction_id
    {"propType": PROP_U32},  # 2: transaction_type (0=add, 1=update, 2=delete)
]

TRADE_TRANSACTION_FIELD_NAMES = [
    "flag_mask",
    "transaction_id",
    "transaction_type",
]

# Trade Update Push balance header (cmd 10, type == 2)
TRADE_UPDATE_BALANCE_SCHEMA = [
    {"propType": PROP_F64},  # 0: balance
    {"propType": PROP_F64},  # 1: credit
    {"propType": PROP_F64},  # 2: acc_profit
    {"propType": PROP_F64},  # 3: equity
    {"propType": PROP_F64},  # 4: margin
    {"propType": PROP_F64},  # 5: margin_free
]

TRADE_UPDATE_BALANCE_FIELD_NAMES = [
    "balance",
    "credit",
    "acc_profit",
    "equity",
    "margin",
    "margin_free",
]

# ---------------------------------------------------------------------------
# Symbol Details Push (cmd 17)
# Extended quote data with options greeks — 39 fields parsed by Uh() in JS
# ---------------------------------------------------------------------------
SYMBOL_DETAILS_SCHEMA = [
    {"propType": PROP_U32},  # 0: symbol_id
    {"propType": PROP_I32},  # 1: tick_time
    {"propType": PROP_U32},  # 2: fields_mask
    {"propType": PROP_F64},  # 3: bid
    {"propType": PROP_F64},  # 4: ask
    {"propType": PROP_F64},  # 5: last
    {"propType": PROP_I64},  # 6: tick_volume
    {"propType": PROP_F64},  # 7: high
    {"propType": PROP_F64},  # 8: low
    {"propType": PROP_F64},  # 9: open
    {"propType": PROP_F64},  # 10: close
    {"propType": PROP_I64},  # 11: volume
    {"propType": PROP_I64},  # 12: real_volume
    {"propType": PROP_F64},  # 13: option_strike
    {"propType": PROP_F64},  # 14: price_settle
    {"propType": PROP_F64},  # 15: price_limit_high
    {"propType": PROP_F64},  # 16: price_limit_low
    {"propType": PROP_F64},  # 17: delta
    {"propType": PROP_F64},  # 18: gamma
    {"propType": PROP_F64},  # 19: theta
    {"propType": PROP_F64},  # 20: vega
    {"propType": PROP_F64},  # 21: rho
    {"propType": PROP_F64},  # 22: omega
    {"propType": PROP_F64},  # 23: price_sensitivity
    {"propType": PROP_U32},  # 24: session_deals
    {"propType": PROP_I64},  # 25: session_buy_volume
    {"propType": PROP_I64},  # 26: session_sell_volume
    {"propType": PROP_I64},  # 27: session_turnover
    {"propType": PROP_I64},  # 28: session_interest
    {"propType": PROP_I64},  # 29: session_buy_orders
    {"propType": PROP_I64},  # 30: session_sell_orders
    {"propType": PROP_F64},  # 31: session_avg_price
    {"propType": PROP_F64},  # 32: session_open
    {"propType": PROP_F64},  # 33: session_close
    {"propType": PROP_F64},  # 34: session_aw
    {"propType": PROP_F64},  # 35: session_price_settle
    {"propType": PROP_F64},  # 36: session_price_limit_high
    {"propType": PROP_F64},  # 37: session_price_limit_low
    {"propType": PROP_U32},  # 38: flags
]

SYMBOL_DETAILS_FIELD_NAMES = [
    "symbol_id",
    "tick_time",
    "fields_mask",
    "bid",
    "ask",
    "last",
    "tick_volume",
    "high",
    "low",
    "open",
    "close",
    "volume",
    "real_volume",
    "option_strike",
    "price_settle",
    "price_limit_high",
    "price_limit_low",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "omega",
    "price_sensitivity",
    "session_deals",
    "session_buy_volume",
    "session_sell_volume",
    "session_turnover",
    "session_interest",
    "session_buy_orders",
    "session_sell_orders",
    "session_avg_price",
    "session_open",
    "session_close",
    "session_aw",
    "session_price_settle",
    "session_price_limit_high",
    "session_price_limit_low",
    "flags",
]

# ---------------------------------------------------------------------------
# Trade Result Push (cmd 19)
# Schema $p/Ap in JS — async trade execution result
# ---------------------------------------------------------------------------
TRADE_RESULT_PUSH_SCHEMA = [
    {"propType": PROP_U32},  # 0: action_result_code
    {"propType": PROP_U32},  # 1: action_id
    {"propType": PROP_U32},  # 2: trade_action
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 3: trade_symbol
    {"propType": PROP_U64},  # 4: trade_volume
    {"propType": PROP_U32},  # 5: digits
    {"propType": PROP_U64},  # 6: trade_order
    {"propType": PROP_U32},  # 7: trade_type
    {"propType": PROP_U32},  # 8: type_filling
    {"propType": PROP_U32},  # 9: type_time
    {"propType": PROP_U32},  # 10: type_flags
    {"propType": PROP_U32},  # 11: type_reason
    {"propType": PROP_F64},  # 12: price_order
    {"propType": PROP_F64},  # 13: price_trigger
    {"propType": PROP_F64},  # 14: price_sl
    {"propType": PROP_F64},  # 15: price_tp
    {"propType": PROP_U64},  # 16: deviation
    {"propType": PROP_F64},  # 17: price_top
    {"propType": PROP_F64},  # 18: price_bottom
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 19: comment
    {"propType": PROP_U64},  # 20: trade_position
]

TRADE_RESULT_PUSH_FIELD_NAMES = [
    "action_result_code",
    "action_id",
    "trade_action",
    "trade_symbol",
    "trade_volume",
    "digits",
    "trade_order",
    "trade_type",
    "type_filling",
    "type_time",
    "type_flags",
    "type_reason",
    "price_order",
    "price_trigger",
    "price_sl",
    "price_tp",
    "deviation",
    "price_top",
    "price_bottom",
    "comment",
    "trade_position",
]

# Trade result response part (appended after the action details)
TRADE_RESULT_RESPONSE_SCHEMA = [
    {"propType": PROP_U32},  # 0: retcode
    {"propType": PROP_U64},  # 1: trade_order
    {"propType": PROP_U64},  # 2: volume
    {"propType": PROP_F64},  # 3: price
    {"propType": PROP_F64},  # 4: bid
    {"propType": PROP_F64},  # 5: ask
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 6: comment
]

TRADE_RESULT_RESPONSE_FIELD_NAMES = [
    "retcode",
    "trade_order",
    "volume",
    "price",
    "bid",
    "ask",
    "comment",
]

# ---------------------------------------------------------------------------
# Corporate Links (cmd 44 response)
# Schema Fp in JS
# ---------------------------------------------------------------------------
CORPORATE_LINK_SCHEMA = [
    {"propType": PROP_U32},  # 0: link_type
    {"propType": PROP_FIXED_STRING, "propLength": 512},  # 1: url
    {"propType": PROP_FIXED_STRING, "propLength": 512},  # 2: label
    {"propType": PROP_U32},  # 3: flags
    {"propType": PROP_BYTES, "propLength": 256},  # 4: icon_data
]

CORPORATE_LINK_FIELD_NAMES = [
    "link_type",
    "url",
    "label",
    "flags",
    "icon_data",
]

# ---------------------------------------------------------------------------
# Account Info (cmd 3 response / cmd 14 push)
# Base account fields parsed by Tl() in JS — the full response has additional
# sections (commissions, margin tiers, trade settings) that are parsed
# separately. These base fields cover the core account data.
# ---------------------------------------------------------------------------
ACCOUNT_BASE_SCHEMA = [
    {"propType": PROP_U64},  # 0: login
    {"propType": PROP_I32},  # 1: trade_mode
    {"propType": PROP_I32},  # 2: leverage
    {"propType": PROP_I32},  # 3: limit_orders
    {"propType": PROP_I32},  # 4: margin_so_mode
    {"propType": PROP_I32},  # 5: trade_allowed
    {"propType": PROP_I32},  # 6: trade_expert
    {"propType": PROP_F64},  # 7: balance
    {"propType": PROP_F64},  # 8: credit
    {"propType": PROP_F64},  # 9: profit
    {"propType": PROP_F64},  # 10: equity
    {"propType": PROP_F64},  # 11: margin
    {"propType": PROP_F64},  # 12: margin_free
    {"propType": PROP_F64},  # 13: margin_level
    {"propType": PROP_F64},  # 14: margin_so_call
    {"propType": PROP_F64},  # 15: margin_so_so
    {"propType": PROP_F64},  # 16: margin_initial
    {"propType": PROP_F64},  # 17: margin_maintenance
    {"propType": PROP_F64},  # 18: assets
    {"propType": PROP_F64},  # 19: liabilities
    {"propType": PROP_F64},  # 20: commission_blocked
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 21: name
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 22: server
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 23: currency
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 24: company
]

ACCOUNT_BASE_FIELD_NAMES = [
    "login",
    "trade_mode",
    "leverage",
    "limit_orders",
    "margin_so_mode",
    "trade_allowed",
    "trade_expert",
    "balance",
    "credit",
    "profit",
    "equity",
    "margin",
    "margin_free",
    "margin_level",
    "margin_so_call",
    "margin_so_so",
    "margin_initial",
    "margin_maintenance",
    "assets",
    "liabilities",
    "commission_blocked",
    "name",
    "server",
    "currency",
    "company",
]

# ---------------------------------------------------------------------------
# Account Config Header (cmd 3 / cmd 14 initial account block)
# Schema Tl in the current Web Terminal bundle.
# ---------------------------------------------------------------------------
ACCOUNT_WEB_MAIN_SCHEMA = [
    {"propType": PROP_U8},  # 0: account_type
    {"propType": PROP_I32},  # 1: rights
    {"propType": PROP_I32},  # 2: permissions_flags
    {"propType": PROP_F64},  # 3: balance
    {"propType": PROP_F64},  # 4: credit
    {"propType": PROP_FIXED_STRING, "propLength": 64},  # 5: account_currency
    {"propType": PROP_U32},  # 6: currency_digits
    {"propType": PROP_U32},  # 7: margin_leverage
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 8: account_name
    {"propType": PROP_U16},  # 9: server_build
    {"propType": PROP_FIXED_STRING, "propLength": 128},  # 10: server_name
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 11: company
    {"propType": PROP_I32},  # 12: timezone_shift
    {"propType": PROP_I8},  # 13: daylightmode
    {"propType": PROP_U32},  # 14: margin_mode
    {"propType": PROP_U32},  # 15: margin_free_mode
    {"propType": PROP_F64},  # 16: margin_so_call
    {"propType": PROP_F64},  # 17: margin_so_so
    {"propType": PROP_U32},  # 18: margin_so_mode
    {"propType": PROP_F64},  # 19: margin_virtual
    {"propType": PROP_U32},  # 20: margin_free_profit_mode
    {"propType": PROP_F64},  # 21: acc_profit
    {"propType": PROP_F64},  # 22: commission_daily
    {"propType": PROP_F64},  # 23: commission_monthly
    {"propType": PROP_U32},  # 24: auth_password_min
    {"propType": PROP_U32},  # 25: otp_status
]

ACCOUNT_WEB_MAIN_FIELD_NAMES = [
    "account_type",
    "rights",
    "permissions_flags",
    "balance",
    "credit",
    "account_currency",
    "currency_digits",
    "margin_leverage",
    "account_name",
    "server_build",
    "server_name",
    "company",
    "timezone_shift",
    "daylightmode",
    "margin_mode",
    "margin_free_mode",
    "margin_so_call",
    "margin_so_so",
    "margin_so_mode",
    "margin_virtual",
    "margin_free_profit_mode",
    "acc_profit",
    "commission_daily",
    "commission_monthly",
    "auth_password_min",
    "otp_status",
]

# ---------------------------------------------------------------------------
# Account Trade Settings (cmd 3 / cmd 14 variable section)
# Schema nl / ll in the current Web Terminal bundle.
# ---------------------------------------------------------------------------
ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 0: symbol_path
    {"propType": PROP_I32},  # 1: spread_diff
    {"propType": PROP_I32},  # 2: spread_diff_balance
    {"propType": PROP_U32},  # 3: trade_mode
    {"propType": PROP_I32},  # 4: trade_stops_level
    {"propType": PROP_I32},  # 5: trade_freeze_level
    {"propType": PROP_U32},  # 6: trade_exemode
    {"propType": PROP_U32},  # 7: trade_fill_flags
    {"propType": PROP_U32},  # 8: trade_time_flags
    {"propType": PROP_U32},  # 9: trade_order_flags
    {"propType": PROP_U32},  # 10: trade_request_flags
    {"propType": PROP_U32},  # 11: trade_request_timeout
    {"propType": PROP_U32},  # 12: trade_instant_flags
    {"propType": PROP_U32},  # 13: trade_instant_timeout
    {"propType": PROP_U32},  # 14: trade_instant_slip_profit
    {"propType": PROP_U32},  # 15: trade_instant_slip_losing
    {"propType": PROP_U64},  # 16: trade_instant_volume
    {"propType": PROP_U32},  # 17: trade_instant_checkmode
    {"propType": PROP_U32},  # 18: trade_market_flags
    {"propType": PROP_U32},  # 19: trade_exchange_flags
    {"propType": PROP_U64},  # 20: volume_min
    {"propType": PROP_U64},  # 21: volume_max
    {"propType": PROP_U64},  # 22: volume_step
    {"propType": PROP_U64},  # 23: volume_limit
    {"propType": PROP_U32},  # 24: margin_flags
    {"propType": PROP_F64},  # 25: margin_initial
    {"propType": PROP_F64},  # 26: margin_maintenance
    {"propType": PROP_BYTES, "propLength": 64},  # 27: margin_rates_initial
    {"propType": PROP_BYTES, "propLength": 64},  # 28: margin_rates_maintenance
    {"propType": PROP_F64},  # 29: margin_rate_liquidity
    {"propType": PROP_F64},  # 30: margin_hedged
    {"propType": PROP_F64},  # 31: margin_rate_currency
    {"propType": PROP_U32},  # 32: swap_mode
    {"propType": PROP_F64},  # 33: swap_long
    {"propType": PROP_F64},  # 34: swap_short
    {"propType": PROP_I32},  # 35: swap_rollover3days
    {"propType": PROP_U32},  # 36: permissions_flags
    {"propType": PROP_U32},  # 37: permissions_bookdepth
    {"propType": PROP_BYTES, "propLength": 56},  # 38: swap_rates
]

ACCOUNT_WEB_TRADE_SETTINGS_FIELD_NAMES = [
    "symbol_path",
    "spread_diff",
    "spread_diff_balance",
    "trade_mode",
    "trade_stops_level",
    "trade_freeze_level",
    "trade_exemode",
    "trade_fill_flags",
    "trade_time_flags",
    "trade_order_flags",
    "trade_request_flags",
    "trade_request_timeout",
    "trade_instant_flags",
    "trade_instant_timeout",
    "trade_instant_slip_profit",
    "trade_instant_slip_losing",
    "trade_instant_volume",
    "trade_instant_checkmode",
    "trade_market_flags",
    "trade_exchange_flags",
    "volume_min",
    "volume_max",
    "volume_step",
    "volume_limit",
    "margin_flags",
    "margin_initial",
    "margin_maintenance",
    "margin_rates_initial",
    "margin_rates_maintenance",
    "margin_rate_liquidity",
    "margin_hedged",
    "margin_rate_currency",
    "swap_mode",
    "swap_long",
    "swap_short",
    "swap_rollover3days",
    "permissions_flags",
    "permissions_bookdepth",
    "swap_rates",
]

# ---------------------------------------------------------------------------
# Account Commission Rules (cmd 3 / cmd 14 variable section)
# Schema _l / ul in the current Web Terminal bundle.
# ---------------------------------------------------------------------------
ACCOUNT_WEB_COMMISSION_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 0: path
    {"propType": PROP_U32},  # 1: mode
    {"propType": PROP_U32},  # 2: mode_range
    {"propType": PROP_U32},  # 3: mode_charge
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 4: mode_currency
    {"propType": PROP_U32},  # 5: mode_entry
]

ACCOUNT_WEB_COMMISSION_FIELD_NAMES = [
    "path",
    "mode",
    "mode_range",
    "mode_charge",
    "mode_currency",
    "mode_entry",
]

ACCOUNT_WEB_COMMISSION_TIER_SCHEMA = [
    {"propType": PROP_U32},  # 0: mode
    {"propType": PROP_U32},  # 1: type
    {"propType": PROP_F64},  # 2: value
    {"propType": PROP_F64},  # 3: range_from
    {"propType": PROP_F64},  # 4: range_to
    {"propType": PROP_F64},  # 5: minimal
    {"propType": PROP_F64},  # 6: maximal
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 7: currency
]

ACCOUNT_WEB_COMMISSION_TIER_FIELD_NAMES = [
    "mode",
    "type",
    "value",
    "range_from",
    "range_to",
    "minimal",
    "maximal",
    "currency",
]

# ---------------------------------------------------------------------------
# Account Leverage Rules (cmd 3 / cmd 14 variable section)
# Schema gl / fl in the current Web Terminal bundle.
# ---------------------------------------------------------------------------
ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA = [
    {"propType": PROP_FIXED_STRING, "propLength": 256},  # 0: path
    {"propType": PROP_U32},  # 1: range_mode
    {"propType": PROP_FIXED_STRING, "propLength": 32},  # 2: range_currency
    {"propType": PROP_U32},  # 3: range_currency_digits
    {"propType": PROP_I32},  # 4: tier_count
]

ACCOUNT_WEB_LEVERAGE_RULE_FIELD_NAMES = [
    "path",
    "range_mode",
    "range_currency",
    "range_currency_digits",
    "tier_count",
]

ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA = [
    {"propType": PROP_F64},  # 0: range_from
    {"propType": PROP_F64},  # 1: range_to
    {"propType": PROP_F64},  # 2: margin_rate_initial
    {"propType": PROP_F64},  # 3: margin_rate_maintenance
]

ACCOUNT_WEB_LEVERAGE_TIER_FIELD_NAMES = [
    "range_from",
    "range_to",
    "margin_rate_initial",
    "margin_rate_maintenance",
]

# ---------------------------------------------------------------------------
# Schema / field-name consistency assertions (Phase 3.2)
# Catches mismatches at import time rather than at runtime.
# ---------------------------------------------------------------------------
_SCHEMA_PAIRS = [
    (TICK_SCHEMA, TICK_FIELD_NAMES, "TICK"),
    (SYMBOL_BASIC_SCHEMA, SYMBOL_BASIC_FIELD_NAMES, "SYMBOL_BASIC"),
    (POSITION_SCHEMA, POSITION_FIELD_NAMES, "POSITION"),
    (ORDER_SCHEMA, ORDER_FIELD_NAMES, "ORDER"),
    (DEAL_SCHEMA, DEAL_FIELD_NAMES, "DEAL"),
    (FULL_SYMBOL_SCHEMA, FULL_SYMBOL_FIELD_NAMES, "FULL_SYMBOL"),
    (RATE_BAR_SCHEMA, RATE_BAR_FIELD_NAMES, "RATE_BAR"),
    (RATE_BAR_SCHEMA_EXT, RATE_BAR_FIELD_NAMES_EXT, "RATE_BAR_EXT"),
    (SYMBOL_GROUP_SCHEMA, SYMBOL_GROUP_FIELD_NAMES, "SYMBOL_GROUP"),
    (SPREAD_SCHEMA, SPREAD_FIELD_NAMES, "SPREAD"),
    (BOOK_HEADER_SCHEMA, BOOK_HEADER_FIELD_NAMES, "BOOK_HEADER"),
    (BOOK_LEVEL_SCHEMA, BOOK_LEVEL_FIELD_NAMES, "BOOK_LEVEL"),
    (TRADE_TRANSACTION_SCHEMA, TRADE_TRANSACTION_FIELD_NAMES, "TRADE_TRANSACTION"),
    (TRADE_UPDATE_BALANCE_SCHEMA, TRADE_UPDATE_BALANCE_FIELD_NAMES, "TRADE_UPDATE_BALANCE"),
    (SYMBOL_DETAILS_SCHEMA, SYMBOL_DETAILS_FIELD_NAMES, "SYMBOL_DETAILS"),
    (TRADE_RESULT_PUSH_SCHEMA, TRADE_RESULT_PUSH_FIELD_NAMES, "TRADE_RESULT_PUSH"),
    (TRADE_RESULT_RESPONSE_SCHEMA, TRADE_RESULT_RESPONSE_FIELD_NAMES, "TRADE_RESULT_RESPONSE"),
    (CORPORATE_LINK_SCHEMA, CORPORATE_LINK_FIELD_NAMES, "CORPORATE_LINK"),
    (ACCOUNT_BASE_SCHEMA, ACCOUNT_BASE_FIELD_NAMES, "ACCOUNT_BASE"),
    (ACCOUNT_WEB_MAIN_SCHEMA, ACCOUNT_WEB_MAIN_FIELD_NAMES, "ACCOUNT_WEB_MAIN"),
    (
        ACCOUNT_WEB_TRADE_SETTINGS_SCHEMA,
        ACCOUNT_WEB_TRADE_SETTINGS_FIELD_NAMES,
        "ACCOUNT_WEB_TRADE_SETTINGS",
    ),
    (ACCOUNT_WEB_COMMISSION_SCHEMA, ACCOUNT_WEB_COMMISSION_FIELD_NAMES, "ACCOUNT_WEB_COMMISSION"),
    (
        ACCOUNT_WEB_COMMISSION_TIER_SCHEMA,
        ACCOUNT_WEB_COMMISSION_TIER_FIELD_NAMES,
        "ACCOUNT_WEB_COMMISSION_TIER",
    ),
    (
        ACCOUNT_WEB_LEVERAGE_RULE_SCHEMA,
        ACCOUNT_WEB_LEVERAGE_RULE_FIELD_NAMES,
        "ACCOUNT_WEB_LEVERAGE_RULE",
    ),
    (
        ACCOUNT_WEB_LEVERAGE_TIER_SCHEMA,
        ACCOUNT_WEB_LEVERAGE_TIER_FIELD_NAMES,
        "ACCOUNT_WEB_LEVERAGE_TIER",
    ),
]

for _schema, _names, _label in _SCHEMA_PAIRS:
    assert len(_schema) == len(_names), (
        f"{_label}_SCHEMA has {len(_schema)} fields but {_label}_FIELD_NAMES has {len(_names)} entries"
    )
