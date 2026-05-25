"""导入任务管理：上传 → 解析预览 → 后台导入 → persona 画像 → 进度查询。

设计：
- 任务状态保存在内存里的 dict（进程级单例）。重启即丢失。
- 每个任务有唯一 task_id，前端通过它查询进度或订阅 SSE。
- 实际导入复用现有 xuwen.ingestion.importer.import_history，但跑在后台 task 里。
- 文件上传后存到 settings.config_ui_uploads_dir，导入完成后保留（用户可手动清）。
- 后台日志通过 print(flush=True) 直接打到 stdout，让用户在终端也能看到进度。
- 完成所有文件入库后，自动用用户指定的 persona_source 跑画像分析，
  产出 persona_card.md / persona_report.json / persona_style_profile.json
  / circadian_profile.json 到 settings.persona_data_dir。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xuwen.config import Settings


def _log(line: str) -> None:
    """把进度信息打到后端控制台。"""
    print(f"[导入] {line}", file=sys.stdout, flush=True)


@dataclass
class ImportTask:
    task_id: str
    files: list[str]
    file_names: list[str]
    status: str
    progress: float = 0.0
    stage: str = ""
    detail: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    report: dict[str, Any] | None = None
    persona_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImportTaskManager:
    """进程级单例：管理所有导入任务。"""

    def __init__(self) -> None:
        self._tasks: dict[str, ImportTask] = {}
        self._lock = asyncio.Lock()
        self._handles: dict[str, asyncio.Task[None]] = {}

    def create(
        self,
        files: list[Path],
        file_names: list[str],
        *,
        persona_source: Path | None = None,
    ) -> ImportTask:
        task_id = uuid.uuid4().hex[:16]
        task = ImportTask(
            task_id=task_id,
            files=[str(p) for p in files],
            file_names=file_names,
            status="pending",
            persona_source=str(persona_source) if persona_source else None,
        )
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> ImportTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[ImportTask]:
        return list(self._tasks.values())

    def list_active(self) -> list[ImportTask]:
        """所有未结束的任务（前端刷新页面后用来恢复跟踪）。"""
        return [
            t for t in self._tasks.values()
            if t.status not in ("done", "failed", "cancelled")
        ]

    async def cancel(self, task_id: str) -> bool:
        handle = self._handles.get(task_id)
        if handle and not handle.done():
            handle.cancel()
            return True
        return False

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
        t = self._tasks.get(task_id)
        if t is None:
            return
        if status is not None:
            t.status = status
            if status in ("done", "failed", "cancelled"):
                t.finished_at = time.time()
        if progress is not None:
            t.progress = max(0.0, min(1.0, progress))
        if stage is not None:
            t.stage = stage
        if detail is not None:
            t.detail = detail
        if error is not None:
            t.error = error
        if report is not None:
            t.report = report

    def attach_handle(self, task_id: str, handle: asyncio.Task[None]) -> None:
        self._handles[task_id] = handle


_MANAGER: ImportTaskManager | None = None


def get_manager() -> ImportTaskManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ImportTaskManager()
    return _MANAGER


def _pick_persona_source(files: list[str]) -> str | None:
    """没指定 persona_source 时，挑消息数最多的那个文件作为画像参考。"""
    if not files:
        return None
    from xuwen.web_ui.inspect_file import inspect_chat_file

    best: tuple[int, str] = (-1, files[0])
    for p in files:
        try:
            ir = inspect_chat_file(Path(p))
            if ir.total_messages > best[0]:
                best = (ir.total_messages, p)
        except Exception:
            continue
    return best[1]


async def _run_persona_analysis(json_path: Path, settings: Settings) -> dict[str, Any]:
    """生成 persona 卡片 + 风格画像 + 作息画像（参考 scripts/analyze_persona.py）。

    返回简要统计供前端展示。
    """
    from xuwen.ingestion.cleaner import Cleaner
    from xuwen.ingestion.parser import load_qq_json, parse_messages
    from xuwen.ingestion.splitter import split_sessions
    from xuwen.persona.analyzer import analyze_persona
    from xuwen.persona.card import (
        render_persona_card,
        save_persona_card,
        save_persona_report,
    )
    from xuwen.persona.circadian import (
        CIRCADIAN_PROFILE_FILENAME,
        compute_circadian_profile,
        save_circadian_profile,
    )
    from xuwen.persona.style_profile import build_style_profile, save_style_profile

    settings.require_identity()
    out_dir = settings.persona_data_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = await asyncio.to_thread(load_qq_json, json_path)
    parsed = await asyncio.to_thread(parse_messages, payload, settings)
    cleaner = Cleaner(settings)
    cleaned = await asyncio.to_thread(cleaner.clean_many, parsed)
    sessions = split_sessions(cleaned, settings)

    report = await asyncio.to_thread(
        analyze_persona,
        sessions,
        friend_name=settings.friend_name,
        self_name=settings.self_name,
    )
    save_persona_report(report, out_dir / "persona_report.json")
    save_persona_card(render_persona_card(report), out_dir / "persona_card.md")

    style_profile = await asyncio.to_thread(
        build_style_profile,
        sessions,
        friend_name=settings.friend_name,
        self_name=settings.self_name,
    )
    save_style_profile(style_profile, out_dir / "persona_style_profile.json")

    circadian = compute_circadian_profile(cleaned)
    save_circadian_profile(circadian, out_dir / CIRCADIAN_PROFILE_FILENAME)

    return {
        "friend_messages": sum(1 for s in sessions for m in s.messages if m.is_friend),
        "sessions": len(sessions),
        "circadian_summary": circadian.summary,
    }


async def _heartbeat(
    task_id: str,
    base_stage: str,
    start_ts: float,
) -> None:
    """每 5 秒刷新 stage 后缀显示已用时，让前端看到任务还活着。"""
    mgr = get_manager()
    while True:
        await asyncio.sleep(5)
        elapsed = int(time.time() - start_ts)
        mins, secs = divmod(elapsed, 60)
        hrs, mins = divmod(mins, 60)
        tag = f"已用时 {hrs}:{mins:02d}:{secs:02d}" if hrs else f"已用时 {mins}:{secs:02d}"
        mgr.update(task_id, stage=f"{base_stage} · {tag}")


@dataclass
class _FileProgressState:
    """单文件处理时的可变进度计数。

    把状态封装在 dataclass 实例里，让 closure 引用本函数局部对象的属性，
    而不是引用循环变量本身，从而避免 B023 告警。
    """

    chunk_done: int = 0
    chunk_total: int = 0
    label_done: int = 0
    label_total: int = 0
    phase: str = "embed"  # embed → upsert_wait → label


async def _process_one_file(
    *,
    task_id: str,
    idx: int,
    total: int,
    path: str,
    display_name: str,
    settings: Settings,
    mgr: ImportTaskManager,
) -> Any:
    """处理单个文件：起心跳 + 调 import_history + 透出条目级进度。

    抽成独立函数主要是为了让所有 closure 引用的都是本函数局部变量（不会随
    外层 for 循环变化），避免循环里嵌套闭包触发 B023 lint。
    """
    from xuwen.ingestion.importer import import_history

    base_stage = f"正在处理 {display_name}（{idx + 1}/{total}）"
    _log(base_stage)
    file_start_progress = 0.05 + 0.80 * (idx / max(total, 1))
    file_end_progress = 0.05 + 0.80 * ((idx + 1) / max(total, 1))
    # 启用打标时：向量化 0%~65%，打标 65%~100%；未启用时向量化占满 0%~100%。
    embed_fraction = 0.65 if settings.labeling_enabled else 1.0
    file_embed_end = (
        file_start_progress + (file_end_progress - file_start_progress) * embed_fraction
    )
    mgr.update(task_id, stage=base_stage, progress=file_start_progress)

    state = _FileProgressState()

    def _on_chunk(done: int, total_chunks: int) -> None:
        state.chunk_done = done
        state.chunk_total = total_chunks
        if total_chunks <= 0:
            return
        file_progress = file_start_progress + (file_embed_end - file_start_progress) * (
            done / total_chunks
        )
        if done >= total_chunks:
            state.phase = "upsert_wait"
            stage = f"{base_stage} · 向量化完成，正在写入数据库…"
        else:
            state.phase = "embed"
            stage = f"{base_stage} · 向量化中 {done}/{total_chunks} 条"
        mgr.update(task_id, progress=file_progress, stage=stage)

    def _on_label(done: int, total_labels: int) -> None:
        state.label_done = done
        state.label_total = total_labels
        state.phase = "label"
        if total_labels <= 0:
            return
        file_progress = file_embed_end + (file_end_progress - file_embed_end) * (
            done / total_labels
        )
        stage = f"{base_stage} · 打标中 {done}/{total_labels} 条"
        mgr.update(task_id, progress=file_progress, stage=stage)

    def _stage_for_heartbeat() -> str:
        if state.phase == "label" and state.label_total > 0:
            return f"{base_stage} · 打标中 {state.label_done}/{state.label_total} 条"
        if state.phase == "upsert_wait":
            return f"{base_stage} · 向量化完成，正在写入数据库…"
        if state.chunk_total > 0:
            return f"{base_stage} · 向量化中 {state.chunk_done}/{state.chunk_total} 条"
        return base_stage

    hb_start = time.time()

    async def _hb_with_chunk() -> None:
        while True:
            await asyncio.sleep(5)
            elapsed = int(time.time() - hb_start)
            mins, secs = divmod(elapsed, 60)
            hrs, mins = divmod(mins, 60)
            tag = (
                f"已用时 {hrs}:{mins:02d}:{secs:02d}" if hrs else f"已用时 {mins}:{secs:02d}"
            )
            mgr.update(task_id, stage=f"{_stage_for_heartbeat()} · {tag}")

    hb = asyncio.create_task(_hb_with_chunk())
    try:
        report = await import_history(
            Path(path),
            settings,
            update_circadian=False,
            chunk_progress_cb=_on_chunk,
            label_progress_cb=_on_label,
        )
    finally:
        hb.cancel()
        try:
            await hb
        except (asyncio.CancelledError, Exception):
            pass
    return report


async def run_import_task(task_id: str, settings: Settings) -> None:
    """后台跑的实际导入流程。

    阶段：
    1. 逐文件向量化入库 + 打标（85% 进度区间）
    2. 用 persona_source 跑画像分析（15% 进度区间）
    """
    mgr = get_manager()
    task = mgr.get(task_id)
    if task is None:
        return

    _log(f"任务 {task_id} 启动，共 {len(task.files)} 个文件")
    try:
        mgr.update(task_id, status="importing", stage="正在导入聊天记录…", progress=0.02)
        reports: list[dict[str, Any]] = []
        total = len(task.files)
        for idx, path in enumerate(task.files):
            display_name = task.file_names[idx]
            report = await _process_one_file(
                task_id=task_id,
                idx=idx,
                total=total,
                path=path,
                display_name=display_name,
                settings=settings,
                mgr=mgr,
            )
            try:
                report_dict = asdict(report)
            except Exception:
                report_dict = {"raw": str(report)}
            reports.append({"file": display_name, "report": report_dict})
            _log(
                f"  → 完成：朋友单条 {getattr(report, 'friend_chunks', 0)}，"
                f"对话窗口 {getattr(report, 'window_chunks', 0)}，"
                f"响应 pair {getattr(report, 'response_pairs', 0)}"
            )

        # ===== 阶段 2：persona 画像 =====
        persona_source = task.persona_source or _pick_persona_source(task.files)
        if persona_source:
            persona_name = (
                task.file_names[task.files.index(persona_source)]
                if persona_source in task.files
                else Path(persona_source).name
            )
            stage = f"生成人格画像与作息分析（参考 {persona_name}）…"
            _log(stage)
            mgr.update(task_id, status="persona", stage=stage, progress=0.90)
            hb = asyncio.create_task(_heartbeat(task_id, stage, time.time()))
            persona_stats: dict[str, Any] | None = None
            try:
                persona_stats = await _run_persona_analysis(Path(persona_source), settings)
            except Exception as e:
                _log(f"  → 画像分析失败（已跳过）：{type(e).__name__}: {e}")
                reports.append({"persona_error": f"{type(e).__name__}: {e}"})
            finally:
                hb.cancel()
                try:
                    await hb
                except (asyncio.CancelledError, Exception):
                    pass
            if persona_stats:
                _log(
                    f"  → 画像完成：朋友消息 {persona_stats['friend_messages']}，"
                    f"会话 {persona_stats['sessions']}，作息：{persona_stats['circadian_summary']}"
                )
                for r in reports:
                    if r.get("file") == persona_name:
                        r["persona"] = persona_stats
                        break

        _log(f"任务 {task_id} 完成")
        mgr.update(
            task_id,
            stage="导入完成",
            progress=1.0,
            status="done",
            report={"files": reports},
        )
    except asyncio.CancelledError:
        _log(f"任务 {task_id} 已取消，已处理的记录保留")
        mgr.update(
            task_id,
            status="cancelled",
            stage="已取消（已处理的记录已保留）",
        )
        raise
    except Exception as e:
        _log(f"任务 {task_id} 失败：{type(e).__name__}: {e}")
        mgr.update(
            task_id,
            status="failed",
            error=f"{type(e).__name__}: {e}",
            stage="导入失败",
        )


async def sse_stream(task_id: str) -> Any:
    """yield SSE 消息字符串。每秒推一次，直到任务结束。"""
    mgr = get_manager()
    last_payload: str | None = None
    while True:
        task = mgr.get(task_id)
        if task is None:
            yield f"event: error\ndata: {json.dumps({'message': 'task not found'})}\n\n"
            return
        payload = json.dumps(task.to_dict(), ensure_ascii=False)
        if payload != last_payload:
            yield f"data: {payload}\n\n"
            last_payload = payload
        if task.status in ("done", "failed", "cancelled"):
            return
        await asyncio.sleep(1.0)
