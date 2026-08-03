"""关系分析的任务端点与隔离只读端点。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from xuwen.analysis.tasks import (
    analysis_sse_stream,
    get_analysis_task_manager,
    run_analysis_task,
)
from xuwen.chat_api.state import AppState, get_state

router = APIRouter(prefix="/analysis", tags=["analysis"])


class AnalysisStartRequest(BaseModel):
    files: list[str] = Field(default_factory=list)
    timeline: bool = True
    personality: bool = True
    resume: bool = True
    since: str | None = None


@router.get("/timeline")
def timeline(state: AppState = Depends(get_state)) -> Any:
    return _read_artifact(state.settings.analysis_data_dir / "timeline.json")


@router.get("/personality")
def personality(state: AppState = Depends(get_state)) -> Any:
    return _read_artifact(state.settings.analysis_data_dir / "personality_report.json")


@router.get("/proactive")
def proactive_analysis(state: AppState = Depends(get_state)) -> Any:
    return _read_artifact(state.settings.analysis_data_dir / "proactive_analysis.json")


@router.get("/experimental")
def experimental(state: AppState = Depends(get_state)) -> Any:
    if not state.settings.analysis_experimental_enabled:
        raise HTTPException(status_code=404, detail="实验性分析未启用")
    profile_path = (
        state.settings.analysis_data_dir / "experimental" / "personality_profile.json"
    )
    if profile_path.exists():
        return _read_artifact(profile_path)
    return _read_artifact(state.settings.analysis_data_dir / "experimental" / "insights.json")


@router.post("/start")
async def start(
    request: AnalysisStartRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    if not request.timeline and not request.personality:
        raise HTTPException(status_code=400, detail="至少选择时间线或性格报告")
    files = _resolve_upload_files(request.files, state.settings.config_ui_uploads_dir)
    if not files:
        raise HTTPException(status_code=400, detail="uploads 目录中没有可分析的 JSON/JSONL 文件")
    manager = get_analysis_task_manager()
    active = manager.list_active()
    if active:
        return active[0].to_dict()
    task = manager.create(files)
    handle = asyncio.create_task(
        run_analysis_task(
            task.task_id,
            files,
            state.settings,
            timeline=request.timeline,
            personality=request.personality,
            resume=request.resume,
            since=request.since,
        )
    )
    manager.attach(task.task_id, handle)
    return task.to_dict()


@router.get("/{task_id}")
def task_status(task_id: str) -> dict[str, Any]:
    task = get_analysis_task_manager().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return task.to_dict()


@router.get("/{task_id}/stream")
async def task_stream(task_id: str) -> StreamingResponse:
    manager = get_analysis_task_manager()
    if manager.get(task_id) is None:
        raise HTTPException(status_code=404, detail="分析任务不存在")

    return StreamingResponse(analysis_sse_stream(task_id), media_type="text/event-stream")


@router.post("/{task_id}/cancel")
async def cancel(task_id: str) -> dict[str, str]:
    manager = get_analysis_task_manager()
    if manager.get(task_id) is None:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    if not await manager.cancel(task_id):
        raise HTTPException(status_code=409, detail="任务已经结束")
    return {"status": "cancelling"}


def _read_artifact(path: Path) -> Any:
    if not path.exists():
        raise HTTPException(status_code=404, detail="分析结果尚未生成")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="分析结果文件损坏") from exc


def _resolve_upload_files(names: list[str], uploads_dir: Path) -> list[Path]:
    root = uploads_dir.resolve()
    if names:
        candidates = [root / name for name in names]
    else:
        candidates = list(root.glob("*.json")) + list(root.glob("*.jsonl"))
    output: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if root not in resolved.parents or resolved.suffix.lower() not in {".json", ".jsonl"}:
            raise HTTPException(status_code=400, detail="文件必须位于 uploads 目录内")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"未找到上传文件：{candidate.name}")
        output.append(resolved)
    return sorted(set(output))
