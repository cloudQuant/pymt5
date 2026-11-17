"""Regression tests for Iteration 07 Group 5: CI/CD hardening, packaging, scripts, and examples."""

from __future__ import annotations

import ast
import importlib.util
import logging
import os
import re
import struct
import subprocess
import sys
import tomllib
import zlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from pymt5.constants import (
    CMD_BOOTSTRAP,
    CMD_LOGIN,
    CMD_PING,
    POSITION_TYPE_BUY,
    POSITION_TYPE_SELL,
    PROP_FIXED_STRING,
    PROP_U16,
    PROP_U32,
)
from pymt5.crypto import AESCipher, initial_cipher, initial_key_bytes
from pymt5.exceptions import MT5ConnectionError
from pymt5.protocol import SeriesCodec, pack_outer, unpack_outer
from pymt5.schemas import SYMBOL_BASIC_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


def _load_module(rel_path: str, monkeypatch: pytest.MonkeyPatch, module_name: str) -> ModuleType:
    """Load a script or example module in isolation without leaking env or sys.path."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("PYMT5_SERVER", "wss://demo.invalid/terminal")
    monkeypatch.setenv("PYMT5_LOGIN", "5047785364")
    monkeypatch.setenv("PYMT5_PASSWORD", "test-password-only")
    full_path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def _encode_response(cipher: AESCipher, command: int, code: int, body: bytes = b"") -> bytes:
    """Build an encrypted outer MT5 response packet."""
    inner = b"\x00\x00" + struct.pack("<HB", command, code) + body
    return pack_outer(cipher.encrypt(inner))


def _encode_basic_symbols(records: list[dict[str, Any]]) -> bytes:
    """Serialize symbol records using SYMBOL_BASIC_SCHEMA prefixed with uint32 count."""
    chunks = [struct.pack("<I", len(records))]
    for rec in records:
        chunks.append(
            SeriesCodec.serialize(
                [
                    (PROP_FIXED_STRING, rec["trade_symbol"], 64),
                    (PROP_FIXED_STRING, rec.get("symbol_description", ""), 128),
                    (PROP_U32, rec.get("digits", 5)),
                    (PROP_U32, rec["symbol_id"]),
                    (PROP_FIXED_STRING, rec.get("symbol_path", "Forex\\EURUSD"), 256),
                    (PROP_U32, rec.get("trade_calc_mode", 0)),
                    (PROP_FIXED_STRING, rec.get("basis", ""), 64),
                    (PROP_U16, rec.get("sector", 0)),
                ]
            )
        )
    assert len(SYMBOL_BASIC_SCHEMA) == 8
    return b"".join(chunks)


def _strip_yaml_comment(line: str) -> str:
    """Strip inline YAML comments outside single/double quotes."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (idx == 0 or line[idx - 1].isspace()):
            return line[:idx].rstrip()
    return line.rstrip()


def _extract_yaml_block(lines: list[str], key: str, indent: int) -> list[str]:
    """Return indented child lines under a mapping key at the given indentation."""
    prefix = " " * indent + f"{key}:"
    start = None
    for idx, raw in enumerate(lines):
        cleaned = _strip_yaml_comment(raw)
        if cleaned == prefix or cleaned.startswith(prefix + " "):
            start = idx + 1
            break
    if start is None:
        return []
    block: list[str] = []
    for raw in lines[start:]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            block.append(raw)
            continue
        cur_indent = len(raw) - len(raw.lstrip(" "))
        if cur_indent <= indent:
            break
        block.append(raw)
    return block


def _parse_kv_block(block: list[str], expected_indent: int) -> dict[str, str]:
    """Parse simple `key: value` entries at a fixed indentation level."""
    result: dict[str, str] = {}
    for raw in block:
        cleaned = _strip_yaml_comment(raw)
        if not cleaned.strip():
            continue
        cur_indent = len(cleaned) - len(cleaned.lstrip(" "))
        if cur_indent == expected_indent and ":" in cleaned:
            k, v = cleaned.strip().split(":", 1)
            result[k.strip()] = v.strip()
    return result


