#!/usr/bin/env python3
"""检查主动消息倾向报告是否适合投入运行时自动发送。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from xuwen.analysis.proactive import load_proactive_analysis
from xuwen.analysis.proactive_quality import evaluate_proactive_quality
from xuwen.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="检查主动消息分析报告的数据质量")
    parser.add_argument("--env-file", type=Path, default=None, help="可选：.env 文件路径")
    parser.add_argument("--report", type=Path, default=None, help="主动消息分析报告路径")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()
    settings = (
        Settings(_env_file=args.env_file)  # type: ignore[call-arg]
        if args.env_file
        else Settings()
    )
    path = args.report or settings.analysis_data_dir / "proactive_analysis.json"
    report = load_proactive_analysis(path)
    if report is None:
        print(f"未找到或无法读取主动消息分析报告：{path}")
        print("请先显式运行：uv run xuwen analyze-proactive <聊天记录文件>")
        return 2

    quality = evaluate_proactive_quality(
        report,
        min_gap_minutes=settings.proactive_learning_min_gap_minutes,
    )
    if args.json:
        print(json.dumps(quality.to_dict(), ensure_ascii=False, indent=2))
    else:
        verdict = "可以启用" if quality.recommended_enabled else "不建议启用"
        print(f"主动消息数据质量：{quality.status}（{verdict}）")
        print(quality.summary)
        print("\n指标：")
        for key, value in quality.metrics.items():
            print(f"  {key}: {value}")
        if quality.issues:
            print("\n问题：")
            for issue in quality.issues:
                print(f"  - {issue}")
        if quality.recommendations:
            print("\n建议：")
            for recommendation in quality.recommendations:
                print(f"  - {recommendation}")
    return 0 if quality.recommended_enabled else 2


if __name__ == "__main__":
    raise SystemExit(main())
