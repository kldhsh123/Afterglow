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

from xuwen.analysis.proactive import (
    load_proactive_analysis,
    render_runtime_proactive_context,
)
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
from xuwen.content_i18n import content_text
from xuwen.core.errors import RetrievalError
from xuwen.core.models import RetrievalQuery, RetrievalResult
from xuwen.core.time import local_now
from xuwen.memory.writer import WritebackTurn
from xuwen.persona.prompt import build_chat_messages
from xuwen.prompt_language import (
    fallback_conversation_language,
    output_language,
    proactive_language_instruction,
    structured_output_instruction,
)

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
    "how are you",
    "how have you been",
    "are you there",
    "still awake",
    "are you still up",
    "what are you doing",
    "have you eaten",
    "good morning",
    "good night",
    "that topic",
    "our previous topic",
    "a little thing",
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

_ASSUMED_USER_STATE_EN_RE = re.compile(
    r"\b(?:you(?:'re| are)? (?:still )?(?:awake|up|online)|"
    r"you (?:haven't|have not) (?:slept|gone to bed)|"
    r"are you (?:still )?(?:awake|up|there|online)|"
    r"why (?:are you still (?:awake|up)|aren't you asleep)|"
    r"what are you (?:doing|up to)|i (?:saw|see) you(?:'re| are)? online)\b",
    re.I,
)


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
            persist=False,
        )
        candidate_valid = await state.proactive.finish_candidate_if_still_valid(
            scope_id,
            candidate_created_at_ms=result.candidate_created_at_ms,
            scheduled_for_ms=result.scheduled_for_ms,
        )
        if candidate_valid:
            if not proactive_response.silenced:
                await _persist_proactive_response(
                    req=ProactiveRequest(
                        conversation_id=req.conversation_id,
                        caller_id=req.caller_id,
                        reason=req.reason,
                        private_context=result.private_context,
                        topic_hint=result.topic_hint,
                    ),
                    state=state,
                    text=proactive_response.message,
                )
            status = "silenced" if proactive_response.silenced else "sent"
        else:
            proactive_response = None
            status = "cancelled"
            result.should_send = False
            result.reason = "cancelled_by_user_activity"
            result.skip_reasons = ["user_activity_during_generation"]
            result.cancelled_by_user_activity = True
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
    persist: bool = True,
) -> ProactiveResponse:
    base_life = state.life.snapshot()
    proactive_analysis = load_proactive_analysis(
        state.settings.analysis_data_dir / "proactive_analysis.json"
    )
    local_time = local_now(state.settings.app_timezone)
    analysis_context = render_runtime_proactive_context(
        proactive_analysis,
        hour=local_time.hour,
        weekday=local_time.weekday(),
    )
    retrieval_query = "\n".join(
        part
        for part in [
            "Retrieval goal: find a concrete memory hook for a proactive opening. Prefer recent unfinished topics, plans or states mentioned by the user, shared jokes or a light point of care.",
            f"Proactive trigger: {req.reason}",
            f"Current activity: {base_life.current_activity}",
            f"Topic seed: {base_life.topic_seed}",
            f"Internal background: {req.private_context}" if req.private_context else "",
            f"Topic direction: {req.topic_hint}" if req.topic_hint else "",
            analysis_context,
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
            logger.warning(
                content_text("Proactive topic retrieval failed; continuing without RAG: %s"),
                e.message,
            )
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
        "analysis_openings": len(proactive_analysis.openings) if proactive_analysis else 0,
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
        try:
            response_decision = await asyncio.wait_for(
                refine_decision_with_llm(
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
                ),
                timeout=state.settings.response_policy_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                content_text(
                    "Proactive response-policy model timed out after %.1fs; retaining the rule decision"
                ),
                state.settings.response_policy_timeout_seconds,
            )
            state.metrics.record(
                "companion.response.policy.refined",
                0.0,
                error="TimeoutError",
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
        + "\nINTERNAL PROACTIVE TRIGGER, not a new user message. Start a natural private conversation. "
        + proactive_language_instruction(state.settings.xuwen_language)
        + _render_cached_context_instruction(cached_context_text)
        + _render_proactive_topic_hook_instruction(topic_hook)
        + "Keep it short: one sentence, one message, no newlines. Include an easy reply point, "
        "preferably a light memory/relationship topic. No bare ping, nickname, abbreviation or "
        "placeholder. Your own state may set the scene but must lead to something to say, a question "
        "or an invitation; do not recite the life state. You have no live presence/read/activity "
        "signals about the user. Never assume they are online, awake or reading. Conditional wording "
        "such as 'if you have time' is fine. Do not fabricate real meetings or commitments. "
        "Output only the message; never explain the task."
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
    messages[0]["content"] += "\n\n" + proactive_language_instruction(state.settings.xuwen_language)

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
        fallback_empty=False,
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

    if persist:
        await _persist_proactive_response(req=req, state=state, text=text)

    return ProactiveResponse(
        message=text,
        life=_life_to_dict(life),
        relationship_memory=relationship_context,
        trace_id=trace_id,
        policy=policy_hint,
        debug=proactive_debug,
    )


async def _persist_proactive_response(
    *,
    req: ProactiveRequest,
    state: AppState,
    text: str,
) -> None:
    """把已确认发送的主动消息写入历史和主动上下文缓存。"""
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
        f"Rejected opening: {original_text!r}",
    ]
    if judgement.reason:
        rewrite_parts.append(f"Quality judgment: {judgement.reason}")
    if judgement.rewrite_instruction:
        rewrite_parts.append(f"Rewrite direction: {judgement.rewrite_instruction}")
    if cached_context_text:
        rewrite_parts.append(f"Recent conversation: \n{cached_context_text}")
    if topic_hook.found:
        rewrite_parts.append(f"Available topic hook: {topic_hook.hook}")
    rewrite_parts.append(
        "INTERNAL PROACTIVE REWRITE, not a new user message. Rewrite the opening as one short natural sentence, no newlines. Use the configured proactive language, including when the context or candidate uses a different language. Include an easy reply point from context. Do not assume current user presence, wakefulness, reading or activity. No bare ping, abbreviation, nickname or placeholder. Your own state must lead to a topic, question or invitation. Conditional wording is fine. Output only the final message."
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
    messages[0]["content"] += "\n\n" + proactive_language_instruction(state.settings.xuwen_language)
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
            fallback_empty=False,
        )
        state.metrics.record(
            "companion.proactive.rewrite",
            (time.perf_counter() - start) * 1000,
            detail=state.settings.chat_model,
        )
    except Exception:
        logger.warning(
            content_text("Proactive opening rewrite failed; using a conservative fallback"),
            exc_info=True,
        )
        debug["fallback_used"] = True
        debug["fallback_reason"] = "rewrite_failed"
        return _fallback_proactive_opening(
            life,
            relationship_context=relationship_context,
            retrieved=retrieved,
            topic_hook=topic_hook,
            language=state.settings.xuwen_language,
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
        language=state.settings.xuwen_language,
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
        "Select a topic hook for a proactive opening; do not write the chat message.\n"
        "Priority: recent conversation > relationship memory > retrieved excerpts. A hook should be "
        "specific without exposing quotations: a plan update, unfinished topic, recent event or light question. "
        "Reject codes, nicknames, bare greetings/pings, sleep checks, claimed user presence, the AI's "
        "own state, overly private quotations or fragments suitable only as replies. If unsupported, "
        "return found=false. Write hook as a short generalized phrase in the configured output language, "
        "at most 80 characters. Do not copy user wording or add quotation marks. "
        "Return JSON only: "
        '{"found":false,"hook":"","source":"none","reason":"brief reason"}. '
        "source must be context_cache/relationship/retrieval/none.\n"
        f"Recent conversation:\n{_short_for_judge(cached_context_text, 1800) or '(none)'}\n"
        f"Relationship memory:\n{_short_for_judge(relationship_context, 1000) or '(none)'}\n"
        f"Retrieved excerpts:\n{_short_for_judge(retrieved_text, 1800) or '(none)'}"
    )
    try:
        raw = await state.response_policy_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": "Select only a proactive conversation topic hook, not a chat message. Return JSON parseable by json.loads(). "
                    + structured_output_instruction(state.settings.xuwen_language),
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
        logger.warning(
            content_text("Topic-hook model failed; using a conservative local fallback"),
            exc_info=True,
        )
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
    return f"Recent conversation (find a continuation; do not copy quotations or disclose the cache):\n{cached_context_text}\n"


def _render_proactive_topic_hook_instruction(topic_hook: _ProactiveTopicHook) -> str:
    if not topic_hook.found:
        return "No reliable memory topic. Do not invent past events; choose a conservative, easy-to-answer opening. "
    return f"Available topic hook: {topic_hook.hook}. Build the opening around it without quoting the cache or explaining its source. "


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
            rewrite_instruction="Use an opening with an easy reply point without assuming the current user state.",
        )
    judge_prompt = (
        "Judge whether the candidate is a suitable FIRST message for a proactive private conversation. "
        "There is no new user message or live online/read/sleep/activity signal. A good opening is "
        "one natural short sentence with a reply point: a question, invitation, something to share or "
        "light context-based topic. Reject mid-conversation replies, state-only reports, bare pings, "
        "nicknames, placeholders, claimed user state, multiple lines/messages or recited life state. "
        "For example, 'Still scrolling before bed' is state-only; 'Are you there?' is a bare ping; "
        "'You are still awake' assumes presence. 'If you have time, can I ask a quick question?' and "
        "'I remembered our earlier topic and wanted to ask something' are suitable structures. "
        "Judge in any language. Return JSON only: "
        '{"should_rewrite":false,"reason":"brief reason","rewrite_instruction":"direction if needed"}.\n'
        f"Candidate: {text!r}\nTrigger: {_proactive_context_text(req)}\n"
        f"Character activity: {life.current_activity}; topic: {life.topic_seed}; availability: {life.availability}\n"
        f"Topic hook: {topic_hook.hook if topic_hook.found else '(none)'}\n"
        f"Relationship summary: {_short_for_judge(relationship_context, 700)}"
    )
    try:
        raw = await state.response_policy_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": "Judge quality only; do not generate chat text. Return JSON parseable by json.loads().",
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
        logger.warning(
            content_text(
                "Proactive opening quality model failed; preventing direct delivery of the original text"
            ),
            exc_info=True,
        )
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason="judge_failed",
            rewrite_instruction="Rewrite as a single-sentence proactive opening with an easy reply point, without restating the current state.",
        )
    return _parse_proactive_opening_judgement(raw)


