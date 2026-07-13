#!/usr/bin/env python
"""离线生成 persona 卡片与报告。

用法：
    uv run python scripts/analyze_persona.py <chat-json-or-jsonl> [more-files ...]

会生成四个文件到 PERSONA_DATA_DIR：
    - persona_report.json（完整统计）
    - persona_card.md     （供 prompt 模板使用的简洁卡片）
    - persona_style_profile.json（按用户话题分桶的真实回应风格）
    - circadian_profile.json（基于全部文件的作息画像）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from xuwen.config import get_settings
from xuwen.persona.generator import generate_persona_artifacts

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analyze-persona",
        description="合并聊天 JSON / JSONL，生成朋友 persona、风格与作息画像",
    )
    parser.add_argument(
        "json_paths",
        type=Path,
        nargs="+",
        help="一个或多个 Afterglow Chat / QQChatExporter / WeFlow JSON 或 JSONL",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="可选：输出目录（默认使用 settings.persona_data_dir）",
    )
    parser.add_argument(
        "--plugin",
        default=None,
        help="可选：强制使用指定 ingestion plugin；混合格式时不要设置",
    )
    parser.add_argument(
        "--json-emoji-mode",
        choices=("raw", "normalized"),
        default="raw",
        help=(
            "JSON 表情占位符模式：raw（默认）自动把方括号内 1-12 个字符视为表情；"
            "normalized 表示文件已预处理，只识别统一标记 [/表情]。"
        ),
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.require_identity()
    out_dir = args.out_dir or settings.persona_data_dir

    console.print(f"[bold]加载[/]：{len(args.json_paths)} 个文件")
    for path in args.json_paths:
        console.print(f"  {path}")
    result = generate_persona_artifacts(
        args.json_paths,
        settings,
        out_dir=out_dir,
        plugin_name=args.plugin,
        json_emoji_mode=args.json_emoji_mode,
    )
    console.print(
        f"解析消息：{result.parsed_messages}，去重：{result.duplicate_messages}，"
        f"唯一消息：{result.unique_messages}"
    )
    console.print(
        f"会话数：{result.sessions}，清洗后朋友消息总数：{result.friend_messages}"
    )
    console.print(f"[green]报告已保存：[/]{result.report_path}")
    console.print(f"[green]卡片已保存：[/]{result.card_path}")
    console.print(f"[green]场景画像已保存：[/]{result.style_profile_path}")
    console.print(
        f"[green]作息画像已保存：[/]{result.circadian_path}"
        f"（样本 {result.circadian_sample_size}，{result.circadian_summary}）"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