def _parse_workflow_steps(job_block: list[str], steps_indent: int = 4) -> list[dict[str, Any]]:
    """Parse GitHub Actions `steps:` list into dicts with name, uses, env, and run."""
    steps_block = _extract_yaml_block(job_block, "steps", steps_indent)
    item_indent = steps_indent + 2
    step_chunks: list[list[str]] = []
    current: list[str] | None = None
    for raw in steps_block:
        if not raw.strip() or raw.lstrip().startswith("#"):
            if current is not None:
                current.append(raw)
            continue
        cur_indent = len(raw) - len(raw.lstrip(" "))
        if cur_indent == item_indent and raw.lstrip().startswith("- "):
            if current is not None:
                step_chunks.append(current)
            current = [" " * (item_indent + 2) + raw.lstrip()[2:]]
        elif current is not None:
            current.append(raw)
    if current is not None:
        step_chunks.append(current)

    parsed: list[dict[str, Any]] = []
    field_indent = item_indent + 2
    for chunk in step_chunks:
        kv = _parse_kv_block(chunk, field_indent)
        env_block = _extract_yaml_block(chunk, "env", field_indent)
        env_map = _parse_kv_block(env_block, field_indent + 2)
        run_lines: list[str] = []
        run_inline = kv.get("run", "")
        if run_inline and run_inline not in ("|", "|-", ">", ">-"):
            run_lines.append(run_inline)
        else:
            run_lines.extend(_extract_yaml_block(chunk, "run", field_indent))
        parsed.append(
            {
                "name": kv.get("name", ""),
                "uses": kv.get("uses", ""),
                "env": env_map,
                "run": "\n".join(run_lines),
            }
        )
    return parsed