def _parse_proactive_opening_judgement(raw: str) -> _ProactiveOpeningJudgement:
    try:
        data = json.loads(_extract_json_object(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason="judge_parse_failed",
            rewrite_instruction="Quality judgment failed. Use one sentence with an easy reply point.",
        )
    if not isinstance(data, dict):
        return _ProactiveOpeningJudgement(
            should_rewrite=True,
            reason="judge_not_object",
            rewrite_instruction="Quality judgment failed. Use one sentence with an easy reply point.",
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
    normalized = text.replace("’", "'")
    for match in _ASSUMED_USER_STATE_EN_RE.finditer(normalized):
        prefix = normalized[max(0, match.start() - 24) : match.start()]
        if not re.search(r"\b(?:if|in case|when|whenever)\s+$", prefix, re.I):
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
    language: str | None = None,
) -> str:
    language = language or output_language()
    hook = topic_hook.hook if topic_hook is not None and topic_hook.found else ""
    if hook and fallback_conversation_language(hook) == language:
        return content_text(
            "If you have a moment, can we pick up on {hook}?", language=language
        ).format(hook=hook)
    hook = _relationship_topic_hook(relationship_context)
    if hook and fallback_conversation_language(hook) == language:
        return content_text("If you have time, how did {hook} turn out?", language=language).format(
            hook=hook
        )
    if life.availability in {"busy", "away", "unavailable"}:
        return content_text(
            "I have a moment now and thought of something to tell you", language=language
        )
    return content_text("If you have a moment, can I ask you something?", language=language)


def _relationship_topic_hook(relationship_context: str) -> str:
    for raw in relationship_context.splitlines():
        line = raw.strip()
        marker = re.search(r"用户说|\bUser (?:said|says|mentioned)\b", line, re.I)
        if marker is None:
            continue
        tail = line[marker.end() :]
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
    if len(cleaned) > 80:
        prefix = cleaned[:80]
        cleaned = prefix.rsplit(" ", 1)[0] if " " in prefix else prefix
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
    parts = [f"Proactive trigger: {req.reason}"]
    if req.private_context:
        parts.append(f"Internal trigger background (not a user message): {req.private_context}")
    if req.topic_hint:
        parts.append(f"Topic direction: {req.topic_hint}")
    return "; ".join(parts)


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
