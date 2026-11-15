"""Sample one symbol's live demo ticks and nearby M1 bars without placing trades.

Set PYMT5_SERVER, PYMT5_LOGIN and PYMT5_PASSWORD in the environment, or pass
--env-file when python-dotenv is available. Output is sanitized JSON containing
market data and status only; it is not an automatic acceptance decision.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _error(stage: str, exc: BaseException) -> dict[str, Any]:
    """Never expose exception text, which can contain sensitive connection data."""
    result: dict[str, Any] = {"stage": stage, "type": type(exc).__name__}
    if type(exc).__name__ == "ProtocolError":
        match = re.search(r"\bcode[= ](\d+)\b", str(exc))
        if match:
            result["server_code"] = int(match.group(1))
    return result


async def collect_sample(
    *,
    uri: str,
    login: int,
    password: str,
    symbol: str,
    seconds: float,
    bar_format: str = "auto",
    client_factory: Any = None,
) -> dict[str, Any]:
    """Read one matching cmd=8 sample and, if available, nearby cmd=11 M1 bars."""
    # Import lazily so the command can set its safe log level before library use.
    from pymt5.client import MT5WebClient
    from pymt5.constants import CMD_TICK_PUSH
    from pymt5.protocol import SeriesCodec, get_series_size
    from pymt5.schemas import TICK_FIELD_NAMES, TICK_SCHEMA

    report: dict[str, Any] = {
        "status": "no_sample",
        "symbol": symbol,
        "started_at_utc": _utc_now(),
        "wait_seconds": seconds,
        "wire_record_size_bytes": get_series_size(TICK_SCHEMA),
        "frames_seen": 0,
        "unsubscribe": "not_attempted",
        "close": "not_attempted",
    }
    client = None
    event = asyncio.Event()
    subscribed = False
    symbol_id: int | None = None
    stage = "client_init"
    try:
        client = (client_factory or MT5WebClient)(uri=uri, timeout=30.0, tick_price_mode="normalized")
        stage = "connect"
        await client.connect()
        report["server_build"] = client.server_build
        stage = "login"
        await client.login(login=login, password=password, auto_heartbeat=False)
        stage = "load_symbols"
        symbols = await client.load_symbols()
        if symbol not in symbols:
            report["status"] = "symbol_not_found"
            return report
        info = symbols[symbol]
        symbol_id = int(info.symbol_id)
        digits = int(info.digits)
        report["digits"] = digits

        def on_tick_frame(result: Any) -> None:
            report["frames_seen"] += 1
            if event.is_set():
                return
            try:
                if result.code != 0:
                    report["status"] = "error"
                    report["error"] = {"stage": "tick_frame", "type": "NonzeroResponse", "server_code": result.code}
                    event.set()
                    return
                body = result.body
                size = get_series_size(TICK_SCHEMA)
                if not body or len(body) % size:
                    report["status"] = "error"
                    report["error"] = {"stage": "tick_parse", "type": "InvalidBodyLength", "body_bytes": len(body)}
                    event.set()
                    return
                for offset in range(0, len(body), size):
                    values = SeriesCodec.parse_at(body, TICK_SCHEMA, offset)
                    tick = dict(zip(TICK_FIELD_NAMES, values))
                    if int(tick["symbol_id"]) != symbol_id:
                        continue
                    wire: dict[str, float | None] = {}
                    scaled: dict[str, float | None] = {}
                    for name in ("bid", "ask", "last"):
                        value = float(tick[name])
                        wire[name] = value if math.isfinite(value) else None
                        scaled[name] = value / 10**digits if math.isfinite(value) and 0 <= digits <= 15 else None
                    report["sample"] = {
                        "source": "cmd8_wire",
                        "observed_at_utc": _utc_now(),
                        "tick_time_unix_ms": int(tick["tick_time"]) * 1000 + int(tick["time_ms_delta"]),
                        "fields": int(tick["fields"]),
                        "flags": int(tick["flags"]),
                        "body_bytes": len(body),
                        "wire_prices": wire,
                        "scaled_candidates": scaled,
                    }
                    report["status"] = "sampled"
                    event.set()
                    return
            except Exception as exc:
                report["status"] = "error"
                report["error"] = _error("tick_parse", exc)
                event.set()

        client.transport.on(CMD_TICK_PUSH, on_tick_frame)
        stage = "subscribe"
        await client.subscribe_ticks([symbol_id])
        subscribed = True
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except TimeoutError:
            report["status"] = "no_sample"

        if report["status"] == "sampled":
            tick_second = report["sample"]["tick_time_unix_ms"] // 1000
            start, end = max(0, tick_second - 120), tick_second + 60
            reference: dict[str, Any] = {
                "source": "cmd11_m1",
                "bar_format": bar_format,
                "window_unix_seconds": [start, end],
            }
            try:
                bars = await client.get_rates(symbol, 1, start, end, bar_format=bar_format)
                reference["status"] = "available" if bars else "no_bars"
                reference["bars"] = []
                for bar in sorted(bars, key=lambda bar: abs(int(bar["time"]) - tick_second))[:3]:
                    item = {"time": int(bar["time"])}
                    for name in ("open", "high", "low", "close"):
                        price = float(bar[name])
                        item[name] = price if math.isfinite(price) else None
                    reference["bars"].append(item)
            except Exception as exc:
                reference["status"] = "error"
                reference["error"] = _error("bars", exc)
            report["bars_reference"] = reference
    except Exception as exc:
        report["status"] = "error"
        report["error"] = _error(stage, exc)
    finally:
        if client is not None and subscribed and symbol_id is not None:
            try:
                await client.unsubscribe_ticks([symbol_id])
                report["unsubscribe"] = "ok"
            except Exception as exc:
                report["unsubscribe"] = _error("unsubscribe", exc)
        if client is not None:
            try:
                await client.close()
                report["close"] = "ok"
            except Exception as exc:
                report["close"] = _error("close", exc)
        report["finished_at_utc"] = _utc_now()
    return report


def _seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or not 1 <= seconds <= 300:
        raise argparse.ArgumentTypeError("seconds must be between 1 and 300")
    return seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="exact server symbol name")
    parser.add_argument("--seconds", type=_seconds, default=60.0, help="sampling duration, 1–300 seconds")
    parser.add_argument("--bar-format", choices=("auto", "standard", "extended"), default="auto")
    parser.add_argument("--env-file", type=Path, help="optional dotenv file; requires python-dotenv")
    args = parser.parse_args()
    if args.env_file:
        try:
            from dotenv import load_dotenv
        except ImportError:
            print(json.dumps({"status": "error", "error": {"stage": "env_file", "type": "MissingPythonDotenv"}}))
            return 1
        if not args.env_file.is_file() or not load_dotenv(args.env_file, override=True):
            print(json.dumps({"status": "error", "error": {"stage": "env_file", "type": "EnvFileUnavailable"}}))
            return 1
    # The client can otherwise log login/session values at INFO when structlog is
    # installed. Force filtering before the first pymt5 import in this process.
    os.environ["PYMT5_LOG_LEVEL"] = "CRITICAL"
    required = ("PYMT5_SERVER", "PYMT5_LOGIN", "PYMT5_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(json.dumps({"status": "error", "error": {"stage": "config", "type": "MissingConfig", "keys": missing}}))
        return 1
    try:
        login = int(os.environ["PYMT5_LOGIN"])
    except ValueError:
        print(json.dumps({"status": "error", "error": {"stage": "config", "type": "InvalidLogin"}}))
        return 1
    report = asyncio.run(
        collect_sample(
            uri=os.environ["PYMT5_SERVER"],
            login=login,
            password=os.environ["PYMT5_PASSWORD"],
            symbol=args.symbol,
            seconds=args.seconds,
            bar_format=args.bar_format,
        )
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "sampled" and report["unsubscribe"] == report["close"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
