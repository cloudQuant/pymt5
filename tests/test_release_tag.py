"""Exercise the release guard using the same entry point as the workflow."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_tag.py"


@pytest.mark.parametrize(
    ("ref", "expected_code"),
    [
        ("refs/tags/v1.0.1", 0),
        ("refs/tags/v1.0.2", 1),
        ("refs/heads/v1.0.1", 1),
        ("", 1),
    ],
)
def test_release_tag_guard(tmp_path: Path, ref: str, expected_code: int) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.1"\n', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_REF": ref},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_code
    if expected_code:
        assert "must equal refs/tags/v1.0.1" in result.stderr
