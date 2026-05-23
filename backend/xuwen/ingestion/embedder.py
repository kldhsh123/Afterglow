"""OpenAI 兼容 Embedding 客户端封装。

通过 OpenAI 兼容的 /embeddings 接口调用（DashScope / 自建网关都支持）。

特性：
- 批处理（默认 batch_size=64）
- 429 / 5xx 指数退避（tenacity）
- 并发限流（asyncio.Semaphore）
- 请求速率限制（可选，按 HTTP 请求数 / 分钟）
- 输出维度强校验
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Sequence
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from xuwen.config import Settings
from xuwen.core.errors import EmbeddingError

logger = logging.getLogger(__name__)


def _resolve_endpoint(base_url: str, suffix: str) -> str:
    """构造最终的 endpoint URL，容忍用户在 `*_API_URL` 里把后缀写进去。

    - base_url=https://api.openai.com/v1            + suffix=/embeddings → /v1/embeddings
    - base_url=https://api.openai.com/v1/embeddings + suffix=/embeddings → /v1/embeddings（不重复拼）
    """
    url = base_url.rstrip("/")
    s = suffix.rstrip("/")
    if url.endswith(s):
        return url
    return url + s


class _RetryableEmbeddingError(EmbeddingError):
    """仅用于 tenacity 内部触发重试的临时异常类型。

    4xx 一律抛 `EmbeddingError`（不可重试）；
    5xx / 429 / 网络错误抛 `_RetryableEmbeddingError`，会被 tenacity 重试。
    """


class _AsyncSlidingWindowRateLimiter:
    """简单滑动窗口限速器。"""

    def __init__(self, max_requests: int, *, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """等待直到当前窗口允许发起下一次 HTTP 请求。"""
        if self.max_requests <= 0:
            return

        while True:
            async with self._lock:
                now = time.monotonic()
                while (
                    self._timestamps
                    and now - self._timestamps[0] >= self.window_seconds
                ):
                    self._timestamps.popleft()

                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return

                wait_seconds = self.window_seconds - (now - self._timestamps[0])

            await asyncio.sleep(max(wait_seconds, 0.0))


class EmbeddingClient:
    """通用 Embedding API 客户端。

    具体模型由 `EMBEDDING_MODEL` 配置决定，默认值只是推荐配置，
    这里不要绑定某个模型名称，避免误导用户以为只能使用 Qwen。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        concurrency: int | None = None,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._url = _resolve_endpoint(str(settings.embedding_api_url), "/embeddings")
        self._headers = {
            "Authorization": f"Bearer {settings.embedding_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        max_concurrency = (
            concurrency
            if concurrency is not None
            else settings.embedding_max_concurrency
        )
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._rate_limiter = _AsyncSlidingWindowRateLimiter(
            settings.embedding_max_requests_per_minute,
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> EmbeddingClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # 公开
    # ------------------------------------------------------------------

    async def embed_texts(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        """批量向量化。

        - 自动按 batch_size 切块并行调用。
        - single 模式下 batch_size 强制为 1（每条独立请求）。
        - 返回与 texts 等长且顺序一致的向量列表。
        """
        if not texts:
            return []

        effective_batch = batch_size if batch_size is not None else self.settings.embedding_batch_size
        if self.settings.embedding_input_mode == "single":
            effective_batch = 1
        effective_batch = max(1, effective_batch)

        batches: list[list[str]] = []
        for i in range(0, len(texts), effective_batch):
            batches.append(list(texts[i : i + effective_batch]))

        async def _run(batch: list[str]) -> list[list[float]]:
            async with self._semaphore:
                return await self._embed_batch(batch)

        results: list[list[list[float]]] = await asyncio.gather(
            *(_run(b) for b in batches)
        )
        out: list[list[float]] = []
        for batch_result in results:
            out.extend(batch_result)
        if len(out) != len(texts):
            raise EmbeddingError(
                f"返回向量数量与输入不一致：期望 {len(texts)}，实际 {len(out)}"
            )
        return out

    async def embed_one(self, text: str) -> list[float]:
        """便捷的单条接口。"""
        vecs = await self.embed_texts([text], batch_size=1)
        return vecs[0]

    def validate_vector(self, vector: Sequence[float]) -> None:
        """强校验维度。"""
        if len(vector) != self.settings.embedding_dim:
            raise EmbeddingError(
                f"向量维度不匹配：期望 {self.settings.embedding_dim}，实际 {len(vector)}"
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """调用一次 /embeddings。带指数退避重试。

        重试次数与等待上限由 settings 控制（默认 8 次 / 单次 10s 上限）。
        典型 429 限流场景下，1-2-4-8-10-10-10s 累计等待约 45s 足以让大多数
        rate-limit window 重置。
        """
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.settings.embedding_retry_attempts),
            wait=wait_exponential(
                multiplier=1.0,
                min=1.0,
                max=self.settings.embedding_retry_max_wait_seconds,
            ),
            retry=retry_if_exception_type((httpx.HTTPError, _RetryableEmbeddingError)),
            reraise=True,
        ):
            with attempt:
                return await self._call_once(batch)
        # 不会到这里（reraise=True 已抛出）
        raise EmbeddingError("意外的重试退出")

    async def _call_once(self, batch: list[str]) -> list[list[float]]:
        # 构造 payload：根据 input_mode 决定 input 是 string 还是 string[]
        input_value: str | list[str]
        if self.settings.embedding_input_mode == "single":
            if len(batch) != 1:
                # single 模式应该已被 embed_texts 切到 batch_size=1；万一传错则强行只发首条
                batch = batch[:1]
            input_value = batch[0]
        else:
            input_value = batch

        payload: dict[str, object] = {
            "model": self.settings.embedding_model,
            "input": input_value,
        }
        if self.settings.embedding_include_encoding_format:
            payload["encoding_format"] = "float"
        if self.settings.embedding_send_dimensions:
            # 显式告诉上游输出维度。支持 MRL 的服务（OpenAI text-embedding-3 /
            # DashScope text-embedding-v3 / Qwen3-Embedding 网关）不发这个字段
            # 会用上游默认值，常被降到 1024，导致维度校验失败。
            payload["dimensions"] = self.settings.embedding_dim

        try:
            await self._rate_limiter.acquire()
            resp = await self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError as e:
            # 网络错误：不带响应正文，避免敏感信息泄露
            raise _RetryableEmbeddingError(
                f"embedding 网络请求失败：{type(e).__name__}",
                detail={"exception_type": type(e).__name__},
            ) from e

        # 错误：只回传 status 与上游 request id（如果有），不回传响应正文
        # 但上游响应体会写入日志，便于本地诊断
        request_id = resp.headers.get("x-request-id") or resp.headers.get("request-id")
        if resp.status_code in (429,) or 500 <= resp.status_code < 600:
            logger.warning(
                "embedding 上游 %d: %s\nembedding 请求诊断: %s",
                resp.status_code,
                resp.text[:500],
                _payload_debug_summary(payload, self._url),
            )
            raise _RetryableEmbeddingError(
                f"embedding API 暂时不可用（HTTP {resp.status_code}）",
                detail={"status": resp.status_code, "request_id": request_id},
            )
        if resp.status_code >= 400:
            # 4xx 不重试，直接报；不包含响应正文（可能含 input 文本）
            # 但写日志让本地用户能看到上游具体反馈
            logger.error(
                "embedding 上游 %d: %s\nembedding 请求诊断: %s",
                resp.status_code,
                resp.text[:500],
                _payload_debug_summary(payload, self._url),
            )
            raise EmbeddingError(
                f"embedding API 客户端错误（HTTP {resp.status_code}），"
                f"请检查 API key、模型名、配额（详细错误见日志）",
                detail={"status": resp.status_code, "request_id": request_id},
            )

        # 解析 JSON；非 JSON 响应明确告警，不直接抛裸 JSONDecodeError
        try:
            data = resp.json()
        except ValueError as e:
            raise EmbeddingError(
                "embedding API 返回非 JSON 响应（请检查 EMBEDDING_API_URL 是否正确）",
                detail={"status": resp.status_code, "request_id": request_id},
            ) from e

        if not isinstance(data, dict) or "data" not in data:
            raise EmbeddingError(
                "embedding 响应格式异常：缺少 data 字段",
                detail={"request_id": request_id},
            )
        items = data["data"]
        if not isinstance(items, list):
            raise EmbeddingError(
                "embedding 响应 data 字段不是数组",
                detail={"request_id": request_id},
            )
        if len(items) != len(batch):
            raise EmbeddingError(
                f"embedding 返回条数不匹配：期望 {len(batch)}，实际 {len(items)}",
                detail={"request_id": request_id},
            )

        out: list[list[float]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise EmbeddingError(
                    f"embedding 响应第 {idx} 项不是 dict",
                    detail={"request_id": request_id},
                )
            vec = item.get("embedding")
            if not isinstance(vec, list):
                raise EmbeddingError(
                    f"embedding 响应第 {idx} 项缺少 embedding 字段",
                    detail={"request_id": request_id},
                )
            if len(vec) != self.settings.embedding_dim:
                raise EmbeddingError(
                    f"向量维度不匹配：期望 {self.settings.embedding_dim}，实际 {len(vec)}（请检查 EMBEDDING_MODEL 与 EMBEDDING_DIM 是否一致）"
                )
            out.append([float(x) for x in vec])
        return out


def _payload_debug_summary(payload: dict[str, object], url: str) -> str:
    input_value = payload.get("input")
    texts: list[str]
    input_type: str
    if isinstance(input_value, str):
        texts = [input_value]
        input_type = "string"
    elif isinstance(input_value, list):
        texts = [str(item) for item in input_value]
        input_type = "array"
    else:
        texts = []
        input_type = type(input_value).__name__

    summary: dict[str, Any] = {
        "url": url,
        "model": payload.get("model"),
        "input_type": input_type,
        "input_count": len(texts),
        "total_chars": sum(len(text) for text in texts),
        "encoding_format": payload.get("encoding_format"),
        "input_preview": [
            {
                "index": idx,
                "chars": len(text),
                "preview": _short_text(text, 240),
            }
            for idx, text in enumerate(texts[:5])
        ],
        "payload_preview": _payload_preview(payload),
    }
    return json.dumps(summary, ensure_ascii=False)


def _payload_preview(payload: dict[str, object]) -> dict[str, object]:
    preview = dict(payload)
    input_value = preview.get("input")
    if isinstance(input_value, str):
        preview["input"] = _short_text(input_value, 500)
    elif isinstance(input_value, list):
        preview["input"] = [
            _short_text(str(item), 240)
            for item in input_value[:8]
        ]
        if len(input_value) > 8:
            preview["input_truncated_count"] = len(input_value) - 8
    return preview


def _short_text(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
