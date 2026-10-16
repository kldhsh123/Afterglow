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
from xuwen.error_i18n import error_text
from xuwen.ingestion.plugins import CHAT_FILE_SUFFIXES

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
        raise HTTPException(status_code=404, detail=error_text("Experimental analysis is disabled"))
    profile_path = state.settings.analysis_data_dir / "experimental" / "personality_profile.json"
    if profile_path.exists():
        return _read_artifact(profile_path)
    return _read_artifact(state.settings.analysis_data_dir / "experimental" / "insights.json")


@router.post("/start")
async def start(
    request: AnalysisStartRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    if not request.timeline and not request.personality:
        raise HTTPException(
            status_code=400, detail=error_text("Select at least a timeline or personality report")
        )
    files = _resolve_upload_files(request.files, state.settings.config_ui_uploads_dir)
    if not files:
        raise HTTPException(
            status_code=400, detail=error_text("No JSON/JSONL files to analyze in uploads")
        )
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
        raise HTTPException(status_code=404, detail=error_text("Analysis task not found"))
    return task.to_dict()


@router.get("/{task_id}/stream")
async def task_stream(task_id: str) -> StreamingResponse:
    manager = get_analysis_task_manager()
    if manager.get(task_id) is None:
        raise HTTPException(status_code=404, detail=error_text("Analysis task not found"))

    return StreamingResponse(analysis_sse_stream(task_id), media_type="text/event-stream")


@router.post("/{task_id}/cancel")
async def cancel(task_id: str) -> dict[str, str]:
    manager = get_analysis_task_manager()
    if manager.get(task_id) is None:
        raise HTTPException(status_code=404, detail=error_text("Analysis task not found"))
    if not await manager.cancel(task_id):
        raise HTTPException(status_code=409, detail=error_text("The task has already finished"))
    return {"status": "cancelling"}


def _read_artifact(path: Path) -> Any:
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=error_text("Analysis results are not available yet")
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=error_text("The analysis result file is corrupt")
        ) from exc


def _resolve_upload_files(names: list[str], uploads_dir: Path) -> list[Path]:
    root = uploads_dir.resolve()
    if names:
        candidates = [root / name for name in names]
    else:
        candidates = [path for path in root.glob("*") if path.suffix.lower() in CHAT_FILE_SUFFIXES]
    output: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if root not in resolved.parents or resolved.suffix.lower() not in CHAT_FILE_SUFFIXES:
            raise HTTPException(
                status_code=400, detail=error_text("Files must be inside the uploads directory")
            )
        if not resolved.is_file():
            raise HTTPException(
                status_code=404,
                detail=error_text("Uploaded file not found: {p0}", p0=candidate.name),
            )
        output.append(resolved)
    return sorted(set(output))
