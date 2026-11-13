"""Trade profile tests using the actual serialized command payload."""

import struct
from unittest.mock import AsyncMock

import pytest

from pymt5 import TRADE_ACTION_MARKET_DEAL, MT5WebClient
from pymt5.constants import (
    CMD_TRADE_REQUEST,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
)
from pymt5.transport import CommandResult


def make_client(profile: str = "legacy") -> MT5WebClient:
    client = MT5WebClient(trade_protocol=profile)
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=b""))
    return client


def payload_for(client: MT5WebClient) -> bytes:
    command, payload = client.transport.send_command.await_args.args
    assert command == CMD_TRADE_REQUEST
    return payload


@pytest.mark.parametrize(("profile", "size", "shift"), [("legacy", 236, 0), ("build_6090", 248, 12)])
async def test_trade_request_layout_and_fields(profile: str, size: int, shift: int) -> None:
    client = make_client(profile)
    await client.trade_request(
        action_id=1234,
        trade_action=TRADE_ACTION_MARKET_DEAL,
        symbol="EURUSD",
        volume=12_345_678,
        digits=5,
        type_flags=7,
        deviation=17,
        comment="trade-备注",
        position_id=0x1122334455667788,
        position_by=0x1020304050607080,
        time_expiration=1_700_000_000,
    )
    payload = payload_for(client)
    assert len(payload) == size
    assert struct.unpack_from("<II", payload) == (1234, TRADE_ACTION_MARKET_DEAL)
    assert struct.unpack_from("<Q", payload, 72)[0] == 12_345_678
    assert struct.unpack_from("<I", payload, 104)[0] == 7
    assert struct.unpack_from("<Q", payload, 144)[0] == 17
    if shift:
        assert payload[152:164] == b"\0" * 12
    comment = "trade-备注".encode("utf-16-le")
    assert payload[152 + shift : 216 + shift] == comment.ljust(64, b"\0")
    assert struct.unpack_from("<Q", payload, 216 + shift)[0] == 0x1122334455667788
    assert struct.unpack_from("<Q", payload, 224 + shift)[0] == 0x1020304050607080
    assert struct.unpack_from("<I", payload, 232 + shift)[0] == 1_700_000_000


async def test_default_is_legacy_even_if_handshake_build_is_6090() -> None:
    client = MT5WebClient()
    client.transport._server_build = 6090
    client.transport.send_command = AsyncMock(return_value=CommandResult(command=CMD_TRADE_REQUEST, code=0, body=b""))
    await client.buy_market("EURUSD", 0.01)
    payload = payload_for(client)
    assert len(payload) == 236
    assert struct.unpack_from("<I", payload, 4)[0] == TRADE_ACTION_DEAL


@pytest.mark.parametrize("invalid", ["", "auto", "6090", "BUILD_6090", None])
def test_invalid_profile_rejected(invalid: str | None) -> None:
    with pytest.raises(ValueError, match="trade_protocol"):
        MT5WebClient(trade_protocol=invalid)


@pytest.mark.parametrize(("profile", "action", "shift"), [("legacy", 1, 0), ("build_6090", 3, 12)])
@pytest.mark.parametrize(("method", "side"), [("buy_market", ORDER_TYPE_BUY), ("sell_market", ORDER_TYPE_SELL)])
async def test_market_helpers_use_profile(profile: str, action: int, shift: int, method: str, side: int) -> None:
    client = make_client(profile)
    await getattr(client, method)("EURUSD", 0.01, comment="test")
    payload = payload_for(client)
    assert len(payload) == 236 + shift
    assert struct.unpack_from("<I", payload, 4)[0] == action
    assert struct.unpack_from("<I", payload, 92)[0] == side
    assert struct.unpack_from("<I", payload, 0)[0] == 0
    assert struct.unpack_from("<I", payload, 104)[0] == 0


@pytest.mark.parametrize(("profile", "action", "shift"), [("legacy", 1, 0), ("build_6090", 3, 12)])
async def test_close_and_other_actions(profile: str, action: int, shift: int) -> None:
    client = make_client(profile)
    await client.close_position("EURUSD", 54321, 0.1, order_type=ORDER_TYPE_SELL)
    payload = payload_for(client)
    assert struct.unpack_from("<I", payload, 4)[0] == action
    assert struct.unpack_from("<I", payload, 92)[0] == ORDER_TYPE_SELL
    assert struct.unpack_from("<Q", payload, 216 + shift)[0] == 54321
    await client.buy_limit("EURUSD", 0.1, 1.1)
    assert struct.unpack_from("<I", payload_for(client), 4)[0] == TRADE_ACTION_PENDING
    await client.cancel_pending_order(1234)
    assert struct.unpack_from("<I", payload_for(client), 4)[0] == TRADE_ACTION_REMOVE
    await client.modify_pending_order("EURUSD", 1234, 1.1)
    assert struct.unpack_from("<I", payload_for(client), 4)[0] == TRADE_ACTION_MODIFY
    await client.modify_position_sltp("EURUSD", 54321, sl=1.0)
    assert struct.unpack_from("<I", payload_for(client), 4)[0] == TRADE_ACTION_SLTP


@pytest.mark.parametrize(
    ("profile", "action"), [("legacy", TRADE_ACTION_DEAL), ("build_6090", TRADE_ACTION_MARKET_DEAL)]
)
async def test_close_position_uses_opposite_side_on_matching_symbol(profile: str, action: int) -> None:
    client = make_client(profile)
    client.get_positions = AsyncMock(return_value=[{"position_id": 1234, "trade_symbol": "EURUSD", "trade_action": 1}])
    await client.close_position("EURUSD", 1234, 0.1)
    payload = payload_for(client)
    assert struct.unpack_from("<I", payload, 4)[0] == action
    assert struct.unpack_from("<I", payload, 92)[0] == ORDER_TYPE_BUY


