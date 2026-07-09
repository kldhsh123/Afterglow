"""/memory/* 路由：暂停 / 恢复回写、软删除、统计、检索调试。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from xuwen.chat_api.schemas import (
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryStatsResponse,
    to_search_hit,
)
from xuwen.chat_api.state import AppState, get_state
from xuwen.core.models import RetrievalQuery
from xuwen.memory.schema import (
    TABLE_FRIEND_MESSAGES,
    TABLE_LIVE_MESSAGES,
    TABLE_RESPONSE_PAIRS,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/stats", response_model=MemoryStatsResponse)
async def stats(state: AppState = Depends(get_state)) -> MemoryStatsResponse:
    s = await state.store.stats()
    return MemoryStatsResponse(
        friend_messages=s.friend_messages,
        dialogue_windows=s.dialogue_windows,
        history_images=s.history_images,
        response_pairs=s.response_pairs,
        live_messages=s.live_messages,
        relationship_memories=s.relationship_memories,
        writeback_enabled=state.settings.writeback_enabled,
        writeback_paused=state.writeback.stats.paused,
    )


@router.post("/writeback/pause")
async def pause_writeback(state: AppState = Depends(get_state)) -> dict[str, Any]:
    state.writeback.pause()
    return {"status": "paused"}


@router.post("/writeback/resume")
async def resume_writeback(state: AppState = Depends(get_state)) -> dict[str, Any]:
    state.writeback.resume()
    return {"status": "running"}


@router.delete("/{table}/{memory_id}")
async def delete_memory(
    table: str,
    memory_id: str,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """软删除某张表里的一行。"""
    allowed = {TABLE_FRIEND_MESSAGES, TABLE_LIVE_MESSAGES, TABLE_RESPONSE_PAIRS}
    if table not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持删除以下表：{sorted(allowed)}",
        )
    ok = await state.store.soft_delete(table, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到对应记录")
    return {"status": "deleted", "table": table, "id": memory_id}


@router.post("/search", response_model=MemorySearchResponse)
async def search(
    req: MemorySearchRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> MemorySearchResponse:
    """调试用接口：直接看检索融合结果，不调用 LLM。"""
    trace_id = str(getattr(request.state, "request_id", "") or "")
    if not req.query.strip():
        return _empty_search_response(trace_id)
    result = await state.retriever.retrieve(
        RetrievalQuery(
            query_text=req.query,
            conversation_id=req.conversation_id,
            final_k=req.top_k,
        )
    )
    return MemorySearchResponse(
        fused=[to_search_hit(c) for c in result.fused],
        response_pairs=[to_search_hit(c) for c in result.response_pairs],
        friend_examples=[to_search_hit(c) for c in result.friend_examples],
        dialogue_windows=[to_search_hit(c) for c in result.dialogue_windows],
        history_images=[to_search_hit(c) for c in result.history_images],
        recent_live=[to_search_hit(c) for c in result.recent_live],
        trace_id=trace_id,
    )


def _empty_search_response(trace_id: str = "") -> MemorySearchResponse:
    return MemorySearchResponse(
        fused=[],
        response_pairs=[],
        friend_examples=[],
        dialogue_windows=[],
        history_images=[],
        recent_live=[],
        trace_id=trace_id,
    )