# ---------------------------------------------------------------------------
# 29.2 (S6-01, S6-12): Remove token / AES key / login body hex dumps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "scripts/live_test.py",
        "scripts/live_test_client.py",
        "scripts/live_test_v4.py",
    ],
)
def test_live_scripts_have_no_token_or_key_hex_dumps_in_source(rel_path: str) -> None:
    """S6-01 / S6-12: scripts must not log token, AES key, or login body hex dumps."""
    source = (ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "hex_dump" not in source
    secret_vars = {"token_hex", "new_token", "new_key_material", "key_material", "init_key"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "hex":
            pytest.fail(f"{rel_path} still calls .hex() at line {node.lineno}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "log"
        ):
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name) and sub.id in secret_vars:
                        # Only boolean/len metadata of secret vars may be logged
                        parent_ok = any(
                            isinstance(p, ast.Call)
                            and isinstance(p.func, ast.Name)
                            and p.func.id in {"bool", "len"}
                            and any(child is sub for child in ast.walk(p))
                            for p in ast.walk(arg)
                        )
                        if not parent_ok:
                            pytest.fail(f"{rel_path} logs raw secret variable {sub.id} at line {node.lineno}")


def test_disposable_demo_creds_preserved_in_v2_v3_v4() -> None:
    """29.2: accepted disposable demo account constants remain intact in v2/v3/v4."""
    for rel_path in ("scripts/live_test_v2.py", "scripts/live_test_v3.py", "scripts/live_test_v4.py"):
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "LOGIN_ID = 5047785364" in text
        assert 'PASSWORD = "NyCh-i4r"' in text


async def test_live_test_logs_no_secret_hex_material(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S6-12: scripts/live_test.py step1/step2/step_ping must not emit token/key/login hex."""
    import websockets

    mod = _load_module("scripts/live_test.py", monkeypatch, "test_mod_live_test")
    secret_token = bytes.fromhex("de" * 32 + "ad" * 32)
    secret_key = bytes.fromhex("ca" * 16 + "fe" * 16)
    secret_login_body = bytes.fromhex("be" * 24 + "ef" * 24)

    init_cipher = initial_cipher()
    session_cipher = AESCipher(secret_key)

    bootstrap_resp = _encode_response(init_cipher, CMD_BOOTSTRAP, 0, b"\x01\x00" + secret_token + secret_key)
    ping_resp = _encode_response(session_cipher, CMD_PING, 0, b"")
    login_resp = _encode_response(session_cipher, CMD_LOGIN, 0, secret_login_body)

    class FakeWS:
        def __init__(self) -> None:
            self._responses = iter([bootstrap_resp, ping_resp, login_resp])
            self.sent: list[bytes] = []
            self.closed = False

        async def send(self, data: bytes) -> None:
            self.sent.append(data)

        async def recv(self) -> bytes:
            return next(self._responses)

        async def close(self) -> None:
            self.closed = True

    fake_ws = FakeWS()

    async def fake_connect(*_args: Any, **_kwargs: Any) -> FakeWS:
        return fake_ws

    monkeypatch.setattr(websockets, "connect", fake_connect)
    caplog.set_level(logging.INFO)

    await mod.main()

    assert fake_ws.closed is True
    log_text = caplog.text.lower()
    assert secret_token.hex() not in log_text
    assert secret_token[:16].hex() not in log_text
    assert secret_key.hex() not in log_text
    assert secret_key[:16].hex() not in log_text
    assert initial_key_bytes().hex() not in log_text
    assert secret_login_body.hex() not in log_text
    assert "token_len=64" in log_text
    assert "key_len=32" in log_text


async def test_live_test_client_and_v4_do_not_log_token_hex(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S6-01: live_test_client.py and live_test_v4.py must not log token_hex slices."""
    secret_token_hex = "9f8e7d6c5b4a3928" * 8

    for rel_path, name in (
        ("scripts/live_test_client.py", "test_mod_live_client_29_2"),
        ("scripts/live_test_v4.py", "test_mod_live_v4_29_2"),
    ):
        caplog.clear()
        caplog.set_level(logging.INFO)
        mod = _load_module(rel_path, monkeypatch, name)

        class FakeClient:
            def __init__(self, **_kwargs: Any) -> None:
                self.closed = False

            async def connect(self) -> None:
                return None

            async def login(self, **_kwargs: Any) -> tuple[str, int]:
                return secret_token_hex, 4242

            async def ping(self) -> float:
                return 1.0

            async def get_symbols(self) -> list[dict[str, Any]]:
                return []

            async def get_positions_and_orders(self) -> dict[str, list[dict[str, Any]]]:
                return {"positions": [], "orders": []}

            async def get_rates(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
                return []

            async def get_trade_history(self, **_kwargs: Any) -> dict[str, list[dict[str, Any]]]:
                return {"deals": [], "orders": []}

            def on_tick(self, _cb: Any) -> None:
                return None

            async def subscribe_ticks(self, _ids: list[int]) -> None:
                return None

            async def logout(self) -> None:
                return None

            async def close(self) -> None:
                self.closed = True

        monkeypatch.setattr(mod, "MT5WebClient", FakeClient)
        await mod.main()

        assert secret_token_hex[:16] not in caplog.text
        assert secret_token_hex not in caplog.text
        assert "has_token=True" in caplog.text
        assert f"token_len={len(secret_token_hex)}" in caplog.text


# ---------------------------------------------------------------------------
# 29.3 (S6-02): demo.yml credential scoping
# ---------------------------------------------------------------------------


def test_demo_workflow_scopes_credentials_to_test_step_only() -> None:
    """S6-02 / 29.3: PYMT5_LOGIN and PYMT5_PASSWORD exist only on the test step env."""
    content = (ROOT / ".github/workflows/demo.yml").read_text(encoding="utf-8")
    lines = content.splitlines()

    jobs_block = _extract_yaml_block(lines, "jobs", 0)
    job_block = _extract_yaml_block(jobs_block, "read-only-integration", 2)
    job_env = _parse_kv_block(_extract_yaml_block(job_block, "env", 4), 6)

    assert "PYMT5_LOGIN" not in job_env
    assert "PYMT5_PASSWORD" not in job_env
    assert "PYMT5_SERVER" in job_env
    assert "PYMT5_TEST_SYMBOL" in job_env

    steps = _parse_workflow_steps(job_block, steps_indent=4)
    assert len(steps) >= 5

    test_steps = [s for s in steps if s["name"] == "Run read-only demo integration tests"]
    assert len(test_steps) == 1
    test_step = test_steps[0]

    for step in steps:
        if step is not test_step:
            assert "PYMT5_LOGIN" not in step["env"], f"Step {step['name'] or step['uses']} leaked PYMT5_LOGIN"
            assert "PYMT5_PASSWORD" not in step["env"], f"Step {step['name'] or step['uses']} leaked PYMT5_PASSWORD"
            assert "secrets.PYMT5_LOGIN" not in step["run"]
            assert "secrets.PYMT5_PASSWORD" not in step["run"]
            assert "PYMT5_LOGIN" not in step["run"]
            assert "PYMT5_PASSWORD" not in step["run"]

    assert test_step["env"].get("PYMT5_LOGIN") == "${{ secrets.PYMT5_LOGIN }}"
    assert test_step["env"].get("PYMT5_PASSWORD") == "${{ secrets.PYMT5_PASSWORD }}"
    assert "PYMT5_LOGIN" in test_step["run"]
    assert "PYMT5_PASSWORD" in test_step["run"]
    assert "tests/test_integration.py" in test_step["run"]

    if importlib.util.find_spec("yaml") is not None:
        import yaml

        parsed = yaml.safe_load(content)
        job = parsed["jobs"]["read-only-integration"]
        assert "PYMT5_LOGIN" not in job["env"]
        assert "PYMT5_PASSWORD" not in job["env"]
        for step in job["steps"]:
            if step.get("name") == "Run read-only demo integration tests":
                assert set(step["env"]) == {"PYMT5_LOGIN", "PYMT5_PASSWORD"}
            else:
                assert "PYMT5_LOGIN" not in step.get("env", {})
                assert "PYMT5_PASSWORD" not in step.get("env", {})


# ---------------------------------------------------------------------------
# 29.4 (S6-03): release.yml SHA pinning
# ---------------------------------------------------------------------------


def test_release_workflow_pins_third_party_actions_to_40_char_shas() -> None:
    """S6-03 / 29.4: third-party release actions must be pinned to verified 40-char SHAs."""
    content = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    uses_refs = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", content, flags=re.MULTILINE)
    assert "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086" in uses_refs
    assert "softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65" in uses_refs

    for ref in uses_refs:
        action, _, version = ref.partition("@")
        if not action.startswith("actions/"):
            assert re.fullmatch(r"[0-9a-f]{40}", version), f"Third-party action not pinned to SHA: {ref}"


# ---------------------------------------------------------------------------
# 29.5 (S6-04): pages.yml minimal permissions
# ---------------------------------------------------------------------------


def test_pages_workflow_scopes_write_permissions_to_deploy_job() -> None:
    """S6-04 / 29.5: top-level and build job are read-only; deploy job has write perms."""
    content = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
    lines = content.splitlines()

    top_perms = _parse_kv_block(_extract_yaml_block(lines, "permissions", 0), 2)
    assert top_perms == {"contents": "read"}

    jobs_block = _extract_yaml_block(lines, "jobs", 0)
    build_block = _extract_yaml_block(jobs_block, "build", 2)
    deploy_block = _extract_yaml_block(jobs_block, "deploy", 2)

    build_perms = _parse_kv_block(_extract_yaml_block(build_block, "permissions", 4), 6)
    assert "pages" not in build_perms
    assert "id-token" not in build_perms
    assert "actions/configure-pages" not in "\n".join(build_block)

    deploy_perms = _parse_kv_block(_extract_yaml_block(deploy_block, "permissions", 4), 6)
    assert deploy_perms == {"pages": "write", "id-token": "write"}

    if importlib.util.find_spec("yaml") is not None:
        import yaml

        parsed = yaml.safe_load(content)
        assert parsed["permissions"] == {"contents": "read"}
        assert "permissions" not in parsed["jobs"]["build"]
        assert parsed["jobs"]["deploy"]["permissions"] == {
            "pages": "write",
            "id-token": "write",
        }


# ---------------------------------------------------------------------------
# 29.6 (S6-05): pyproject.toml setuptools>=77.0 & coverage preservation
# ---------------------------------------------------------------------------


def test_pyproject_build_system_and_coverage_config() -> None:
    """S6-05 / 29.6: pyproject.toml requires setuptools>=77.0 without wheel and preserves coverage."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["build-system"]["requires"] == ["setuptools>=77.0"]
    assert data["project"]["license"] == "MIT"
    assert data["tool"]["coverage"]["run"]["omit"] == ["*/pytest-of-*/*"]


# ---------------------------------------------------------------------------
# 30.1 (S6-06) & 30.2 (S6-07, S6-11): examples/04_trading.py & 07_all_order_types.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_mode", ["none_info", "empty_bars"])
async def test_example_04_trading_guards_none_info_and_empty_bars(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    missing_mode: str,
) -> None:
    """S6-06 / 30.1: 04_trading.py handles info is None and empty bars without crashing."""
    mod = _load_module("examples/04_trading.py", monkeypatch, f"test_mod_ex04_guards_{missing_mode}")
    caplog.set_level(logging.INFO)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def login(self, **_kwargs: Any) -> tuple[str, int]:
            return "00", 1

        async def load_symbols(self) -> list[dict[str, Any]]:
            return []

        async def get_positions_and_orders(self) -> dict[str, list[dict[str, Any]]]:
            return {"positions": [], "orders": []}

        def get_symbol_info(self, _symbol: str) -> Any:
            if missing_mode == "none_info":
                return None
            return SimpleNamespace(symbol_id=1, digits=5)

        async def get_rates(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return []

        async def buy_market(self, *_args: Any, **_kwargs: Any) -> Any:
            calls.append("buy_market")
            return "unexpected"

    monkeypatch.setattr(mod, "MT5WebClient", FakeClient)
    await mod.main()
    assert calls == []


@pytest.mark.parametrize(
    ("rel_path", "trade_action", "expect_buy"),
    [
        ("examples/04_trading.py", POSITION_TYPE_BUY, True),
        ("examples/04_trading.py", POSITION_TYPE_SELL, False),
        ("examples/04_trading.py", 99, None),
        ("examples/07_all_order_types.py", POSITION_TYPE_BUY, True),
        ("examples/07_all_order_types.py", POSITION_TYPE_SELL, False),
        ("examples/07_all_order_types.py", 99, None),
    ],
)
async def test_examples_sltp_respects_buy_and_sell_direction(
    monkeypatch: pytest.MonkeyPatch,
    rel_path: str,
    trade_action: int,
    expect_buy: bool | None,
) -> None:
    """S6-07 / S6-11 / 30.2: BUY sets sl < price_open < tp; SELL sets tp < price_open < sl."""
    mod = _load_module(rel_path, monkeypatch, f"test_mod_{Path(rel_path).stem}_{trade_action}")
    recorded_sltp: list[tuple[str, int, float, float]] = []
    price_open = 1.10000

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def login(self, **_kwargs: Any) -> tuple[str, int]:
            return "00", 1

        async def load_symbols(self) -> list[dict[str, Any]]:
            return []

        async def get_positions_and_orders(self) -> dict[str, list[dict[str, Any]]]:
            return {
                "positions": [
                    {
                        "position_id": 777,
                        "trade_symbol": "EURUSD",
                        "trade_action": trade_action,
                        "price_open": price_open,
                        "trade_volume": 1_000_000,
                        "profit": 1.25,
                    }
                ],
                "orders": [],
            }

        def get_symbol_info(self, _symbol: str) -> Any:
            return SimpleNamespace(symbol_id=1, digits=5)

        async def get_rates(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return [{"close": 1.10100}]

        @staticmethod
        def _volume_to_lots(vol: float) -> int:
            return int(vol * 10**8)

        async def buy_market(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def sell_market(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def buy_limit(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def sell_limit(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def buy_stop(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def sell_stop(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def buy_stop_limit(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def sell_stop_limit(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

        async def trade_request(self, **_kwargs: Any) -> str:
            return "ok"

        async def modify_position_sltp(self, symbol: str, position_id: int, sl: float, tp: float) -> str:
            recorded_sltp.append((symbol, position_id, sl, tp))
            return "ok"

        async def close_position(self, *_args: Any, **_kwargs: Any) -> str:
            return "ok"

    monkeypatch.setattr(mod, "MT5WebClient", FakeClient)
    await mod.main()

    if expect_buy is None:
        assert recorded_sltp == []
    else:
        assert len(recorded_sltp) == 1
        sym, pos_id, sl, tp = recorded_sltp[0]
        assert sym == "EURUSD"
        assert pos_id == 777
        if expect_buy:
            assert sl < price_open < tp
        else:
            assert tp < price_open < sl


def test_live_scripts_do_not_use_legacy_symbol_dict_keys() -> None:
    """S6-08 / S6-13: scripts must use symbol_id/trade_symbol/currency_base instead of id/symbol/currency."""
    for rel_path in (
        "scripts/live_test_client.py",
        "scripts/live_test_v2.py",
        "scripts/live_test_v3.py",
    ):
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        assert re.search(r"""\[['"](id|symbol|currency)['"]\]""", text) is None, rel_path
        assert re.search(r"""\.get\(['"](id|symbol|currency)['"]""", text) is None, rel_path


# ---------------------------------------------------------------------------
# 30.3 (S6-08, S6-13, S6-15): symbol dict keys, symbols init, and bars unit
# ---------------------------------------------------------------------------


async def test_live_test_client_uses_symbol_id_and_handles_failed_get_symbols(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S6-08 / S6-15 / 30.3: uses symbol_id/trade_symbol/currency_base, initializes symbols, logs bars."""
    mod = _load_module("scripts/live_test_client.py", monkeypatch, "test_mod_live_client_30_3")
    subscribed: list[list[int]] = []
    close_calls: list[bool] = []

    class FakeClientSuccess:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def connect(self) -> None:
            return None

        async def login(self, **_kwargs: Any) -> tuple[str, int]:
            return "ab" * 32, 99

        async def ping(self) -> float:
            return 1.0

        async def get_symbols(self) -> list[dict[str, Any]]:
            return [
                {"trade_symbol": "EURUSD", "symbol_id": 101, "digits": 5, "currency_base": "EUR"},
                {"trade_symbol": "GBPUSD", "symbol_id": 202, "digits": 5, "currency_base": "GBP"},
            ]

        async def get_positions_and_orders(self) -> dict[str, list[dict[str, Any]]]:
            return {"positions": [], "orders": []}

        async def get_rates(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return [{"close": 1.1}, {"close": 1.2}, {"close": 1.3}]

        def on_tick(self, cb: Any) -> None:
            cb([{"symbol_id": 101, "bid": 1.1, "ask": 1.1002, "tick_time_ms": 1234}])

        async def subscribe_ticks(self, ids: list[int]) -> None:
            subscribed.append(list(ids))

        async def get_trade_history(self, **_kwargs: Any) -> dict[str, list[dict[str, Any]]]:
            return {"deals": [], "orders": []}

        async def logout(self) -> None:
            return None

        async def close(self) -> None:
            close_calls.append(True)

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(mod, "MT5WebClient", FakeClientSuccess)
    await mod.main()

    assert subscribed == [[101, 202]]
    assert close_calls == [True]
    assert "EURUSD" in caplog.text
    assert "101" in caplog.text
    assert "currency=EUR" in caplog.text
    assert "3 bars" in caplog.text
    assert "3 bytes" not in caplog.text

    # Failure path: get_symbols raises -> no UnboundLocalError and client.close() still runs
    caplog.clear()
    close_calls.clear()

    class FakeClientFailure(FakeClientSuccess):
        async def get_symbols(self) -> list[dict[str, Any]]:
            raise RuntimeError("symbols unavailable")

    monkeypatch.setattr(mod, "MT5WebClient", FakeClientFailure)
    await mod.main()
    assert close_calls == [True]
    assert "get_symbols failed" in caplog.text


async def test_live_test_v2_and_v3_use_schema_symbol_keys_and_fallback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S6-13 / 30.3: live_test_v2 and v3 extract symbol_id/trade_symbol and v2 falls back cleanly."""
    import websockets

    key_material = b"\x11" * 32
    token = b"\x22" * 64
    init_cipher = initial_cipher()
    session_cipher = AESCipher(key_material)

    raw_symbols = _encode_basic_symbols(
        [
            {"trade_symbol": "EURUSD", "symbol_id": 42, "digits": 5},
            {"trade_symbol": "USDJPY", "symbol_id": 84, "digits": 3},
        ]
    )
    compressed_symbols = b"\x00\x00\x00\x00" + zlib.compress(raw_symbols)

    # --- live_test_v2 with valid symbols -> cmd=7 subscribes [42, 84] ---
    mod_v2 = _load_module("scripts/live_test_v2.py", monkeypatch, "test_mod_live_v2_30_3")
    sent_commands_v2: list[tuple[int, bytes]] = []

    class FakeWSV2:
        def __init__(self, include_symbols: bool) -> None:
            self.closed = False
            sym_resp = (
                _encode_response(session_cipher, 34, 0, compressed_symbols)
                if include_symbols
                else _encode_response(session_cipher, 34, 0, b"")
            )
            self._queue: list[bytes] = [
                _encode_response(init_cipher, CMD_BOOTSTRAP, 0, b"\x01\x00" + token + key_material),
                _encode_response(session_cipher, CMD_LOGIN, 0, b"\x00" * 16),
                # drain_pushes -> TimeoutError
            ]
            self._after_drain: list[bytes] = [
                _encode_response(session_cipher, 29, 0, b""),
                sym_resp,
                _encode_response(session_cipher, 4, 0, struct.pack("<I", 0)),
                _encode_response(session_cipher, 11, 0, b""),
            ]
            self._stage = "initial"

        async def send(self, packet: bytes) -> None:
            _, _, enc = unpack_outer(packet)
            # Detect cmd from outer packet via appropriate cipher
            try:
                dec = session_cipher.decrypt(enc)
                cmd = struct.unpack_from("<H", dec, 2)[0]
                sent_commands_v2.append((cmd, dec[4:]))
                if cmd == 29 and self._stage == "after_drain":
                    self._queue = list(self._after_drain)
            except Exception:
                pass

        async def recv(self) -> bytes:
            if self._queue:
                return self._queue.pop(0)
            if self._stage == "initial":
                self._stage = "after_drain"
            raise TimeoutError

        async def close(self) -> None:
            self.closed = True

    ws_with_symbols = FakeWSV2(include_symbols=True)

    async def fake_connect_v2_syms(*_args: Any, **_kwargs: Any) -> FakeWSV2:
        return ws_with_symbols

    monkeypatch.setattr(websockets, "connect", fake_connect_v2_syms)
    caplog.set_level(logging.INFO)
    await mod_v2.main()

    assert ws_with_symbols.closed is True
    cmd7_payloads = [payload for cmd, payload in sent_commands_v2 if cmd == 7]
    assert cmd7_payloads == [struct.pack("<3I", 2, 42, 84)]
    assert "EURUSD" in caplog.text and "id=    42" in caplog.text

    # --- live_test_v2 fallback when symbols are empty -> cmd=7 subscribes [1, 2, 3] ---
    sent_commands_v2.clear()
    ws_no_symbols = FakeWSV2(include_symbols=False)

    async def fake_connect_v2_empty(*_args: Any, **_kwargs: Any) -> FakeWSV2:
        return ws_no_symbols

    monkeypatch.setattr(websockets, "connect", fake_connect_v2_empty)
    await mod_v2.main()

    assert ws_no_symbols.closed is True
    cmd7_payloads = [payload for cmd, payload in sent_commands_v2 if cmd == 7]
    assert cmd7_payloads == [struct.pack("<4I", 3, 1, 2, 3)]

    # --- live_test_v3 symbol printing ---
    caplog.clear()
    mod_v3 = _load_module("scripts/live_test_v3.py", monkeypatch, "test_mod_live_v3_30_3")

    class FakeWSV3:
        def __init__(self) -> None:
            self.closed = False
            self._queue: list[bytes] = [
                _encode_response(init_cipher, CMD_BOOTSTRAP, 0, b"\x01\x00" + token + key_material),
                _encode_response(session_cipher, CMD_LOGIN, 0, b"\x00" * 16),
            ]
            self._phase = 0

        async def send(self, packet: bytes) -> None:
            _, _, enc = unpack_outer(packet)
            try:
                dec = session_cipher.decrypt(enc)
                cmd = struct.unpack_from("<H", dec, 2)[0]
                if cmd == 9:
                    self._queue = [_encode_response(session_cipher, 9, 0, struct.pack("<I", 0))]
                elif cmd == 34:
                    self._queue = [_encode_response(session_cipher, 34, 0, compressed_symbols)]
                elif cmd == 4:
                    self._queue = [_encode_response(session_cipher, 4, 0, struct.pack("<I", 0))]
                elif cmd == 11:
                    self._queue = [_encode_response(session_cipher, 11, 0, b"")]
            except Exception:
                pass

        async def recv(self) -> bytes:
            if self._queue:
                return self._queue.pop(0)
            raise TimeoutError

        async def close(self) -> None:
            self.closed = True

    ws_v3 = FakeWSV3()

    async def fake_connect_v3(*_args: Any, **_kwargs: Any) -> FakeWSV3:
        return ws_v3

    monkeypatch.setattr(websockets, "connect", fake_connect_v3)
    await mod_v3.main()

    assert ws_v3.closed is True
    assert "EURUSD" in caplog.text
    assert "42" in caplog.text
    assert "#0: ?" not in caplog.text


# ---------------------------------------------------------------------------
# 30.4 (S6-09): analyze_docstrings.py recommendation ordering
# ---------------------------------------------------------------------------


def test_analyze_docstrings_select_recommended_file_prefers_non_test_most_issues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S6-09 / 30.4: non-test file with the most issues is ranked first."""
    mod = _load_module("scripts/analyze_docstrings.py", monkeypatch, "test_mod_analyze_docstrings")

    (tmp_path / "test_many_gaps.py").write_text(
        "def a():\n    pass\n\ndef b():\n    pass\n\ndef c():\n    pass\n\ndef d():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "alpha_few.py").write_text(
        '"""Doc."""\n\ndef one():\n    pass\n',
        encoding="utf-8",
    )
    (tmp_path / "beta_most.py").write_text(
        "def f1():\n    pass\n\ndef f2():\n    pass\n\ndef f3():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "clean_file.py").write_text(
        '"""Module doc."""\n\n\ndef ok():\n    """Ok doc."""\n    return 1\n',
        encoding="utf-8",
    )

    selected = mod.select_recommended_file(str(tmp_path))
    assert selected is not None
    assert Path(selected["filepath"]).name == "beta_most.py"
    assert selected["missing_docstrings"] == 4

    # When only test files have gaps, select the test file with the most issues
    test_only = tmp_path / "tests_only"
    test_only.mkdir()
    (test_only / "test_one.py").write_text('"""Doc."""\ndef x():\n    pass\n', encoding="utf-8")
    (test_only / "test_two.py").write_text("def x():\n    pass\n\ndef y():\n    pass\n", encoding="utf-8")
    selected_test = mod.select_recommended_file(str(test_only))
    assert selected_test is not None
    assert Path(selected_test["filepath"]).name == "test_two.py"


# ---------------------------------------------------------------------------
# 30.5 (S6-10, S6-14): bootstrap validation without assert & ws cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "scripts/live_test_v2.py",
        "scripts/live_test_v3.py",
        "scripts/live_test_symbols.py",
    ],
)
@pytest.mark.parametrize(
    ("code", "body"),
    [
        (1, b"\x01\x00" + b"\x00" * 96),
        (0, b"\x01\x00" + b"\x00" * 64),  # 66 bytes (< 82)
        (0, b"\x01\x00" + b"\x00" * 64 + b"\x11" * 17),  # invalid AES key length 17
    ],
)
async def test_live_scripts_reject_invalid_bootstrap_and_close_ws(
    monkeypatch: pytest.MonkeyPatch,
    rel_path: str,
    code: int,
    body: bytes,
) -> None:
    """S6-10 / S6-14 / 30.5: invalid bootstrap code/length raises MT5ConnectionError and closes ws."""
    import websockets

    source = (ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(n, ast.Assert) for n in ast.walk(tree)), f"{rel_path} still contains assert"

    mod = _load_module(rel_path, monkeypatch, f"test_mod_boot_{Path(rel_path).stem}")
    init_cipher = initial_cipher()
    bad_bootstrap = _encode_response(init_cipher, CMD_BOOTSTRAP, code, body)

    class FakeWS:
        def __init__(self) -> None:
            self.closed = False

        async def send(self, _packet: bytes) -> None:
            return None

        async def recv(self) -> bytes:
            return bad_bootstrap

        async def close(self) -> None:
            self.closed = True

    fake_ws = FakeWS()

    async def fake_connect(*_args: Any, **_kwargs: Any) -> FakeWS:
        return fake_ws

    monkeypatch.setattr(websockets, "connect", fake_connect)

    with pytest.raises(MT5ConnectionError):
        await mod.main()

    assert fake_ws.closed is True


@pytest.mark.parametrize(
    "rel_path",
    [
        "scripts/live_test_v2.py",
        "scripts/live_test_v3.py",
        "scripts/live_test_symbols.py",
    ],
)
def test_live_scripts_bootstrap_validation_survives_python_O_mode(rel_path: str) -> None:
    """30.5: running under `python -O` must still raise MT5ConnectionError and close ws."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("COV_CORE_", "COVERAGE_"))}
    env["PYMT5_SERVER"] = "wss://demo.invalid/terminal"
    env["PYMT5_LOGIN"] = "5047785364"
    env["PYMT5_PASSWORD"] = "test-password-only"

    runner = f"""
import asyncio, importlib.util, struct, sys
from pathlib import Path
from unittest.mock import patch
from pymt5.constants import CMD_BOOTSTRAP
from pymt5.crypto import initial_cipher
from pymt5.exceptions import MT5ConnectionError
from pymt5.protocol import pack_outer

cipher = initial_cipher()
inner = b"\\x00\\x00" + struct.pack("<HB", CMD_BOOTSTRAP, 1) + b"\\x01\\x00" + b"\\x00" * 96
packet = pack_outer(cipher.encrypt(inner))

class FakeWS:
    closed = False
    async def send(self, _p): pass
    async def recv(self): return packet
    async def close(self): self.closed = True

ws = FakeWS()
async def fake_connect(*_a, **_kw): return ws

spec = importlib.util.spec_from_file_location("target", Path({str(ROOT / rel_path)!r}))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

async def run():
    with patch("websockets.connect", fake_connect):
        try:
            await mod.main()
        except MT5ConnectionError:
            assert ws.closed, "ws was not closed"
            return
    raise AssertionError("Expected MT5ConnectionError under -O")

asyncio.run(run())
"""
    proc = subprocess.run(
        [sys.executable, "-O", "-c", runner],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"
