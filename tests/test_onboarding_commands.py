from unittest.mock import AsyncMock

from pymt5.client import (
    AccountDocument,
    DemoAccountRequest,
    MT5WebClient,
    OpenAccountResult,
    RealAccountRequest,
    VerificationStatus,
    _parse_open_account_result,
    _parse_verification_status,
)
from pymt5.constants import (
    CMD_INIT,
    CMD_OPEN_DEMO,
    CMD_OPEN_REAL,
    CMD_OTP_SETUP,
    CMD_SEND_VERIFY_CODES,
    CMD_VERIFY_CODE,
    PROP_FIXED_STRING,
    PROP_I64,
    PROP_TIME,
    PROP_U8,
    PROP_U32,
    PROP_U64,
)
from pymt5.protocol import SeriesCodec, get_series_size
from pymt5.transport import CommandResult

OPENING_CID = b"0123456789abcdef"


def _open_account_body(
    code: int = 0,
    login: int = 12345678,
    password: str = "demo-pass",
    investor_password: str = "investor-pass",
) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U32, code),
            (PROP_I64, login),
            (PROP_FIXED_STRING, password, 32),
            (PROP_FIXED_STRING, investor_password, 32),
        ]
    )


def _verification_body(email: bool, phone: bool) -> bytes:
    return SeriesCodec.serialize(
        [
            (PROP_U8, int(email)),
            (PROP_U8, int(phone)),
        ]
    )


def test_parse_verification_status_helper():
    parsed = _parse_verification_status(_verification_body(True, False))
    assert parsed == VerificationStatus(email=True, phone=False)
    assert bool(parsed) is True


def test_parse_open_account_result_helper():
    parsed = _parse_open_account_result(_open_account_body())
    assert parsed == OpenAccountResult(
        code=0,
        login=12345678,
        password="demo-pass",
        investor_password="investor-pass",
    )
    assert parsed.success is True


async def test_request_opening_verification_initializes_and_parses_status():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        side_effect=[
            CommandResult(command=CMD_INIT, code=0, body=b""),
            CommandResult(command=CMD_VERIFY_CODE, code=0, body=_verification_body(True, False)),
        ]
    )
    request = DemoAccountRequest(
        first_name="Ada",
        second_name="Lovelace",
        email="ada@example.com",
        phone="+441234567",
        group="demo-group",
        country="GB",
        city="London",
        deposit=2500.0,
        leverage=200,
        agreements=1,
        utm_campaign="spring",
    )

    status = await client.request_opening_verification(
        request,
        build=4321,
        cid=OPENING_CID,
    )

    assert status == VerificationStatus(email=True, phone=False)
    assert client.transport.send_command.call_count == 2

    init_command, init_payload = client.transport.send_command.call_args_list[0][0]
    assert init_command == CMD_INIT
    assert init_payload[196:212] == OPENING_CID

    verify_command, verify_payload = client.transport.send_command.call_args_list[1][0]
    assert verify_command == CMD_VERIFY_CODE
    assert int.from_bytes(verify_payload[:2], "little", signed=True) == 4321
    assert verify_payload[2:18] == OPENING_CID
    assert verify_payload[18:] == client._build_opening_base_payload(request)


async def test_submit_opening_verification_uses_base_payload():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(
            command=CMD_SEND_VERIFY_CODES,
            code=0,
            body=_verification_body(True, True),
        )
    )
    request = DemoAccountRequest(
        first_name="Ada",
        second_name="Lovelace",
        email_confirm_code=123456,
        phone_confirm_code=654321,
    )

    status = await client.submit_opening_verification(request, cid=OPENING_CID)

    assert status == VerificationStatus(email=True, phone=True)
    command, payload = client.transport.send_command.call_args[0]
    assert command == CMD_SEND_VERIFY_CODES
    assert payload == client._build_opening_base_payload(request)


async def test_open_demo_account_uses_frontend_payload_and_parses_response():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        side_effect=[
            CommandResult(command=CMD_INIT, code=0, body=b""),
            CommandResult(command=CMD_OPEN_DEMO, code=0, body=_open_account_body(login=777)),
        ]
    )
    request = DemoAccountRequest(
        first_name="Ada",
        second_name="Lovelace",
        email="ada@example.com",
        group="demo-group",
        deposit=5000.0,
        leverage=100,
        agreements=1,
    )

    result = await client.open_demo_account(request, cid=OPENING_CID)

    assert result.login == 777
    assert result.password == "demo-pass"
    assert result.investor_password == "investor-pass"

    command, payload = client.transport.send_command.call_args_list[1][0]
    assert command == CMD_OPEN_DEMO
    assert payload == client._build_opening_base_payload(request)
    assert len(payload) == 1664


