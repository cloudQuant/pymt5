"""Tests for explicit and ambiguous historical rate bar formats."""

import struct
from unittest.mock import AsyncMock

import pytest

from pymt5 import MT5WebClient
from pymt5._parsers import _parse_rate_bars
from pymt5.constants import CMD_GET_RATES
from pymt5.exceptions import ProtocolError
from pymt5.transport import CommandResult


def make_bars(times: list[int], *, extended: bool) -> bytes:
    fields = [(t, 1.15, 1.16, 1.14, 1.155, 100, 5) for t in times]
    if extended:
        return b"".join(struct.pack("<iddddqiq", *bar, 50000) for bar in fields)
    return b"".join(struct.pack("<iddddqi", *bar) for bar in fields)


@pytest.mark.parametrize("extended,count", [(False, 7), (True, 6)])
@pytest.mark.parametrize("step", [14400, -14400, 0])
def test_ambiguous_bars_require_explicit_format(extended: bool, count: int, step: int) -> None:
    times = [1773293460 + step * i for i in range(count)]
    body = make_bars(times, extended=extended)
    assert len(body) == 336
    with pytest.raises(ProtocolError, match="ambiguous"):
        _parse_rate_bars(body)
    bars = _parse_rate_bars(body, "extended" if extended else "standard")
    assert [bar["time"] for bar in bars] == times
    assert all(("real_volume" in bar) == extended for bar in bars)


@pytest.mark.parametrize("extended", [False, True])
def test_unambiguous_bars_auto_detect(extended: bool) -> None:
    body = make_bars([1773293460], extended=extended)
    bars = _parse_rate_bars(body)
    assert len(bars) == 1
    assert ("real_volume" in bars[0]) == extended


@pytest.mark.parametrize("bar_format", ["auto", "standard", "extended"])
def test_invalid_body_and_empty_body(bar_format: str) -> None:
    with pytest.raises(ProtocolError):
        _parse_rate_bars(b"\x00" * 49, bar_format)
    assert _parse_rate_bars(b"", bar_format) == []
    assert _parse_rate_bars(None, bar_format) == []


def test_invalid_format_rejected() -> None:
    with pytest.raises(ValueError, match="bar_format"):
        MT5WebClient(rate_bar_format="bad")
    with pytest.raises(ValueError, match="bar_format"):
        _parse_rate_bars(b"", "bad")


@pytest.mark.parametrize("configured", ["auto", "standard", "extended"])
async def test_get_rates_uses_configuration_and_override(configured: str) -> None:
    body = make_bars([1773293460 + i * 60 for i in range(7)], extended=False)
    client = MT5WebClient(rate_bar_format=configured)
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_GET_RATES, code=0, body=body))
    if configured == "auto":
        with pytest.raises(ProtocolError, match="ambiguous"):
            await client.get_rates("EURUSD", 1, 0, 1)
    elif configured == "standard":
        assert len(await client.get_rates("EURUSD", 1, 0, 1)) == 7
    else:
        assert len(await client.get_rates("EURUSD", 1, 0, 1)) == 6
    assert len(await client.get_rates("EURUSD", 1, 0, 1, bar_format="standard")) == 7
    calls = client.transport.send_command.await_count
    with pytest.raises(ValueError, match="bar_format"):
        await client.get_rates("EURUSD", 1, 0, 1, bar_format="bad")
    assert client.transport.send_command.await_count == calls


@pytest.mark.parametrize("method", ["copy_rates_range", "copy_rates_from", "copy_rates_from_pos"])
async def test_copy_rates_forwards_override(method: str) -> None:
    client = MT5WebClient()
    client.get_rates = AsyncMock(return_value=[{"time": 1}])
    result = await getattr(client, method)("EURUSD", 1, 0, 1, bar_format="extended")
    assert result == [{"time": 1}]
    assert client.get_rates.await_args.kwargs == {"bar_format": "extended"}


@pytest.mark.parametrize("method", ["get_rates", "get_rates_raw"])
@pytest.mark.parametrize("body", [b"", make_bars([1773293460], extended=False)])
async def test_rates_reject_nonzero_response_before_using_body(method: str, body: bytes) -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_GET_RATES, code=3, body=body))
    with pytest.raises(ProtocolError, match="response code 3"):
        await getattr(client, method)("EURUSD", 1, 0, 1)


@pytest.mark.parametrize(("method", "expected"), [("get_rates", []), ("get_rates_raw", b"")])
async def test_rates_successful_empty_response(method: str, expected: object) -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_GET_RATES, code=0, body=b""))
    assert await getattr(client, method)("EURUSD", 1, 0, 1) == expected


@pytest.mark.parametrize("method", ["copy_rates_from", "copy_rates_from_pos"])
@pytest.mark.parametrize("count", [0, -1])
async def test_copy_rates_empty_count_still_validates_override(method: str, count: int) -> None:
    client = MT5WebClient()
    client.transport.send_command = AsyncMock()
    with pytest.raises(ValueError, match="bar_format"):
        await getattr(client, method)("EURUSD", 1, 0, count, bar_format="bad")
    client.transport.send_command.assert_not_awaited()
