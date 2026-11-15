#!/usr/bin/env python3
"""Check generated bilingual HTML for broken local links and language switches."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class PageInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.language = ""
        self.links: list[str] = []
        self.switches: dict[str, str] = {}
        self.author_profiles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
        if tag == "html":
            self.language = attributes.get("lang") or ""
        if tag == "a" and (switch := attributes.get("data-language-switch")):
            self.switches[switch] = attributes.get("href") or ""
        if profile := attributes.get("data-pymt5-author"):
            self.author_profiles.append(profile)
        for attribute in ("href", "src"):
            if value := attributes.get(attribute):
                self.links.append(value)


def inspect_page(path: Path) -> PageInspector:
    inspector = PageInspector()
    inspector.feed(path.read_text(encoding="utf-8"))
    return inspector


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent / "_build" / "html",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []

    required = [
        root / "index.html",
        root / "en" / "index.html",
        root / "zh-CN" / "index.html",
        root / "en" / "author.html",
        root / "zh-CN" / "author.html",
        root / "en" / "_static" / "author.js",
        root / "zh-CN" / "_static" / "author.js",
        root / ".nojekyll",
    ]
    for path in required:
        if not path.exists():
            errors.append(f"missing required artifact: {path.relative_to(root)}")
    if errors:
        raise SystemExit("\n".join(errors))

    pages = sorted(root.rglob("*.html"))
    cache = {path: inspect_page(path) for path in pages}
    for path, inspector in cache.items():
        relative = path.relative_to(root)
        if relative == Path("index.html"):
            for language in ("en", "zh-CN"):
                if language not in inspector.switches:
                    errors.append(f"{relative}: missing {language} language selector")
            continue
        if relative.parts[0] not in {"en", "zh-CN"}:
            continue

        expected_language = "zh-CN" if relative.parts[0] == "zh-CN" else "en"
        if inspector.language != expected_language:
            errors.append(f"{relative}: expected html lang={expected_language}, got {inspector.language!r}")
        other_language = "zh-CN" if expected_language == "en" else "en"
        if relative == Path(expected_language) / "author.html":
            if inspector.author_profiles != ["cloudQuant"]:
                errors.append(f"{relative}: missing or duplicate cloudQuant author profile container")
            if not any(urlsplit(link).path.endswith("_static/author.js") for link in inspector.links):
                errors.append(f"{relative}: author profile script is not loaded")
        switch_href = inspector.switches.get(other_language)
        if switch_href is None:
            errors.append(f"{relative}: missing {other_language} language switch")
            continue

        parsed_switch = urlsplit(switch_href)
        switch_target = (path.parent / unquote(parsed_switch.path)).resolve()
        expected_target = root / other_language / Path(*relative.parts[1:])
        if parsed_switch.scheme or parsed_switch.netloc or switch_target != expected_target:
            errors.append(f"{relative}: language switch does not target {expected_target.relative_to(root)}")

    for path, inspector in cache.items():
        relative = path.relative_to(root)
        for link in inspector.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
                continue
            target = (path.parent / unquote(parsed.path)).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"{relative}: link leaves artifact: {link}")
                continue
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                errors.append(f"{relative}: broken local link: {link}")
                continue
            if parsed.fragment and target.suffix == ".html":
                target_inspector = cache.get(target) or inspect_page(target)
                if parsed.fragment not in target_inspector.ids:
                    errors.append(f"{relative}: missing fragment target: {link}")

    if errors:
        raise SystemExit("Documentation checks failed:\n" + "\n".join(errors))
    print(f"Documentation checks passed: {len(pages)} HTML pages, local links and language switches verified.")


if __name__ == "__main__":
    main()
