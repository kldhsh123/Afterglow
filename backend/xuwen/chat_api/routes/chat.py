"""/v1/chat/completions：OpenAI 兼容的对话端点。"""

from __future__ import annotations

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
    extract_and_apply_life_marker,
    fallback_for_rejected_sticker,
    is_ai_silence_signal,
    looks_like_sticker_only_intent,
)
from xuwen.chat_api.companion_prompt import (
    build_persona_card_with_companion_context,
    empty_retrieval_result,
    render_life_memory_context,
)
from xuwen.chat_api.image_store import ImageError, save_data_url
from xuwen.chat_api.llm_client import GenerationParams
from xuwen.chat_api.output_filter import AssistantOutputFilter, sanitize_assistant_text
from xuwen.chat_api.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ImagePart,
    ImageUrlPayload,
    PolicyHint,
    TextPart,
    Usage,
)
from xuwen.chat_api.schemas import (
    ChatMessage as APIChatMessage,
)
from xuwen.chat_api.state import AppState, get_state
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
from xuwen.memory.writer import WritebackTurn
from xuwen.persona.prompt import ChatMessage as PromptMessage
from xuwen.persona.prompt import build_chat_messages

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


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

    retrieval_query = current_user_text if current_user_text else "（用户发了一张图片）"
    _retrieval_start = time.perf_counter()
    try:
        retrieved = await state.retriever.retrieve(
            RetrievalQuery(
                query_text=retrieval_query,
                conversation_id=req.conversation_id,
            )
        )
        state.metrics.record(
            "retrieval",
            (time.perf_counter() - _retrieval_start) * 1000,
            detail=f"final={len(retrieved.fused)}",
        )
    except RetrievalError as e:
        logger.warning("检索失败，降级到无 RAG 模式：%s", e.message)
        state.metrics.record(
            "retrieval",
            (time.perf_counter() - _retrieval_start) * 1000,
            error=type(e).__name__,
        )
        retrieved = empty_retrieval_result()

    # 模型名固定使用后端配置的 CHAT_MODEL；req.model 接受但忽略
    model_name = state.settings.chat_model

    relationship_block = await state.relationship_memory.render_context(retrieval_query)
    life = await state.life.decide_for_turn(
        llm=state.life_llm,
        model=state.settings.resolved_life_model,
        current_user_text=current_user_text,
        recent=recent,
        relationship_context=relationship_block,
        memory_context=render_life_memory_context(retrieved, state.settings),
        trigger="chat",
        trace_id=trace_id,
        metrics=state.metrics,
    )
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

    # silence 短路
    if not response_decision.should_reply:
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

    persona_card = build_persona_card_with_companion_context(
        settings=state.settings,
        life=life,
        relationship_context=relationship_block,
        style_query=current_user_text,
        response_policy_context=response_decision.render_prompt_block(
            silence_sentinel=effective_silence_sentinel(state.settings),
        ),
    )
    web_context = ""
    web_should_search = should_search_web(current_user_text)
    if state.web_search is not None and web_should_search:
        web_results = await state.web_search.search(
            current_user_text,
            trace_id=trace_id,
            metrics=state.metrics,
        )
        web_context = render_web_context(web_results)
    else:
        _record_web_search_skipped(
            state,
            trace_id=trace_id,
            query=current_user_text,
            should_search=web_should_search,
        )

    url_context = ""
    urls: list[str] = []
    if state.web_fetch is not None:
        urls = await resolve_fetch_urls(
            current_user_text,
            llm=state.life_llm,
            model=state.settings.resolved_life_model,
            limit=state.settings.web_fetch_max_urls,
            trace_id=trace_id,
            metrics=state.metrics,
        )
        if urls:
            url_results = await state.web_fetch.fetch_many(
                urls,
                trace_id=trace_id,
                metrics=state.metrics,
            )
            url_context = render_url_context(url_results)
    if not urls:
        _record_web_fetch_skipped(state, trace_id=trace_id, urls=urls)

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
        stripped = extract_and_apply_life_marker(
            raw_assistant_text,
            state.life,
            enabled=state.settings.life_marker_update_enabled,
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
                    retry_stripped = extract_and_apply_life_marker(
                        retry_raw,
                        state.life,
                        enabled=state.settings.life_marker_update_enabled,
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
    # 假流式：客户端传 stream=true 但后端配置不启用真流式 → 把完整内容包装成
    # 单个 content chunk + 收尾，按 OpenAI SSE 协议返回，客户端无感。
    if req.stream:
        return StreamingResponse(
            _pseudo_stream_chunks(
                model_name=model_name,
                trace_id=trace_id,
                assistant_text=assistant_text,
                policy=policy_hint,
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
    )


async def _pseudo_stream_chunks(
    *,
    model_name: str,
    trace_id: str,
    assistant_text: str,
    policy: PolicyHint,
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

    yield _format_sse(_chunk_dict({}, finish=finish_reason, extra={"policy": policy.model_dump()}))
    yield b"data: [DONE]\n\n"

    # 流结束后用累积的完整 raw 文本跑 life-update 标记块解析（apply 即可，
    # 不影响已发出 chunk —— 标记块已在 sanitize 流程里被剥离）。
    extract_and_apply_life_marker(
        raw_full,
        state.life,
        enabled=state.settings.life_marker_update_enabled,
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