@pytest.mark.parametrize("profile", ["legacy", "build_6090"])
@pytest.mark.parametrize("method", ["buy_market", "sell_market", "close_position"])
async def test_market_helpers_preserve_action_id_and_flags(profile: str, method: str) -> None:
    client = make_client(profile)
    if method == "close_position":
        await client.close_position("EURUSD", 1234, 0.1, order_type=ORDER_TYPE_SELL, action_id=1001, type_flags=2)
    else:
        await getattr(client, method)("EURUSD", 0.1, action_id=1001, type_flags=2)
    payload = payload_for(client)
    assert struct.unpack_from("<I", payload, 0)[0] == 1001
    assert struct.unpack_from("<I", payload, 104)[0] == 2


@pytest.mark.parametrize("profile", ["legacy", "build_6090"])
@pytest.mark.parametrize("position_id", [0, -1])
async def test_close_position_rejects_invalid_id_without_sending(profile: str, position_id: int) -> None:
    client = make_client(profile)
    client.get_positions = AsyncMock()
    with pytest.raises(ValueError, match="position_id must be > 0"):
        await client.close_position("EURUSD", position_id, 0.1)
    client.get_positions.assert_not_awaited()
    client.transport.send_command.assert_not_awaited()


@pytest.mark.parametrize("profile", ["legacy", "build_6090"])
@pytest.mark.parametrize(
    ("positions", "failure"),
    [
        ([], None),
        ([{"position_id": 1234, "trade_symbol": "EURUSD"}], None),
        ([{"position_id": 1234, "trade_symbol": "EURUSD", "trade_action": 17}], None),
        (None, RuntimeError("position query failed")),
    ],
)
async def test_close_position_fails_safely_if_direction_unknown(profile: str, positions, failure) -> None:
    client = make_client(profile)
    client.get_positions = AsyncMock(return_value=positions, side_effect=failure)
    with pytest.raises(ValueError):
        await client.close_position("EURUSD", 1234, 0.1)
    client.transport.send_command.assert_not_awaited()


@pytest.mark.parametrize("profile", ["legacy", "build_6090"])
@pytest.mark.parametrize("position_symbol", [None, "", "GBPUSD"])
async def test_close_position_rejects_missing_or_mismatched_symbol(profile: str, position_symbol: str | None) -> None:
    client = make_client(profile)
    position = {"position_id": 1234, "trade_action": 1}
    if position_symbol is not None:
        position["trade_symbol"] = position_symbol
    client.get_positions = AsyncMock(return_value=[position])
    with pytest.raises(ValueError, match="symbol mismatch"):
        await client.close_position("EURUSD", 1234, 0.1)
    client.transport.send_command.assert_not_awaited()


@pytest.mark.parametrize("profile", ["legacy", "build_6090"])
@pytest.mark.parametrize("action", [TRADE_ACTION_DEAL, TRADE_ACTION_MARKET_DEAL])
async def test_order_send_preserves_explicit_action_and_flags(profile: str, action: int) -> None:
    client = make_client(profile)
    await client.order_send(
        {"action": action, "symbol": "EURUSD", "volume": 0.1, "type": ORDER_TYPE_BUY, "action_id": 42, "type_flags": 9}
    )
    payload = payload_for(client)
    assert struct.unpack_from("<II", payload) == (42, action)
    assert struct.unpack_from("<I", payload, 104)[0] == 9


@pytest.mark.parametrize("profile", ["legacy", "build_6090"])
async def test_market_action_requires_positive_volume(profile: str) -> None:
    client = make_client(profile)
    with pytest.raises(ValueError, match="volume must be > 0"):
        await client.trade_request(trade_action=TRADE_ACTION_MARKET_DEAL, volume=0)
    with pytest.raises(ValueError):
        await client.buy_market("EURUSD", 0)
    client.transport.send_command.assert_not_awaited()


@pytest.mark.parametrize("action", [TRADE_ACTION_DEAL, TRADE_ACTION_MARKET_DEAL])
@pytest.mark.parametrize(
    ("volume", "margin", "retcode"),
    [(0.1, 100.0, 0), (0.005, 100.0, TRADE_RETCODE_INVALID_VOLUME), (0.1, 1000.0, TRADE_RETCODE_NO_MONEY)],
)
async def test_order_check_market_action_validation_and_margin(action: int, volume: float, margin: float, retcode: int):
    client = make_client("build_6090")
    client.symbol_info = AsyncMock(
        return_value={
            "trade_mode": 4,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "point": 0.0001,
        }
    )
    client.get_account = AsyncMock(return_value={"balance": 1000.0, "equity": 1000.0, "margin": 50.0})
    client.order_calc_margin = AsyncMock(return_value=margin)
    result = await client.order_check(
        {"action": action, "symbol": "EURUSD", "volume": volume, "type": ORDER_TYPE_BUY, "price": 1.1}
    )
    assert result["retcode"] == retcode
    if retcode == TRADE_RETCODE_INVALID_VOLUME:
        client.order_calc_margin.assert_not_awaited()
    else:
        client.order_calc_margin.assert_awaited_once_with(ORDER_TYPE_BUY, "EURUSD", volume, 1.1)
        assert result["margin"] == pytest.approx(50.0 + margin)
