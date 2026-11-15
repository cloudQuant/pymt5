"""Exercise the real pytest warning gate in isolated child processes."""

import os
import subprocess
import sys
from pathlib import Path


def run_pytest(directory: Path, source: str, filters: list[str]) -> subprocess.CompletedProcess[str]:
    test_file = directory / "test_warning_probe.py"
    test_file.write_text(source, encoding="utf-8")
    config = directory / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "-c", str(config), *filters, str(test_file)],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_warning_gate_rejects_unawaited_coroutine(tmp_path: Path) -> None:
    source = """\
import gc

async def warning_gate_forgotten_coroutine():
    pass

def test_probe():
    warning_gate_forgotten_coroutine()
    gc.collect()
"""
    unraisable_filter = ["-W", "error::pytest.PytestUnraisableExceptionWarning"]
    control = run_pytest(tmp_path, source, unraisable_filter)
    assert control.returncode == 0, control.stdout + control.stderr
    assert "was never awaited" in control.stdout + control.stderr

    result = run_pytest(tmp_path, source, ["-W", "error::RuntimeWarning", *unraisable_filter])
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "PytestUnraisableExceptionWarning" in output
    assert "warning_gate_forgotten_coroutine" in output
    assert "was never awaited" in output


def test_warning_gate_rejects_unraisable_exception(tmp_path: Path) -> None:
    source = """\
import gc

class WarningGateBrokenFinalizer:
    def __del__(self):
        raise ValueError("warning-gate-unraisable-sentinel")

def test_probe():
    value = WarningGateBrokenFinalizer()
    del value
    gc.collect()
"""
    runtime_filter = ["-W", "error::RuntimeWarning"]
    control = run_pytest(tmp_path, source, runtime_filter)
    control_output = control.stdout + control.stderr
    assert control.returncode == 0, control_output
    assert "PytestUnraisableExceptionWarning" in control_output
    assert "warning-gate-unraisable-sentinel" in control_output

    result = run_pytest(tmp_path, source, [*runtime_filter, "-W", "error::pytest.PytestUnraisableExceptionWarning"])
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "PytestUnraisableExceptionWarning" in output
    assert "warning-gate-unraisable-sentinel" in output


def test_warning_gate_allows_normal_test(tmp_path: Path) -> None:
    filters = ["-W", "error::RuntimeWarning", "-W", "error::pytest.PytestUnraisableExceptionWarning"]
    result = run_pytest(tmp_path, "def test_probe():\n    assert 2 + 2 == 4\n", filters)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
