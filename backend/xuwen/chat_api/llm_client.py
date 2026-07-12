"""OpenAI 兼容的对话模型客户端。

特性：
- 同时支持流式（SSE delta）与非流式
- 指数退避重试 5xx / 429 / 网络错误；4xx 立即失败
- 错误信息不回显响应正文（避免泄露 prompt 与 key 详情）
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from xuwen.config import ChatApiProtocol, Settings
from xuwen.core.errors import LLMError
from xuwen.core.metrics import MetricsRecorder
from xuwen.ingestion.embedder import _resolve_endpoint

_SCHEDULE_HINT_RE = re.compile(
    r"<schedule-hint>\s*(.*?)\s*</schedule-hint>",
    re.DOTALL | re.IGNORECASE,
)

class _RetryableLLMError(LLMError):
    """仅供 tenacity 内部触发重试。"""


@dataclass(slots=True, frozen=True)
class GenerationParams:
    """传给上游 LLM 的可选参数。"""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None


class LLMClient:
    """OpenAI Chat Completions / Responses 兼容客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
        api_url: str | None = None,
        api_key: str | None = None,
        api_protocol: ChatApiProtocol = "chat_completions",
        max_retries: int = 3,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )
        self._api_protocol = api_protocol
        self._url = _resolve_llm_endpoint(
            api_url or str(settings.openai_base_url),
            api_protocol,
        )
        resolved_key = api_key or settings.openai_api_key.get_secret_value()
        # 重试次数：主聊天 LLM 默认 3（没有兜底，必须撑住短暂网络抖动）；
        # rerank / query_rewrite / refine_decision / life 这些 fail-open 路径
        # 应该传 max_retries=1，单次失败立刻退快路径，不要把延迟放大几倍。
        self._max_retries = max(1, max_retries)
        self._headers = {
            "Authorization": f"Bearer {resolved_key}",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # 公开
    # ------------------------------------------------------------------

    async def complete_chat(
        self,
        messages: list[dict[str, Any]],
        params: GenerationParams | None = None,
        *,
        model: str | None = None,
        trace_id: str = "",
        stage: str = "llm.complete",
        metrics: MetricsRecorder | None = None,
    ) -> str:
        """非流式：返回完整 assistant 文本。"""
        payload = self._build_payload(messages, params, model=model, stream=False)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=10.0),
            retry=retry_if_exception_type((httpx.HTTPError, _RetryableLLMError)),
            reraise=True,
        ):
            with attempt:
                return await self._call_once(
                    payload,
                    trace_id=trace_id,
                    stage=stage,
                    metrics=metrics,
                    attempt_number=_attempt_number(attempt.retry_state),
                )
        raise LLMError("意外的重试退出")

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        params: GenerationParams | None = None,
        *,
        model: str | None = None,
        trace_id: str = "",
        stage: str = "llm.stream",
        metrics: MetricsRecorder | None = None,
    ) -> AsyncIterator[str]:
        """流式：逐 token 产出 assistant 内容片段（按 OpenAI delta 顺序）。

        网络错误 / 5xx / 429 在**首字节到来前**会重试；
        一旦开始 yield 就不再重试，避免重复输出。
        """
        payload = self._build_payload(messages, params, model=model, stream=True)
        # 重试只覆盖建立流的过程
        gen: AsyncGenerator[str, None] | None = None
        first: str | None = None
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(multiplier=1.0, min=1.0, max=10.0),
                retry=retry_if_exception_type((httpx.HTTPError, _RetryableLLMError)),
                reraise=True,
            ):
                with attempt:
                    gen = self._open_stream(
                        payload,
                        trace_id=trace_id,
                        stage=stage,
                        metrics=metrics,
                        attempt_number=_attempt_number(attempt.retry_state),
                    )
                    # 先取首个 chunk 以触发可能的早期错误
                    try:
                        first = await gen.__anext__()
                    except StopAsyncIteration:
                        await gen.aclose()
                        return
                    break
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 流式请求失败：{type(e).__name__}") from e

        if gen is None or first is None:  # pragma: no cover - reraise=True 保证不到这里
            raise LLMError("无法建立流式连接")

        async def _emit() -> AsyncIterator[str]:
            try:
                yield first
                async for piece in gen:
                    yield piece
            finally:
                await gen.aclose()

        async for piece in _emit():
            yield piece

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        params: GenerationParams | None,
        *,
        model: str | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.settings.chat_model,
            "stream": stream,
        }
        if self._api_protocol == "responses":
            payload["input"] = _responses_input(messages)
            # Responses 默认会保存响应；Afterglow 的隐私边界要求上游不持久化。
            payload["store"] = False
        else:
            payload["messages"] = messages
        if params is None:
            params = GenerationParams()
        if params.temperature is not None:
            payload["temperature"] = params.temperature
        if params.top_p is not None:
            payload["top_p"] = params.top_p
        if params.max_tokens is not None:
            token_key = (
                "max_output_tokens"
                if self._api_protocol == "responses"
                else "max_tokens"
            )
            payload[token_key] = params.max_tokens
        if params.presence_penalty is not None and self._api_protocol == "chat_completions":
            payload["presence_penalty"] = params.presence_penalty
        if params.frequency_penalty is not None and self._api_protocol == "chat_completions":
            payload["frequency_penalty"] = params.frequency_penalty
        return payload

    async def _call_once(
        self,
        payload: dict[str, Any],
        *,
        trace_id: str,
        stage: str,
        metrics: MetricsRecorder | None,
        attempt_number: int,
    ) -> str:
        start = time.perf_counter()
        include_full_payloads = self.settings.debug_model_full_payloads_enabled
        request_summary = _request_summary(
            payload,
            include_full=include_full_payloads,
        )
        try:
            resp = await self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError as e:
            _record_model_call(
                metrics,
                trace_id=trace_id,
                stage=stage,
                attempt=attempt_number,
                payload=payload,
                url=self._url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                request=request_summary,
                error=type(e).__name__,
            )
            raise _RetryableLLMError(
                f"LLM 网络请求失败：{type(e).__name__}",
            ) from e

        request_id = resp.headers.get("x-request-id") or resp.headers.get("request-id")
        if resp.status_code in (429,) or 500 <= resp.status_code < 600:
            _record_model_call(
                metrics,
                trace_id=trace_id,
                stage=stage,
                attempt=attempt_number,
                payload=payload,
                url=self._url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                status_code=resp.status_code,
                upstream_request_id=request_id or "",
                request=request_summary,
                response=_response_summary(resp.text, include_full=include_full_payloads),
                error=f"HTTP {resp.status_code}",
            )
            raise _RetryableLLMError(
                f"LLM 暂时不可用（HTTP {resp.status_code}）",
                detail={"status": resp.status_code, "request_id": request_id},
            )
        if resp.status_code >= 400:
            _record_model_call(
                metrics,
                trace_id=trace_id,
                stage=stage,
                attempt=attempt_number,
                payload=payload,
                url=self._url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                status_code=resp.status_code,
                upstream_request_id=request_id or "",
                request=request_summary,
                response=_response_summary(resp.text, include_full=include_full_payloads),
                error=f"HTTP {resp.status_code}",
            )
            raise LLMError(
                f"LLM 客户端错误（HTTP {resp.status_code}），请检查 OPENAI_API_KEY、CHAT_MODEL 与配额",
                detail={"status": resp.status_code, "request_id": request_id},
            )

        try:
            data = resp.json()
        except ValueError as e:
            _record_model_call(
                metrics,
                trace_id=trace_id,
                stage=stage,
                attempt=attempt_number,
                payload=payload,
                url=self._url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                status_code=resp.status_code,
                upstream_request_id=request_id or "",
                request=request_summary,
                response=_response_summary(resp.text, include_full=include_full_payloads),
                error="invalid_json",
            )
            raise LLMError(
                "LLM 返回非 JSON 响应（请检查 OPENAI_BASE_URL 是否正确）",
                detail={"status": resp.status_code, "request_id": request_id},
            ) from e

        try:
            text = _extract_content_text(data, self._api_protocol)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            _record_model_call(
                metrics,
                trace_id=trace_id,
                stage=stage,
                attempt=attempt_number,
                payload=payload,
                url=self._url,
                latency_ms=(time.perf_counter() - start) * 1000,
                status="error",
                status_code=resp.status_code,
                upstream_request_id=request_id or "",
                request=request_summary,
                response=_json_response_summary(data),
                error="missing_content",
            )
            raise LLMError(
                (
                    "LLM 响应缺少 output_text"
                    if self._api_protocol == "responses"
                    else "LLM 响应缺少 choices[0].message.content"
                ),
                detail={"request_id": request_id},
            ) from e
        _record_model_call(
            metrics,
            trace_id=trace_id,
            stage=stage,
            attempt=attempt_number,
            payload=payload,
            url=self._url,
            latency_ms=(time.perf_counter() - start) * 1000,
            status="ok",
            status_code=resp.status_code,
            upstream_request_id=request_id or "",
            request=request_summary,
            response=_content_response_summary(text, data, include_full=include_full_payloads),
        )
        return text

    async def _open_stream(
        self,
        payload: dict[str, Any],
        *,
        trace_id: str,
        stage: str,
        metrics: MetricsRecorder | None,
        attempt_number: int,
    ) -> AsyncGenerator[str, None]:
        """打开 SSE 流并逐条 yield delta.content。"""
        start = time.perf_counter()
        include_full_payloads = self.settings.debug_model_full_payloads_enabled
        request_summary = _request_summary(
            payload,
            include_full=include_full_payloads,
        )
        pieces = 0
        chars = 0
        preview_parts: list[str] = []
        full_parts: list[str] = []
        stream_error = ""
        stream_usage: dict[str, Any] | None = None
        async with self._client.stream(
            "POST", self._url, headers=self._headers, json=payload
        ) as resp:
            request_id = resp.headers.get("x-request-id") or resp.headers.get("request-id")
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                body = await resp.aread()
                _record_model_call(
                    metrics,
                    trace_id=trace_id,
                    stage=stage,
                    attempt=attempt_number,
                    payload=payload,
                    url=self._url,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    status="error",
                    status_code=resp.status_code,
                    upstream_request_id=request_id or "",
                    request=request_summary,
                    response=_response_summary(
                        body.decode("utf-8", errors="replace"),
                        include_full=include_full_payloads,
                    ),
                    error=f"HTTP {resp.status_code}",
                )
                raise _RetryableLLMError(
                    f"LLM 暂时不可用（HTTP {resp.status_code}）",
                    detail={"status": resp.status_code, "request_id": request_id},
                )
            if resp.status_code >= 400:
                body = await resp.aread()
                _record_model_call(
                    metrics,
                    trace_id=trace_id,
                    stage=stage,
                    attempt=attempt_number,
                    payload=payload,
                    url=self._url,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    status="error",
                    status_code=resp.status_code,
                    upstream_request_id=request_id or "",
                    request=request_summary,
                    response=_response_summary(
                        body.decode("utf-8", errors="replace"),
                        include_full=include_full_payloads,
                    ),
                    error=f"HTTP {resp.status_code}",
                )
                raise LLMError(
                    f"LLM 客户端错误（HTTP {resp.status_code}）",
                    detail={"status": resp.status_code, "request_id": request_id},
                )

            try:
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    # OpenAI SSE 格式：每行 `data: {json}`，结束符 `data: [DONE]`
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload_str = line[5:].strip()
                    if payload_str == "[DONE]":
                        return
                    try:
                        chunk = json.loads(payload_str)
                    except json.JSONDecodeError:
                        # 单条 chunk 解析失败时跳过，让其它 chunk 继续；
                        # 上层若拿到空字符串说明整段流都失败
                        continue
                    if self._api_protocol == "responses":
                        event_type = str(chunk.get("type") or "")
                        if event_type == "response.output_text.delta":
                            content = chunk.get("delta")
                        elif event_type in {"response.completed", "response.incomplete"}:
                            response = chunk.get("response")
                            if isinstance(response, dict) and isinstance(
                                response.get("usage"), dict
                            ):
                                stream_usage = response["usage"]
                            continue
                        elif event_type in {"error", "response.failed"}:
                            stream_error = event_type
                            raise LLMError(
                                "LLM Responses 流返回错误事件",
                                detail={"request_id": request_id},
                            )
                        else:
                            continue
                    else:
                        try:
                            delta = chunk["choices"][0]["delta"]
                        except (KeyError, IndexError, TypeError):
                            continue
                        content = delta.get("content")
                    if isinstance(content, str) and content:
                        pieces += 1
                        chars += len(content)
                        if sum(len(p) for p in preview_parts) < 240:
                            preview_parts.append(content)
                        if include_full_payloads:
                            full_parts.append(content)
                        yield content
                    # 兼容 reasoning 字段（一些上游会在 thinking 阶段送 delta.reasoning）；本期暂不渲染
                    await asyncio.sleep(0)  # 让出事件循环
            except httpx.HTTPError as e:
                stream_error = type(e).__name__
                if pieces <= 0:
                    raise _RetryableLLMError(
                        f"LLM 流式响应建立后中断：{stream_error}",
                        detail={"request_id": request_id},
                    ) from e
                raise LLMError(
                    f"LLM 流式响应中断：{stream_error}",
                    detail={"request_id": request_id},
                ) from e
            finally:
                _record_model_call(
                    metrics,
                    trace_id=trace_id,
                    stage=stage,
                    attempt=attempt_number,
                    payload=payload,
                    url=self._url,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    status="error" if stream_error else "ok",
                    status_code=resp.status_code,
                    upstream_request_id=request_id or "",
                    request=request_summary,
                    response=_stream_response_summary(
                        stream_chunks=pieces,
                        content_chars=chars,
                        content_preview=_short_text("".join(preview_parts), 240),
                        content_text="".join(full_parts),
                        include_full=include_full_payloads,
                        usage=stream_usage,
                    ),
                    error=stream_error,
                )


