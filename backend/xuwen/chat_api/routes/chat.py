"""/v1/chat/completions：OpenAI 兼容的对话端点。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from xuwen.chat_api.chat_pipeline import (
    available_sticker_names,
    build_policy_hint,
    build_sticker_retry_hint,
    effective_reply_delay_seconds,
    effective_silence_sentinel,
    extract_life_marker_async,
    extract_schedule_hints,
    fallback_for_rejected_sticker,
    is_ai_silence_signal,
    looks_like_sticker_only_intent,
)
from xuwen.chat_api.companion_prompt import (
    build_persona_card_with_companion_context,
    render_life_memory_context_from_recent,
)
from xuwen.chat_api.image_store import ImageError, save_data_url
from xuwen.chat_api.llm_client import GenerationParams
from xuwen.chat_api.output_filter import AssistantOutputFilter, sanitize_assistant_text
from xuwen.chat_api.schedule_extractor import extract_schedule_tasks
from xuwen.chat_api.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ImagePart,
    ImageUrlPayload,
    PolicyHint,
    ScheduleTask,
    TextPart,
    Usage,
)
from xuwen.chat_api.schemas import (
    ChatMessage as APIChatMessage,
)
from xuwen.chat_api.state import AppState, get_state
from xuwen.chat_api.turn_coordinator import TurnSnapshot
from xuwen.chat_api.vision_client import VisionClient
from xuwen.chat_api.web_fetch import render_url_context, resolve_fetch_urls
from xuwen.chat_api.web_search import render_web_context, should_search_web
from xuwen.companion.response_policy import (
    ResponseDecision,
    decide_response_policy,
    refine_decision_with_llm,
)
from xuwen.config import Settings
from xuwen.core.errors import RetrievalError, XuwenError
from xuwen.core.models import RetrievalQuery
from xuwen.core.time import local_now
from xuwen.memory.writer import WritebackTurn
from xuwen.persona.prompt import ChatMessage as PromptMessage
from xuwen.persona.prompt import build_chat_messages

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

_CHAT_RETRIEVAL_TIMEOUT_SECONDS = 15.0
_CHAT_LIFE_TIMEOUT_SECONDS = 12.0
_CHAT_RELATIONSHIP_TIMEOUT_SECONDS = 5.0


@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    req: ChatCompletionRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> StreamingResponse | ChatCompletionResponse:
    trace_id = str(getattr(request.state, "request_id", "") or "")
    user_messages = [m for m in req.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="messages 中至少要有一条 role=user")
    last_user = user_messages[-1]

    image_shas: list[str] = []
    vlm_descriptions: list[str] = []
    images_in_last = last_user.image_urls()

    if images_in_last:
        if not state.settings.vision_enabled:
            raise HTTPException(
                status_code=400,
                detail="未启用视觉理解。请在后端 .env 设置 VISION_ENABLED=true。",
            )
        for url in images_in_last:
            try:
                ref = save_data_url(url, state.settings)
            except ImageError as e:
                raise HTTPException(status_code=400, detail=e.message) from e
            image_shas.append(ref.sha)
        if not state.settings.chat_model_supports_vision:
            if (
                not state.settings.vision_api_url
                or not state.settings.vision_api_key.get_secret_value()
            ):
                raise HTTPException(
                    status_code=400,
                    detail="主模型不支持视觉，且 VISION_API_URL / VISION_API_KEY 未配置。",
                )
            async with VisionClient(state.settings) as vc:
                vlm_descriptions = await vc.describe_images(images_in_last)

    recent: list[PromptMessage] = [
        PromptMessage(role=m.role, content=m.text_only())
        for m in req.messages[:-1]
        if m.role in {"user", "assistant"}
    ]
    current_user_text = last_user.text_only().strip()
    if vlm_descriptions:
        desc_block = "\n".join(
            f"[图片{i + 1}描述：{d}]" for i, d in enumerate(vlm_descriptions)
        )
        current_user_text = (current_user_text + "\n" + desc_block).strip()

    turn_snapshot: TurnSnapshot | None = None
    if req.caller_id:
        coordinator = getattr(state, "turn_coordinator", None)
        if coordinator is not None:
            turn_snapshot = await coordinator.begin_turn(
                caller_id=req.caller_id,
                message_id=req.client_message_id,
                text=current_user_text,
                image_shas=image_shas,
                image_urls=images_in_last,
            )
            current_user_text = turn_snapshot.combined_text() or current_user_text
            image_shas = turn_snapshot.combined_image_shas()
            images_in_last = turn_snapshot.combined_image_urls()

    retrieval_query = current_user_text if current_user_text else "（用户发了一张图片）"
    _retrieval_start = time.perf_counter()

    async def _retrieve_with_metrics() -> Any:
        try:
            result = await asyncio.wait_for(
                state.retriever.retrieve(
                    RetrievalQuery(
                        query_text=retrieval_query,
                        conversation_id=req.conversation_id,
                    ),
                    metrics=state.metrics,
                    trace_id=trace_id,
                ),
                timeout=_CHAT_RETRIEVAL_TIMEOUT_SECONDS,
            )
            state.metrics.record(
                "retrieval",
                (time.perf_counter() - _retrieval_start) * 1000,
                detail=f"final={len(result.fused)}",
            )
            return result
        except TimeoutError:
            logger.warning(
                "检索超时 %.1fs，停止本轮聊天",
                _CHAT_RETRIEVAL_TIMEOUT_SECONDS,
            )
            state.metrics.record(
                "retrieval",
                (time.perf_counter() - _retrieval_start) * 1000,
                error="TimeoutError",
            )
            raise HTTPException(
                status_code=504,
                detail=(
                    f"记忆检索超时（>{_CHAT_RETRIEVAL_TIMEOUT_SECONDS:.0f}s）。"
                    "本轮已停止：请先检查 Embedding/向量模型连通性。"
                ),
            ) from None
        except RetrievalError as e:
            logger.warning("检索失败，停止本轮聊天：%s", e.message)
            state.metrics.record(
                "retrieval",
                (time.perf_counter() - _retrieval_start) * 1000,
                error=type(e).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail=f"记忆检索失败，本轮已停止：{e.message}",
            ) from e

    async def _web_search_or_skip() -> str:
        web_should_search = should_search_web(current_user_text)
        if state.web_search is None or not web_should_search:
            _record_web_search_skipped(
                state,
                trace_id=trace_id,
                query=current_user_text,
                should_search=web_should_search,
            )
            return ""
        try:
            results = await state.web_search.search(
                current_user_text,
                trace_id=trace_id,
                metrics=state.metrics,
            )
            return render_web_context(results)
        except Exception:
            logger.warning("web_search 调用失败", exc_info=True)
            return ""

    async def _resolve_urls_or_skip() -> list[str]:
        if state.web_fetch is None:
            return []
        try:
            return await resolve_fetch_urls(
                current_user_text,
                llm=state.life_llm,
                model=state.settings.resolved_life_model,
                limit=state.settings.web_fetch_max_urls,
                trace_id=trace_id,
                metrics=state.metrics,
            )
        except Exception:
            logger.warning("resolve_fetch_urls 失败", exc_info=True)
            return []

    # life 进 Layer A 并发：memory_context 改用 recent（不依赖 retrieved），
    # relationship_context 同步读 markdown 文件（几 ms 不阻塞），让 life 也能塞进 gather。
    # 慢路径时 life 模型看不到"相似历史片段"，但 circadian + 上次状态 + recent + 用户输入
    # 仍是 life 决策的核心信号，影响很小；多数轮走快路径（缓存早退）则几乎零成本。
    life_markdown = state.relationship_memory.load_markdown()

    async def _life_in_parallel():
        async with state.life_apply_lock:
            try:
                return await asyncio.wait_for(
                    state.life.decide_for_turn(
                        llm=state.life_llm,
                        model=state.settings.resolved_life_model,
                        current_user_text=current_user_text,
                        recent=recent,
                        relationship_context=life_markdown,
                        memory_context=render_life_memory_context_from_recent(recent, state.settings),
                        trigger="chat",
                        trace_id=trace_id,
                        metrics=state.metrics,
                    ),
                    timeout=_CHAT_LIFE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "life 决策超时 %.1fs，沿用当前 snapshot",
                    _CHAT_LIFE_TIMEOUT_SECONDS,
                )
                state.metrics.record("life.decide", 0.0, error="TimeoutError")
                return state.life.snapshot()

    async def _relationship_context_or_empty() -> str:
        try:
            return await asyncio.wait_for(
                state.relationship_memory.render_context(
                    retrieval_query,
                    include_relevant=False,
                    metrics=state.metrics,
                    trace_id=trace_id,
                ),
                timeout=_CHAT_RELATIONSHIP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "关系记忆渲染超时 %.1fs，降级为空上下文",
                _CHAT_RELATIONSHIP_TIMEOUT_SECONDS,
            )
            state.metrics.record("relationship.context", 0.0, error="TimeoutError")
            return ""

    # Layer A：只放"无论是否回复都需要"的预决策任务（检索 / 关系记忆 / life）。
    # Web Search / URL Resolve 涉及外部 API 调用 + 用户隐私文本外发，必须等 decision
    # 确认 should_reply=True 才能启动；否则用户说"别回我"也会触发搜索调用（隐私 + 费用泄漏）。
    retrieved, relationship_block, life = await asyncio.gather(
        _retrieve_with_metrics(),
        _relationship_context_or_empty(),
        _life_in_parallel(),
    )

    # 模型名固定使用后端配置的 CHAT_MODEL；req.model 接受但忽略
    model_name = state.settings.chat_model

    # fetch_many 依赖 fetch_urls；定义函数体，等 silence 决策确认后再启动
    async def _fetch_many_or_skip(urls: list[str]) -> str:
        if not urls or state.web_fetch is None:
            _record_web_fetch_skipped(state, trace_id=trace_id, urls=urls)
            return ""
        try:
            url_results = await state.web_fetch.fetch_many(
                urls,
                trace_id=trace_id,
                metrics=state.metrics,
            )
            return render_url_context(url_results)
        except Exception:
            logger.warning("fetch_many 失败", exc_info=True)
            return ""

    response_decision = decide_response_policy(
        current_user_text=current_user_text,
        has_images=bool(images_in_last),
        retrieved=retrieved,
        life=life,
        relationship_context=relationship_block,
        recent=recent,
    )
    state.metrics.record(
        "response.policy",
        0.0,
        detail=f"trace={trace_id},{response_decision.metric_detail()}",
    )
    if state.settings.response_policy_model_enabled:
        response_decision = await refine_decision_with_llm(
            base=response_decision,
            llm=state.response_policy_llm,
            model=state.settings.resolved_response_policy_model,
            settings=state.settings,
            current_user_text=current_user_text,
            recent=recent,
            life=life,
            relationship_context=relationship_block,
            has_images=bool(images_in_last),
            trace_id=trace_id,
            metrics=state.metrics,
        )
        state.metrics.record(
            "response.policy.refined",
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

    # silence 短路：放在 web/url 调用之前，避免用户说"别说话"还把消息发到搜索 / URL 解析端
    if not response_decision.should_reply:
        if await _turn_was_cancelled(state, turn_snapshot):
            if req.stream:
                return StreamingResponse(
                    _stream_cancelled(model_name=model_name, trace_id=trace_id),
                    media_type="text/event-stream",
                )
            return _cancelled_response(model_name=model_name, trace_id=trace_id)
        if req.conversation_id and (current_user_text or image_shas):
            await state.writeback.enqueue_turn(
                WritebackTurn(
                    conversation_id=req.conversation_id,
                    user_text=current_user_text,
                    assistant_text="",
                    user_image_shas=image_shas,
                )
            )
        state.metrics.record(
            "chat.silenced",
            0.0,
            detail=f"trace={trace_id},{response_decision.metric_detail()}",
        )
        await _ack_turn(state, turn_snapshot)
        if req.stream:
            return StreamingResponse(
                _stream_silenced(
                    settings=state.settings,
                    model_name=model_name,
                    trace_id=trace_id,
                    policy=policy_hint,
                ),
                media_type="text/event-stream",
            )
        return ChatCompletionResponse(
            model=model_name,
            choices=[
                Choice(
                    index=0,
                    message=APIChatMessage(
                        role="assistant",
                        content=state.settings.silence_response_sentinel,
                    ),
                    finish_reason=state.settings.silence_finish_reason,
                )
            ],
            usage=Usage(),
            trace_id=trace_id,
            policy=policy_hint,
        )

    # Layer B：decision 确认要回复后才并发跑 Web Search + URL Resolve。
    # fetch_many 依赖 fetch_urls，依旧 fire-and-forget 让它在 prompt 组装 / LLM 调用前完成。
    web_context, fetch_urls = await asyncio.gather(
        _web_search_or_skip(),
        _resolve_urls_or_skip(),
    )
    fetch_many_task: asyncio.Task[str] = asyncio.create_task(_fetch_many_or_skip(fetch_urls))

    persona_card = build_persona_card_with_companion_context(
        settings=state.settings,
        life=life,
        relationship_context=relationship_block,
        style_query=current_user_text,
        response_policy_context=response_decision.render_prompt_block(
            silence_sentinel=effective_silence_sentinel(state.settings),
        ),
        # 本路由（ChatCompletions）会调用 schedule_extractor 并回传 schedule_tasks，
        # 所以可以安全地教 AI 输出 <schedule-hint>。Responses / Companion 路由
        # 未接入提取链路，传 False（默认）以免任务被静默丢失。
        include_schedule_hint=True,
    )
    # 等 Layer B 起的 fetch_many 跑完。如果 prompt 组装已盖住 fetch RTT，这里 await 接近 0ms。
    url_context = await fetch_many_task

    messages = build_chat_messages(
        settings=state.settings,
        persona_card=persona_card,
        retrieved=retrieved,
        recent=recent,
        current_user_message=current_user_text or "（图片）",
        web_context=web_context,
        url_context=url_context,
    )

    if (
        images_in_last
        and state.settings.chat_model_supports_vision
        and messages
        and messages[-1]["role"] == "user"
    ):
        text_for_user = messages[-1]["content"]
        if isinstance(text_for_user, str):
            multimodal_content: list[dict[str, Any]] = [
                {"type": "text", "text": text_for_user},
            ]
            for url in images_in_last:
                multimodal_content.append(
                    {"type": "image_url", "image_url": {"url": url}}
                )
            messages[-1] = {"role": "user", "content": multimodal_content}  # type: ignore[dict-item]

    params = GenerationParams(
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_tokens,
        presence_penalty=req.presence_penalty,
        frequency_penalty=req.frequency_penalty,
    )

    # 真流式：仅当 RESPONSE_STREAMING_ENABLED=true 时启用。
    # 否则即使客户端传 stream=true 也走非流式路径，最后再包装成 SSE 单 chunk 发出
    # （Afterglow 模拟"真人发消息"，不应该逐字蹦）。
    if req.stream and state.settings.response_streaming_enabled:
        return StreamingResponse(
            _stream_response(
                state=state,
                messages=messages,
                params=params,
                model_name=model_name,
                conversation_id=req.conversation_id,
                user_text=current_user_text,
                image_shas=image_shas,
                trace_id=trace_id,
                policy=policy_hint,
                decision=response_decision,
                turn_snapshot=turn_snapshot,
            ),
            media_type="text/event-stream",
        )

    _llm_start = time.perf_counter()
    try:
        raw_assistant_text = await state.llm.complete_chat(
            messages,
            params,
            model=model_name,
            trace_id=trace_id,
            stage="chat.complete",
            metrics=state.metrics,
        )
        # 先解析 + 应用 life-update 标记块（顺手剥离），再走通用 sanitize
        stripped = extract_life_marker_async(
            raw_assistant_text,
            state.life,
            enabled=state.settings.life_marker_update_enabled,
            apply_lock=state.life_apply_lock,
            pending_tasks=state.pending_life_tasks,
        )
        valid_names = available_sticker_names(state.settings)
        assistant_text = sanitize_assistant_text(
            stripped,
            valid_sticker_names=valid_names,
        )
        # AI 自主沉默：主模型严格输出 sentinel → 转沉默路径。
        # unsafe 等硬边界场景由 is_ai_silence_signal 内部守卫，不会进入这里。
        ai_silenced = is_ai_silence_signal(
            assistant_text,
            sentinel=effective_silence_sentinel(state.settings),
            decision=response_decision,
        )
        if ai_silenced:
            state.metrics.record(
                "chat.silenced.ai",
                0.0,
                detail=f"trace={trace_id},{response_decision.metric_detail()}",
            )
        # 模型整段只发了不存在的 sticker → sanitize 后空。
        # 先尝试让主模型重新生成一次（带明确提示），失败再退回 reply_mode-aware 短句。
        if (
            not ai_silenced
            and assistant_text in {"嗯", ""}
            and looks_like_sticker_only_intent(stripped)
        ):
            retried = False
            if state.settings.sticker_reject_retry:
                hint = build_sticker_retry_hint(stripped, valid_names)
                retry_messages = [
                    *messages,
                    {"role": "system", "content": hint},
                ]
                try:
                    retry_raw = await state.llm.complete_chat(
                        retry_messages,
                        params,
                        model=model_name,
                        trace_id=trace_id,
                        stage="chat.complete.sticker_retry",
                        metrics=state.metrics,
                    )
                    retry_stripped = extract_life_marker_async(
                        retry_raw,
                        state.life,
                        enabled=state.settings.life_marker_update_enabled,
                        apply_lock=state.life_apply_lock,
                        pending_tasks=state.pending_life_tasks,
                    )
                    retry_text = sanitize_assistant_text(
                        retry_stripped,
                        valid_sticker_names=valid_names,
                    )
                    if retry_text and retry_text != "嗯" and not looks_like_sticker_only_intent(
                        retry_stripped
                    ):
                        assistant_text = retry_text
                        retried = True
                        state.metrics.record(
                            "chat.sticker.retry_ok",
                            0.0,
                            detail=f"trace={trace_id},mode={response_decision.reply_mode}",
                        )
                except Exception:
                    logger.warning("sticker retry 失败，回退到短句兜底", exc_info=True)
            if not retried:
                assistant_text = (
                    fallback_for_rejected_sticker(response_decision.reply_mode)
                    or assistant_text
                )
                state.metrics.record(
                    "chat.sticker.rejected",
                    0.0,
                    detail=f"trace={trace_id},mode={response_decision.reply_mode}",
                )
        state.metrics.record(
            "llm.complete",
            (time.perf_counter() - _llm_start) * 1000,
            detail=model_name,
        )
    except XuwenError as e:
        state.metrics.record(
            "llm.complete",
            (time.perf_counter() - _llm_start) * 1000,
            error=e.code,
        )
        raise

    if await _turn_was_cancelled(state, turn_snapshot):
        return _cancelled_response(model_name=model_name, trace_id=trace_id)

    if req.conversation_id:
        await state.writeback.enqueue_turn(
            WritebackTurn(
                conversation_id=req.conversation_id,
                user_text=current_user_text,
                # 沉默时写空 assistant_text，保持与规则层 silence 短路一致：
                # 历史里不留 sentinel 文本，避免后续检索把 [silent] 当成真人风格。
                assistant_text="" if ai_silenced else assistant_text,
                user_image_shas=image_shas,
            )
        )
        await state.relationship_memory.remember_turn(
            conversation_id=req.conversation_id,
            user_text=current_user_text,
            assistant_text="" if ai_silenced else assistant_text,
        )
    await _ack_turn(state, turn_snapshot)
    # Feature #9：从主模型原始输出抽 <schedule-hint>，调用小模型解析为 ScheduleTask。
    # 失败/未启用时为 None；不影响正常回复链路。
    schedule_tasks_field = None
    if state.settings.schedule_extract_enabled and not ai_silenced:
        hints = extract_schedule_hints(
            raw_assistant_text,
            max_hints=state.settings.schedule_max_hints_per_turn,
        )
        if hints:
            tasks = await extract_schedule_tasks(
                hints,
                llm=state.schedule_extractor_llm,
                settings=state.settings,
                now=local_now(state.settings.app_timezone),
                trace_id=trace_id,
                metrics=state.metrics,
            )
            schedule_tasks_field = tasks or None

    # 假流式：客户端传 stream=true 但后端配置不启用真流式 → 把完整内容包装成
    # 单个 content chunk + 收尾，按 OpenAI SSE 协议返回，客户端无感。
    if req.stream:
        return StreamingResponse(
            _pseudo_stream_chunks(
                model_name=model_name,
                trace_id=trace_id,
                assistant_text=assistant_text,
                policy=policy_hint,
                schedule_tasks=schedule_tasks_field,
                finish_reason=(
                    state.settings.silence_finish_reason if ai_silenced else "stop"
                ),
            ),
            media_type="text/event-stream",
        )

    return ChatCompletionResponse(
        model=model_name,
        choices=[
            Choice(
                index=0,
                message=APIChatMessage(role="assistant", content=assistant_text),
                finish_reason=(
                    state.settings.silence_finish_reason if ai_silenced else "stop"
                ),
            )
        ],
        usage=Usage(),
        trace_id=trace_id,
        policy=policy_hint,
        schedule_tasks=schedule_tasks_field,
    )


async def _pseudo_stream_chunks(
    *,
    model_name: str,
    trace_id: str,
    assistant_text: str,
    policy: PolicyHint,
    schedule_tasks: list[ScheduleTask] | None = None,
    finish_reason: str = "stop",
) -> AsyncIterator[bytes]:
    """OpenAI SSE 协议包装：把已经生成好的完整 assistant_text 作为单个 content chunk
    发出，再发 finish + [DONE]。等同非流式行为，但符合 stream=true 客户端协议预期。"""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _chunk(
        delta: dict[str, Any],
        finish: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "trace_id": trace_id,
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish},
            ],
        }
        if extra:
            payload.update(extra)
        return payload

    yield _format_sse(
        _chunk({"role": "assistant"}, extra={"policy": policy.model_dump()})
    )
    if assistant_text:
        yield _format_sse(_chunk({"content": assistant_text}))
    final = _chunk({}, finish=finish_reason)
    final["policy"] = policy.model_dump()
    if schedule_tasks:
        final["schedule_tasks"] = [t.model_dump() for t in schedule_tasks]
    yield _format_sse(final)
    yield b"data: [DONE]\n\n"


async def _stream_response(
    *,
    state: AppState,
    messages: list[dict[str, Any]],
    params: GenerationParams,
    model_name: str,
    conversation_id: str | None,
    user_text: str,
    image_shas: list[str],
    trace_id: str,
    policy: PolicyHint,
    decision: ResponseDecision,
    turn_snapshot: TurnSnapshot | None = None,
) -> AsyncIterator[bytes]:
    """OpenAI SSE 格式生成 chunk；收尾块带 policy 字段。"""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    buffer: list[str] = []
    output_filter = AssistantOutputFilter(
        valid_sticker_names=available_sticker_names(state.settings),
    )

    def _chunk_dict(
        delta: dict[str, Any],
        finish: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "trace_id": trace_id,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish,
                }
            ],
        }
        if extra:
            payload.update(extra)
        return payload

    if await _turn_was_cancelled(state, turn_snapshot):
        async for chunk in _stream_cancelled(model_name=model_name, trace_id=trace_id):
            yield chunk
        return

    yield _format_sse(
        _chunk_dict({"role": "assistant"}, extra={"policy": policy.model_dump()})
    )

    _stream_start = time.perf_counter()
    try:
        async for piece in state.llm.stream_chat(
            messages,
            params,
            model=model_name,
            trace_id=trace_id,
            stage="chat.stream",
            metrics=state.metrics,
        ):
            if await _turn_was_cancelled(state, turn_snapshot):
                yield _format_sse(_chunk_dict({}, finish="cancelled"))
                yield b"data: [DONE]\n\n"
                return
            filtered = output_filter.feed(piece)
            if not filtered:
                continue
            buffer.append(filtered)
            yield _format_sse(_chunk_dict({"content": filtered}))
        tail = output_filter.flush()
        if tail:
            buffer.append(tail)
            yield _format_sse(_chunk_dict({"content": tail}))
        state.metrics.record(
            "llm.stream",
            (time.perf_counter() - _stream_start) * 1000,
            detail=f"{model_name},chars={sum(len(p) for p in buffer)}",
        )
    except XuwenError as e:
        state.metrics.record(
            "llm.stream",
            (time.perf_counter() - _stream_start) * 1000,
            error=e.code,
        )
        yield _format_sse(
            {
                "error": {
                    "code": e.code,
                    "message": e.message,
                    "trace_id": trace_id,
                }
            }
        )
        yield b"data: [DONE]\n\n"
        return

    # 流式补救：模型整条只发了不存在的 sticker → 所有 chunk 都被剥离 →
    # 用户什么也没看到。这里在 finish chunk 之前补发一段 fallback delta。
    raw_full = output_filter.raw_text()
    if not "".join(buffer).strip() and looks_like_sticker_only_intent(raw_full):
        fallback = fallback_for_rejected_sticker(policy.reply_mode)
        if fallback:
            buffer.append(fallback)
            yield _format_sse(_chunk_dict({"content": fallback}))
            state.metrics.record(
                "chat.sticker.rejected",
                0.0,
                detail=f"trace={trace_id},mode={policy.reply_mode},stream",
            )

    # AI 自主沉默：累积完整 buffer == sentinel → finish_reason 改 silenced，
    # 写历史时 assistant_text 置空（与规则层 silence 短路保持一致）。
    full_text = "".join(buffer)
    ai_silenced = is_ai_silence_signal(
        full_text,
        sentinel=effective_silence_sentinel(state.settings),
        decision=decision,
    )
    if ai_silenced:
        state.metrics.record(
            "chat.silenced.ai",
            0.0,
            detail=f"trace={trace_id},{decision.metric_detail()},stream",
        )
    finish_reason = state.settings.silence_finish_reason if ai_silenced else "stop"

    # Feature #9 Finding 1：真流式同样要把 schedule_tasks 放进收尾 chunk，
    # 与假流式 / 非流式 schema 一致。失败/未启用时 None，不影响协议兼容。
    final_extra: dict[str, Any] = {"policy": policy.model_dump()}
    if state.settings.schedule_extract_enabled and not ai_silenced:
        hints = extract_schedule_hints(
            raw_full,
            max_hints=state.settings.schedule_max_hints_per_turn,
        )
        if hints:
            stream_tasks = await extract_schedule_tasks(
                hints,
                llm=state.schedule_extractor_llm,
                settings=state.settings,
                now=local_now(state.settings.app_timezone),
                trace_id=trace_id,
                metrics=state.metrics,
            )
            if stream_tasks:
                final_extra["schedule_tasks"] = [t.model_dump() for t in stream_tasks]

    if await _turn_was_cancelled(state, turn_snapshot):
        yield _format_sse(_chunk_dict({}, finish="cancelled"))
        yield b"data: [DONE]\n\n"
        return

    yield _format_sse(_chunk_dict({}, finish=finish_reason, extra=final_extra))
    yield b"data: [DONE]\n\n"

    if await _turn_was_cancelled(state, turn_snapshot):
        return

    # 流结束后用累积的完整 raw 文本跑 life-update 标记块解析（apply 即可，
    # 不影响已发出 chunk —— 标记块已在 sanitize 流程里被剥离）。
    extract_life_marker_async(
        raw_full,
        state.life,
        enabled=state.settings.life_marker_update_enabled,
        apply_lock=state.life_apply_lock,
        pending_tasks=state.pending_life_tasks,
    )

    assistant_text = "" if ai_silenced else full_text
    if conversation_id and (assistant_text or ai_silenced):
        await state.writeback.enqueue_turn(
            WritebackTurn(
                conversation_id=conversation_id,
                user_text=user_text,
                assistant_text=assistant_text,
                user_image_shas=image_shas,
            )
        )
        await state.relationship_memory.remember_turn(
            conversation_id=conversation_id,
            user_text=user_text,
            assistant_text=assistant_text,
        )
    await _ack_turn(state, turn_snapshot)


async def _stream_silenced(
    *,
    settings: Settings,
    model_name: str,
    trace_id: str,
    policy: PolicyHint,
) -> AsyncIterator[bytes]:
    """决策层选择不回复时按 OpenAI SSE 协议返回最小响应。"""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _chunk(
        delta: dict[str, Any],
        finish: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "trace_id": trace_id,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish,
                }
            ],
        }
        if extra:
            payload.update(extra)
        return payload

    yield _format_sse(
        _chunk({"role": "assistant"}, extra={"policy": policy.model_dump()})
    )
    sentinel = settings.silence_response_sentinel
    if sentinel:
        yield _format_sse(_chunk({"content": sentinel}))
    final = _chunk({}, finish=settings.silence_finish_reason)
    final["policy"] = policy.model_dump()
    yield _format_sse(final)
    yield b"data: [DONE]\n\n"


async def _turn_was_cancelled(
    state: AppState,
    turn_snapshot: TurnSnapshot | None,
) -> bool:
    if turn_snapshot is None:
        return False
    if turn_snapshot.cancel_event.is_set():
        return True
    coordinator = getattr(state, "turn_coordinator", None)
    if coordinator is None:
        return False
    return not await coordinator.is_current(turn_snapshot)


async def _ack_turn(state: AppState, turn_snapshot: TurnSnapshot | None) -> None:
    if turn_snapshot is None:
        return
    coordinator = getattr(state, "turn_coordinator", None)
    if coordinator is None:
        return
    await coordinator.ack(turn_snapshot)


def _cancelled_response(*, model_name: str, trace_id: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        model=model_name,
        choices=[
            Choice(
                index=0,
                message=APIChatMessage(role="assistant", content=""),
                finish_reason="cancelled",
            )
        ],
        usage=Usage(),
        trace_id=trace_id,
    )


async def _stream_cancelled(
    *,
    model_name: str,
    trace_id: str,
) -> AsyncIterator[bytes]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "trace_id": trace_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    yield _format_sse(_chunk({"role": "assistant"}))
    yield _format_sse(_chunk({}, finish="cancelled"))
    yield b"data: [DONE]\n\n"


def _format_sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _record_web_search_skipped(
    state: AppState,
    *,
    trace_id: str,
    query: str,
    should_search: bool,
) -> None:
    if not state.settings.web_access_enabled:
        reason = "web_access_disabled"
    elif state.web_search is None:
        reason = "web_search_client_inactive"
    elif not should_search:
        reason = "trigger_not_matched"
    else:
        reason = "unknown"
    state.metrics.record(
        "web.search.skipped",
        0.0,
        detail=(
            f"reason={reason},trace={trace_id},"
            f"should_search={str(should_search).lower()},query_chars={len(query)}"
        ),
    )


def _record_web_fetch_skipped(
    state: AppState,
    *,
    trace_id: str,
    urls: list[str],
) -> None:
    if not state.settings.web_access_enabled:
        reason = "web_access_disabled"
    elif not state.settings.web_fetch_enabled:
        reason = "web_fetch_disabled"
    elif state.web_fetch is None:
        reason = "web_fetch_client_inactive"
    elif not urls:
        reason = "no_url"
    else:
        reason = "unknown"
    state.metrics.record(
        "web.fetch.skipped",
        0.0,
        detail=f"reason={reason},trace={trace_id},url_count={len(urls)}",
    )


_unused: tuple[type, ...] = (ChatMessage, ImagePart, ImageUrlPayload, TextPart)
