"""Account management mixin for MT5WebClient.

Provides account information retrieval, password management, demo/real
account opening, OTP setup, notifications, and corporate links.
"""

from __future__ import annotations

import logging
import struct
from typing import TYPE_CHECKING, Any, TypeVar

from pymt5._parsers import (
    _parse_account_response,
    _parse_counted_records,
    _parse_open_account_result,
    _parse_verification_status,
)
from pymt5.constants import (
    CMD_CHANGE_PASSWORD,
    CMD_GET_ACCOUNT,
    CMD_GET_CORPORATE_LINKS,
    CMD_NOTIFY,
    CMD_OPEN_DEMO,
    CMD_OPEN_REAL,
    CMD_OTP_SETUP,
    CMD_SEND_VERIFY_CODES,
    CMD_VERIFY_CODE,
    PROP_BYTES,
    PROP_F64,
    PROP_FIXED_STRING,
    PROP_I16,
    PROP_TIME,
    PROP_U32,
    PROP_U64,
)
from pymt5.protocol import SeriesCodec
from pymt5.schemas import (
    CORPORATE_LINK_FIELD_NAMES,
    CORPORATE_LINK_SCHEMA,
)
from pymt5.transport import CommandResult
from pymt5.types import (
    REAL_ACCOUNT_RESERVED_PAYLOAD,
    AccountDocument,
    AccountInfo,
    AccountOpeningRequest,
    DemoAccountRequest,
    OpenAccountResult,
    RealAccountRequest,
    Record,
    RecordList,
    VerificationStatus,
)

if TYPE_CHECKING:
    from pymt5.transport import MT5WebSocketTransport

_T = TypeVar("_T")

logger = logging.getLogger("pymt5.client")

MT5_TERMINAL_VERSION = 500
OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES = {
    5687: "15 Mar 2026",
}


