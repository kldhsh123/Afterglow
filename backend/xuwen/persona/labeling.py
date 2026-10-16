"""离线 / 批量打标的协调器。

把 LanceDB 里"还没打过 mood"的 friend chunks 拉出来，调 Labeler 批量打标，写回。
- 用于 CLI 一次性打标
- 也用于 importer 完成后自动跑一遍
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.memory.schema import TABLE_FRIEND_MESSAGES
from xuwen.memory.store import MemoryStore
from xuwen.persona.labeler import ChunkLabel, Labeler

logger = logging.getLogger(__name__)

_LABEL_WRITE_BATCH_SIZE = 256


@dataclass(slots=True)
class LabelRunReport:
    total_unlabeled: int
    labeled: int
    failed: int
    batches: int
    duration_seconds: float


@dataclass(slots=True)
class _BatchResult:
    rows: list[dict[str, Any]]
    labels: list[ChunkLabel | None]
    failed: int


async def label_all_unlabeled(
    settings: Settings,
    *,
    store: MemoryStore | None = None,
    labeler: Labeler | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    limit: int = 100000,
) -> LabelRunReport:
    """把所有未标的 friend chunks 一次性打完。

    - settings.labeling_enabled = False 时直接返回 0
    - 离线增量：只对 mood 为空的 chunk 调 LLM
    - progress_cb(done, total) 可用于 CLI 进度条
    """
    import time

    if not settings.labeling_enabled:
        return LabelRunReport(0, 0, 0, 0, 0.0)

    start = time.perf_counter()
    owns_store = store is None
    owns_labeler = labeler is None
    if store is None:
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
    if labeler is None:
        labeler = Labeler(settings)

    try:
        pending = await store.list_unlabeled_friend_chunks(limit=limit)
        total = len(pending)
        if total == 0:
            return LabelRunReport(0, 0, 0, 0, 0.0)

        batch_size = max(1, settings.label_batch_size)
        max_concurrency = max(1, settings.label_max_concurrency)
        request_interval = max(0.0, settings.label_request_interval_seconds)
        batches_to_run = [pending[i : i + batch_size] for i in range(0, total, batch_size)]
        semaphore = asyncio.Semaphore(max_concurrency)
        rate_lock = asyncio.Lock()
        next_request_at = 0.0
        done = 0
        failed = 0
        pending_updates: list[dict[str, object]] = []
        pending_done = 0

        async def _flush_updates() -> None:
            nonlocal done, pending_done, pending_updates
            if not pending_updates and pending_done == 0:
                return
            if pending_updates:
                await store.update_labels(TABLE_FRIEND_MESSAGES, pending_updates)
                pending_updates = []
            done += pending_done
            pending_done = 0
            if progress_cb:
                progress_cb(done, total)

        async def _wait_for_rate_slot() -> None:
            nonlocal next_request_at
            if request_interval <= 0:
                return
            async with rate_lock:
                now = time.perf_counter()
                wait_seconds = next_request_at - now
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                    now = time.perf_counter()
                next_request_at = max(now, next_request_at) + request_interval

        async def _run_batch(batch: list[dict[str, Any]]) -> _BatchResult:
            texts = [str(r.get("text") or "") for r in batch]
            async with semaphore:
                await _wait_for_rate_slot()
                try:
                    labels = await labeler.label_messages(texts)
                except Exception as e:  # 极端失败兜底
                    logger.warning(content_text("Labeling batch error: %s"), type(e).__name__)
                    return _BatchResult(
                        rows=batch,
                        labels=[None] * len(batch),
                        failed=len(batch),
                    )
            batch_labels: list[ChunkLabel | None] = list(labels)
            return _BatchResult(rows=batch, labels=batch_labels, failed=0)

        tasks = [asyncio.create_task(_run_batch(batch)) for batch in batches_to_run]
        try:
            for task in asyncio.as_completed(tasks):
                result = await task
                failed += result.failed
                pending_done += len(result.rows)

                for row, lab in zip(result.rows, result.labels, strict=False):
                    if lab is None:
                        continue
                    pending_updates.append(
                        {
                            "id": row.get("id"),
                            "mood": lab.mood,
                            "topic": lab.topic,
                            "importance": lab.importance,
                        }
                    )
                if len(pending_updates) >= _LABEL_WRITE_BATCH_SIZE:
                    await _flush_updates()
            await _flush_updates()
        except asyncio.CancelledError:
            # 用户 Ctrl+C 时尽量把已经返回的标签先落库，再交还取消信号。
            await _flush_updates()
            raise
        finally:
            unfinished = [task for task in tasks if not task.done()]
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)

        duration = round(time.perf_counter() - start, 2)
        return LabelRunReport(
            total_unlabeled=total,
            labeled=done - failed,
            failed=failed,
            batches=len(batches_to_run),
            duration_seconds=duration,
        )
    finally:
        if owns_labeler:
            await labeler.aclose()
        # store 不主动关
        _ = owns_store
