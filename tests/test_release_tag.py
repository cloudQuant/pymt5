"""Exercise the release guard using the same entry point as the workflow."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_tag.py"


def write_release_metadata(root: Path) -> None:
    (root / "pyproject.toml").write_text('[project]\nversion = "1.0.1"\n', encoding="utf-8")
    (root / "docs" / "zh_CN").mkdir(parents=True)
    (root / "pymt5").mkdir()
    (root / "docs" / "changelog.rst").write_text("v1.0.1 (2026-01-01)\n", encoding="utf-8")
    (root / "docs" / "zh_CN" / "changelog.rst").write_text("v1.0.1 (2026-01-01)\n", encoding="utf-8")
    (root / "pymt5" / "__init__.py").write_text('__version__ = "1.0.1"\n', encoding="utf-8")
    (root / "docs" / "conf.py").write_text('release = "1.0.1"\n', encoding="utf-8")


def run_guard(root: Path, ref: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=root,
        env={**os.environ, "GITHUB_REF": ref},
        capture_output=True,
        text=True,
        check=False,
    )


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
    write_release_metadata(tmp_path)
    result = run_guard(tmp_path, ref)
    assert result.returncode == expected_code
    if expected_code:
        assert "must equal refs/tags/v1.0.1" in result.stderr


def test_release_guard_requires_bilingual_changelogs(tmp_path: Path) -> None:
    write_release_metadata(tmp_path)
    (tmp_path / "docs" / "zh_CN" / "changelog.rst").unlink()
    result = run_guard(tmp_path, "refs/tags/v1.0.1")
    assert result.returncode == 1
    assert "Cannot read release notes" in result.stderr


def test_release_guard_requires_matching_version_fallbacks(tmp_path: Path) -> None:
    write_release_metadata(tmp_path)
    (tmp_path / "pymt5" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    result = run_guard(tmp_path, "refs/tags/v1.0.1")
    assert result.returncode == 1
    assert "must equal 1.0.1" in result.stderr