class _AccountMixin:
    """Mixin providing account management methods for MT5WebClient."""

    # Attributes provided by MT5WebClient.__init__ / other mixins
    transport: MT5WebSocketTransport
    _last_error: tuple[int, str]

    if TYPE_CHECKING:

        def _fail_last_error(self, code: int, message: str) -> _T | None: ...
        def _clear_last_error(self) -> None: ...
        def _resolve_client_id(self, cid: bytes | None) -> bytes: ...
        async def init_session(
            self,
            version: int = 0,
            password: str = "",
            otp: str = "",
            cid: bytes | None = None,
        ) -> CommandResult: ...
        def _build_init_payload(
            self,
            *,
            version: int,
            password: str,
            otp: str,
            cid: bytes | None,
        ) -> bytes: ...
        def _build_otp_setup_payload(
            self,
            *,
            login: int,
            password: str,
            otp: str = "",
            otp_secret: str = "",
            otp_secret_check: str = "",
            cid: bytes | None,
        ) -> bytes: ...
        async def get_positions_and_orders(self) -> dict[str, RecordList]: ...

    async def get_account(self) -> Record:
        """Get full account information (cmd=3): balance, equity, margin, leverage, etc.

        Returns a dict with all account fields. This is the proper way to get
        balance/equity/margin information from the Web Terminal.

        The response has a complex multi-section format (header + trade settings).
        """
        result = await self.transport.send_command(CMD_GET_ACCOUNT)
        return _parse_account_response(result.body)

    async def account_info(self) -> Record:
        """Official-style alias for get_account()."""
        return await self.get_account()

    async def terminal_info(self) -> Record:
        """Best-effort terminal/server info derived from the Web account config."""
        account = await self.get_account()
        trade_allowed = bool(account.get("trade_allowed", False))
        return {
            "build": int(account.get("server_build", 0) or 0),
            "company": str(account.get("company", "") or ""),
            "name": str(account.get("server_name", "") or ""),
            "server": str(account.get("server_name", "") or ""),
            "connected": bool(self.transport.is_ready),
            "trade_allowed": trade_allowed,
            "tradeapi_disabled": bool(account.get("is_read_only", False) or not trade_allowed),
            "timezone_shift": int(account.get("timezone_shift", 0) or 0),
            "server_offset_time": int(account.get("server_offset_time", 0) or 0),
            "path": "",
            "data_path": "",
            "commondata_path": "",
        }

    async def version(self) -> tuple[int, int, str] | None:
        """Best-effort official-style terminal version tuple.

        The Web Terminal does not expose a dedicated ``version()`` RPC in the
        current command surface. This compatibility helper combines the
        ``cmd=3`` build field with locally observed public Web Terminal
        release-date metadata. Unknown builds return an empty release-date
        string.
        """
        try:
            account = await self.get_account()
            build = int(account.get("server_build", 0) or 0)
            if build <= 0:
                return self._fail_last_error(-7, "terminal build unavailable for version()")
            self._clear_last_error()
            return (
                MT5_TERMINAL_VERSION,
                build,
                OBSERVED_WEBTERMINAL_BUILD_RELEASE_DATES.get(build, ""),
            )
        except (KeyError, ValueError, TypeError, struct.error) as exc:
            return self._fail_last_error(-99, f"version() failed: {exc}")

    async def get_account_summary(self) -> AccountInfo:
        """Get account summary using get_account (cmd=3) and positions/orders.

        This uses the proper account info command to get balance, equity,
        margin, leverage, etc., and supplements with position/order counts.
        Falls back to computing from positions if cmd=3 fails.
        """
        data = await self.get_positions_and_orders()
        positions = data["positions"]
        orders = data["orders"]
        try:
            acct = await self.get_account()
            if acct:
                return AccountInfo(
                    balance=acct.get("balance", 0.0),
                    equity=acct.get("equity", 0.0),
                    margin=acct.get("margin", 0.0),
                    margin_free=acct.get("margin_free", 0.0),
                    margin_level=acct.get("margin_level", 0.0),
                    profit=acct.get("profit", 0.0),
                    credit=acct.get("credit", 0.0),
                    leverage=int(acct.get("leverage", 0)),
                    currency=acct.get("currency", ""),
                    server=acct.get("server", ""),
                    positions_count=len(positions),
                    orders_count=len(orders),
                )
        except (KeyError, ValueError, TypeError, struct.error, RuntimeError) as exc:
            logger.debug("get_account failed, falling back to positions: %s", exc)
        floating_profit = sum(p.get("profit", 0.0) for p in positions)
        floating_commission = sum(p.get("commission", 0.0) for p in positions)
        floating_swap = sum(p.get("storage", 0.0) for p in positions)
        total_profit = floating_profit + floating_commission + floating_swap
        return AccountInfo(
            profit=total_profit,
            positions_count=len(positions),
            orders_count=len(orders),
        )

    async def change_password(self, new_password: str, old_password: str, is_investor: bool = False) -> int:
        payload = SeriesCodec.serialize(
            [
                (4, int(is_investor)),
                (PROP_FIXED_STRING, (new_password or "")[:32], 64),
                (PROP_FIXED_STRING, (old_password or "")[:32], 64),
            ]
        )
        result = await self.transport.send_command(CMD_CHANGE_PASSWORD, payload)
        return int.from_bytes(result.body[:4], "little", signed=True)

    async def trader_params(self) -> tuple[str, str]:
        from pymt5.constants import CMD_TRADER_PARAMS
        from pymt5.types import TRADER_PARAMS_SCHEMA

        result = await self.transport.send_command(CMD_TRADER_PARAMS)
        first, second = SeriesCodec.parse(result.body, TRADER_PARAMS_SCHEMA)
        return str(first), str(second)

    async def open_demo(
        self,
        *,
        password: str = "",
        otp: str = "",
        cid: bytes | None = None,
        version: int = 0,
    ) -> CommandResult:
        """Request demo account creation (cmd=30).

        Returns:
            Raw CommandResult with server response (account details).
        """
        payload = self._build_init_payload(
            version=version,
            password=password,
            otp=otp,
            cid=cid,
        )
        return await self.transport.send_command(CMD_OPEN_DEMO, payload)

    async def open_demo_account(
        self,
        request: DemoAccountRequest,
        *,
        cid: bytes | None = None,
        initialize: bool = True,
    ) -> OpenAccountResult:
        """Open a demo account using the current frontend registration payload (cmd=30)."""
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_opening_base_payload(request)
        result = await self.transport.send_command(CMD_OPEN_DEMO, payload)
        return _parse_open_account_result(result.body)

    async def open_real_account(
        self,
        request: RealAccountRequest,
        *,
        cid: bytes | None = None,
        initialize: bool = True,
    ) -> OpenAccountResult:
        """Open a real account using the current frontend onboarding payload (cmd=39)."""
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_real_account_payload(request)
        result = await self.transport.send_command(CMD_OPEN_REAL, payload)
        return _parse_open_account_result(result.body)

    async def verify_code(self, code: str) -> CommandResult:
        """Send a verification code (cmd=27), e.g. for two-factor authentication.

        Args:
            code: The verification/OTP code string.

        Returns:
            Raw CommandResult with server response.
        """
        payload = SeriesCodec.serialize(
            [
                (PROP_FIXED_STRING, code[:32], 64),
            ]
        )
        return await self.transport.send_command(CMD_VERIFY_CODE, payload)

    async def request_opening_verification(
        self,
        request: AccountOpeningRequest,
        *,
        build: int = 0,
        cid: bytes | None = None,
        initialize: bool = True,
    ) -> VerificationStatus:
        """Request email/SMS verification requirements for account opening (cmd=27).

        The current frontend passes the Web Terminal build number in the first
        field. When that value is unavailable, ``build=0`` is a safe fallback.
        """
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_opening_verification_payload(
            request=request,
            build=build,
            cid=client_id,
        )
        result = await self.transport.send_command(CMD_VERIFY_CODE, payload)
        return _parse_verification_status(result.body)

    async def submit_opening_verification(
        self,
        request: AccountOpeningRequest,
        *,
        cid: bytes | None = None,
        initialize: bool = False,
    ) -> VerificationStatus:
        """Submit email/SMS verification codes for account opening (cmd=40)."""
        client_id = self._resolve_client_id(cid)
        if initialize:
            await self.init_session(cid=client_id)
        payload = self._build_opening_base_payload(request)
        result = await self.transport.send_command(CMD_SEND_VERIFY_CODES, payload)
        return _parse_verification_status(result.body)

    async def enable_otp(
        self,
        login: int,
        password: str,
        *,
        otp_secret: str,
        otp_secret_check: str,
        cid: bytes | None = None,
    ) -> CommandResult:
        """Enable/configure TOTP for an account via cmd=43."""
        payload = self._build_otp_setup_payload(
            login=login,
            password=password,
            otp_secret=otp_secret,
            otp_secret_check=otp_secret_check,
            cid=cid,
        )
        return await self.transport.send_command(CMD_OTP_SETUP, payload)

    async def disable_otp(
        self,
        login: int,
        password: str,
        *,
        otp: str,
        cid: bytes | None = None,
    ) -> bool:
        """Disable TOTP for an account via cmd=43.

        The current frontend treats any non-error response as success.
        """
        payload = self._build_otp_setup_payload(
            login=login,
            password=password,
            otp=otp,
            cid=cid,
        )
        await self.transport.send_command(CMD_OTP_SETUP, payload)
        return True

    async def send_notification(self, message: str) -> CommandResult:
        """Send a notification message to the server (cmd=42).

        Args:
            message: Notification text.

        Returns:
            Raw CommandResult with server acknowledgement.
        """
        payload = SeriesCodec.serialize(
            [
                (PROP_FIXED_STRING, message[:128], 256),
            ]
        )
        return await self.transport.send_command(CMD_NOTIFY, payload)

    async def get_corporate_links(self) -> RecordList:
        """Get broker corporate links (cmd=44): support, education, social, etc.

        Returns list of dicts with keys: link_type, url, label, flags, icon_data.
        """
        result = await self.transport.send_command(CMD_GET_CORPORATE_LINKS)
        return _parse_counted_records(result.body, CORPORATE_LINK_SCHEMA, CORPORATE_LINK_FIELD_NAMES)

    def _build_opening_base_payload(self, request: AccountOpeningRequest) -> bytes:
        full_name = " ".join(part for part in (request.first_name, request.second_name) if part)
        fields: list[tuple[Any, ...]] = [
            (PROP_FIXED_STRING, full_name[:128], 256),
            (PROP_FIXED_STRING, (request.group or "")[:64], 128),
            (PROP_FIXED_STRING, (request.phone_password or "")[:32], 64),
            (PROP_FIXED_STRING, (request.country or "")[:32], 64),
            (PROP_FIXED_STRING, (request.city or "")[:32], 64),
            (PROP_FIXED_STRING, (request.state or "")[:32], 64),
            (PROP_FIXED_STRING, (request.zipcode or "")[:16], 32),
            (PROP_FIXED_STRING, (request.address or "")[:128], 256),
            (PROP_FIXED_STRING, (request.phone or "")[:32], 64),
            (PROP_FIXED_STRING, (request.email or "")[:64], 128),
            (PROP_F64, float(request.deposit or 0.0)),
            (PROP_U32, int(request.leverage or 0)),
            (PROP_U32, 0),
            (PROP_U32, 1),
            (PROP_FIXED_STRING, (request.domain or "")[:64], 128),
            (PROP_FIXED_STRING, (request.utm_campaign or "")[:32], 64),
            (PROP_FIXED_STRING, (request.utm_source or "")[:32], 64),
            (PROP_U32, int(request.email_confirm_code or 0)),
            (PROP_U32, int(request.phone_confirm_code or 0)),
            (PROP_FIXED_STRING, (request.first_name or "")[:64], 128),
            (PROP_FIXED_STRING, (request.second_name or "")[:64], 128),
            (PROP_U32, int(request.agreements or 0)),
        ]
        return SeriesCodec.serialize(fields)

    def _build_document_payload(self, document: AccountDocument) -> bytes:
        front_buffer = self._coerce_bytes(document.front_buffer)
        back_buffer = self._coerce_bytes(document.back_buffer)
        fields: list[tuple[Any, ...]] = [
            (PROP_U32, int(document.data_type)),
            (PROP_U32, int(document.document_type)),
            (PROP_FIXED_STRING, (document.front_name or "")[:260], 520),
            (PROP_U32, len(front_buffer)),
            (PROP_FIXED_STRING, (document.back_name or "")[:260], 520),
            (PROP_U32, len(back_buffer)),
            (PROP_BYTES, front_buffer),
            (PROP_BYTES, back_buffer),
        ]
        return SeriesCodec.serialize(fields)

    def _build_opening_verification_payload(
        self,
        *,
        request: AccountOpeningRequest,
        build: int,
        cid: bytes | None,
    ) -> bytes:
        client_id = self._resolve_client_id(cid)
        base_payload = self._build_opening_base_payload(request)
        fields: list[tuple[Any, ...]] = [
            (PROP_I16, int(build or 0)),
            (PROP_BYTES, client_id, 16),
            (PROP_BYTES, base_payload),
        ]
        return SeriesCodec.serialize(fields)

    def _build_real_account_payload(self, request: RealAccountRequest) -> bytes:
        base_payload = self._build_opening_base_payload(request)
        extra_fields: list[tuple[Any, ...]] = [
            (PROP_FIXED_STRING, (request.first_name or "")[:64], 128),
            (PROP_FIXED_STRING, (request.second_name or "")[:64], 128),
            (PROP_FIXED_STRING, (request.middle_name or "")[:64], 128),
            (PROP_TIME, int(request.birth_date_ms or 0)),
            (PROP_U32, int(request.gender or 0)),
            (PROP_FIXED_STRING, (request.language or "")[:64], 128),
            (PROP_FIXED_STRING, (request.citizenship or "")[:32], 64),
            (PROP_FIXED_STRING, (request.tax_id or "")[:64], 128),
            (PROP_U32, int(request.employment or 0)),
            (PROP_U32, int(request.industry or 0)),
            (PROP_U32, int(request.education or 0)),
            (PROP_U32, int(request.wealth or 0)),
            (PROP_U64, int(request.annual_income or 0)),
            (PROP_U64, int(request.net_worth or 0)),
            (PROP_U64, int(request.annual_deposit or 0)),
            (PROP_U32, int(request.experience_fx or 0)),
            (PROP_U32, int(request.experience_cfd or 0)),
            (PROP_U32, int(request.experience_futures or 0)),
            (PROP_U32, int(request.experience_stocks or 0)),
            (PROP_BYTES, REAL_ACCOUNT_RESERVED_PAYLOAD, 512),
        ]
        extra_payload = SeriesCodec.serialize(extra_fields)
        document_payload = b"".join(self._build_document_payload(doc) for doc in request.documents)
        return base_payload + extra_payload + document_payload

    @staticmethod
    def _split_password_blob(password: str) -> tuple[str, bytes | None]:
        if password and len(password) == 320:
            return "", bytes.fromhex(password)
        return (password or "")[:32], None

    @staticmethod
    def _coerce_bytes(value: bytes | bytearray | memoryview) -> bytes:
        return bytes(value)
