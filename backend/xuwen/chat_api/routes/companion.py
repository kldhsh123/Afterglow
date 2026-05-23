"""/v1/companion/*：AI 主动性与新关系接口。"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from xuwen.chat_api.chat_pipeline import (
    available_sticker_names,
    build_policy_hint,
    is_ai_silence_signal,
)
from xuwen.chat_api.companion_prompt import (
    build_persona_card_with_companion_context,
    empty_retrieval_result,
    render_life_memory_context,
)
from xuwen.chat_api.llm_client import GenerationParams
from xuwen.chat_api.output_filter import sanitize_assistant_text
from xuwen.chat_api.schemas import PolicyHint
from xuwen.chat_api.state import AppState, get_state
from xuwen.chat_api.web_search import render_web_context, should_search_web
from xuwen.companion.life import LifeSnapshot
from xuwen.companion.response_policy import (
    decide_response_policy,
    refine_decision_with_llm,
)
from xuwen.core.errors import RetrievalError
from xuwen.core.models import RetrievalQuery
from xuwen.memory.writer import WritebackTurn
from xuwen.persona.prompt import build_chat_messages

router = APIRouter(prefix="/v1/companion", tags=["companion"])
logger = logging.getLogger(__name__)


class ProactiveRequest(BaseModel):
    conversation_id: str | None = Field(default=None)
    reason: str = Field(default="idle", description="触发原因，如 idle / morning / manual")
    private_context: str = Field(
        default="",
        description="外部调度器提供的内部触发背景，不会当作用户消息写入历史",
    )
    topic_hint: str = Field(default="", description="可选：希望主动开启的话题方向")


class ProactiveResponse(BaseModel):
    message: str
    life: dict[str, str | int]
    relationship_memory: str = ""
    trace_id: str = ""
    policy: PolicyHint | None = None
    silenced: bool = False


@router.post("/proactive", response_model=ProactiveResponse)
async def proactive(
    req: ProactiveRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> ProactiveResponse:
    """让 AI 主动开启一个自然话题。"""
    trace_id = str(getattr(request.state, "request_id", "") or "")
    base_life = state.life.snapshot()
    retrieval_query = "\n".join(
        part
        for part in [
            f"主动话题触发：{req.reason}",
            f"当前状态：{base_life.current_activity}",
            f"可聊话题：{base_life.topic_seed}",
            f"内部背景：{req.private_context}" if req.private_context else "",
            f"话题方向：{req.topic_hint}" if req.topic_hint else "",
        ]
        if part
    )
    _retrieval_start = time.perf_counter()
    try:
        retrieved = await state.retriever.retrieve(
            RetrievalQuery(
                query_text=retrieval_query,
                conversation_id=req.conversation_id,
            )
        )
        state.metrics.record(
            "companion.retrieval",
            (time.perf_counter() - _retrieval_start) * 1000,
            detail=f"final={len(retrieved.fused)}",
        )
    except RetrievalError as e:
        logger.warning("主动话题检索失败，降级到无 RAG 模式：%s", e.message)
        state.metrics.record(
            "companion.retrieval",
            (time.perf_counter() - _retrieval_start) * 1000,
            error=type(e).__name__,
        )
        retrieved = empty_retrieval_result()

    relationship_context = await state.relationship_memory.render_context(retrieval_query)
    life = await state.life.decide_for_turn(
        llm=state.life_llm,
        model=state.settings.resolved_life_model,
        current_user_text=_proactive_context_text(req),
        recent=[],
        relationship_context=relationship_context,
        memory_context=render_life_memory_context(retrieved, state.settings),
        trigger=f"proactive:{req.reason}",
        trace_id=trace_id,
        metrics=state.metrics,
    )
    response_decision = decide_response_policy(
        current_user_text=_proactive_context_text(req),
        has_images=False,
        retrieved=retrieved,
        life=life,
        relationship_context=relationship_context,
        recent=[],
    )
    state.metrics.record(
        "companion.response.policy",
        0.0,
        detail=f"trace={trace_id},{response_decision.metric_detail()}",
    )
    if state.settings.response_policy_model_enabled:
        response_decision = await refine_decision_with_llm(
            base=response_decision,
            llm=state.response_policy_llm,
            model=state.settings.resolved_response_policy_model,
            settings=state.settings,
            current_user_text=_proactive_context_text(req),
            recent=[],
            life=life,
            relationship_context=relationship_context,
            has_images=False,
            trace_id=trace_id,
            metrics=state.metrics,
        )
        state.metrics.record(
            "companion.response.policy.refined",
            0.0,
            detail=f"trace={trace_id},{response_decision.metric_detail()}",
        )
    policy_hint = build_policy_hint(response_decision)

    if not response_decision.should_reply:
        state.metrics.record(
            "companion.silenced",
            0.0,
            detail=f"trace={trace_id},{response_decision.metric_detail()}",
        )
        return ProactiveResponse(
            message=state.settings.silence_response_sentinel,
            life=_life_to_dict(life),
            relationship_memory=relationship_context,
            trace_id=trace_id,
            policy=policy_hint,
            silenced=True,
        )

    persona_card = build_persona_card_with_companion_context(
        settings=state.settings,
        life=life,
        relationship_context=relationship_context,
        style_query=req.topic_hint or retrieval_query,
        response_policy_context=response_decision.render_prompt_block(
            silence_sentinel=state.settings.silence_response_sentinel,
        ),
    )
    proactive_user_message = (
        _proactive_context_text(req)
        + "。请主动开启一个自然话题。"
        "要求：短、像真实私聊、不要解释系统任务；"
        "可以轻描淡写用自己的当前状态，但不要编造现实见面或承诺。"
    )
    web_query = "\n".join(part for part in [req.topic_hint, req.private_context] if part)
    web_context = ""
    if state.web_search is not None and should_search_web(web_query):
        web_results = await state.web_search.search(
            web_query,
            trace_id=trace_id,
            metrics=state.metrics,
        )
        web_context = render_web_context(web_results)
    messages = build_chat_messages(
        settings=state.settings,
        persona_card=persona_card,
        retrieved=retrieved,
        recent=[],
        current_user_message=proactive_user_message,
        web_context=web_context,
    )

    delay_seconds = max(life.reply_delay_seconds, response_decision.reply_delay_seconds)
    if delay_seconds > 0:
        await asyncio.sleep(
            min(
                delay_seconds,
                state.settings.life_max_reply_delay_seconds,
            )
        )

    start = time.perf_counter()
    text = sanitize_assistant_text(
        await state.llm.complete_chat(
            messages,
            GenerationParams(temperature=0.7, max_tokens=200),
            model=state.settings.chat_model,
            trace_id=trace_id,
            stage="companion.proactive",
            metrics=state.metrics,
        ),
        valid_sticker_names=available_sticker_names(state.settings),
    )
    state.metrics.record(
        "companion.proactive",
        (time.perf_counter() - start) * 1000,
        detail=state.settings.chat_model,
    )

    # AI 自主沉默：主动话题轮 AI 选择"算了不开话题了"也合理；
    # 命中 sentinel 时按沉默语义返回，但不写历史正文（避免 [silent] 污染检索）。
    ai_silenced = is_ai_silence_signal(
        text,
        sentinel=state.settings.silence_response_sentinel,
        decision=response_decision,
    )
    if ai_silenced:
        state.metrics.record(
            "companion.silenced.ai",
            0.0,
            detail=f"trace={trace_id},{response_decision.metric_detail()}",
        )
        return ProactiveResponse(
            message=state.settings.silence_response_sentinel,
            life=_life_to_dict(life),
            relationship_memory=relationship_context,
            trace_id=trace_id,
            policy=policy_hint,
            silenced=True,
        )

    if req.conversation_id and text:
        await state.writeback.enqueue_turn(
            WritebackTurn(
                conversation_id=req.conversation_id,
                user_text="",
                assistant_text=text,
            )
        )

    return ProactiveResponse(
        message=text,
        life=_life_to_dict(life),
        relationship_memory=relationship_context,
        trace_id=trace_id,
        policy=policy_hint,
    )


def _proactive_context_text(req: ProactiveRequest) -> str:
    parts = [f"主动话题触发：{req.reason}"]
    if req.private_context:
        parts.append(f"内部触发背景（不是用户消息）：{req.private_context}")
    if req.topic_hint:
        parts.append(f"话题方向：{req.topic_hint}")
    return "；".join(parts)


def _life_to_dict(life: LifeSnapshot) -> dict[str, str | int]:
    """把 LifeSnapshot 序列化为响应里 life 字段的 dict 形式。"""
    return {
        "date": life.date,
        "time_slot": life.time_slot,
        "current_activity": life.current_activity,
        "recent_meal": life.recent_meal,
        "mood": life.mood,
        "availability": life.availability,
        "topic_seed": life.topic_seed,
        "next_update_at": life.next_update_at,
        "reply_delay_seconds": life.reply_delay_seconds,
        "reply_delay_reason": life.reply_delay_reason,
        "day_plan_summary": life.day_plan_summary,
        "recent_timeline_summary": life.recent_timeline_summary,
    }
