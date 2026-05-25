"""离线导入流水线编排：load → parse → clean → split → chunk → embed → upsert。

入口：`import_history(path, settings)`。
所有 IO 都通过依赖注入，方便 mock。

中断续跑保证：
- chunk_id 基于内容 hash 确定性生成，相同源文件得到相同 id；
- 三路 chunk（friend / window / response_pair）各自独立执行 "查库去重 →
  分批 embed → 立刻 upsert" 流程；
- 任意一批 embedding API 失败时，**已经 upsert 入库的 batch 不丢**，重跑
  会跳过这些已存在的 chunk_id，仅对剩余未入库的部分发 embedding 调用。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xuwen.config import Settings
from xuwen.core.errors import IngestionError
from xuwen.core.models import (
    ChunkBundle,
    DialogueWindowChunk,
    FriendMessageChunk,
    ImportReport,
    ResponsePairChunk,
)
from xuwen.ingestion.chunker import (
    build_friend_chunks,
    build_response_pair_chunks,
    build_window_chunks,
)
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.parser import load_qq_json, parse_messages
from xuwen.ingestion.splitter import build_windows, split_sessions
from xuwen.memory.schema import (
    TABLE_DIALOGUE_WINDOWS,
    TABLE_FRIEND_MESSAGES,
    TABLE_RESPONSE_PAIRS,
)
from xuwen.memory.store import MemoryStore

# 单路 upsert 流水线深度：embed 一批后异步丢后台 upsert，最多积压这么多个未完成
# task。LanceDB 写锁让真正在执行的始终只有 1 个，剩下的排队，主要影响内存中暂存
# 的 embeddings 数量。4 × 100 条 × 4096 维 ≈ 6 MB / track，整体可控。
_UPSERT_INFLIGHT_LIMIT = 4


async def import_history(
    json_path: str | Path,
    settings: Settings,
    *,
    store: MemoryStore | None = None,
    embedder: EmbeddingClient | None = None,
    plugin_name: str | None = None,
    label_progress_cb: Callable[[int, int], None] | None = None,
    chunk_progress_cb: Callable[[int, int], None] | None = None,
    update_circadian: bool = True,
) -> ImportReport:
    """从导出 JSON 文件导入到 LanceDB。

    plugin_name 强制使用某个 plugin；不传则按 plugin 注册顺序自动 match。
    label_progress_cb 透传给打标阶段，用于 CLI 显示进度（done, total）。
    chunk_progress_cb(done, total)：三路 chunk 入库的合并进度。
        total = friend + window + response_pair 的总 chunk 数；
        done 单调递增（每路独立计数后汇总），用于 UI 显示文件内部细化进度。
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

    # 5) embed + upsert（每批边 embed 边写库，支持中断续跑）
    owns_embedder = embedder is None
    if embedder is None:
        embedder = EmbeddingClient(settings)
    if store is None:
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
    try:
        # 三路独立处理：每路先查库去重，再分批 embed + 立刻 upsert。
        # 三路之间并行；同一路内的 batch 之间是串行的，确保 upsert 顺序与 embed 完成顺序一致。
        # 外层 batch_size = embedding_batch_size * embedding_max_concurrency，
        # 让 embedder.embed_texts() 内部仍能切成多个 HTTP 批次跑满并发，
        # 同时控制每次 upsert 的颗粒度（防止上千条一次性进内存）。
        upsert_batch = max(
            settings.embedding_batch_size,
            settings.embedding_batch_size * max(1, settings.embedding_max_concurrency),
        )
        # 三路独立累加各自 done，汇总后通过 chunk_progress_cb 报告整体进度。
        # total 是三路 chunk 总数（去重前），_embed_and_upsert_track 内部
        # 用的是 chunks_by_id 去重后的数；微小偏差可以接受。
        done_per_track = [0, 0, 0]
        total_all = (
            len(bundle.friend_chunks)
            + len(bundle.window_chunks)
            + len(bundle.response_pair_chunks)
        )

        def _make_track_cb(track_idx: int) -> Callable[[int, int], None] | None:
            if chunk_progress_cb is None:
                return None

            def _cb(done: int, _total: int) -> None:
                done_per_track[track_idx] = done
                chunk_progress_cb(sum(done_per_track), total_all)

            return _cb

        friend_stats, window_stats, pair_stats = await asyncio.gather(
            _embed_and_upsert_track(
                embedder=embedder,
                store=store,
                chunks=bundle.friend_chunks,
                text_of=lambda c: c.dialogue_snippet or c.text,
                table=TABLE_FRIEND_MESSAGES,
                upsert_fn=store.upsert_friend_chunks,
                batch_size=upsert_batch,
                progress_cb=_make_track_cb(0),
            ),
            _embed_and_upsert_track(
                embedder=embedder,
                store=store,
                chunks=bundle.window_chunks,
                text_of=lambda c: c.text,
                table=TABLE_DIALOGUE_WINDOWS,
                upsert_fn=store.upsert_window_chunks,
                batch_size=upsert_batch,
                progress_cb=_make_track_cb(1),
            ),
            _embed_and_upsert_track(
                embedder=embedder,
                store=store,
                chunks=bundle.response_pair_chunks,
                text_of=lambda c: c.user_text,
                table=TABLE_RESPONSE_PAIRS,
                upsert_fn=store.upsert_response_pair_chunks,
                batch_size=upsert_batch,
                progress_cb=_make_track_cb(2),
            ),
        )
    finally:
        if owns_embedder:
            await embedder.aclose()
        # store 不主动关：LanceDB 连接复用更稳

    n_friend = friend_stats["upserted"]
    n_window = window_stats["upserted"]
    n_pair = pair_stats["upserted"]
    skipped_friend = friend_stats["skipped"]
    skipped_window = window_stats["skipped"]
    skipped_pair = pair_stats["skipped"]
    embedded_friend = friend_stats["embedded"]
    embedded_window = window_stats["embedded"]
    embedded_pair = pair_stats["embedded"]
    if skipped_friend or skipped_window or skipped_pair:
        notes.append(
            f"续跑跳过已入库的 chunk："
            f"friend {skipped_friend} / window {skipped_window} / pair {skipped_pair}"
        )

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
        embedded_friend=embedded_friend,
        embedded_window=embedded_window,
        embedded_response_pairs=embedded_pair,
        upserted_friend=n_friend,
        upserted_window=n_window,
        upserted_response_pairs=n_pair,
        duration_seconds=duration,
        notes=notes,
    )

    # 如果启用了打标，导入完成后顺手跑一遍（仅对未打标 chunk 操作）。
    # 续跑场景下 n_friend 可能为 0（全部已存在），但库里仍可能有未打标的 chunk，
    # 所以触发条件含 skipped_friend；labeling 本身是增量的，重复调用无害。
    if settings.labeling_enabled and (n_friend > 0 or skipped_friend > 0):
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


