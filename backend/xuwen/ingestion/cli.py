"""xuwen ingestion CLI。

用法：
    uv run python -m xuwen.ingestion.cli import <path-to-qq-json>
    uv run python -m xuwen.ingestion.cli stats
    uv run python -m xuwen.ingestion.cli import data.json --ascii  # 终端不支持中文/宽字符时
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from xuwen.config import Settings, get_settings
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.importer import import_history
from xuwen.memory.store import MemoryStore

console = Console()

# 初始化日志：把 WARNING 及以上的消息打到控制台（包括 embedding 上游错误正文）
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, show_time=False, show_path=False)],
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xuwen-ingest",
        description="续温 / Afterglow 历史聊天导入工具",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="使用纯 ASCII 列名输出表格（适用于不支持中文宽字符的终端）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="把导出的 JSON 导入向量库")
    p_import.add_argument(
        "json_paths",
        type=Path,
        nargs="+",
        help="一个或多个导出 JSON 文件路径（自动识别格式，可混合 QQ 和微信）",
    )
    p_import.add_argument("--env-file", type=Path, default=None, help="可选：.env 文件路径")
    p_import.add_argument(
        "--plugin",
        default=None,
        help="可选：强制使用某个 plugin（如 qqexporter_v5 / wechat_weflow）。不指定则自动识别。",
    )

    sub.add_parser("stats", help="显示向量库当前统计")
    sub.add_parser("plugins", help="列出所有内置导入 plugin")
    p_label = sub.add_parser("label", help="给未打标的 friend_messages 跑一遍小模型打标")
    p_label.add_argument(
        "--limit",
        type=int,
        default=100000,
        help="本次最多处理多少条（默认 10 万，等于全标）",
    )
    return parser


def _load_settings(env_file: Path | None) -> Settings:
    if env_file is not None:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return get_settings()


# 中文 / ASCII 双套列名
_LABELS_CN = {
    "metric": "指标",
    "value": "值",
    "raw": "原始消息数",
    "parsed": "解析成功",
    "skipped": "跳过 / 失败",
    "sessions": "会话数",
    "friend": "朋友单条 chunk",
    "window": "对话窗口 chunk",
    "pair": "问答响应 pair",
    "emb_friend": "向量化（单条）",
    "emb_window": "向量化（窗口）",
    "emb_pair": "向量化（pair）",
    "up_friend": "入库（单条）",
    "up_window": "入库（窗口）",
    "up_pair": "入库（pair）",
    "duration": "耗时 (秒)",
    "title": "导入报告",
    "tip": "提示：",
}
_LABELS_ASCII = {
    "metric": "metric",
    "value": "value",
    "raw": "raw_messages",
    "parsed": "parsed",
    "skipped": "skipped",
    "sessions": "sessions",
    "friend": "friend_chunks",
    "window": "window_chunks",
    "pair": "response_pairs",
    "emb_friend": "embedded_friend",
    "emb_window": "embedded_window",
    "emb_pair": "embedded_response_pairs",
    "up_friend": "upserted_friend",
    "up_window": "upserted_window",
    "up_pair": "upserted_response_pairs",
    "duration": "duration_sec",
    "title": "Import Report",
    "tip": "TIP:",
}


async def _run_import(args: argparse.Namespace) -> int:
    settings = _load_settings(args.env_file)
    L = _LABELS_ASCII if args.ascii else _LABELS_CN

    paths: list[Path] = list(args.json_paths)
    if not paths:
        console.print("[red]错误：至少需要一个 JSON 文件路径。[/red]")
        return 1

    multi = len(paths) > 1
    if multi:
        console.print(
            f"[bold]批量导入：[/]{len(paths)} 个文件，共享 LanceDB 连接和 Embedding 客户端"
        )
    else:
        console.print(f"[bold]开始导入：[/]{paths[0]}")

    # 打标阶段进度回调：按 batch 打印「已打标 done/total」
    # 只有 labeling_enabled=true 时才会真正触发（否则 importer 内部跳过）
    label_started = [False]

    def _label_progress(done: int, total: int) -> None:
        if not label_started[0]:
            console.print(
                f"[dim]·[/] 开始打标 [bold]{total}[/] 条新 chunk "
                f"（模型 {settings.label_model}，batch={settings.label_batch_size}，"
                f"并发={settings.label_max_concurrency}，间隔={settings.label_request_interval_seconds}s）"
            )
            label_started[0] = True
        # 不刷屏：每完成 10% 或最后一批才打印
        if done == total or done % max(1, total // 10) < settings.label_batch_size:
            console.print(f"[dim]·[/] 已打标 {done}/{total}")

    # 多文件场景：CLI 拥有 store + embedder，循环里复用同一份；
    # 单文件场景也走这条路径，省一次 connect/disconnect。
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()
    embedder = EmbeddingClient(settings)

    reports: list = []
    try:
        for idx, path in enumerate(paths):
            is_last = idx == len(paths) - 1
            if multi:
                console.print(
                    f"\n[bold cyan][{idx + 1}/{len(paths)}][/] 处理 {path}"
                )
            report = await import_history(
                path,
                settings,
                store=store,
                embedder=embedder,
                plugin_name=args.plugin,
                label_progress_cb=_label_progress if settings.labeling_enabled else None,
                # 中间文件跳过 circadian 计算，避免被后续文件覆盖；
                # 最后一个文件触发，画像基于该文件的 cleaned 数据生成。
                update_circadian=is_last,
            )
            reports.append((path, report))
            _print_report(report, L, header=str(path) if multi else None)
    finally:
        await embedder.aclose()

    if multi:
        _print_aggregate(reports, L)
        # circadian 局限提示：只反映最后一个文件
        console.print(
            "[yellow]提示：[/]作息画像 (circadian_profile.json) 仅基于最后一个文件计算，"
            "建议把数据量最大或最具代表性的对话放在最后一位。"
        )
    return 0


def _print_report(report, L: dict[str, str], *, header: str | None = None) -> None:
    """打印单个文件的导入报告。"""
    title = L["title"] if header is None else f"{L['title']} — {header}"
    tbl = Table(title=title, show_lines=False)
    tbl.add_column(L["metric"])
    tbl.add_column(L["value"], justify="right")
    tbl.add_row(L["raw"], str(report.total_raw_messages))
    tbl.add_row(L["parsed"], str(report.parsed_messages))
    tbl.add_row(L["skipped"], str(report.skipped_messages))
    tbl.add_row(L["sessions"], str(report.sessions))
    tbl.add_row(L["friend"], str(report.friend_chunks))
    tbl.add_row(L["window"], str(report.window_chunks))
    tbl.add_row(L["pair"], str(report.response_pairs))
    tbl.add_row(L["emb_friend"], str(report.embedded_friend))
    tbl.add_row(L["emb_window"], str(report.embedded_window))
    tbl.add_row(L["emb_pair"], str(report.embedded_response_pairs))
    tbl.add_row(L["up_friend"], str(report.upserted_friend))
    tbl.add_row(L["up_window"], str(report.upserted_window))
    tbl.add_row(L["up_pair"], str(report.upserted_response_pairs))
    tbl.add_row(L["duration"], str(report.duration_seconds))
    console.print(tbl)
    for note in report.notes:
        console.print(f"[yellow]{L['tip']}[/]{note}")


def _print_aggregate(reports: list, L: dict[str, str]) -> None:
    """打印多文件汇总。所有数值简单相加，duration 累加为总耗时。"""
    if not reports:
        return
    fields = (
        "total_raw_messages", "parsed_messages", "skipped_messages", "sessions",
        "friend_chunks", "window_chunks", "response_pairs",
        "embedded_friend", "embedded_window", "embedded_response_pairs",
        "upserted_friend", "upserted_window", "upserted_response_pairs",
        "duration_seconds",
    )
    totals: dict[str, float] = {f: 0 for f in fields}
    for _, r in reports:
        for f in fields:
            totals[f] += getattr(r, f) or 0

    label_map = (
        (L["raw"], "total_raw_messages"),
        (L["parsed"], "parsed_messages"),
        (L["skipped"], "skipped_messages"),
        (L["sessions"], "sessions"),
        (L["friend"], "friend_chunks"),
        (L["window"], "window_chunks"),
        (L["pair"], "response_pairs"),
        (L["emb_friend"], "embedded_friend"),
        (L["emb_window"], "embedded_window"),
        (L["emb_pair"], "embedded_response_pairs"),
        (L["up_friend"], "upserted_friend"),
        (L["up_window"], "upserted_window"),
        (L["up_pair"], "upserted_response_pairs"),
        (L["duration"], "duration_seconds"),
    )
    tbl = Table(title=f"{L['title']} (合计 {len(reports)} 个文件)", show_lines=False)
    tbl.add_column(L["metric"])
    tbl.add_column(L["value"], justify="right")
    for label, field in label_map:
        val = totals[field]
        tbl.add_row(label, f"{val:.3f}" if field == "duration_seconds" else str(int(val)))
    console.print(tbl)


async def _run_stats(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()
    s = await store.stats()
    console.print(
        f"[bold]LanceDB 路径：[/]{settings.lance_db_path}\n"
        f"friend_messages: {s.friend_messages}\n"
        f"dialogue_windows: {s.dialogue_windows}\n"
        f"live_messages: {s.live_messages}"
    )
    return 0


async def _run_label(args: argparse.Namespace) -> int:
    """给未打标的 friend chunks 跑一遍小模型打标。"""
    from xuwen.persona.labeling import label_all_unlabeled

    settings = get_settings()
    if not settings.labeling_enabled:
        console.print(
            "[yellow]LABELING_ENABLED=false，已跳过打标。"
            "如需启用，请在 .env 设置 LABELING_ENABLED=true 并配置 LABEL_API_KEY。[/yellow]"
        )
        return 1

    last_done = [0]

    def progress_cb(done: int, total: int) -> None:
        # 每 batch 一次回调，console 打印当前进度
        last_done[0] = done
        console.print(f"  · 已打标 {done}/{total}")

    console.print(
        f"[bold]开始打标[/]：模型={settings.label_model}, "
        f"batch={settings.label_batch_size}, "
        f"并发={settings.label_max_concurrency}, "
        f"间隔={settings.label_request_interval_seconds}s"
    )
    report = await label_all_unlabeled(
        settings,
        progress_cb=progress_cb,
        limit=args.limit,
    )
    tbl = Table(title="打标报告")
    tbl.add_column("指标")
    tbl.add_column("值", justify="right")
    tbl.add_row("待标 chunk", str(report.total_unlabeled))
    tbl.add_row("已打标", str(report.labeled))
    tbl.add_row("失败/退化为 unknown", str(report.failed))
    tbl.add_row("LLM 调用次数", str(report.batches))
    tbl.add_row("耗时 (秒)", str(report.duration_seconds))
    console.print(tbl)
    return 0


def _run_plugins(_args: argparse.Namespace) -> int:
    from xuwen.ingestion.plugins import list_plugins

    plugins = list_plugins()
    if not plugins:
        console.print("[yellow]当前没有注册任何 plugin。[/yellow]")
        return 1
    tbl = Table(title="已注册的导入 plugin", show_lines=False)
    tbl.add_column("name")
    tbl.add_column("display_name")
    for p in plugins:
        tbl.add_row(p.name, p.display_name)
    console.print(tbl)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "import":
        return asyncio.run(_run_import(args))
    if args.cmd == "stats":
        return asyncio.run(_run_stats(args))
    if args.cmd == "plugins":
        return _run_plugins(args)
    if args.cmd == "label":
        return asyncio.run(_run_label(args))
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
