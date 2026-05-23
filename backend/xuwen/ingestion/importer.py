"""离线导入流水线编排：load → parse → clean → split → chunk → embed → upsert。

入口：`import_history(path, settings)`。
所有 IO 都通过依赖注入，方便 mock。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from xuwen.config import Settings
from xuwen.core.errors import IngestionError
from xuwen.core.models import ChunkBundle, ImportReport
from xuwen.ingestion.chunker import (
    build_friend_chunks,
    build_response_pair_chunks,
    build_window_chunks,
)
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.parser import load_qq_json, parse_messages
from xuwen.ingestion.splitter import build_windows, split_sessions
from xuwen.memory.store import MemoryStore


async def import_history(
    json_path: str | Path,
    settings: Settings,
    *,
    store: MemoryStore | None = None,
    embedder: EmbeddingClient | None = None,
    plugin_name: str | None = None,
    label_progress_cb: Callable[[int, int], None] | None = None,
    update_circadian: bool = True,
) -> ImportReport:
    """从导出 JSON 文件导入到 LanceDB。

    plugin_name 强制使用某个 plugin；不传则按 plugin 注册顺序自动 match。
    label_progress_cb 透传给打标阶段，用于 CLI 显示进度（done, total）。
    update_circadian=False 时跳过画像生成，留给多文件批量导入的调用方在最末一次或
    跑完所有文件后单独触发，避免中间文件覆盖最终画像。
    """
    settings.require_identity()

    start = time.perf_counter()
    payload = load_qq_json(json_path)
    raw_count = len(payload.get("messages") or [])

    # 1) parse（plugin 自动识别）
    parsed = parse_messages(payload, settings, plugin_name=plugin_name)

    # 2) clean
    cleaner = Cleaner(settings)
    cleaned = cleaner.clean_many(parsed)

    # 3) split
    sessions = split_sessions(cleaned, settings)
    windows = build_windows(sessions, settings)

    # 4) chunk —— 直接基于 sessions，确保 friend chunk 的上下文不跨 session
    friend_chunks = build_friend_chunks(sessions, settings)
    window_chunks = build_window_chunks(windows, settings)
    response_pair_chunks = build_response_pair_chunks(sessions, settings)

    bundle = ChunkBundle(
        friend_chunks=friend_chunks,
        window_chunks=window_chunks,
        response_pair_chunks=response_pair_chunks,
    )

    notes: list[str] = []
    if not bundle.friend_chunks:
        notes.append(
            "未产出任何朋友单条 chunk，请确认 FRIEND_UID 是否正确"
            "（检查 .env 中 SELF_UID/FRIEND_UID 与导出 JSON 的 chatInfo.selfUid 是否对应）。"
        )

    if not bundle.friend_chunks and not bundle.window_chunks:
        return ImportReport(
            total_raw_messages=raw_count,
            parsed_messages=len(parsed),
            skipped_messages=raw_count - len(parsed),
            sessions=len(sessions),
            friend_chunks=0,
            window_chunks=0,
            response_pairs=0,
            embedded_friend=0,
            embedded_window=0,
            embedded_response_pairs=0,
            upserted_friend=0,
            upserted_window=0,
            upserted_response_pairs=0,
            duration_seconds=round(time.perf_counter() - start, 3),
            notes=notes or ["未产出任何 chunk，请检查导入数据"],
        )

    # 5) embed
    owns_embedder = embedder is None
    if embedder is None:
        embedder = EmbeddingClient(settings)
    if store is None:
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
    try:
        # 三路 embedding 并行执行，节省 API 延迟
        friend_embeddings, window_embeddings, pair_embeddings = await asyncio.gather(
            _embed_chunks(
                embedder,
                [c.chunk_id for c in bundle.friend_chunks],
                [c.dialogue_snippet or c.text for c in bundle.friend_chunks],
            ),
            _embed_chunks(
                embedder,
                [c.chunk_id for c in bundle.window_chunks],
                [c.text for c in bundle.window_chunks],
            ),
            _embed_chunks(
                embedder,
                [c.chunk_id for c in bundle.response_pair_chunks],
                [c.user_text for c in bundle.response_pair_chunks],
            ),
        )

        # 6) upsert
        n_friend = await store.upsert_friend_chunks(bundle.friend_chunks, friend_embeddings)
        n_window = await store.upsert_window_chunks(bundle.window_chunks, window_embeddings)
        n_pair = await store.upsert_response_pair_chunks(
            bundle.response_pair_chunks,
            pair_embeddings,
        )
    finally:
        if owns_embedder:
            await embedder.aclose()
        # store 不主动关：LanceDB 连接复用更稳

    # 生成 / 更新作息画像 circadian_profile.json
    # 多文件批量导入场景下 CLI 会传 update_circadian=False 跳过本步，
    # 由 CLI 在循环结束后统一处理，避免每个中间文件都重算一次画像。
    if update_circadian:
        try:
            from xuwen.persona.circadian import (
                CIRCADIAN_PROFILE_FILENAME,
                compute_circadian_profile,
                save_circadian_profile,
            )

            profile = compute_circadian_profile(cleaned)
            save_circadian_profile(
                profile,
                settings.persona_data_dir / CIRCADIAN_PROFILE_FILENAME,
            )
        except Exception:
            # 画像生成失败不影响导入主链路
            import logging

            logging.getLogger(__name__).warning(
                "circadian profile 生成失败，已忽略", exc_info=True
            )

    duration = round(time.perf_counter() - start, 3)
    report = ImportReport(
        total_raw_messages=raw_count,
        parsed_messages=len(parsed),
        skipped_messages=raw_count - len(parsed),
        sessions=len(sessions),
        friend_chunks=len(bundle.friend_chunks),
        window_chunks=len(bundle.window_chunks),
        response_pairs=len(bundle.response_pair_chunks),
        embedded_friend=len(friend_embeddings),
        embedded_window=len(window_embeddings),
        embedded_response_pairs=len(pair_embeddings),
        upserted_friend=n_friend,
        upserted_window=n_window,
        upserted_response_pairs=n_pair,
        duration_seconds=duration,
        notes=notes,
    )

    # 如果启用了打标，导入完成后顺手跑一遍（仅新增 chunk）
    if settings.labeling_enabled and n_friend > 0:
        from xuwen.persona.labeling import label_all_unlabeled

        try:
            label_report = await label_all_unlabeled(
                settings,
                store=store,
                progress_cb=label_progress_cb,
            )
            report.notes.append(
                f"打标：{label_report.labeled}/{label_report.total_unlabeled} 条已标，"
                f"耗时 {label_report.duration_seconds}s"
            )
        except Exception as e:  # 打标失败不影响导入
            report.notes.append(f"打标失败：{type(e).__name__}（可稍后用 cli label 手动重试）")

    return report


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


async def _embed_chunks(
    embedder: EmbeddingClient,
    ids: list[str],
    texts: list[str],
) -> dict[str, list[float]]:
    """把文本列表向量化并按 id 组装为字典。"""
    if not ids:
        return {}
    if len(ids) != len(texts):
        raise IngestionError("chunk ids 与 texts 长度不一致")
    vectors = await embedder.embed_texts(texts)
    return {cid: vec for cid, vec in zip(ids, vectors, strict=True)}


def run_import_sync(json_path: str | Path, settings: Settings) -> ImportReport:
    """同步入口，方便 CLI 调用。"""
    return asyncio.run(import_history(json_path, settings))
