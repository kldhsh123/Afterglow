#!/usr/bin/env python
"""离线生成 persona 卡片与报告。

用法：
    uv run python scripts/analyze_persona.py <path-to-qq-json>

会生成三个文件到 PERSONA_DATA_DIR：
    - persona_report.json（完整统计）
    - persona_card.md     （供 prompt 模板使用的简洁卡片）
    - persona_style_profile.json（按用户话题分桶的真实回应风格）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from xuwen.config import get_settings
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.parser import load_qq_json, parse_messages
from xuwen.ingestion.splitter import split_sessions
from xuwen.persona.analyzer import analyze_persona
from xuwen.persona.card import render_persona_card, save_persona_card, save_persona_report
from xuwen.persona.circadian import (
    CIRCADIAN_PROFILE_FILENAME,
    compute_circadian_profile,
    save_circadian_profile,
)
from xuwen.persona.style_profile import build_style_profile, save_style_profile

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analyze-persona",
        description="对 QQ 导出 JSON 做朋友画像统计并生成 persona 卡片",
    )
    parser.add_argument("json_path", type=Path, help="QQChatExporter V5 导出的 JSON")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="可选：输出目录（默认使用 settings.persona_data_dir）",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.require_identity()
    out_dir = args.out_dir or settings.persona_data_dir

    console.print(f"[bold]加载[/]：{args.json_path}")
    payload = load_qq_json(args.json_path)
    parsed = parse_messages(payload, settings)
    cleaner = Cleaner(settings)
    cleaned = cleaner.clean_many(parsed)
    sessions = split_sessions(cleaned, settings)

    console.print(f"会话数：{len(sessions)}，朋友消息数：" + str(
        sum(1 for s in sessions for m in s.messages if m.is_friend)
    ))

    report = analyze_persona(
        sessions,
        friend_name=settings.friend_name,
        self_name=settings.self_name,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "persona_report.json"
    card_path = out_dir / "persona_card.md"

    save_persona_report(report, json_path)
    card_md = render_persona_card(report)
    save_persona_card(card_md, card_path)
    style_path = out_dir / "persona_style_profile.json"
    style_profile = build_style_profile(
        sessions,
        friend_name=settings.friend_name,
        self_name=settings.self_name,
    )
    save_style_profile(style_profile, style_path)

    # 作息画像
    circadian_profile = compute_circadian_profile(cleaned)
    circadian_path = out_dir / CIRCADIAN_PROFILE_FILENAME
    save_circadian_profile(circadian_profile, circadian_path)

    console.print(f"[green]报告已保存：[/]{json_path}")
    console.print(f"[green]卡片已保存：[/]{card_path}")
    console.print(f"[green]场景画像已保存：[/]{style_path}")
    console.print(
        f"[green]作息画像已保存：[/]{circadian_path}"
        f"（样本 {circadian_profile.sample_size}，{circadian_profile.summary}）"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
