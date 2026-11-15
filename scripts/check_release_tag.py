"""Validate release tag, changelog entries, and source version fallbacks."""

import ast
import os
import re
import sys
import tomllib
from pathlib import Path


def fallback_versions(path: Path, variable: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    versions = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Constant):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == variable for target in targets) and isinstance(
            node.value.value, str
        ):
            versions.append(node.value.value)
    return versions


def main() -> None:
    ref = os.environ.get("GITHUB_REF", "")
    root = Path.cwd()
    with (root / "pyproject.toml").open("rb") as project_file:
        version = tomllib.load(project_file)["project"]["version"]
    if not isinstance(version, str) or not version or ref != f"refs/tags/v{version}":
        sys.exit(f"Release ref {ref!r} must equal refs/tags/v{version}")

    errors = []
    for changelog in (root / "docs" / "changelog.rst", root / "docs" / "zh_CN" / "changelog.rst"):
        try:
            content = changelog.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"Cannot read release notes {changelog}: {exc}")
            continue
        if not re.search(rf"^v{re.escape(version)}\s+\(", content, flags=re.MULTILINE):
            errors.append(f"Missing v{version} release notes in {changelog}")

    for path, variable in (
        (root / "pymt5" / "__init__.py", "__version__"),
        (root / "docs" / "conf.py", "release"),
    ):
        try:
            versions = fallback_versions(path, variable)
        except (OSError, SyntaxError) as exc:
            errors.append(f"Cannot validate version fallback in {path}: {exc}")
            continue
        if not versions or any(candidate != version for candidate in versions):
            errors.append(f"Version fallback {variable} in {path} must equal {version}; found {versions}")

    if errors:
        sys.exit("\n".join(errors))
    print(f"Release tag, bilingual changelogs, and version fallbacks match {version}")


if __name__ == "__main__":
    main()