def _resolve_llm_endpoint(base_url: str, protocol: ChatApiProtocol) -> str:
    """允许基础地址或任一 LLM endpoint，并切换到所选协议。"""
    url = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    target = "/responses" if protocol == "responses" else "/chat/completions"
    return _resolve_endpoint(url, target)


def _responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        content = item.get("content")
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for raw_part in content:
                if not isinstance(raw_part, dict):
                    continue
                part_type = str(raw_part.get("type") or "")
                if part_type == "text":
                    parts.append(
                        {"type": "input_text", "text": str(raw_part.get("text") or "")}
                    )
                elif part_type == "image_url":
                    image = raw_part.get("image_url")
                    if isinstance(image, dict):
                        image_url = str(image.get("url") or "")
                        detail = image.get("detail")
                    else:
                        image_url = str(image or "")
                        detail = None
                    if image_url:
                        image_part: dict[str, Any] = {
                            "type": "input_image",
                            "image_url": image_url,
                        }
                        if detail is not None:
                            image_part["detail"] = detail
                        parts.append(image_part)
                elif part_type in {"input_text", "input_image", "input_file"}:
                    parts.append(dict(raw_part))
            item["content"] = parts
        converted.append(item)
    return converted


def _extract_content_text(
    data: dict[str, Any],
    protocol: ChatApiProtocol,
) -> str:
    if protocol == "chat_completions":
        content = data["choices"][0]["message"]["content"]
        return str(content or "")

    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = data.get("output")
    if not isinstance(output, list):
        raise ValueError("Responses output 缺失")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            texts.append(str(part.get("text") or ""))
    return "".join(texts)


