#!/usr/bin/env python3
"""Generate the documentation site or flat Wiki from the bilingual sources."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote

from prepare_env_editor import build_schema

from prepare_wiki import (
    _MARKDOWN_LINK_RE,
    _code_ranges,
    _position_in_ranges,
    prepare_wiki,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PAGES = json.loads((DOCS / "pages.json").read_text(encoding="utf-8"))


def source(page: dict, language: str) -> Path:
    return (
        DOCS
        / ("wiki" if language == "zh" else "en")
        / f"{page['zh'] if language == 'zh' else page['slug']}.md"
    )


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]*>", "", text).replace("`", "").lower().strip()
    return "".join(
        c
        for c in text
        if c in "-_ " or not unicodedata.category(c).startswith(("P", "S"))
    ).replace(" ", "-")


def headings(text: str) -> list[str]:
    ranges = _code_ranges(text)
    return [
        m.group(1)
        for m in re.finditer(r"^#{1,6} (.+)$", text, re.MULTILINE)
        if not _position_in_ranges(m.start(), ranges)
    ]


def generate(target: str, output: Path, site_url: str, wiki_url: str) -> None:
    output = output.resolve()
    # Never write generated output over or inside tracked article directories.
    source_dirs = {(DOCS / name).resolve() for name in ("wiki", "en")}
    if (
        output == DOCS
        or output in DOCS.parents
        or any(
            output == directory or directory in output.parents
            for directory in source_dirs
        )
    ):
        raise ValueError(
            "Output must be a generated directory, never a source directory"
        )
    sources = {}
    for page in PAGES:
        if (
            not re.fullmatch(r"[a-z][a-z-]*", page["slug"])
            or Path(page["zh"]).name != page["zh"]
        ):
            raise ValueError(f"Invalid page mapping: {page}")
        for language in ("zh", "en"):
            path = source(page, language)
            if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
                raise ValueError(f"Missing or unsafe documentation source: {path}")
            sources[path.resolve()] = (page, language)
    output.mkdir(parents=True, exist_ok=True)
    if target == "site":
        for language in ("zh", "en"):
            shutil.rmtree(output / language, ignore_errors=True)
            (output / language).mkdir()
    sections = {}
    for page in PAGES:
        paired = {
            locale: headings(source(page, locale).read_text(encoding="utf-8"))
            for locale in ("zh", "en")
        }
        if len(paired["zh"]) != len(paired["en"]):
            raise ValueError(
                f"{page['slug']}: section counts differ: zh={len(paired['zh'])}, en={len(paired['en'])}"
            )
        sections[page["slug"]] = [
            {locale: f"section-{i}" for locale in ("zh", "en")}
            for i in range(len(paired["zh"]))
        ]
        for language in ("zh", "en"):
            path = source(page, language)
            text = path.read_text(encoding="utf-8")
            ranges = _code_ranges(text)

            def replace(match: re.Match) -> str:
                if _position_in_ranges(match.start(), ranges):
                    return match.group(0)
                label, relative, anchor = match.groups()
                link_path = (path.parent / unquote(relative)).resolve()
                if link_path not in sources:
                    raise ValueError(f"{path}: missing or unmapped page {relative}")
                linked, lang = sources[link_path]
                if target == "site":
                    name = f"{linked['slug']}.md"
                    if lang != language:
                        name = f"../{lang}/{name}"
                    if anchor:
                        titles = headings(link_path.read_text(encoding="utf-8"))
                        slugs = [slugify(h) for h in titles]
                        key = unquote(anchor[1:])
                        if key not in slugs:
                            raise ValueError(
                                f"{path}: missing anchor {relative}{anchor}"
                            )
                        anchor = f"#section-{slugs.index(key)}"
                else:
                    name = (
                        f"{linked['zh'] if lang == 'zh' else 'EN-' + linked['slug']}.md"
                    )
                return f"[{label}]({name}{anchor or ''})"

            text = _MARKDOWN_LINK_RE.sub(replace, text)
            if target == "site":
                # Paired IDs let language switching retain the current section.
                ranges = _code_ranges(text)
                index = -1

                def heading(match: re.Match) -> str:
                    nonlocal index
                    if _position_in_ranges(match.start(), ranges):
                        return match.group(0)
                    index += 1
                    title = match.group(2)
                    return f'<span id="{slugify(title)}"></span>\n\n{match.group(1)} {title} {{#section-{index}}}'

                text = re.sub(r"^(#{1,6}) (.+)$", heading, text, flags=re.MULTILINE)
                text = text.replace("```env\n", "```dotenv\n")
                if page["slug"] == "config-editor":
                    text += "\n<ClientOnly><ConfigEditor /></ClientOnly>\n"
                frontmatter = f"sourcePath: {path.relative_to(DOCS).as_posix()}\n"
                if page["slug"] == "index":
                    frontmatter += (
                        "layout: page\npageClass: afterglow-home\nsidebar: false\n"
                    )
                    text = re.sub(
                        r"^# (.+) (\{#section-0\})$",
                        r"## \1 \2",
                        text,
                        flags=re.MULTILINE,
                    )
                    text = (
                        '<DocsHome />\n\n<div class="vp-doc docs-home-index">\n\n'
                        + text
                        + "\n\n</div>\n"
                    )
                text = "---\n" + frontmatter + "---\n\n" + text
                destination = output / language / f"{page['slug']}.md"
            else:
                other = "en" if language == "zh" else "zh"
                other_name = f"{page['zh'] if other == 'zh' else 'EN-' + page['slug']}"
                current_url = f"{site_url.rstrip('/')}/{language}/{'' if page['slug'] == 'index' else page['slug'] + '.html'}"
                notice = f"[文档网站 / Documentation]({current_url}) · [{'English' if other == 'en' else '简体中文'}]({wiki_url.rstrip('/')}/{quote(other_name)})\n\n"
                text = notice + text
                destination = (
                    output
                    / f"{page['zh'] if language == 'zh' else 'EN-' + page['slug']}.md"
                )
            destination.write_text(text, encoding="utf-8")
    schema = build_schema()
    if target == "site":
        brand_output = output / "public" / "brand"
        brand_output.mkdir(parents=True, exist_ok=True)
        for filename in (
            "afterglow-mark.svg",
            "afterglow-mark-dark.svg",
            "afterglow-icon.svg",
            "afterglow-icon-light.svg",
            "favicon.svg",
            "favicon.ico",
            "favicon-32.png",
        ):
            shutil.copyfile(
                ROOT / "assets" / "brand" / filename, brand_output / filename
            )
        (output / "env-schema.json").write_text(
            json.dumps(schema, ensure_ascii=False), encoding="utf-8"
        )
        (output / "index.md").write_text(
            "---\nlayout: page\ntitle: Documentation · 文档\n---\n\n<LanguageLanding />\n",
            encoding="utf-8",
        )
        (output / "sections.json").write_text(json.dumps(sections), encoding="utf-8")
    else:
        sidebar = ["[简体中文](Home.md) · [English](EN-index.md)", ""]
        for language, title in [("zh", "中文文档"), ("en", "English documentation")]:
            sidebar.append(f"**{title}**\n")
            sidebar += [
                f"- [{headings(source(p, language).read_text(encoding='utf-8'))[0]}]({p['zh'] if language == 'zh' else 'EN-' + p['slug']}.md)"
                for p in PAGES
            ]
            sidebar.append("")
        (output / "_Sidebar.md").write_text("\n".join(sidebar), encoding="utf-8")
        (output / "_Footer.md").write_text(
            f"[文档网站 / Documentation]({site_url}) · Generated from docs/wiki and docs/en. Edit sources in the main repository.\n",
            encoding="utf-8",
        )
        prepare_wiki(output, wiki_url)
    print(f"Generated {len(PAGES) * 2} {target} pages in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["site", "wiki"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--site-url", default="https://kldhsh123.github.io/Afterglow/")
    parser.add_argument(
        "--wiki-url", default="https://github.com/kldhsh123/Afterglow/wiki"
    )
    args = parser.parse_args()
    output = args.output or (
        DOCS / ".site" if args.target == "site" else ROOT / ".wiki-preview"
    )
    generate(args.target, output, args.site_url, args.wiki_url)


if __name__ == "__main__":
    main()
