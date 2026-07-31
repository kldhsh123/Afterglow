"""关系分析后台任务与进度状态。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xuwen.analysis.pipeline import analyze_relationship
from xuwen.config import Settings


@dataclass(slots=True)
class AnalysisTask:
    task_id: str
    files: list[str]
    status: str = "pending"
    progress: float = 0.0
    stage: str = "等待开始"
    detail: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnalysisTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, AnalysisTask] = {}
        self._handles: dict[str, asyncio.Task[None]] = {}

    def create(self, files: list[Path]) -> AnalysisTask:
        task = AnalysisTask(
            task_id=uuid.uuid4().hex[:16],
            files=[str(path) for path in files],
        )
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> AnalysisTask | None:
        return self._tasks.get(task_id)

    def list_active(self) -> list[AnalysisTask]:
        return [
            task
            for task in self._tasks.values()
            if task.status not in {"done", "failed", "cancelled"}
        ]

    def attach(self, task_id: str, handle: asyncio.Task[None]) -> None:
        self._handles[task_id] = handle

    async def cancel(self, task_id: str) -> bool:
        handle = self._handles.get(task_id)
        if handle is None or handle.done():
            return False
        handle.cancel()
        return True

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        stage: str | None = None,
        detail: str | None = None,
        error: str | None = None,
        report: dict[str, Any] | None = None,
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        if status is not None:
            task.status = status
            if status in {"done", "failed", "cancelled"}:
                task.finished_at = time.time()
        if progress is not None:
            task.progress = max(0.0, min(1.0, progress))
        if stage is not None:
            task.stage = stage
        if detail is not None:
            task.detail = detail
        if error is not None:
            task.error = error
        if report is not None:
            task.report = report


_MANAGER = AnalysisTaskManager()


def get_analysis_task_manager() -> AnalysisTaskManager:
    return _MANAGER


async def analysis_sse_stream(task_id: str) -> AsyncIterator[str]:
    """推送任务快照，任务结束后关闭 SSE。"""
    manager = get_analysis_task_manager()
    previous = ""
    while True:
        task = manager.get(task_id)
        if task is None:
            return
        payload = json.dumps(task.to_dict(), ensure_ascii=False)
        if payload != previous:
            yield f"data: {payload}\n\n"
            previous = payload
        if task.status in {"done", "failed", "cancelled"}:
            return
        await asyncio.sleep(0.5)


async def run_analysis_task(
    task_id: str,
    files: list[Path],
    settings: Settings,
    *,
    timeline: bool,
    personality: bool,
    resume: bool,
    since: str | None,
) -> None:
    manager = get_analysis_task_manager()
    manager.update(task_id, status="running", progress=0.02, stage="读取与清洗聊天记录")

    def progress(done: int, total: int, stage: str) -> None:
        ratio = done / total if total else 0.0
        if stage == "map":
            value = 0.08 + ratio * 0.72
            label = f"分析对话块 {done}/{total}"
        elif stage == "monthly_reduce":
            value = 0.80 + ratio * 0.10
            label = f"归并月度摘要 {done}/{total}"
        else:
            value = 0.90 + ratio * 0.08
            label = "生成最终报告"
        manager.update(task_id, progress=value, stage=label)

    try:
        report = await analyze_relationship(
            files,
            settings,
            timeline=timeline,
            personality=personality,
            resume=resume,
            since=since,
            progress_cb=progress,
        )
    except asyncio.CancelledError:
        manager.update(task_id, status="cancelled", stage="已取消")
        raise
    except Exception as exc:
        manager.update(
            task_id,
            status="failed",
            stage="分析失败",
            error=f"{type(exc).__name__}: {exc}",
        )
    else:
        manager.update(
            task_id,
            status="done",
            progress=1.0,
            stage="分析完成",
            report=report.to_dict(),
        )
