"""/v1/responses：OpenAI Responses API 兼容端点（中等子集）。

支持：input(str / message array / 含多模态 input_image)、instructions、stream、
temperature / top_p / max_output_tokens、previous_response_id（LRU 缓存）、conversation_id。

不支持：tools / function_call / file inputs / image_generation / code_interpreter /
MCP / background mode。

model 字段是 OpenAI 协议占位，实际使用 .env 的 CHAT_MODEL。
"""

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
from xuwen.chat_api.responses_store import ResponseRecord
from xuwen.chat_api.schemas import (
    PolicyHint,
    ResponsesInputImageContent,
    ResponsesInputMessage,
    ResponsesInputTextContent,
    ResponsesOutputMessage,
    ResponsesOutputTextContent,
    ResponsesRequest,
    ResponsesResponse,
    ResponsesUsage,
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
from xuwen.core.errors import RetrievalError, XuwenError
from xuwen.core.models import RetrievalQuery
from xuwen.memory.writer import WritebackTurn
from xuwen.persona.prompt import ChatMessage as PromptMessage
from xuwen.persona.prompt import build_chat_messages

logger = logging.getLogger(__name__)
router = APIRouter(tags=["responses"])


@router.post("/v1/responses", response_model=None)
async def responses(
    req: ResponsesRequest,
    request: Request,
    state: AppState = Depends(get_state),
) -> StreamingResponse | ResponsesResponse:
    trace_id = str(getattr(request.state, "request_id", "") or "")

    history, last_user_text, last_user_images = _normalize_input(req)

    conversation_id = req.conversation_id
    if conversation_id is None and req.previous_response_id:
        prev = state.responses_store.get(req.previous_response_id)
        if prev is not None and prev.conversation_id:
            conversation_id = prev.conversation_id

    image_shas: list[str] = []
    vlm_descriptions: list[str] = []
    if last_user_images:
        if not state.settings.vision_enabled:
            raise HTTPException(
                status_code=400,
                detail="未启用视觉理解。请在后端 .env 设置 VISION_ENABLED=true。",
            )
        for url in last_user_images:
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
                vlm_descriptions = await vc.describe_images(last_user_images)

    recent = history
    current_user_text = (last_user_text or "").strip()
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
                conversation_id=conversation_id,
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

    # model 字段占位：永远用 .env 的 CHAT_MODEL
    model_name = state.settings.chat_model

    relationship_block = await state.relationship_memory.render_context(retrieval_query)
    life = await state.life.decide_for_turn(
        llm=state.life_llm,
        model=state.settings.resolved_life_model,
        current_user_text=current_user_text,
        recent=recent,
        relationship_context=relationship_block,
        memory_context=render_life_memory_context(retrieved, state.settings),
        trigger="responses",
        trace_id=trace_id,
        metrics=state.metrics,
    )
    decision = decide_response_policy(
        current_user_text=current_user_text,
        has_images=bool(last_user_images),
        retrieved=retrieved,
        life=life,
        relationship_context=relationship_block,
        recent=recent,
    )
    state.metrics.record(
        "response.policy",
        0.0,
        detail=f"trace={trace_id},{decision.metric_detail()}",
    )
    if state.settings.response_policy_model_enabled:
        decision = await refine_decision_with_llm(
            base=decision,
            llm=state.response_policy_llm,
            model=state.settings.resolved_response_policy_model,
            settings=state.settings,
            current_user_text=current_user_text,
            recent=recent,
            life=life,
            relationship_context=relationship_block,
            has_images=bool(last_user_images),
            trace_id=trace_id,
            metrics=state.metrics,
        )
        state.metrics.record(
            "response.policy.refined",
            0.0,
            detail=f"trace={trace_id},{decision.metric_detail()}",
        )
    policy_hint = build_policy_hint(decision)
    response_id = _new_response_id()

    # silence 短路
    if not decision.should_reply:
        sentinel = state.settings.silence_response_sentinel
        if conversation_id and (current_user_text or image_shas):
            await state.writeback.enqueue_turn(
                WritebackTurn(
                    conversation_id=conversation_id,
                    user_text=current_user_text,
                    assistant_text="",
                    user_image_shas=image_shas,
                )
            )
        state.metrics.record(
            "responses.silenced",
            0.0,
            detail=f"trace={trace_id},{decision.metric_detail()}",
        )
        if req.stream:
            return StreamingResponse(
                _stream_silenced(
                    response_id=response_id,
                    model_name=model_name,
                    trace_id=trace_id,
                    sentinel=sentinel,
                    policy=policy_hint,
                    previous_response_id=req.previous_response_id,
                ),
                media_type="text/event-stream",
            )
        state.responses_store.put(
            ResponseRecord(
                response_id=response_id,
                conversation_id=conversation_id,
                user_text=current_user_text,
                assistant_text="",
                created_at=int(time.time()),
                model=model_name,
            )
        )
        return _build_completed_response(
            response_id=response_id,
            model_name=model_name,
            text=sentinel,
            policy=policy_hint,
            trace_id=trace_id,
            previous_response_id=req.previous_response_id,
        )

    persona_card = build_persona_card_with_companion_context(
        settings=state.settings,
        life=life,
        relationship_context=relationship_block,
        style_query=current_user_text,
        response_policy_context=decision.render_prompt_block(
            silence_sentinel=state.settings.silence_response_sentinel,
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
    url_context = ""
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
        last_user_images
        and state.settings.chat_model_supports_vision
        and messages
        and messages[-1]["role"] == "user"
    ):
        text_for_user = messages[-1]["content"]
        if isinstance(text_for_user, str):
            mm_content: list[dict[str, Any]] = [{"type": "text", "text": text_for_user}]
            for url in last_user_images:
                mm_content.append({"type": "image_url", "image_url": {"url": url}})
            messages[-1] = {"role": "user", "content": mm_content}  # type: ignore[dict-item]

    params = GenerationParams(
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_output_tokens,
    )

    initial_delay_seconds = max(life.reply_delay_seconds, decision.reply_delay_seconds)

    if req.stream and state.settings.response_streaming_enabled:
        return StreamingResponse(
            _stream_response(
                state=state,
                response_id=response_id,
                messages=messages,
                params=params,
                model_name=model_name,
                conversation_id=conversation_id,
                user_text=current_user_text,
                image_shas=image_shas,
                initial_delay_seconds=initial_delay_seconds,
                trace_id=trace_id,
                policy=policy_hint,
                previous_response_id=req.previous_response_id,
                decision=decision,
            ),
            media_type="text/event-stream",
        )

    if initial_delay_seconds > 0:
        import asyncio
        await asyncio.sleep(
            min(initial_delay_seconds, state.settings.life_max_reply_delay_seconds)
        )
    _llm_start = time.perf_counter()
    try:
        raw_assistant_text = await state.llm.complete_chat(
            messages,
            params,
            model=model_name,
            trace_id=trace_id,
            stage="responses.complete",
            metrics=state.metrics,
        )
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
        # AI 自主沉默：主模型严格输出 sentinel → 转沉默路径，跳过 sticker 兜底。
        ai_silenced = is_ai_silence_signal(
            assistant_text,
            sentinel=state.settings.silence_response_sentinel,
            decision=decision,
        )
        if ai_silenced:
            state.metrics.record(
                "responses.silenced.ai",
                0.0,
                detail=f"trace={trace_id},{decision.metric_detail()}",
            )
        # 同 chat.py：模型只发了不存在 sticker → 先 retry，失败再退到短句
        if (
            not ai_silenced
            and assistant_text in {"嗯", ""}
            and looks_like_sticker_only_intent(stripped)
        ):
            retried = False
            if state.settings.sticker_reject_retry:
                hint = build_sticker_retry_hint(stripped, valid_names)
                retry_messages = list(messages) + [
                    {"role": "system", "content": hint},
                ]
                try:
                    retry_raw = await state.llm.complete_chat(
                        retry_messages,
                        params,
                        model=model_name,
                        trace_id=trace_id,
                        stage="responses.complete.sticker_retry",
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
                            "responses.sticker.retry_ok",
                            0.0,
                            detail=f"trace={trace_id},mode={decision.reply_mode}",
                        )
                except Exception:
                    logger.warning("sticker retry 失败，回退到短句兜底", exc_info=True)
            if not retried:
                assistant_text = (
                    fallback_for_rejected_sticker(decision.reply_mode) or assistant_text
                )
                state.metrics.record(
                    "responses.sticker.rejected",
                    0.0,
                    detail=f"trace={trace_id},mode={decision.reply_mode}",
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

    if conversation_id:
        await state.writeback.enqueue_turn(
            WritebackTurn(
                conversation_id=conversation_id,
                user_text=current_user_text,
                # 沉默时写空 assistant_text，与规则层 silence 短路一致。
                assistant_text="" if ai_silenced else assistant_text,
                user_image_shas=image_shas,
            )
        )
        await state.relationship_memory.remember_turn(
            conversation_id=conversation_id,
            user_text=current_user_text,
            assistant_text="" if ai_silenced else assistant_text,
        )

    state.responses_store.put(
        ResponseRecord(
            response_id=response_id,
            conversation_id=conversation_id,
            user_text=current_user_text,
            assistant_text="" if ai_silenced else assistant_text,
            created_at=int(time.time()),
            model=model_name,
        )
    )

    # 假流式：stream=true 但后端不启用真流式 → 把完整 assistant_text 包装成
    # 完整事件序列（一个 output_text.delta 含全部内容）发出。
    # 沉默时切到 _stream_silenced，与规则层 silence 短路保持一致。
    if req.stream:
        if ai_silenced:
            return StreamingResponse(
                _stream_silenced(
                    response_id=response_id,
                    model_name=model_name,
                    trace_id=trace_id,
                    sentinel=state.settings.silence_response_sentinel,
                    policy=policy_hint,
                    previous_response_id=req.previous_response_id,
                ),
                media_type="text/event-stream",
            )
        return StreamingResponse(
            _pseudo_stream_events(
                response_id=response_id,
                model_name=model_name,
                trace_id=trace_id,
                assistant_text=assistant_text,
                policy=policy_hint,
                previous_response_id=req.previous_response_id,
            ),
            media_type="text/event-stream",
        )

    return _build_completed_response(
        response_id=response_id,
        model_name=model_name,
        text=assistant_text,
        policy=policy_hint,
        trace_id=trace_id,
        previous_response_id=req.previous_response_id,
    )


async def _pseudo_stream_events(
    *,
    response_id: str,
    model_name: str,
    trace_id: str,
    assistant_text: str,
    policy: PolicyHint,
    previous_response_id: str | None,
) -> AsyncIterator[bytes]:
    """假流式：把完整 assistant_text 按 Responses 事件协议一次性包装发出。"""
    created_at = int(time.time())
    message_id = _new_message_id()
    base_response = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "model": model_name,
        "status": "in_progress",
        "output": [],
        "trace_id": trace_id,
        "policy": policy.model_dump(),
        "previous_response_id": previous_response_id,
    }
    yield _format_event("response.created", {"response": base_response})
    yield _format_event("response.in_progress", {"response": base_response})
    item = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "status": "in_progress",
        "content": [],
    }
    yield _format_event(
        "response.output_item.added",
        {"output_index": 0, "item": item},
    )
    content_part = {"type": "output_text", "text": "", "annotations": []}
    yield _format_event(
        "response.content_part.added",
        {"item_id": message_id, "output_index": 0, "content_index": 0, "part": content_part},
    )
    if assistant_text:
        yield _format_event(
            "response.output_text.delta",
            {"item_id": message_id, "output_index": 0, "content_index": 0, "delta": assistant_text},
        )
    yield _format_event(
        "response.output_text.done",
        {"item_id": message_id, "output_index": 0, "content_index": 0, "text": assistant_text},
    )
    final_part = {**content_part, "text": assistant_text}
    yield _format_event(
        "response.content_part.done",
        {"item_id": message_id, "output_index": 0, "content_index": 0, "part": final_part},
    )
    final_item = {**item, "status": "completed", "content": [final_part]}
    yield _format_event(
        "response.output_item.done",
        {"output_index": 0, "item": final_item},
    )
    completed = {
        **base_response,
        "status": "completed",
        "output": [final_item],
        "output_text": assistant_text,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    yield _format_event("response.completed", {"response": completed})
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _normalize_input(req: ResponsesRequest) -> tuple[list[PromptMessage], str, list[str]]:
    """把 Responses 协议的 input + instructions 平整为内部 PromptMessage 列表。"""
    messages: list[ResponsesInputMessage] = []
    if req.instructions:
        messages.append(
            ResponsesInputMessage(role="system", content=req.instructions),
        )
    if isinstance(req.input, str):
        messages.append(ResponsesInputMessage(role="user", content=req.input))
    else:
        messages.extend(req.input)

    user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
    if not user_indices:
        raise HTTPException(status_code=400, detail="input 中至少要有一条 role=user")
    last_user_idx = user_indices[-1]
    last_user = messages[last_user_idx]
    last_user_text, last_user_images = _extract_text_and_images(last_user)

    history: list[PromptMessage] = []
    for i, m in enumerate(messages):
        if i == last_user_idx:
            continue
        if m.role not in {"system", "user", "assistant", "developer"}:
            continue
        role = "system" if m.role == "developer" else m.role
        text, _ = _extract_text_and_images(m)
        if not text and role != "system":
            continue
        history.append(PromptMessage(role=role, content=text))
    return history, last_user_text, last_user_images


def _extract_text_and_images(msg: ResponsesInputMessage) -> tuple[str, list[str]]:
    if isinstance(msg.content, str):
        return msg.content, []
    text_parts: list[str] = []
    images: list[str] = []
    for part in msg.content:
        if isinstance(part, ResponsesInputTextContent):
            text_parts.append(part.text)
        elif isinstance(part, ResponsesInputImageContent):
            images.append(part.image_url)
    return "".join(text_parts), images


def _new_response_id() -> str:
    return f"resp_{uuid.uuid4().hex[:24]}"


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _build_completed_response(
    *,
    response_id: str,
    model_name: str,
    text: str,
    policy: PolicyHint,
    trace_id: str,
    previous_response_id: str | None,
) -> ResponsesResponse:
    return ResponsesResponse(
        id=response_id,
        created_at=int(time.time()),
        model=model_name,
        status="completed",
        output=[
            ResponsesOutputMessage(
                id=_new_message_id(),
                content=[ResponsesOutputTextContent(text=text)],
            )
        ],
        output_text=text,
        usage=ResponsesUsage(),
        trace_id=trace_id,
        policy=policy,
        previous_response_id=previous_response_id,
    )


# ---------------------------------------------------------------------------
# 流式：OpenAI Responses 事件协议
# ---------------------------------------------------------------------------


def _format_event(event_type: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {body}\n\n".encode()


async def _stream_silenced(
    *,
    response_id: str,
    model_name: str,
    trace_id: str,
    sentinel: str,
    policy: PolicyHint,
    previous_response_id: str | None,
) -> AsyncIterator[bytes]:
    """决策层选择不回复时的 Responses 事件序列。"""
    created_at = int(time.time())
    message_id = _new_message_id()
    base_response = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "model": model_name,
        "status": "in_progress",
        "output": [],
        "trace_id": trace_id,
        "policy": policy.model_dump(),
        "previous_response_id": previous_response_id,
    }
    yield _format_event("response.created", {"response": base_response})
    yield _format_event("response.in_progress", {"response": base_response})

    item = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "status": "in_progress",
        "content": [],
    }
    yield _format_event(
        "response.output_item.added",
        {"output_index": 0, "item": item},
    )
    content_part = {"type": "output_text", "text": "", "annotations": []}
    yield _format_event(
        "response.content_part.added",
        {
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "part": content_part,
        },
    )
    if sentinel:
        yield _format_event(
            "response.output_text.delta",
            {
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "delta": sentinel,
            },
        )
    yield _format_event(
        "response.output_text.done",
        {
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "text": sentinel,
        },
    )
    final_part = {**content_part, "text": sentinel}
    yield _format_event(
        "response.content_part.done",
        {
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "part": final_part,
        },
    )
    final_item = {
        **item,
        "status": "completed",
        "content": [final_part],
    }
    yield _format_event(
        "response.output_item.done",
        {"output_index": 0, "item": final_item},
    )
    completed_response = {
        **base_response,
        "status": "completed",
        "output": [final_item],
        "output_text": sentinel,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    yield _format_event("response.completed", {"response": completed_response})
    yield b"data: [DONE]\n\n"


async def _stream_response(
    *,
    state: AppState,
    response_id: str,
    messages: list[dict[str, Any]],
    params: GenerationParams,
    model_name: str,
    conversation_id: str | None,
    user_text: str,
    image_shas: list[str],
    initial_delay_seconds: int,
    trace_id: str,
    policy: PolicyHint,
    previous_response_id: str | None,
    decision: ResponseDecision,
) -> AsyncIterator[bytes]:
    """正常流式：把主模型 delta 翻译成 Responses 事件序列。"""
    import asyncio

    created_at = int(time.time())
    message_id = _new_message_id()
    buffer: list[str] = []
    output_filter = AssistantOutputFilter(
        valid_sticker_names=available_sticker_names(state.settings),
    )

    base_response = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "model": model_name,
        "status": "in_progress",
        "output": [],
        "trace_id": trace_id,
        "policy": policy.model_dump(),
        "previous_response_id": previous_response_id,
    }
    yield _format_event("response.created", {"response": base_response})
    yield _format_event("response.in_progress", {"response": base_response})

    item = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "status": "in_progress",
        "content": [],
    }
    yield _format_event(
        "response.output_item.added",
        {"output_index": 0, "item": item},
    )
    content_part = {"type": "output_text", "text": "", "annotations": []}
    yield _format_event(
        "response.content_part.added",
        {
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "part": content_part,
        },
    )

    if initial_delay_seconds > 0:
        await asyncio.sleep(
            min(initial_delay_seconds, state.settings.life_max_reply_delay_seconds)
        )

    _stream_start = time.perf_counter()
    try:
        async for piece in state.llm.stream_chat(
            messages,
            params,
            model=model_name,
            trace_id=trace_id,
            stage="responses.stream",
            metrics=state.metrics,
        ):
            filtered = output_filter.feed(piece)
            if not filtered:
                continue
            buffer.append(filtered)
            yield _format_event(
                "response.output_text.delta",
                {
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": filtered,
                },
            )
        tail = output_filter.flush()
        if tail:
            buffer.append(tail)
            yield _format_event(
                "response.output_text.delta",
                {
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": tail,
                },
            )
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
        yield _format_event(
            "response.failed",
            {
                "response": {
                    **base_response,
                    "status": "failed",
                    "error": {
                        "code": e.code,
                        "message": e.message,
                    },
                }
            },
        )
        yield b"data: [DONE]\n\n"
        return

    assistant_text = "".join(buffer)
    yield _format_event(
        "response.output_text.done",
        {
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "text": assistant_text,
        },
    )
    final_part = {**content_part, "text": assistant_text}
    yield _format_event(
        "response.content_part.done",
        {
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "part": final_part,
        },
    )
    final_item = {
        **item,
        "status": "completed",
        "content": [final_part],
    }
    yield _format_event(
        "response.output_item.done",
        {"output_index": 0, "item": final_item},
    )
    completed_response = {
        **base_response,
        "status": "completed",
        "output": [final_item],
        "output_text": assistant_text,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    yield _format_event("response.completed", {"response": completed_response})
    yield b"data: [DONE]\n\n"

    # 流结束后用累积 raw 跑 life-update apply（不影响已发出事件）。
    raw_full = output_filter.raw_text()
    extract_and_apply_life_marker(
        raw_full,
        state.life,
        enabled=state.settings.life_marker_update_enabled,
    )

    # AI 自主沉默：累积完整 buffer == sentinel → 写历史时置空，避免污染检索；
    # 流式事件已经发完，前端收到的内容仍是 sentinel，由 sentinel 自身表达沉默语义。
    ai_silenced = is_ai_silence_signal(
        assistant_text,
        sentinel=state.settings.silence_response_sentinel,
        decision=decision,
    )
    if ai_silenced:
        state.metrics.record(
            "responses.silenced.ai",
            0.0,
            detail=f"trace={trace_id},{decision.metric_detail()},stream",
        )
    persisted_text = "" if ai_silenced else assistant_text

    if conversation_id and (persisted_text or ai_silenced):
        await state.writeback.enqueue_turn(
            WritebackTurn(
                conversation_id=conversation_id,
                user_text=user_text,
                assistant_text=persisted_text,
                user_image_shas=image_shas,
            )
        )
        await state.relationship_memory.remember_turn(
            conversation_id=conversation_id,
            user_text=user_text,
            assistant_text=persisted_text,
        )

    state.responses_store.put(
        ResponseRecord(
            response_id=response_id,
            conversation_id=conversation_id,
            user_text=user_text,
            assistant_text=persisted_text,
            created_at=created_at,
            model=model_name,
        )
    )
