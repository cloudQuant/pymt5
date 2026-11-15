#!/usr/bin/env python3
"""Build the pymt5 documentation for GitHub Pages or Read the Docs."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DOCS_DIR.parent
LANGUAGES = {
    "en": (DOCS_DIR, "en"),
    "zh-CN": (DOCS_DIR / "zh_CN", "zh_CN"),
}


def build_site(language_directory: str, destination: Path, jobs: str) -> None:
    source_dir, sphinx_language = LANGUAGES[language_directory]
    environment = os.environ.copy()
    environment["PYMT5_DOCS_LANGUAGE"] = language_directory
    command = [
        sys.executable,
        "-m",
        "sphinx",
        "-b",
        "html",
        "-c",
        str(DOCS_DIR),
        str(source_dir),
        str(destination),
        "-D",
        f"language={sphinx_language}",
        "-D",
        "root_doc=index",
        "-D",
        "master_doc=index",
        "-E",
        "-a",
        "-j",
        jobs,
        "-W",
        "--keep-going",
    ]
    print(f"Building {language_directory}: {source_dir} -> {destination}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def readthedocs_language() -> str:
    requested = os.environ.get("READTHEDOCS_LANGUAGE", "").strip().lower().replace("_", "-")
    if requested == "en":
        return "en"
    if requested in {"zh", "zh-cn"}:
        return "zh-CN"
    raise ValueError(f"Unsupported READTHEDOCS_LANGUAGE {requested!r}; expected 'en' or 'zh-cn'")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DOCS_DIR / "_build" / "html",
        help="GitHub Pages artifact root (default: docs/_build/html)",
    )
    parser.add_argument("--jobs", default="auto", help="parallel Sphinx jobs (default: auto)")
    parser.add_argument(
        "--readthedocs",
        action="store_true",
        help="build READTHEDOCS_LANGUAGE directly into READTHEDOCS_OUTPUT/html",
    )
    args = parser.parse_args()

    if args.readthedocs:
        try:
            language_directory = readthedocs_language()
            output_variable = os.environ["READTHEDOCS_OUTPUT"]
        except (KeyError, ValueError) as exc:
            parser.error(str(exc))
        destination = Path(output_variable).resolve() / "html"
        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True, exist_ok=True)
        build_site(language_directory, destination, args.jobs)
        print(f"Built Read the Docs {language_directory} documentation at {destination}", flush=True)
        return

    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    for language_directory in LANGUAGES:
        destination = output_root / language_directory
        shutil.rmtree(destination, ignore_errors=True)
        build_site(language_directory, destination, args.jobs)

    shutil.copyfile(DOCS_DIR / "landing.html", output_root / "index.html")
    (output_root / ".nojekyll").touch()
    print(f"Built bilingual documentation at {output_root}", flush=True)


if __name__ == "__main__":
    main()
