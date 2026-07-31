"""xuwen 聊天记录导入命令行工具。

用法：
    uv run python -m xuwen.ingestion.cli import <path-to-qq-json>
    uv run python -m xuwen.ingestion.cli stats
    uv run python -m xuwen.ingestion.cli import data.json --ascii  # 终端不支持中文/宽字符时
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from xuwen.config import Settings, get_settings
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.importer import import_history
from xuwen.memory.schema import (
    TABLE_DIALOGUE_WINDOWS,
    TABLE_FRIEND_MESSAGES,
    TABLE_RESPONSE_PAIRS,
)
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

    p_import = sub.add_parser("import", help="把导出的 JSON / JSONL 导入向量库")
    p_import.add_argument(
        "json_paths",
        type=Path,
        nargs="+",
        help="一个或多个导出 JSON / JSONL 文件路径（自动识别格式，可混合 QQ 和微信）",
    )
    p_import.add_argument("--env-file", type=Path, default=None, help="可选：.env 文件路径")
    p_import.add_argument(
        "--plugin",
        default=None,
        help="可选：强制使用某个 plugin（如 qqexporter_v5 / wechat_weflow）。不指定则自动识别。",
    )
    p_import.add_argument(
        "--json-emoji-mode",
        choices=("raw", "normalized"),
        default="raw",
        help=(
            "JSON 表情占位符模式：raw（默认）自动把方括号内 1-12 个字符视为表情；"
            "normalized 表示文件已由用户预处理，只识别统一标记 [/表情]。"
        ),
    )
    p_import.add_argument(
        "--rebuild-tables",
        default="",
        help=(
            "导入前重建指定索引表，逗号分隔：friend,window,pair,all。"
            "切到 CHUNKING_STRATEGY=adaptive 并希望旧数据也生效时，建议用 window,friend。"
        ),
    )
    p_images = sub.add_parser(
        "import-images",
        help="离线导入导出包中的历史图片，调用视觉模型生成摘要并写入图片索引",
    )
    p_images.add_argument(
        "export_dir",
        type=Path,
        help="导出目录；自动收集其中的聊天 JSON/JSONL 和图片资源",
    )
    p_images.add_argument("--env-file", type=Path, default=None, help="可选：.env 文件路径")
    p_images.add_argument(
        "--plugin",
        default=None,
        help="可选：强制使用某个文本导入 plugin 解析消息身份（如 afterglow_v1 / qqexporter_v5）",
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
    p_index = sub.add_parser(
        "index",
        help="对向量表建索引以加速检索（已建过的表跳过；按行数自动分层选索引类型）",
    )
    p_index.add_argument(
        "--min-rows",
        type=int,
        default=None,
        help="表行数低于此值不建索引；默认读 LANCE_INDEX_MIN_ROWS",
    )
    p_index.add_argument(
        "--index-type",
        choices=["auto", "IVF_FLAT", "IVF_PQ", "IVF_HNSW_SQ", "IVF_HNSW_PQ"],
        default="auto",
        help=(
            "索引类型：auto(默认，按行数分层)/IVF_FLAT(<50k 精确)/IVF_PQ(50k-500k 量化)"
            "/IVF_HNSW_SQ(>=500k 大库)；显式指定会覆盖自动选择"
        ),
    )
    p_index.add_argument(
        "--force",
        action="store_true",
        help="即使已建过索引也强制重建（用于从旧 IVF_PQ 切到新分层；replace 现有索引）",
    )
    sub.add_parser(
        "optimize",
        help="对所有向量表跑 table.optimize()：把新写入数据并入索引、清理旧版本",
    )
    p_analyze = sub.add_parser("analyze", help="从原始聊天文件生成关系时间线与性格报告")
    p_analyze.add_argument("paths", nargs="+", type=Path, help="聊天 JSON / JSONL 文件")
    p_analyze.add_argument("--env-file", type=Path, default=None, help="可选：.env 文件路径")
    p_analyze.add_argument("--out-dir", type=Path, default=None, help="输出目录")
    inspect_mode = p_analyze.add_mutually_exclusive_group()
    inspect_mode.add_argument(
        "--list-blocks",
        action="store_true",
        help="只列出确定性分析块，不调用模型",
    )
    inspect_mode.add_argument(
        "--inspect-block",
        default=None,
        metavar="BLOCK_ID",
        help="打印指定分析块的元数据和完整文本，不调用模型",
    )
    mode = p_analyze.add_mutually_exclusive_group()
    mode.add_argument("--timeline-only", action="store_true", help="只生成时间线")
    mode.add_argument("--personality-only", action="store_true", help="只生成性格报告")
    p_analyze.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="复用已完成的确定性分析块（默认开启）",
    )
    p_analyze.add_argument("--since", default=None, metavar="YYYY-MM", help="只分析该月份以后")
    return parser


def _load_settings(env_file: Path | None) -> Settings:
    if env_file is not None:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return get_settings()


async def _rebuild_proactive_profile_from_store(
    settings: Settings,
    store: MemoryStore,
) -> str:
    """从已入库窗口统一重建主动开聊画像，返回摘要。"""
    from xuwen.persona.proactive_profile import (
        PROACTIVE_PROFILE_FILENAME,
        compute_proactive_profile_from_window_rows,
        save_proactive_profile,
    )

    rows = await store.list_dialogue_windows(
        limit=settings.proactive_profile_window_limit
    )
    profile = compute_proactive_profile_from_window_rows(
        rows,
        friend_name=settings.friend_name or "TA",
        self_name=settings.self_name or "我",
        min_gap_minutes=settings.proactive_learning_min_gap_minutes,
        timezone=settings.app_timezone,
    )
    save_proactive_profile(
        profile,
        settings.persona_data_dir / PROACTIVE_PROFILE_FILENAME,
    )
    return profile.summary


async def _rebuild_circadian_profile_from_files(
    settings: Settings,
    paths: list[Path],
    *,
    plugin_name: str | None,
    json_emoji_mode: str,
) -> str:
    """基于批量导入的全部文件重建作息画像。"""
    from xuwen.persona.circadian import (
        CIRCADIAN_PROFILE_FILENAME,
        compute_circadian_profile,
        save_circadian_profile,
    )
    from xuwen.persona.generator import load_persona_dataset

    dataset = await asyncio.to_thread(
        load_persona_dataset,
        paths,
        settings,
        plugin_name=plugin_name,
        json_emoji_mode=json_emoji_mode,
    )
    profile = compute_circadian_profile(dataset.messages)
    save_circadian_profile(
        profile,
        settings.persona_data_dir / CIRCADIAN_PROFILE_FILENAME,
    )
    return (
        f"合并 {dataset.source_files} 个文件，样本 {profile.sample_size}，"
        f"{profile.summary}"
    )


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
        console.print("[red]错误：至少需要一个 JSON / JSONL 文件路径。[/red]")
        return 1

    multi = len(paths) > 1
    if multi:
        console.print(
            f"[bold]批量导入：[/]{len(paths)} 个文件，共享 LanceDB 连接和 Embedding 客户端"
        )
    else:
        console.print(f"[bold]开始导入：[/]{paths[0]}")
    console.print(f"[dim]JSON 表情模式：{args.json_emoji_mode}[/]")

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
    rebuild_tables = _parse_rebuild_tables(args.rebuild_tables)
    if rebuild_tables:
        console.print(
            "[yellow]重建索引表：[/]"
            + ", ".join(rebuild_tables)
            + "（旧表数据会被删除后按本次导入重新生成）"
        )
        _delete_lancedb_table_dirs(settings, rebuild_tables)

    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()
    embedder = EmbeddingClient(settings)

    reports: list = []
    try:
        for idx, path in enumerate(paths):
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
                json_emoji_mode=args.json_emoji_mode,
                label_progress_cb=_label_progress if settings.labeling_enabled else None,
                # 批量场景在全部文件完成后统一重建作息画像。
                update_circadian=not multi,
                update_proactive=not multi,
            )
            reports.append((path, report))
            _print_report(report, L, header=str(path) if multi else None)
    finally:
        await embedder.aclose()

    if multi:
        _print_aggregate(reports, L)
        try:
            circadian_summary = await _rebuild_circadian_profile_from_files(
                settings,
                paths,
                plugin_name=args.plugin,
                json_emoji_mode=args.json_emoji_mode,
            )
            console.print(f"[dim]·[/] 已基于全部文件重建作息画像：{circadian_summary}")
        except Exception:
            logging.getLogger(__name__).warning(
                "批量导入后重建 circadian profile 失败，已忽略",
                exc_info=True,
            )
        try:
            proactive_summary = await _rebuild_proactive_profile_from_store(
                settings,
                store,
            )
            console.print(f"[dim]·[/] 已基于已入库窗口重建主动开聊画像：{proactive_summary}")
        except Exception:
            logging.getLogger(__name__).warning(
                "批量导入后重建 proactive profile 失败，已忽略",
                exc_info=True,
            )

    await _auto_build_vector_indices(settings)

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
        f"history_images: {s.history_images}\n"
        f"live_messages: {s.live_messages}"
    )
    return 0


async def _run_import_images(args: argparse.Namespace) -> int:
    from xuwen.ingestion.image_importer import import_history_images

    settings = _load_settings(args.env_file)
    console.print(f"[bold]开始导入历史图片：[/]{args.export_dir}")
    report = await import_history_images(
        args.export_dir,
        settings,
        plugin_name=args.plugin,
    )
    tbl = Table(title="图片导入报告", show_lines=False)
    tbl.add_column("指标")
    tbl.add_column("值", justify="right")
    tbl.add_row("图片引用", str(report.total_refs))
    tbl.add_row("匹配文件", str(report.matched_files))
    tbl.add_row("缺失/跳过", str(report.missing_files))
    tbl.add_row("唯一图片", str(report.unique_images))
    tbl.add_row("生成/复用摘要", str(report.described_images))
    tbl.add_row("复用重复 sha", str(report.reused_descriptions))
    tbl.add_row("识别失败跳过", str(report.skipped_failed_descriptions))
    tbl.add_row("已存在关联", str(report.skipped_existing_rows))
    tbl.add_row("写入图片索引", str(report.upserted_rows))
    console.print(tbl)
    await _auto_build_vector_indices(settings)
    return 0


async def _auto_build_vector_indices(settings: Settings) -> None:
    """导入完成后自动尝试建索引；小表/已有索引会由 store 内部跳过。"""
    if settings.lance_index_min_rows <= 0:
        return
    store_for_index = MemoryStore(settings)
    await store_for_index.connect()
    store_for_index.ensure_tables()
    index_report = await store_for_index.ensure_vector_indices()
    built = [t for t, s in index_report.items() if s.startswith("built")]
    if built:
        console.print(
            f"[dim]·[/] 已自动为 {len(built)} 张表建立向量索引："
            + ", ".join(built)
        )
    # 其它状态（skip_small / already_indexed / error）不喧宾夺主，cli index 子命令可详查


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


def _parse_rebuild_tables(raw: str) -> list[str]:
    if not raw.strip():
        return []
    mapping = {
        "friend": TABLE_FRIEND_MESSAGES,
        "friends": TABLE_FRIEND_MESSAGES,
        "friend_messages": TABLE_FRIEND_MESSAGES,
        "window": TABLE_DIALOGUE_WINDOWS,
        "windows": TABLE_DIALOGUE_WINDOWS,
        "dialogue_windows": TABLE_DIALOGUE_WINDOWS,
        "pair": TABLE_RESPONSE_PAIRS,
        "pairs": TABLE_RESPONSE_PAIRS,
        "response_pairs": TABLE_RESPONSE_PAIRS,
    }
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if "all" in items:
        return [TABLE_FRIEND_MESSAGES, TABLE_DIALOGUE_WINDOWS, TABLE_RESPONSE_PAIRS]
    out: list[str] = []
    for item in items:
        table = mapping.get(item)
        if table is None:
            raise SystemExit(f"未知 --rebuild-tables 值：{item}")
        if table not in out:
            out.append(table)
    return out


def _delete_lancedb_table_dirs(settings: Settings, tables: list[str]) -> None:
    """连接前删除 LanceDB 表目录。

    LanceDB 的 `drop_table` 在部分本地文件系统组合下可能阻塞；对于离线 CLI
    重建流程，连接数据库前删除选定表目录更简单，之后 `ensure_tables` 会立即重建。
    """
    db_path = Path(settings.lance_db_path)
    for table in tables:
        table_dir = db_path / f"{table}.lance"
        if table_dir.exists():
            shutil.rmtree(table_dir)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "import":
        return asyncio.run(_run_import(args))
    if args.cmd == "import-images":
        return asyncio.run(_run_import_images(args))
    if args.cmd == "stats":
        return asyncio.run(_run_stats(args))
    if args.cmd == "plugins":
        return _run_plugins(args)
    if args.cmd == "label":
        return asyncio.run(_run_label(args))
    if args.cmd == "index":
        return asyncio.run(_run_index(args))
    if args.cmd == "optimize":
        return asyncio.run(_run_optimize(args))
    if args.cmd == "analyze":
        return asyncio.run(_run_analyze(args))
    parser.print_help()
    return 1


async def _run_analyze(args: argparse.Namespace) -> int:
    from xuwen.analysis.pipeline import (
        AnalysisPipelineError,
        analyze_relationship,
        load_analysis_blocks,
    )

    settings = _load_settings(args.env_file)
    if args.list_blocks or args.inspect_block:
        _, _, blocks = await load_analysis_blocks(
            args.paths,
            settings,
            since=args.since,
        )
        if args.list_blocks:
            table = Table(title=f"关系分析块（共 {len(blocks)} 个）", show_lines=False)
            table.add_column("序号", justify="right")
            table.add_column("块 ID")
            table.add_column("起始时间")
            table.add_column("消息", justify="right")
            table.add_column("字符", justify="right")
            table.add_column("会话", justify="right")
            for index, block in enumerate(blocks, 1):
                first_line = block.text.splitlines()[0] if block.text else ""
                table.add_row(
                    str(index),
                    block.block_id,
                    first_line[1:17] if first_line.startswith("[") else "",
                    str(block.message_count),
                    str(len(block.text)),
                    str(len(block.session_ids)),
                )
            console.print(table)
            return 0

        block = next(
            (item for item in blocks if item.block_id == args.inspect_block),
            None,
        )
        if block is None:
            console.print(
                f"[bold red]未找到分析块：[/]{args.inspect_block}。"
                "请确认输入文件、身份配置、--since 和 ANALYSIS_BLOCK_CHAR_BUDGET 与失败时一致。"
            )
            return 1
        console.print_json(
            data={
                "block_id": block.block_id,
                "start_time_ms": block.start_time_ms,
                "end_time_ms": block.end_time_ms,
                "message_count": block.message_count,
                "char_count": len(block.text),
                "session_ids": block.session_ids,
            }
        )
        console.print(block.text, markup=False)
        return 0

    console.print("[bold]正在分析关系记录...[/]")

    def progress(done: int, total: int, stage: str) -> None:
        if total:
            console.print(f"  {stage}: {done}/{total}")

    try:
        report = await analyze_relationship(
            args.paths,
            settings,
            out_dir=args.out_dir,
            timeline=not args.personality_only,
            personality=not args.timeline_only,
            resume=args.resume,
            since=args.since,
            progress_cb=progress,
        )
    except AnalysisPipelineError as exc:
        console.print(f"[bold red]关系分析失败：[/]{exc}")
        return 1
    if report.skipped:
        console.print(
            f"[bold yellow]已跳过 {report.skipped} 个模型拒绝或坏 JSON 的分析块。[/]"
            "详情见输出目录的 failures/ 和 manifest.json。"
        )
    console.print_json(data=report.to_dict())
    return 0


async def _run_index(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()
    # auto = 让 store 内部按行数自动分层选；显式值则强制覆盖
    index_type = None if args.index_type == "auto" else args.index_type
    label = index_type or "auto(按行数分层)"
    force_label = "，强制重建" if args.force else ""
    console.print(f"[bold]建立向量索引（{label}{force_label}）...[/]")
    report = await store.ensure_vector_indices(
        min_rows=args.min_rows,
        index_type=index_type,
        force=args.force,
    )
    for table, status in report.items():
        console.print(f"  {table}: {status}")
    return 0


async def _run_optimize(_args: argparse.Namespace) -> int:
    settings = get_settings()
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()
    console.print("[bold]优化所有表（合并增量到索引 + 清理旧版本）...[/]")
    report = await store.optimize_all_tables()
    for table, status in report.items():
        console.print(f"  {table}: {status}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
