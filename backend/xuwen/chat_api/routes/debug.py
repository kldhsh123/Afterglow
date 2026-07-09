"""/debug/* 端点：暴露运行时统计、配置快照、调用延迟分布。

仅当 DEBUG_ENDPOINTS_ENABLED=true 时挂载到 app。
所有端点都过 API key 守卫（默认无 key 时自由访问）；不会回传任何聊天原文或 prompt 正文。
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from xuwen import __version__
from xuwen.chat_api.state import AppState, get_state

router = APIRouter(prefix="/debug", tags=["debug"])


class ProactiveForceDueRequest(BaseModel):
    conversation_id: str = ""
    caller_id: str = ""
    at_ms: int | None = None


@router.get("/stats")
async def stats(state: AppState = Depends(get_state)) -> dict[str, Any]:
    """汇总运行时指标。"""
    memory_stats = await state.store.stats()
    metrics = state.metrics
    kinds = metrics.kinds()
    return {
        "version": __version__,
        "memory": {
            "friend_messages": memory_stats.friend_messages,
            "dialogue_windows": memory_stats.dialogue_windows,
            "response_pairs": memory_stats.response_pairs,
            "history_images": memory_stats.history_images,
            "live_messages": memory_stats.live_messages,
            "relationship_memories": memory_stats.relationship_memories,
        },
        "database": state.store.db_perf_snapshot(),
        "life": _life_to_dict(state),
        "proactive": state.proactive.snapshot(),
        "writeback": {
            "enqueued": state.writeback.stats.enqueued,
            "written": state.writeback.stats.written,
            "flushed_batches": state.writeback.stats.flushed_batches,
            "dropped": state.writeback.stats.dropped,
            "failed": state.writeback.stats.failed,
            "paused": state.writeback.stats.paused,
            "pending_turns": state.writeback.stats.pending_turns,
        },
        "calls": {
            kind: _stats_to_dict(metrics.stats(kind)) for kind in kinds
        },
        "model_chain": [
            _model_call_to_dict(record)
            for record in metrics.model_chain(limit=80)
        ],
    }


@router.get("/config")
def config_snapshot(state: AppState = Depends(get_state)) -> dict[str, Any]:
    """脱敏的配置快照。"""
    s = state.settings
    # 把 SecretStr 转成 "set" / "unset" 标志，不暴露具体 key
    return {
        "app_name": s.app_name,
        "app_slogan": s.app_slogan,
        "app_timezone": s.app_timezone,
        "self_name": s.self_name,
        "friend_name": s.friend_name,
        "relationship_type": s.relationship_type,
        "persona_template": s.persona_template,
        "chat_model": s.chat_model,
        "debug": {
            "endpoints_enabled": s.debug_endpoints_enabled,
            "model_full_payloads_enabled": s.debug_model_full_payloads_enabled,
            "metrics_capacity": s.metrics_capacity,
        },
        "embedding_model": s.embedding_model,
        "embedding_dim": s.embedding_dim,
        "embedding_input_mode": s.embedding_input_mode,
        "embedding_batch_size": s.embedding_batch_size,
        "embedding_max_concurrency": s.embedding_max_concurrency,
        "embedding_max_requests_per_minute": s.embedding_max_requests_per_minute,
        "chunking_strategy": s.chunking_strategy,
        "adaptive_chunk_model_enabled": s.adaptive_chunk_model_enabled,
        "adaptive_chunk_model": s.resolved_adaptive_chunk_model,
        "session_gap_minutes": s.session_gap_minutes,
        "window_size": s.window_size,
        "window_overlap": s.window_overlap,
        "single_context_max_chars": s.single_context_max_chars,
        "final_context_k": s.final_context_k,
        "rrf_k": s.rrf_k,
        "recency_half_life_days": s.recency_half_life_days,
        "query_rewrite": {
            "enabled": s.query_rewrite_enabled,
            "model": s.resolved_query_rewrite_model,
            "endpoint_overridden": bool(s.query_rewrite_api_url.strip()),
            "key_overridden": _is_secret_set(
                s.query_rewrite_api_key.get_secret_value()
            ),
            "max_variants": s.query_rewrite_max_variants,
        },
        "rerank": {
            "enabled": s.rerank_enabled,
            "mode": s.rerank_mode,
            "model": s.resolved_rerank_model,
            "endpoint_overridden": bool(s.rerank_api_url.strip()),
            "key_overridden": _is_secret_set(s.rerank_api_key.get_secret_value()),
            "top_k": s.rerank_top_k,
            "min_candidates": s.rerank_min_candidates,
            "timeout_seconds": s.rerank_timeout_seconds,
        },
        "cross_rerank": {
            "enabled": s.cross_rerank_enabled,
            "protocol": s.cross_rerank_protocol,
            "model": s.cross_rerank_model,
            "endpoint_set": bool(s.cross_rerank_api_url.strip()),
            "key_set": _is_secret_set(s.cross_rerank_api_key.get_secret_value()),
            "input_k": s.cross_rerank_input_k,
            "top_n": s.cross_rerank_top_n,
            "timeout_seconds": s.cross_rerank_timeout_seconds,
        },
        "writeback_enabled": s.writeback_enabled,
        "writeback_batch_turns": s.writeback_batch_turns,
        "writeback_vectorize": s.writeback_vectorize,
        "live_top_k": s.live_top_k,
        "ai_generated_source_weight": s.ai_generated_source_weight,
        "ai_generated_long_term_enabled": s.ai_generated_long_term_enabled,
        "response_policy": {
            "model_enabled": s.response_policy_model_enabled,
            "model": s.resolved_response_policy_model,
            "endpoint_overridden": bool(s.response_policy_api_url.strip()),
            "key_overridden": _is_secret_set(
                s.response_policy_api_key.get_secret_value()
            ),
            "temperature": s.response_policy_temperature,
            "max_tokens": s.response_policy_max_tokens,
        },
        "schedule": {
            "extract_enabled": s.schedule_extract_enabled,
            "model": s.resolved_schedule_model,
            "endpoint_overridden": bool(s.schedule_api_url.strip()),
            "key_overridden": _is_secret_set(s.schedule_api_key.get_secret_value()),
            "temperature": s.schedule_temperature,
            "max_tokens": s.schedule_max_tokens,
            "max_hints_per_turn": s.schedule_max_hints_per_turn,
            "timeout_seconds": s.schedule_extract_timeout_seconds,
        },
        "silence_response_sentinel": s.silence_response_sentinel,
        "silence_finish_reason": s.silence_finish_reason,
        "responses_store_capacity": s.responses_store_capacity,
        "proactive": {
            "enabled": s.proactive_enabled,
            "learning_min_gap_minutes": s.proactive_learning_min_gap_minutes,
            "score_threshold": s.proactive_score_threshold,
            "check_interval_seconds": s.proactive_check_interval_seconds,
            "min_idle_minutes": s.proactive_min_idle_minutes,
            "max_per_day": s.proactive_max_per_day,
            "quiet_hours": s.proactive_quiet_hours,
            "skip_when_life_busy": s.proactive_skip_when_life_busy,
            "profile_window_limit": s.proactive_profile_window_limit,
            "audit_max_records": s.proactive_audit_max_records,
        },
        "vision_enabled": s.vision_enabled,
        "chat_model_supports_vision": s.chat_model_supports_vision,
        "web_access_enabled": s.web_access_enabled,
        "web_search_provider": s.web_search_provider,
        "web_search_base_url_configured": bool(s.web_search_base_url.strip()),
        "web_search_client_active": state.web_search is not None,
        "web_fetch_enabled": s.web_fetch_enabled,
        "web_fetch_client_active": state.web_fetch is not None,
        "web_fetch_max_urls": s.web_fetch_max_urls,
        "web_fetch_max_bytes": s.web_fetch_max_bytes,
        "web_fetch_max_chars": s.web_fetch_max_chars,
        "enable_pii_redaction": s.enable_pii_redaction,
        "api_keys_configured": {
            "openai": _is_secret_set(s.openai_api_key.get_secret_value()),
            "embedding": _is_secret_set(s.embedding_api_key.get_secret_value()),
            "vision": _is_secret_set(s.vision_api_key.get_secret_value()),
            "web_search": _is_secret_set(s.web_search_api_key.get_secret_value()),
            "local_guard": s.xuwen_api_key is not None,
        },
        "paths": {
            "lance_db": str(s.lance_db_path),
            "persona": str(s.persona_data_dir),
            "images": str(s.image_data_dir),
        },
        "env": {
            "python": os.environ.get("PYTHON_VERSION", ""),
            "process_pid": os.getpid(),
        },
    }


@router.post("/metrics/reset")
def reset_metrics(state: AppState = Depends(get_state)) -> dict[str, str]:
    """清空所有调用统计（不影响 LanceDB 数据）。"""
    state.metrics.reset()
    return {"status": "ok"}


@router.post("/proactive/force-due")
async def force_proactive_candidate_due(
    req: ProactiveForceDueRequest,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """实验性调试：把指定会话的主动聊天 pending candidate 改成已到期。"""
    scope_id = req.conversation_id.strip() or req.caller_id.strip()
    return await state.proactive.debug_force_candidate_due(
        scope_id,
        at_ms=req.at_ms,
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _stats_to_dict(s) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "count": s.count,
        "error_count": s.error_count,
        "error_rate": round(s.error_rate, 4),
        "avg_latency_ms": round(s.avg_latency_ms, 2),
        "p50_latency_ms": round(s.p50_latency_ms, 2),
        "p95_latency_ms": round(s.p95_latency_ms, 2),
        "last": [
            {
                "ts_ms": r.ts_ms,
                "latency_ms": r.latency_ms,
                "status": r.status,
                "detail": r.detail,
            }
            for r in s.last_records
        ],
    }


def _model_call_to_dict(record) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "ts_ms": record.ts_ms,
        "trace_id": record.trace_id,
        "stage": record.stage,
        "attempt": record.attempt,
        "model": record.model,
        "url": record.url,
        "stream": record.stream,
        "latency_ms": record.latency_ms,
        "status": record.status,
        "status_code": record.status_code,
        "upstream_request_id": record.upstream_request_id,
        "request": record.request,
        "response": record.response,
        "error": record.error,
    }


def _life_to_dict(state: AppState) -> dict[str, Any]:
    snapshot = state.life.snapshot()
    raw: dict[str, Any] = {}
    exists = state.life.path.exists()
    if exists:
        try:
            loaded = json.loads(state.life.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}

    timeline = raw.get("timeline")
    daily_plan = raw.get("daily_plan")
    current = raw.get("current")
    return {
        "state_file": str(state.life.path),
        "state_file_exists": exists,
        "snapshot": {
            "date": snapshot.date,
            "time_slot": snapshot.time_slot,
            "current_activity": snapshot.current_activity,
            "recent_meal": snapshot.recent_meal,
            "mood": snapshot.mood,
            "topic_seed": snapshot.topic_seed,
            "availability": snapshot.availability,
            "next_update_at": snapshot.next_update_at,
            "reply_delay_seconds": snapshot.reply_delay_seconds,
            "reply_delay_reason": snapshot.reply_delay_reason,
            "current_event_id": snapshot.current_event_id,
            "day_plan_summary": snapshot.day_plan_summary,
            "recent_timeline_summary": snapshot.recent_timeline_summary,
        },
        "model_decision": current if isinstance(current, dict) else {},
        "plan_decided_by_model": bool(raw.get("plan_decided_by_model")),
        "daily_plan": daily_plan if isinstance(daily_plan, list) else [],
        "recent_timeline": timeline[-20:] if isinstance(timeline, list) else [],
    }


def _is_secret_set(value: str) -> bool:
    return bool(value and value.strip())
