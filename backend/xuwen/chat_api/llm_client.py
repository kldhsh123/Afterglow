"""OpenAI 兼容的对话模型客户端。

特性：
- 同时支持流式（SSE delta）与非流式
- 指数退避重试 5xx / 429 / 网络错误；4xx 立即失败
- 错误信息不回显响应正文（避免泄露 prompt 与 key 详情）
"""

from __future__ import annotations

import asyncio
import json
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

from xuwen.config import Settings
from xuwen.core.errors import LLMError
from xuwen.core.metrics import MetricsRecorder
from xuwen.ingestion.embedder import _resolve_endpoint


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
    """OpenAI Chat Completions 兼容客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
        api_url: str | None = None,
        api_key: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )
        self._url = _resolve_endpoint(
            api_url or str(settings.openai_base_url),
            "/chat/completions",
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
        messages: list[dict[str, str]],
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
        messages: list[dict[str, str]],
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
                first = await gen.__anext__()
                break
        else:  # pragma: no cover - reraise=True 保证不到这里
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
        messages: list[dict[str, str]],
        params: GenerationParams | None,
        *,
        model: str | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.settings.chat_model,
            "messages": messages,
            "stream": stream,
        }
        if params is None:
            params = GenerationParams()
        if params.temperature is not None:
            payload["temperature"] = params.temperature
        if params.top_p is not None:
            payload["top_p"] = params.top_p
        if params.max_tokens is not None:
            payload["max_tokens"] = params.max_tokens
        if params.presence_penalty is not None:
            payload["presence_penalty"] = params.presence_penalty
        if params.frequency_penalty is not None:
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
        request_summary = _request_summary(payload)
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
                response=_response_summary(resp.text),
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
                response=_response_summary(resp.text),
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
                response=_response_summary(resp.text),
                error="invalid_json",
            )
            raise LLMError(
                "LLM 返回非 JSON 响应（请检查 OPENAI_BASE_URL 是否正确）",
                detail={"status": resp.status_code, "request_id": request_id},
            ) from e

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
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
                "LLM 响应缺少 choices[0].message.content",
                detail={"request_id": request_id},
            ) from e
        text = str(content or "")
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
            response=_content_response_summary(text, data),
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
        request_summary = _request_summary(payload)
        pieces = 0
        chars = 0
        preview_parts: list[str] = []
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
                    response=_response_summary(body.decode("utf-8", errors="replace")),
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
                    response=_response_summary(body.decode("utf-8", errors="replace")),
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
                        yield content
                    # 兼容 reasoning 字段（一些上游会在 thinking 阶段送 delta.reasoning）；本期暂不渲染
                    await asyncio.sleep(0)  # 让出事件循环
            finally:
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
                    response={
                        "stream_chunks": pieces,
                        "content_chars": chars,
                        "content_preview": _short_text("".join(preview_parts), 240),
                    },
                )


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


def _request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    message_summaries: list[dict[str, Any]] = []
    role_counts: dict[str, int] = {}
    total_chars = 0
    image_count = 0
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            role_counts[role] = role_counts.get(role, 0) + 1
            text, images = _message_text_and_images(msg.get("content"))
            total_chars += len(text)
            image_count += images
            message_summaries.append(
                {
                    "role": role,
                    "chars": len(text),
                    "image_count": images,
                    "preview": _short_text(text, 300),
                }
            )
    return {
        "model": payload.get("model"),
        "stream": payload.get("stream"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "max_tokens": payload.get("max_tokens"),
        "presence_penalty": payload.get("presence_penalty"),
        "frequency_penalty": payload.get("frequency_penalty"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "role_counts": role_counts,
        "total_text_chars": total_chars,
        "image_count": image_count,
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
        if part.get("type") == "text":
            texts.append(str(part.get("text") or ""))
        elif part.get("type") == "image_url":
            images += 1
    return "\n".join(texts), images


def _response_summary(text: str) -> dict[str, Any]:
    return {
        "raw_chars": len(text),
        "raw_preview": _short_text(text, 240),
    }


def _json_response_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(str(key) for key in data.keys()),
        "choice_count": len(data.get("choices", [])) if isinstance(data.get("choices"), list) else 0,
    }


def _content_response_summary(text: str, data: dict[str, Any]) -> dict[str, Any]:
    summary = _json_response_summary(data)
    summary.update(
        {
            "content_chars": len(text),
            "content_preview": _short_text(text, 240),
        }
    )
    usage = data.get("usage")
    if isinstance(usage, dict):
        summary["usage"] = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if key in usage
        }
    return summary


def _short_text(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
