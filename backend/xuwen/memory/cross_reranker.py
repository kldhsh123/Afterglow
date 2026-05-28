"""专用 cross-encoder reranker 客户端（粗排阶段）。

设计目标：
- 走 RRF 之后、SemanticReranker（LLM 精排）之前，把候选池砍到 cross_rerank_top_n
- fail-open：网络失败 / 非法响应一律返回原顺序，主链路不受影响
- 同时支持两种主流协议：
    * jina：Jina / SiliconFlow / Cohere v2 / 大多数自建 bge-reranker 服务都用这种
    * dashscope：阿里 DashScope text-rerank 原生 API，结构嵌套在 input/output 下

Cross-encoder 只做相关性筛选，不做"风格证据"判断 —— 那是 SemanticReranker 的活。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import httpx

from xuwen.config import Settings
from xuwen.core.metrics import MetricsRecorder
from xuwen.core.models import ScoredChunk
from xuwen.ingestion.embedder import _resolve_endpoint

logger = logging.getLogger(__name__)


class CrossReranker:
    """专用 reranker 客户端。

    构造时不发请求；首次 rerank 才建立连接。失败一律 fail-open 返回原顺序。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.cross_rerank_timeout_seconds, connect=5.0),
        )
        protocol = settings.cross_rerank_protocol
        if protocol == "dashscope":
            self._url = _resolve_endpoint(
                settings.cross_rerank_api_url,
                "/services/rerank/text-rerank/text-rerank",
            )
        else:
            self._url = _resolve_endpoint(settings.cross_rerank_api_url, "/rerank")
        self._headers = {
            "Authorization": f"Bearer {settings.cross_rerank_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> CrossReranker:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def rerank(
        self,
        *,
        query_text: str,
        candidates: list[ScoredChunk],
        top_n: int,
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
    ) -> list[ScoredChunk]:
        """对候选做相关性粗排，返回前 top_n 条（保留原 ScoredChunk，只重排顺序）。

        - 候选数 <= top_n 直接返回原顺序（不调用上游）
        - 上游失败 / 解析失败时返回 candidates[:top_n]，不影响后续 LLM 精排
        - chunk.score 不动（仍是 RRF 分），相关性分数写到 metadata.cross_rerank_score
        """
        if not candidates:
            return candidates
        if top_n <= 0:
            return candidates
        if len(candidates) <= top_n:
            return _annotate_passthrough(candidates, top_n=top_n)

        documents = [_candidate_text(c) for c in candidates]
        try:
            results = await self._call(query_text, documents, top_n)
        except Exception:
            logger.warning("cross reranker call failed; using RRF order", exc_info=True)
            if metrics is not None:
                metrics.record(
                    "retrieval.cross_rerank",
                    0.0,
                    error="upstream_error",
                    detail=f"trace={trace_id}",
                )
            return _annotate_passthrough(candidates, top_n=top_n, reason="error")

        if not results:
            if metrics is not None:
                metrics.record(
                    "retrieval.cross_rerank",
                    0.0,
                    error="empty_results",
                    detail=f"trace={trace_id}",
                )
            return _annotate_passthrough(candidates, top_n=top_n, reason="empty")

        reordered: list[ScoredChunk] = []
        seen: set[int] = set()
        for index, score in results:
            if not 0 <= index < len(candidates):
                continue
            if index in seen:
                continue
            seen.add(index)
            chunk = candidates[index]
            reordered.append(
                replace(
                    chunk,
                    metadata={
                        **chunk.metadata,
                        "cross_rerank_score": round(float(score), 4),
                        "cross_rerank": "model",
                    },
                )
            )
            if len(reordered) >= top_n:
                break

        # 上游可能漏返某些 index：补齐到 top_n，避免下游候选不够用
        if len(reordered) < top_n:
            for i, chunk in enumerate(candidates):
                if i in seen:
                    continue
                reordered.append(
                    replace(
                        chunk,
                        metadata={**chunk.metadata, "cross_rerank": "fill"},
                    )
                )
                if len(reordered) >= top_n:
                    break

        if metrics is not None:
            metrics.record(
                "retrieval.cross_rerank",
                0.0,
                detail=f"trace={trace_id},in={len(candidates)},out={len(reordered)}",
            )
        return reordered

    # ------------------------------------------------------------------
    # 内部：协议适配
    # ------------------------------------------------------------------

    async def _call(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        if self.settings.cross_rerank_protocol == "dashscope":
            return await self._call_dashscope(query, documents, top_n)
        return await self._call_jina_style(query, documents, top_n)

    async def _call_jina_style(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        payload = {
            "model": self.settings.cross_rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "return_documents": False,
        }
        response = await self._client.post(self._url, headers=self._headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return _parse_jina_results(data)

    async def _call_dashscope(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        payload = {
            "model": self.settings.cross_rerank_model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": top_n, "return_documents": False},
        }
        response = await self._client.post(self._url, headers=self._headers, json=payload)
        response.raise_for_status()
        data = response.json()
        # DashScope 把结果嵌在 output 下
        return _parse_jina_results(data.get("output") or {})


def _parse_jina_results(data: dict[str, Any]) -> list[tuple[int, float]]:
    """从 Jina / Cohere v2 风格响应里抽 (index, score) 列表。"""
    results = data.get("results")
    if not isinstance(results, list):
        return []
    out: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score")
        if score is None:
            score = item.get("score")
        if not isinstance(index, int):
            continue
        if not isinstance(score, (int, float)):
            continue
        out.append((index, float(score)))
    return out


def _candidate_text(chunk: ScoredChunk) -> str:
    """把 ScoredChunk 压成给 cross-encoder 评分的文档文本。

    response_pair 拼成"用户/TA"两行，让 cross-encoder 能看到 reply pair 全貌；
    其它种类优先用 dialogue_snippet（带 speaker 前缀更准），否则原文本。
    """
    meta = chunk.metadata or {}
    if chunk.kind == "response_pair":
        user_text = str(meta.get("text") or "")
        friend_reply = str(meta.get("friend_reply") or "")
        return f"用户：{user_text}\nTA：{friend_reply}".strip()
    snippet = meta.get("dialogue_snippet")
    if snippet:
        return str(snippet)
    return str(chunk.text or "")


def _annotate_passthrough(
    candidates: list[ScoredChunk],
    *,
    top_n: int,
    reason: str = "passthrough",
) -> list[ScoredChunk]:
    """fail-open / 短候选场景下，给截断后的候选挂上元信息便于 debug。"""
    selected = candidates[:top_n]
    return [
        replace(c, metadata={**c.metadata, "cross_rerank": reason}) for c in selected
    ]
