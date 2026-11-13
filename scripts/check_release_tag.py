"""Ensure the release tag matches the package version before publishing."""

import os
import sys
import tomllib
from pathlib import Path


def main() -> None:
    ref = os.environ.get("GITHUB_REF", "")
    with Path("pyproject.toml").open("rb") as project_file:
        version = tomllib.load(project_file)["project"]["version"]
    if not isinstance(version, str) or not version or ref != f"refs/tags/v{version}":
        sys.exit(f"Release ref {ref!r} must equal refs/tags/v{version}")
    print(f"Release tag matches project version {version}")


if __name__ == "__main__":
    main()