def _attempt_number(state: RetryCallState) -> int:
    return int(state.attempt_number or 1)


def _record_model_call(
    metrics: MetricsRecorder | None,
    *,
    trace_id: str,
    stage: str,
    attempt: int,
    payload: dict[str, Any],
    url: str,
    latency_ms: float,
    status: str,
    status_code: int | None = None,
    upstream_request_id: str = "",
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if metrics is None:
        return
    metrics.record_model_call(
        trace_id=trace_id,
        stage=stage,
        attempt=attempt,
        model=str(payload.get("model") or ""),
        url=url,
        stream=bool(payload.get("stream")),
        latency_ms=latency_ms,
        status=status,
        status_code=status_code,
        upstream_request_id=upstream_request_id,
        request=request,
        response=response,
        error=error,
    )


def _request_summary(payload: dict[str, Any], *, include_full: bool) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = payload.get("input")
    message_summaries: list[dict[str, Any]] = []
    role_counts: dict[str, int] = {}
    all_texts: list[str] = []
    total_chars = 0
    image_count = 0
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            role_counts[role] = role_counts.get(role, 0) + 1
            text, images = _message_text_and_images(msg.get("content"))
            all_texts.append(text)
            total_chars += len(text)
            image_count += images
            item = {
                "role": role,
                "chars": len(text),
                "image_count": images,
                "preview": _short_text(text, 300),
            }
            if include_full:
                item["text"] = text
            message_summaries.append(item)
    full_text = "\n".join(all_texts)
    return {
        "model": payload.get("model"),
        "stream": payload.get("stream"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "max_tokens": payload.get("max_tokens") or payload.get("max_output_tokens"),
        "api_protocol": "responses" if "input" in payload else "chat_completions",
        "store": payload.get("store"),
        "presence_penalty": payload.get("presence_penalty"),
        "frequency_penalty": payload.get("frequency_penalty"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "role_counts": role_counts,
        "total_text_chars": total_chars,
        "image_count": image_count,
        "schedule": _schedule_request_debug(full_text),
        "messages": message_summaries,
    }


def _message_text_and_images(content: object) -> tuple[str, int]:
    if isinstance(content, str):
        return content, 0
    if not isinstance(content, list):
        return "", 0
    texts: list[str] = []
    images = 0
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"text", "input_text", "output_text"}:
            texts.append(str(part.get("text") or ""))
        elif part.get("type") in {"image_url", "input_image"}:
            images += 1
    return "\n".join(texts), images


def _response_summary(text: str, *, include_full: bool) -> dict[str, Any]:
    summary = {
        "raw_chars": len(text),
        "raw_preview": _short_text(text, 240),
        "schedule": _schedule_response_debug(text),
    }
    if include_full:
        summary["raw_text"] = text
    return summary


def _json_response_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(str(key) for key in data.keys()),
        "choice_count": len(data.get("choices", [])) if isinstance(data.get("choices"), list) else 0,
        "output_count": len(data.get("output", [])) if isinstance(data.get("output"), list) else 0,
    }


def _content_response_summary(
    text: str,
    data: dict[str, Any],
    *,
    include_full: bool,
) -> dict[str, Any]:
    summary = _json_response_summary(data)
    summary.update(
        {
            "content_chars": len(text),
            "content_preview": _short_text(text, 240),
            "schedule": _schedule_response_debug(text),
        }
    )
    if include_full:
        summary["content_text"] = text
    usage = _usage_summary(data.get("usage"))
    if usage:
        summary["usage"] = usage
    return summary


def _stream_response_summary(
    *,
    stream_chunks: int,
    content_chars: int,
    content_preview: str,
    content_text: str,
    include_full: bool,
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "stream_chunks": stream_chunks,
        "content_chars": content_chars,
        "content_preview": content_preview,
    }
    if include_full:
        summary["content_text"] = content_text
    usage_summary = _usage_summary(usage)
    if usage_summary:
        summary["usage"] = usage_summary
    return summary


def _usage_summary(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    summary = {
        key: raw.get(key)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        )
        if key in raw
    }
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
    if isinstance(details, dict) and "cached_tokens" in details:
        summary["cached_tokens"] = details.get("cached_tokens")
    return summary


def _schedule_request_debug(text: str) -> dict[str, Any]:
    """调试定时任务链路：主模型请求是否注入了内部协议指令。"""
    lower = text.lower()
    return {
        "prompt_has_schedule_instruction": "【定时任务" in text
        or "<schedule-hint>" in lower,
        "schedule_hint_tag_mentions": lower.count("<schedule-hint>"),
    }


def _schedule_response_debug(text: str) -> dict[str, Any]:
    """调试定时任务链路：主模型 raw 回复是否吐出了 hint。"""
    hints = _extract_schedule_hints_for_debug(text)
    lower = text.lower()
    return {
        "hint_count": len(hints),
        "hint_previews": [_short_text(h, 120) for h in hints],
        "has_open_tag": "<schedule-hint>" in lower,
        "has_close_tag": "</schedule-hint>" in lower,
    }


def _extract_schedule_hints_for_debug(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in _SCHEDULE_HINT_RE.findall(text):
        hint = " ".join(str(raw).split())
        if not hint or hint in seen:
            continue
        seen.add(hint)
        result.append(hint)
    return result


def _short_text(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