async def _embed_and_upsert_track(
    *,
    embedder: EmbeddingClient,
    store: MemoryStore,
    chunks: list[FriendMessageChunk] | list[DialogueWindowChunk] | list[ResponsePairChunk],
    text_of: Callable[[Any], str],
    table: str,
    upsert_fn: Callable[[list[Any], dict[str, list[float]]], Any],
    batch_size: int,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """一路 chunk 的"查库去重 → 分批 embed → 异步 upsert"流程。

    流水线策略：embed 一批后**不等 upsert 完成**就开始 embed 下一批，
    upsert 在后台 task 中跑。Semaphore 限制 in-flight upsert 数量，
    避免高数据量场景下 task 堆积爆内存。LanceDB store 内部有 _write_lock，
    所有后台 upsert 仍会串行执行写入。

    progress_cb(done, total)：每批 embed 完成后回调一次，让上层显示细化进度。
    done = 跳过（已存）+ 已 embed 的数量；total = chunks 总数。

    返回 {total, skipped, embedded, upserted}：
    - total：本次 chunker 产出的 chunk 总数
    - skipped：因 chunk_id 已存在库中而被跳过的（续跑场景）
    - embedded：实际发送给 embedding API 的 chunk 数
    - upserted：成功写入 LanceDB 的 chunk 数（理论上 == embedded）

    单批 embedding 失败时，已 spawn 的 upsert task 会被等到完成（让已 embed
    的批落库）后再向上抛出 embed 错误；下次重跑通过 chunk_id 跳过这些。
    """
    stats = {"total": len(chunks), "skipped": 0, "embedded": 0, "upserted": 0}
    if not chunks:
        if progress_cb is not None:
            progress_cb(0, 0)
        return stats

    # 1) 库去重：跳过 chunk_id 已存在的（续跑场景下避免重复 embed）
    chunks_by_id: dict[str, Any] = {c.chunk_id: c for c in chunks if c.chunk_id}
    existing = await store.existing_ids(table, list(chunks_by_id.keys()))
    pending = [c for cid, c in chunks_by_id.items() if cid not in existing]
    stats["skipped"] = len(existing)
    total_unique = len(chunks_by_id)
    if progress_cb is not None:
        # 已 skip 的算已完成
        progress_cb(stats["skipped"], total_unique)

    # 2) 分批 embed + 异步 upsert（流水线化）
    bs = max(1, batch_size)
    sem = asyncio.Semaphore(_UPSERT_INFLIGHT_LIMIT)
    upsert_tasks: list[asyncio.Task[int]] = []

    async def _upsert_one(
        batch_: list[Any], embeddings_: dict[str, list[float]]
    ) -> int:
        try:
            return await upsert_fn(batch_, embeddings_)
        finally:
            sem.release()

    try:
        for offset in range(0, len(pending), bs):
            batch = pending[offset : offset + bs]
            if not batch:
                continue
            texts = [text_of(c) for c in batch]
            vectors = await embedder.embed_texts(texts)
            if len(vectors) != len(batch):
                raise IngestionError("embedding 返回向量数与 chunk 数不一致")
            embeddings = {
                c.chunk_id: vec for c, vec in zip(batch, vectors, strict=True)
            }
            stats["embedded"] += len(embeddings)
            if progress_cb is not None:
                progress_cb(stats["skipped"] + stats["embedded"], total_unique)
            # 拿一个 slot；slot 满时阻塞 embed loop，防止 task 堆积爆内存
            await sem.acquire()
            upsert_tasks.append(
                asyncio.create_task(_upsert_one(batch, embeddings))
            )
    except BaseException:
        # embed 阶段抛错：等所有已 spawn 的 upsert 完成（给已 embed 的批落库机会），
        # 然后向上抛 embed 错误；upsert 自身错误这里吞掉，不覆盖主因。
        if upsert_tasks:
            results = await asyncio.gather(*upsert_tasks, return_exceptions=True)
            for r in results:
                if not isinstance(r, BaseException):
                    stats["upserted"] += int(r)
        raise

    # embed 全部成功：等所有后台 upsert 落库
    results = await asyncio.gather(*upsert_tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
        stats["upserted"] += int(r)
    return stats


def run_import_sync(json_path: str | Path, settings: Settings) -> ImportReport:
    """同步入口，方便 CLI 调用。"""
    return asyncio.run(import_history(json_path, settings))
