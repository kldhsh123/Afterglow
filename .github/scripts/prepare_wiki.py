#!/usr/bin/env python3
"""校验仓库相对 Markdown 链接，并将其改写为 GitHub Wiki 链接。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote


_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[([^\]]+)\]\((?![a-z][a-z0-9+.-]*:)([^)#?]+\.md)(#[^)]+)?\)",
    re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(
    r"^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_INLINE_CODE_RE = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)[^\n]*?(?P=ticks)(?!`)"
)


def prepare_wiki(root: Path, wiki_base_url: str) -> int:
    root = root.resolve()
    rewritten = 0
    for page in sorted(root.glob("*.md")):
        original = page.read_text(encoding="utf-8")
        code_ranges = _code_ranges(original)
        def replace_link(match: re.Match[str]) -> str:
            nonlocal rewritten
            if _position_in_ranges(match.start(), code_ranges):
                return match.group(0)
            label, relative, anchor = match.groups()
            target = (page.parent / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"{page.name}: link escapes Wiki root: {relative}"
                ) from exc
            if not target.is_file():
                raise FileNotFoundError(
                    f"{page.name}: missing Wiki page: {relative}"
                )
            rewritten += 1
            page_name = "Home" if target.name == "Home.md" else target.stem
            url = f"{wiki_base_url.rstrip('/')}/{quote(page_name)}{anchor or ''}"
            return f"[{label}]({url})"

        prepared = _MARKDOWN_LINK_RE.sub(replace_link, original)
        if prepared != original:
            page.write_text(prepared, encoding="utf-8")
    return rewritten


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """返回围栏代码和行内代码区间，改写 Markdown 链接时应跳过这些位置。"""
    ranges = [
        (match.start(), match.end())
        for match in _FENCED_CODE_RE.finditer(text)
    ]
    for match in _INLINE_CODE_RE.finditer(text):
        if not _position_in_ranges(match.start(), ranges):
            ranges.append((match.start(), match.end()))
    return sorted(ranges)


def _position_in_ranges(position: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)


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
