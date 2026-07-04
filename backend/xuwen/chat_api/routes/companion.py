"""/v1/companion/*：AI 主动性与新关系接口。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from xuwen.chat_api.chat_pipeline import (
    available_sticker_names,
    build_policy_hint,
    effective_reply_delay_seconds,
    effective_silence_sentinel,
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
from xuwen.companion.proactive import ProactiveDecision, ProactivePollResult
from xuwen.companion.proactive_context import (
    ProactiveContextItem,
    render_proactive_context_cache,
)
from xuwen.companion.response_policy import (
    decide_response_policy,
    refine_decision_with_llm,
)
from xuwen.core.errors import RetrievalError
from xuwen.core.models import RetrievalQuery, RetrievalResult
from xuwen.memory.writer import WritebackTurn
from xuwen.persona.prompt import build_chat_messages

router = APIRouter(prefix="/v1/companion", tags=["companion"])
logger = logging.getLogger(__name__)

_ASSUMED_USER_STATE_PATTERNS = (
    "你还没睡",
    "还没睡啊",
    "还没睡吗",
    "怎么还没睡",
    "你没睡",
    "还醒着",
    "你醒着",
    "你睡",
    "睡了吗",
    "你在干嘛",
    "在干嘛",
    "看到你消息",
    "看你在线",
    "你在线",
)
_BARE_USER_STATE_OPENINGS = {
    "还没睡",
    "没睡",
    "睡了吗",
    "还醒着",
    "醒着吗",
    "在吗",
}
_LOW_INFORMATION_TOPIC_HOOKS = {
    "最近怎么样",
    "在吗",
    "还没睡",
    "睡了吗",
    "你在干嘛",
    "在干嘛",
    "干嘛呢",
    "吃了吗",
    "醒了吗",
    "早安",
    "晚安",
    "之前那个话题",
    "那个话题",
    "刚才没聊完的事",
    "没聊完的事",
    "小事",
    "有个小事",
}


@dataclass(slots=True, frozen=True)
class _ProactiveOpeningJudgement:
    should_rewrite: bool
    reason: str = ""
    rewrite_instruction: str = ""


@dataclass(slots=True, frozen=True)
class _ProactiveTopicHook:
    found: bool = False
    hook: str = ""
    source: str = "none"
    reason: str = ""


class ProactiveRequest(BaseModel):
    conversation_id: str | None = Field(default=None)
    caller_id: str | None = Field(
        default=None,
        description="调用方稳定标识；用于读取对应最近上下文缓存。",
    )
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
    debug: dict[str, str | int | bool] | None = None


class ProactiveDecisionRequest(BaseModel):
    conversation_id: str | None = Field(default=None)
    caller_id: str | None = Field(default=None)
    auto_send: bool = Field(
        default=False,
        description="通过主动门控时是否立即生成主动消息",
    )
    reason: str = Field(default="learned_rhythm")


class ProactivePollRequest(BaseModel):
    conversation_id: str | None = Field(default=None)
    caller_id: str | None = Field(default=None)
    reason: str = Field(default="learned_rhythm")
    auto_send: bool = Field(
        default=True,
        description="到点且门控通过时是否立即返回主动消息内容",
    )
    last_user_message_at_ms: int | None = Field(
        default=None,
        description="可选兼容字段；普通 chat/responses 请求会由后端自动记录用户活动",
    )


class ProactivePollResponse(BaseModel):
    state: str
    should_send: bool
    score: float
    threshold: float
    reason: str
    skip_reasons: list[str]
    features: dict[str, float]
    private_context: str
    topic_hint: str
    profile_summary: str
    next_poll_at_ms: int
    scheduled_for_ms: int
    candidate_created_at_ms: int = 0
    cancelled_by_user_activity: bool = False
    proactive: ProactiveResponse | None = None


class ProactiveDecisionResponse(BaseModel):
    should_send: bool
    score: float
    threshold: float
    reason: str
    skip_reasons: list[str]
    features: dict[str, float]
    private_context: str
    topic_hint: str
    profile_summary: str
    next_check_seconds: int
    proactive: ProactiveResponse | None = None


@router.post("/proactive", response_model=ProactiveResponse)
async def proactive(
    req: ProactiveRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> ProactiveResponse:
    """实验性、不推荐使用：让 AI 主动开启一个自然话题。"""
    trace_id = str(getattr(request.state, "request_id", "") or "")
    return await _generate_proactive_response(req, state=state, trace_id=trace_id)


@router.post("/proactive/decide", response_model=ProactiveDecisionResponse)
async def proactive_decide(
    req: ProactiveDecisionRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> ProactiveDecisionResponse:
    """实验性、不推荐使用：学习画像驱动的主动聊天门控。"""
    trace_id = str(getattr(request.state, "request_id", "") or "")
    life = state.life.snapshot()
    scope_id = _proactive_scope_id(req.conversation_id, req.caller_id)
    decision = await state.proactive.decide(
        conversation_id=scope_id,
        life=life,
        reason=req.reason,
    )
    proactive_response = None
    status = "skipped"
    if req.auto_send and decision.should_send:
        proactive_response = await _generate_proactive_response(
            ProactiveRequest(
                conversation_id=req.conversation_id,
                caller_id=req.caller_id,
                reason=req.reason,
                private_context=decision.private_context,
                topic_hint=decision.topic_hint,
            ),
            state=state,
            trace_id=trace_id,
        )
        status = "silenced" if proactive_response.silenced else "sent"
    await state.proactive.record_decision(
        conversation_id=scope_id,
        decision=decision,
        status=status,
        message_preview=proactive_response.message if proactive_response else "",
    )
    return ProactiveDecisionResponse(
        should_send=decision.should_send,
        score=decision.score,
        threshold=decision.threshold,
        reason=decision.reason,
        skip_reasons=decision.skip_reasons,
        features=decision.features,
        private_context=decision.private_context,
        topic_hint=decision.topic_hint,
        profile_summary=decision.profile_summary,
        next_check_seconds=decision.next_check_seconds,
        proactive=proactive_response,
    )


@router.post("/proactive/poll", response_model=ProactivePollResponse)
async def proactive_poll(
    req: ProactivePollRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> ProactivePollResponse:
    """实验性、不推荐使用：轮询式主动聊天。"""
    trace_id = str(getattr(request.state, "request_id", "") or "")
    life = state.life.snapshot()
    scope_id = _proactive_scope_id(req.conversation_id, req.caller_id)
    result = await state.proactive.poll(
        conversation_id=scope_id,
        life=life,
        reason=req.reason,
        last_user_message_at_ms=req.last_user_message_at_ms,
    )
    proactive_response = None
    status = result.state
    if req.auto_send and result.should_send:
        proactive_response = await _generate_proactive_response(
            ProactiveRequest(
                conversation_id=req.conversation_id,
                caller_id=req.caller_id,
                reason=req.reason,
                private_context=result.private_context,
                topic_hint=result.topic_hint,
            ),
            state=state,
            trace_id=trace_id,
        )
        status = "silenced" if proactive_response.silenced else "sent"
        await state.proactive.finish_candidate(scope_id)
        next_result = await state.proactive.poll(
            conversation_id=scope_id,
            life=life,
            reason=req.reason,
            last_user_message_at_ms=req.last_user_message_at_ms,
        )
        result.next_poll_at_ms = next_result.next_poll_at_ms
        result.scheduled_for_ms = next_result.scheduled_for_ms
    await state.proactive.record_decision(
        conversation_id=scope_id,
        decision=_poll_to_decision(result),
        status=status,
        message_preview=proactive_response.message if proactive_response else "",
    )
    return ProactivePollResponse(
        state=status,
        should_send=result.should_send,
        score=result.score,
        threshold=result.threshold,
        reason=result.reason,
        skip_reasons=result.skip_reasons,
        features=result.features,
        private_context=result.private_context,
        topic_hint=result.topic_hint,
        profile_summary=result.profile_summary,
        next_poll_at_ms=result.next_poll_at_ms,
        scheduled_for_ms=result.scheduled_for_ms,
        candidate_created_at_ms=result.candidate_created_at_ms,
        cancelled_by_user_activity=result.cancelled_by_user_activity,
        proactive=proactive_response,
    )


@router.get("/proactive/profile")
async def proactive_profile(state: AppState = Depends(get_state)) -> dict[str, object]:
    """实验性调试：查看主动聊天画像与最近审计记录。"""
    return state.proactive.snapshot()


def _poll_to_decision(result: ProactivePollResult) -> ProactiveDecision:
    return ProactiveDecision(
        should_send=result.should_send,
        score=result.score,
        threshold=result.threshold,
        reason=result.reason,
        skip_reasons=result.skip_reasons,
        features=result.features,
        private_context=result.private_context,
        topic_hint=result.topic_hint,
        profile_summary=result.profile_summary,
        next_check_seconds=0,
    )


async def _generate_proactive_response(
    req: ProactiveRequest,
    *,
    state: AppState,
    trace_id: str,
) -> ProactiveResponse:
    base_life = state.life.snapshot()
    retrieval_query = "\n".join(
        part
        for part in [
            "检索目标：找一个适合主动开聊的具体记忆钩子；优先最近未展开的话题、用户提过的计划/状态、共同梗或轻量关心点。",
            f"主动话题触发：{req.reason}",
            f"当前状态：{base_life.current_activity}",
            f"可聊话题：{base_life.topic_seed}",
            f"内部背景：{req.private_context}" if req.private_context else "",
            f"话题方向：{req.topic_hint}" if req.topic_hint else "",
        ]
        if part
    )
    _retrieval_start = time.perf_counter()

    async def _retrieve_with_metrics() -> RetrievalResult:
        try:
            result = await state.retriever.retrieve(
                RetrievalQuery(
                    query_text=retrieval_query,
                    conversation_id=req.conversation_id,
                ),
                metrics=state.metrics,
                trace_id=trace_id,
            )
            state.metrics.record(
                "companion.retrieval",
                (time.perf_counter() - _retrieval_start) * 1000,
                detail=f"final={len(result.fused)}",
            )
            return result
        except RetrievalError as e:
            logger.warning("主动话题检索失败，降级到无 RAG 模式：%s", e.message)
            state.metrics.record(
                "companion.retrieval",
                (time.perf_counter() - _retrieval_start) * 1000,
                error=type(e).__name__,
            )
            return empty_retrieval_result()

    # retrieve 与 relationship_memory.render_context 互相独立，并发省一次 embedding+lance RTT
    retrieved, relationship_context = await asyncio.gather(
        _retrieve_with_metrics(),
        state.relationship_memory.render_context(
            retrieval_query,
            include_relevant=False,
            metrics=state.metrics,
            trace_id=trace_id,
        ),
    )
    cached_context = await state.proactive_context_cache.recent(
        caller_id=req.caller_id,
        conversation_id=req.conversation_id,
    )
    cached_context_text = render_proactive_context_cache(cached_context)
    topic_hook = await _extract_proactive_topic_hook(
        state=state,
        retrieved=retrieved,
        relationship_context=relationship_context,
        cached_context=cached_context,
        trace_id=trace_id,
    )
    proactive_debug: dict[str, str | int | bool] = {
        "cached_context_items": len(cached_context),
        "retrieved_fused": len(retrieved.fused),
        "retrieved_live": len(retrieved.recent_live),
        "retrieved_response_pairs": len(retrieved.response_pairs),
        "relationship_hook": bool(_relationship_topic_hook(relationship_context)),
        "topic_hook_found": topic_hook.found,
        "topic_hook_source": topic_hook.source,
        "topic_hook": topic_hook.hook,
        "topic_hook_reason": topic_hook.reason,
        "opening_rewritten": False,
        "opening_judge_reason": "",
        "fallback_used": False,
    }

    async with state.life_apply_lock:
        life = await state.life.decide_for_turn(
            llm=state.life_llm,
            model=state.settings.resolved_life_model,
            current_user_text=_proactive_context_text(req),
            recent=[],
            relationship_context=relationship_context,
            memory_context="\n".join(
                part
                for part in [
                    cached_context_text,
                    render_life_memory_context(retrieved, state.settings),
                ]
                if part
            ),
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
    reply_delay_seconds = effective_reply_delay_seconds(
        life=life,
        decision=response_decision,
        settings=state.settings,
    )
    policy_hint = build_policy_hint(
        response_decision,
        reply_delay_seconds=reply_delay_seconds,
        reply_delay_reason=life.reply_delay_reason,
    )

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
            debug=proactive_debug,
        )

    persona_card = build_persona_card_with_companion_context(
        settings=state.settings,
        life=life,
        relationship_context=relationship_context,
        style_query=req.topic_hint or retrieval_query,
        response_policy_context=response_decision.render_prompt_block(
            silence_sentinel=effective_silence_sentinel(state.settings),
        ),
    )
    proactive_user_message = (
        _proactive_context_text(req)
        + "。请主动开启一个自然话题。"
        "要求：短、像真实私聊、不要解释系统任务；"
        f"{_render_cached_context_instruction(cached_context_text)}"
        f"{_render_proactive_topic_hook_instruction(topic_hook)}"
        "必须包含一个用户容易接住的可回复点，优先从记忆或关系上下文里挑轻量话题钩子；"
        "不要只输出低信息量纯 ping、单字短缩写、纯亲昵称呼或纯占位。"
        "不要只陈述自己的状态；自己的状态只能当铺垫，后面必须接想说的事、问题或邀请。"
        "不要把 life 状态直接复述成消息。"
        "只能输出一条单句消息，不能换行、不能用空行拆成多条。"
        "你不知道用户当前是否在线、是否没睡、是否正在看消息，所以不要断言用户当前状态、活动或已读。"
        "可以轻描淡写用自己的当前状态，或用“你要是醒着/有空的话”这种条件句；"
        "不要编造现实见面或承诺。"
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
    opening_judgement = await _judge_proactive_opening(
        state=state,
        req=req,
        text=text,
        life=life,
        relationship_context=relationship_context,
        topic_hook=topic_hook,
        trace_id=trace_id,
    )
    if opening_judgement.should_rewrite:
        proactive_debug["opening_rewritten"] = True
        proactive_debug["opening_judge_reason"] = opening_judgement.reason
        text = await _rewrite_proactive_opening(
            state=state,
            req=req,
            persona_card=persona_card,
            retrieved=retrieved,
            web_context=web_context,
            original_text=text,
            judgement=opening_judgement,
            life=life,
            relationship_context=relationship_context,
            topic_hook=topic_hook,
            cached_context_text=cached_context_text,
            debug=proactive_debug,
            trace_id=trace_id,
        )

    # AI 自主沉默：主动话题轮 AI 选择"算了不开话题了"也合理；
    # 命中 sentinel 时按沉默语义返回，但不写历史正文（避免 [silent] 污染检索）。
    ai_silenced = is_ai_silence_signal(
        text,
        sentinel=effective_silence_sentinel(state.settings),
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
            debug=proactive_debug,
        )

    if req.conversation_id and text:
        await state.writeback.enqueue_turn(
            WritebackTurn(
                conversation_id=req.conversation_id,
                user_text="",
                assistant_text=text,
            )
        )
    await state.proactive_context_cache.append_turn(
        caller_id=req.caller_id,
        conversation_id=req.conversation_id,
        user_text="",
        assistant_text=text,
    )

    return ProactiveResponse(
        message=text,
        life=_life_to_dict(life),
        relationship_memory=relationship_context,
        trace_id=trace_id,
        policy=policy_hint,
        debug=proactive_debug,
    )


async def _rewrite_proactive_opening(
    *,
    state: AppState,
    req: ProactiveRequest,
    persona_card: str,
    retrieved: RetrievalResult,
    web_context: str,
    original_text: str,
    judgement: _ProactiveOpeningJudgement,
    life: LifeSnapshot,
    relationship_context: str,
    topic_hook: _ProactiveTopicHook,
    cached_context_text: str,
    debug: dict[str, str | int | bool],
    trace_id: str,
) -> str:
    rewrite_parts = [
        _proactive_context_text(req),
        f"上一版主动开场不合格：{original_text!r}",
    ]
    if judgement.reason:
        rewrite_parts.append(f"质量判断：{judgement.reason}")
    if judgement.rewrite_instruction:
        rewrite_parts.append(f"重写方向：{judgement.rewrite_instruction}")
    if cached_context_text:
        rewrite_parts.append(f"最近上下文缓存：\n{cached_context_text}")
    if topic_hook.found:
        rewrite_parts.append(f"可用话题钩子：{topic_hook.hook}")
    rewrite_parts.append(
        "请重写成一句新的主动开场。硬性要求："
        "1）短、像真实私聊；"
        "2）必须有一个用户容易接住的可回复点，优先使用记忆/关系上下文里的轻量话题；"
        "3）不要推断用户当前在线、没睡、在看消息或在干嘛；"
        "4）不要只发低信息量纯 ping、单字短缩写、纯亲昵称呼或纯占位；"
        "5）不要只陈述自己的状态；自己的状态后面必须接想说的事、问题或邀请；"
        "6）可以用“你要是醒着/有空的话”这种条件句。"
        "7）只能输出一条单句消息，不能换行、不能用空行拆成多条。"
        "只输出最终消息。"
    )
    rewrite_user_message = "\n".join(rewrite_parts)
    messages = build_chat_messages(
        settings=state.settings,
        persona_card=persona_card,
        retrieved=retrieved,
        recent=[],
        current_user_message=rewrite_user_message,
        web_context=web_context,
    )
    start = time.perf_counter()
    try:
        rewritten = sanitize_assistant_text(
            await state.llm.complete_chat(
                messages,
                GenerationParams(temperature=0.55, max_tokens=120),
                model=state.settings.chat_model,
                trace_id=trace_id,
                stage="companion.proactive.rewrite",
                metrics=state.metrics,
            ),
            valid_sticker_names=available_sticker_names(state.settings),
        )
        state.metrics.record(
            "companion.proactive.rewrite",
            (time.perf_counter() - start) * 1000,
            detail=state.settings.chat_model,
        )
    except Exception:
        logger.warning("主动开场重写失败，使用保守 fallback", exc_info=True)
        debug["fallback_used"] = True
        debug["fallback_reason"] = "rewrite_failed"
        return _fallback_proactive_opening(
            life,
            relationship_context=relationship_context,
            retrieved=retrieved,
            topic_hook=topic_hook,
        )
    rewritten_judgement = await _judge_proactive_opening(
        state=state,
        req=req,
        text=rewritten,
        life=life,
        relationship_context="",
        topic_hook=topic_hook,
        trace_id=trace_id,
    )
    if rewritten and not rewritten_judgement.should_rewrite:
        return rewritten
    state.metrics.record(
        "companion.proactive.rewrite_fallback",
        0.0,
        detail="rewrite_still_low_quality",
    )
    debug["fallback_used"] = True
    debug["fallback_reason"] = rewritten_judgement.reason or "rewrite_still_low_quality"
    return _fallback_proactive_opening(
        life,
        relationship_context=relationship_context,
        retrieved=retrieved,
        topic_hook=topic_hook,
    )


async def _extract_proactive_topic_hook(
    *,
    state: AppState,
    retrieved: RetrievalResult,
    relationship_context: str,
    cached_context: list[ProactiveContextItem],
    trace_id: str,
) -> _ProactiveTopicHook:
    cached_context_text = render_proactive_context_cache(cached_context)
    local_fallback = _local_proactive_topic_hook(
        relationship_context=relationship_context,
    )
    if not cached_context_text and not relationship_context.strip() and not retrieved.fused:
        return local_fallback

    retrieved_text = _render_retrieved_for_topic_hook(retrieved)
    prompt = (
        "你是主动聊天的话题钩子选择器，只判断“有没有适合主动开场的可聊点”，不要生成聊天正文。\n"
        "优先级：最近上下文缓存 > 关系记忆 > RAG 片段。\n"
        "合格钩子应该具体但不暴露原句：可以是计划进展、上次未聊完的话题、用户提过的近况、一个轻量问题。\n"
        "不合格：单字/暗号/亲昵称呼/纯问候/在吗/睡了吗/还没睡/用户当前在线状态/AI 自己状态/过度私密原文/只适合作为回复的半句话。\n"
        "如果上下文只有低信息闲聊或不适合主动提起，就 found=false。\n"
        "hook 要写成不超过 18 个中文字符的泛化短语，不要复制用户原句，不要带引号。\n"
        "只输出 JSON，不要 markdown。格式："
        '{"found": true|false, "hook": "短话题", "source": "context_cache|relationship|retrieval|none", "reason": "简短原因"}'
        "\n\n"
        f"最近上下文缓存：\n{_short_for_judge(cached_context_text, 1800) or '（无）'}\n\n"
        f"关系记忆：\n{_short_for_judge(relationship_context, 1000) or '（无）'}\n\n"
        f"RAG 片段：\n{_short_for_judge(retrieved_text, 1800) or '（无）'}"
    )
    try:
        raw = await state.response_policy_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": "你只选择主动聊天话题钩子，不生成聊天正文。必须输出可被 json.loads 解析的 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            GenerationParams(
                temperature=state.settings.response_policy_temperature,
                max_tokens=220,
            ),
            model=state.settings.resolved_response_policy_model,
            trace_id=trace_id,
            stage="companion.proactive.topic_hook",
            metrics=state.metrics,
        )
    except Exception:
        logger.warning("主动话题钩子小模型判断失败，使用本地保守兜底", exc_info=True)
        return local_fallback
    parsed = _parse_proactive_topic_hook(raw)
    if parsed.found or parsed.reason != "topic_hook_parse_failed":
        return parsed
    return local_fallback


def _parse_proactive_topic_hook(raw: str) -> _ProactiveTopicHook:
    try:
        data = json.loads(_extract_json_object(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return _ProactiveTopicHook(reason="topic_hook_parse_failed")
    if not isinstance(data, dict):
        return _ProactiveTopicHook(reason="topic_hook_not_object")
    found = bool(data.get("found"))
    hook = _clean_topic_hook(str(data.get("hook") or ""))
    source = str(data.get("source") or "none")
    if source not in {"context_cache", "relationship", "retrieval", "none"}:
        source = "none"
    reason = str(data.get("reason") or "")[:180]
    if not found or not hook:
        return _ProactiveTopicHook(found=False, source="none", reason=reason)
    if _is_low_information_hook(hook):
        return _ProactiveTopicHook(
            found=False,
            source="none",
            reason="low_information_topic_hook",
        )
    return _ProactiveTopicHook(found=True, hook=hook, source=source, reason=reason)


def _local_proactive_topic_hook(
    *,
    relationship_context: str,
) -> _ProactiveTopicHook:
    hook = _relationship_topic_hook(relationship_context)
    if hook:
        cleaned = _clean_topic_hook(hook)
        if cleaned and not _is_low_information_hook(cleaned):
            return _ProactiveTopicHook(
                found=True,
                hook=cleaned,
                source="relationship",
                reason="relationship_memory_available",
            )
    return _ProactiveTopicHook(found=False, source="none", reason="no_context")


def _render_retrieved_for_topic_hook(retrieved: RetrievalResult) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for chunk in [*retrieved.recent_live, *retrieved.response_pairs, *retrieved.fused]:
        text = _short_for_judge(chunk.text, 220)
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(f"- {chunk.kind}: {text}")
        if len(lines) >= 10:
            break
    return "\n".join(lines)


def _render_cached_context_instruction(cached_context_text: str) -> str:
    if not cached_context_text:
        return ""
    return (
        "最近上下文缓存如下，只能用来找一个可接续的话题，不要照抄原句或暴露缓存存在：\n"
        f"{cached_context_text}\n"
    )


def _render_proactive_topic_hook_instruction(topic_hook: _ProactiveTopicHook) -> str:
    if not topic_hook.found:
        return "当前没有可靠记忆话题钩子；不要硬编具体往事，使用保守但可回复的轻量开场。"
    return (
        f"本轮可用话题钩子：{topic_hook.hook}。"
        "主动开场必须围绕这个钩子，但不要照抄缓存原文，也不要解释钩子来源。"
    )


async def _judge_proactive_opening(
    *,
    state: AppState,
    req: ProactiveRequest,
    text: str,
    life: LifeSnapshot,
    relationship_context: str,
    topic_hook: _ProactiveTopicHook,
    trace_id: str,
) -> _ProactiveOpeningJudgement:
    hard_reason = _proactive_opening_hard_violation(text)
    if hard_reason:
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason=hard_reason,
            rewrite_instruction="改成不推断用户当前状态、并带一个可回复点的主动开场。",
        )
    judge_prompt = (
        "你是聊天产品的主动开场质量审查器。判断候选消息是否适合作为“主动发起聊天”的第一句话。\n"
        "注意：当前没有用户新消息，也没有用户在线、已读、没睡、正在看手机等实时 presence 信号。\n"
        "合格标准：像自然私聊；只能是一条单句消息；可以很短；但必须能开启对话，不能只是中途回复、状态播报、低信息量 ping、"
        "纯称呼、纯占位；不能断言用户当前状态、活动或已读；最好有一个问题、邀请、想说的事，"
        "或来自记忆/关系上下文的轻量话题钩子。多行、空行分条、直接复述 life 状态都不合格。\n"
        "通用例子："
        "不合格：'刚准备睡但还在刷手机'（只是状态播报）；"
        "不合格：'在吗'（低信息 ping）；"
        "不合格：'还没睡'（没有 presence 依据）；"
        "合格：'你有空的话，我想问你个小事'；"
        "合格：'我刚想到上次那个话题，想接着问问你'。\n"
        "只输出 JSON，不要 markdown。格式："
        '{"should_rewrite": true|false, "reason": "简短原因", "rewrite_instruction": "如果需要重写，给一句方向"}'
        "\n\n"
        f"候选消息：{text!r}\n"
        f"触发背景：{_proactive_context_text(req)}\n"
        f"AI 当前状态：{life.current_activity}；可聊话题：{life.topic_seed}；可用性：{life.availability}\n"
        f"主动话题钩子：{topic_hook.hook if topic_hook.found else '（无）'}\n"
        f"关系记忆摘要：{_short_for_judge(relationship_context, 700)}"
    )
    try:
        raw = await state.response_policy_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": "你只做质量判断，不生成聊天正文。必须输出可被 json.loads 解析的 JSON。",
                },
                {"role": "user", "content": judge_prompt},
            ],
            GenerationParams(
                temperature=state.settings.response_policy_temperature,
                max_tokens=180,
            ),
            model=state.settings.resolved_response_policy_model,
            trace_id=trace_id,
            stage="companion.proactive.opening_judge",
            metrics=state.metrics,
        )
    except Exception:
        logger.warning("主动开场小模型质量判断失败，禁止直接发送原文", exc_info=True)
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason="judge_failed",
            rewrite_instruction="重写成单句、有可回复点、且不复述状态的主动开场。",
        )
    return _parse_proactive_opening_judgement(raw)


def _parse_proactive_opening_judgement(raw: str) -> _ProactiveOpeningJudgement:
    try:
        data = json.loads(_extract_json_object(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason="judge_parse_failed",
            rewrite_instruction="质量判断失败，改成单句、有可回复点的主动开场。",
        )
    if not isinstance(data, dict):
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason="judge_not_object",
            rewrite_instruction="质量判断失败，改成单句、有可回复点的主动开场。",
        )
    return _ProactiveOpeningJudgement(
        should_rewrite=bool(data.get("should_rewrite")),
        reason=str(data.get("reason") or "")[:180],
        rewrite_instruction=str(data.get("rewrite_instruction") or "")[:240],
    )


def _extract_json_object(raw: str) -> str:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    return text[start : end + 1]


def _proactive_opening_hard_violation(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return "multi_line_opening"
    compact = _compact_proactive_text(text)
    if not compact:
        return "empty_message"
    if compact in _BARE_USER_STATE_OPENINGS:
        return "assumes_user_current_state"
    if any(pattern in compact for pattern in _ASSUMED_USER_STATE_PATTERNS):
        return "assumes_user_current_state"
    return ""


def _compact_proactive_text(text: str) -> str:
    return re.sub(r"[\s，。！？!?~～…,.、：:；;\"'“”‘’（）()\[\]]+", "", text).lower()


def _short_for_judge(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "..."


def _fallback_proactive_opening(
    life: LifeSnapshot,
    *,
    relationship_context: str = "",
    retrieved: RetrievalResult | None = None,
    topic_hook: _ProactiveTopicHook | None = None,
) -> str:
    if topic_hook is not None and topic_hook.found and topic_hook.hook:
        return f"你有空的话，我想接着问问{topic_hook.hook}"
    hook = _relationship_topic_hook(relationship_context)
    if hook:
        return f"你有空的话，我想问问你之前说的{hook}后来怎么样了"
    if life.availability in {"busy", "away", "unavailable"}:
        return "我刚忙里偷闲想到个小事，想跟你说一下"
    return "你有空的话，我想问你个小事"


def _relationship_topic_hook(relationship_context: str) -> str:
    for raw in relationship_context.splitlines():
        line = raw.strip()
        if not line or "用户说" not in line:
            continue
        _, _, tail = line.partition("用户说")
        tail = tail.lstrip("：: ，,")
        tail = _clean_topic_hook(tail)
        if tail and not _is_low_information_hook(tail):
            return f"「{tail}」"
    return ""


def _clean_topic_hook(text: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", text)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -—:：，,。；;「」“”\"'‘’")
    if len(cleaned) < 4:
        return ""
    if len(cleaned) > 24:
        cleaned = cleaned[:24].rstrip()
    return cleaned


def _is_low_information_hook(hook: str) -> bool:
    compact = _compact_proactive_text(hook)
    if not compact:
        return True
    return compact in {_compact_proactive_text(item) for item in _LOW_INFORMATION_TOPIC_HOOKS}


def _proactive_scope_id(conversation_id: str | None, caller_id: str | None) -> str | None:
    if conversation_id and conversation_id.strip():
        return conversation_id.strip()
    if caller_id and caller_id.strip():
        return caller_id.strip()
    return None


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
