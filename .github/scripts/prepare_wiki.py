#!/usr/bin/env python3
"""Validate and rewrite repository-relative Markdown links for GitHub Wiki."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote


_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]+)\]\((?![a-z][a-z0-9+.-]*:)([^)#?]+\.md)(#[^)]+)?\)",
    re.IGNORECASE,
)
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)\|[^\]]+\]\]")


def prepare_wiki(root: Path, wiki_base_url: str) -> int:
    root = root.resolve()
    rewritten = 0
    for page in sorted(root.glob("*.md")):
        original = page.read_text(encoding="utf-8")
        for wiki_page in _WIKI_LINK_RE.findall(original):
            target = root / f"{wiki_page}.md"
            if not target.is_file():
                raise FileNotFoundError(
                    f"{page.name}: missing Wiki page: {wiki_page}.md"
                )

        def replace_link(match: re.Match[str]) -> str:
            nonlocal rewritten
            label, relative, anchor = match.groups()
            target = (page.parent / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"{page.name}: link escapes Wiki root: {relative}") from exc
            if not target.is_file():
                raise FileNotFoundError(f"{page.name}: missing Wiki page: {relative}")
            rewritten += 1
            page_name = "Home" if target.name == "Home.md" else target.stem
            url = f"{wiki_base_url.rstrip('/')}/{quote(page_name)}{anchor or ''}"
            return f"[{label}]({url})"

        prepared = _MARKDOWN_LINK_RE.sub(replace_link, original)
        if prepared != original:
            page.write_text(prepared, encoding="utf-8")
    return rewritten


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("wiki_base_url")
    args = parser.parse_args()
    count = prepare_wiki(args.root, args.wiki_base_url)
    print(f"Prepared {count} internal Wiki links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