async def test_open_real_account_serializes_birth_date_and_documents():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        side_effect=[
            CommandResult(command=CMD_INIT, code=0, body=b""),
            CommandResult(command=CMD_OPEN_REAL, code=0, body=_open_account_body(login=999)),
        ]
    )
    document = AccountDocument(
        data_type=1,
        document_type=3,
        front_name="front.jpg",
        front_buffer=b"front-bytes",
        back_name="back.jpg",
        back_buffer=b"back-bytes",
    )
    request = RealAccountRequest(
        first_name="Ada",
        second_name="Lovelace",
        middle_name="Byron",
        email="ada@example.com",
        phone="+441234567",
        group="real-group",
        country="GB",
        city="London",
        address="12 St James",
        language="en",
        citizenship="GB",
        tax_id="ABCD1234",
        birth_date_ms=1710501234567,
        gender=1,
        employment=2,
        industry=3,
        education=4,
        wealth=5,
        annual_income=100000,
        net_worth=200000,
        annual_deposit=50000,
        experience_fx=1,
        experience_cfd=2,
        experience_futures=3,
        experience_stocks=4,
        documents=[document],
    )

    result = await client.open_real_account(request, cid=OPENING_CID)

    assert result.login == 999

    command, payload = client.transport.send_command.call_args_list[1][0]
    assert command == CMD_OPEN_REAL

    base_payload = client._build_opening_base_payload(request)
    assert payload[: len(base_payload)] == base_payload

    extra_schema = [
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_TIME},
        {"propType": PROP_U32},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 64},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_U32},
        {"propType": PROP_U32},
        {"propType": PROP_U32},
        {"propType": PROP_U32},
        {"propType": PROP_U64},
        {"propType": PROP_U64},
        {"propType": PROP_U64},
        {"propType": PROP_U32},
        {"propType": PROP_U32},
        {"propType": PROP_U32},
        {"propType": PROP_U32},
        {"propType": 12, "propLength": 512},
    ]
    extra_offset = len(base_payload)
    extra_size = get_series_size(extra_schema)
    extra_values = SeriesCodec.parse(payload, extra_schema, extra_offset)

    assert extra_values[0] == "Ada"
    assert extra_values[1] == "Lovelace"
    assert extra_values[2] == "Byron"
    assert extra_values[3] == 1710501234567
    assert extra_values[4] == 1
    assert extra_values[5] == "en"
    assert extra_values[6] == "GB"
    assert extra_values[7] == "ABCD1234"
    assert extra_values[12] == 100000
    assert extra_values[13] == 200000
    assert extra_values[14] == 50000
    assert payload[extra_offset + extra_size :] == client._build_document_payload(document)


async def test_enable_otp_builds_expected_payload():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(
            command=CMD_OTP_SETUP,
            code=0,
            body=b"",
        )
    )

    await client.enable_otp(
        login=12345678,
        password="plain-password",
        otp_secret="SECRETKEY",
        otp_secret_check="123456",
        cid=OPENING_CID,
    )

    command, payload = client.transport.send_command.call_args[0]
    assert command == CMD_OTP_SETUP

    otp_schema = [
        {"propType": PROP_U32},
        {"propType": PROP_U64},
        {"propType": PROP_FIXED_STRING, "propLength": 64},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": 12, "propLength": 16},
    ]
    values = SeriesCodec.parse(payload, otp_schema)

    assert values[0] == 5
    assert values[1] == 12345678
    assert values[2] == "plain-password"
    assert values[3] == ""
    assert values[4] == "SECRETKEY"
    assert values[5] == "123456"
    assert values[6] == OPENING_CID


async def test_disable_otp_returns_true_and_sends_otp_code():
    client = MT5WebClient()
    client.transport.send_command = AsyncMock(
        return_value=CommandResult(
            command=CMD_OTP_SETUP,
            code=0,
            body=b"",
        )
    )

    ok = await client.disable_otp(
        login=12345678,
        password="plain-password",
        otp="654321",
        cid=OPENING_CID,
    )

    assert ok is True
    command, payload = client.transport.send_command.call_args[0]
    assert command == CMD_OTP_SETUP

    otp_schema = [
        {"propType": PROP_U32},
        {"propType": PROP_U64},
        {"propType": PROP_FIXED_STRING, "propLength": 64},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": PROP_FIXED_STRING, "propLength": 128},
        {"propType": 12, "propLength": 16},
    ]
    values = SeriesCodec.parse(payload, otp_schema)

    assert values[0] == 5
    assert values[1] == 12345678
    assert values[2] == "plain-password"
    assert values[3] == "654321"
    assert values[4] == ""
    assert values[5] == ""
    assert values[6] == OPENING_CID
