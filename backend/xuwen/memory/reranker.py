"""可选的 LLM 辅助查询改写与语义重排。

两个辅助流程都刻意采用失败开放策略：模型调用失败或返回无效 JSON 时，
检索会退回现有的向量检索与 RRF 排序。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
from xuwen.core.metrics import MetricsRecorder
from xuwen.core.models import ScoredChunk

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_AMBIGUOUS_SHORT_RE = re.compile(
    r"^(在吗|在不在|晚安|早安|想你了?|抱抱|陪我|怎么了|咋了|嗯+|好+|哈哈+|你在干嘛|你在干什么)$"
)


class QueryRewriter:
    """把简短、口语化的用户输入改写为更适合检索的多个变体。"""

    def __init__(self, settings: Settings, llm: LLMClient) -> None:
        self.settings = settings
        self.llm = llm

    async def rewrite(
        self,
        query_text: str,
        *,
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
    ) -> list[str]:
        """返回查询变体，并始终把原始查询放在首位。"""
        original = query_text.strip()
        if not original or not self.settings.query_rewrite_enabled:
            return [original]

        messages = [
            {
                "role": "system",
                "content": (
                    "You rewrite queries for RAG. Return JSON only, no Markdown. "
                    "Turn a short, colloquial or emotional user input into 1-3 history retrieval queries in the input language. "
                    "Do not add facts the user did not express or turn the input into an answer."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Current user input:\n"
                    f"{original}\n\n"
                    "Output format:\n"
                    '{"queries":["..."],"intent":"short label"}'
                ),
            },
        ]
        try:
            raw = await self.llm.complete_chat(
                messages,
                GenerationParams(
                    temperature=self.settings.query_rewrite_temperature,
                    max_tokens=self.settings.query_rewrite_max_tokens,
                ),
                model=self.settings.resolved_query_rewrite_model,
                trace_id=trace_id,
                stage="retrieval.query_rewrite",
                metrics=metrics,
            )
        except Exception:
            logger.warning("query rewrite failed; using original query", exc_info=True)
            return [original]

        data = _parse_json_object(raw)
        if not data:
            return [original]
        raw_queries = data.get("queries")
        if not isinstance(raw_queries, list):
            return [original]

        out = [original]
        limit = max(0, self.settings.query_rewrite_max_variants)
        for item in raw_queries:
            if not isinstance(item, str):
                continue
            cleaned = " ".join(item.split()).strip()
            if not cleaned or cleaned == original or cleaned in out:
                continue
            out.append(cleaned[:160])
            if len(out) >= limit + 1:
                break
        return out


class SemanticReranker:
    """使用 OpenAI 兼容聊天模型重排融合后的检索候选。"""

    def __init__(self, settings: Settings, llm: LLMClient) -> None:
        self.settings = settings
        self.llm = llm

    def candidate_limit(self, final_k: int) -> int:
        if not self.settings.rerank_enabled or self.settings.rerank_mode == "never":
            return final_k
        return max(final_k, self.settings.rerank_top_k)

    async def rerank(
        self,
        *,
        query_text: str,
        candidates: list[ScoredChunk],
        final_k: int,
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
    ) -> list[ScoredChunk]:
        if not self._should_rerank(query_text, candidates):
            return _finalize_ranks(candidates[:final_k], reason="skipped")

        pool = candidates[: max(final_k, self.settings.rerank_top_k)]
        messages = [
            {
                "role": "system",
                "content": (
                    "You rerank chat memories. Return JSON only, no Markdown. "
                    "Independently score each candidate from 0 to 1 for relevance as evidence for the current reply. "
                    "Prioritize genuine human chat, then response_pair (user -> friend reply), then "
                    "matching context. Downrank low-signal greetings, acknowledgements and bare emoji. "
                    "Do not assign all candidates the same score. Do not generate a reply or rewrite candidates."
                ),
            },
            {"role": "user", "content": _build_rerank_prompt(query_text, pool)},
        ]
        try:
            raw = await self.llm.complete_chat(
                messages,
                GenerationParams(
                    temperature=self.settings.rerank_temperature,
                    max_tokens=self.settings.rerank_max_tokens,
                ),
                model=self.settings.resolved_rerank_model,
                trace_id=trace_id,
                stage="retrieval.rerank",
                metrics=metrics,
            )
        except Exception:
            logger.warning("rerank failed; using RRF order", exc_info=True)
            return _finalize_ranks(candidates[:final_k], reason="error")

        scores = _parse_rerank_scores(raw)
        if not scores:
            return _finalize_ranks(candidates[:final_k], reason="invalid_json")

        reranked = _blend_scores(
            pool,
            scores,
            weight=self.settings.rerank_weight,
        )
        selected = _select_with_session_diversity(
            reranked,
            final_k=final_k,
            max_same_session=self.settings.rerank_max_same_session,
        )
        if len(selected) < final_k:
            selected_ids = {c.chunk_id for c in selected}
            for chunk in reranked:
                if chunk.chunk_id in selected_ids:
                    continue
                selected.append(chunk)
                selected_ids.add(chunk.chunk_id)
                if len(selected) >= final_k:
                    break
        return _finalize_ranks(selected[:final_k], reason="model")

    def _should_rerank(self, query_text: str, candidates: list[ScoredChunk]) -> bool:
        if not self.settings.rerank_enabled or self.settings.rerank_mode == "never":
            return False
        if len(candidates) < max(1, self.settings.rerank_min_candidates):
            return False
        if self.settings.rerank_mode == "always":
            return True
        compact = re.sub(r"\s+", "", query_text.strip())
        if _AMBIGUOUS_SHORT_RE.fullmatch(compact):
            return True
        if len(compact) <= 18:
            return True
        top = candidates[0].score if candidates else 0.0
        compare = candidates[min(4, len(candidates) - 1)].score if candidates else 0.0
        if top <= 0:
            return True
        return (top - compare) / top < 0.18


def _build_rerank_prompt(query_text: str, candidates: list[ScoredChunk]) -> str:
    lines = [
        "[Current user input]",
        query_text.strip(),
        "",
        "[Candidate memories]",
    ]
    for idx, chunk in enumerate(candidates, 1):
        meta = chunk.metadata
        if chunk.kind == "response_pair":
            text = (
                f"User said: {meta.get('text') or ''}\n"
                f"Friend replied: {meta.get('friend_reply') or ''}\n"
                f"Context: {chunk.text}"
            )
        else:
            text = str(meta.get("dialogue_snippet") or chunk.text or "")
        lines.append(
            "\n".join(
                [
                    f"[{idx}] id={chunk.chunk_id}",
                    f"kind={chunk.kind} source={chunk.source} score={chunk.score:.6f} warmth={chunk.warmth:.2f}",
                    _short(text, 520),
                ]
            )
        )
    lines.extend(
        [
            "",
            "Return JSON. Format example (illustrative numbers; do not copy):",
            '{"ranked":[{"id":"first_id","score":0.92},{"id":"second_id","score":0.45},{"id":"third_id","score":0.08}]}',
            "Score from 0 to 1 independently according to actual relevance:",
            "  - 0.7-0.95: strong evidence, e.g. a genuine response_pair on the same topic.",
            "  - 0.3-0.6: generally related background.",
            "  - 0-0.2: unrelated, low-signal greeting, or mostly the user’s own words.",
            "Score EVERY candidate id without omissions.",
            "Do not use one score for all candidates or copy the example scores.",
            "Return no ids outside the candidates. JSON only, no explanation or Markdown.",
        ]
    )
    return "\n".join(lines)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_rerank_scores(raw: str) -> dict[str, float]:
    data = _parse_json_object(raw)
    if not data:
        return {}
    ranked = data.get("ranked")
    if not isinstance(ranked, list):
        return {}
    scores: dict[str, float] = {}
    for item in ranked:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        score = item.get("score")
        if not isinstance(cid, str) or not cid:
            continue
        if not isinstance(score, (int, float)):
            continue
        scores[cid] = max(0.0, min(1.0, float(score)))
    return scores


def _blend_scores(
    candidates: list[ScoredChunk],
    rerank_scores: dict[str, float],
    *,
    weight: float,
) -> list[ScoredChunk]:
    if not candidates:
        return []
    max_score = max((c.score for c in candidates), default=0.0) or 1.0
    out: list[ScoredChunk] = []
    for chunk in candidates:
        rrf_norm = max(0.0, min(1.0, chunk.score / max_score))
        model_score = rerank_scores.get(chunk.chunk_id, 0.0)
        blended = (1.0 - weight) * rrf_norm + weight * model_score
        out.append(
            replace(
                chunk,
                score=blended,
                metadata={
                    **chunk.metadata,
                    "rerank_score": round(model_score, 4),
                    "rerank_rrf_norm": round(rrf_norm, 4),
                    "rerank_weight": weight,
                },
            )
        )
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def _select_with_session_diversity(
    candidates: list[ScoredChunk],
    *,
    final_k: int,
    max_same_session: int,
) -> list[ScoredChunk]:
    if max_same_session <= 0:
        return candidates[:final_k]
    selected: list[ScoredChunk] = []
    session_counts: dict[str, int] = {}
    for chunk in candidates:
        sid = chunk.session_id
        if sid:
            count = session_counts.get(sid, 0)
            if count >= max_same_session:
                continue
            session_counts[sid] = count + 1
        selected.append(chunk)
        if len(selected) >= final_k:
            break
    return selected


def _finalize_ranks(chunks: list[ScoredChunk], *, reason: str) -> list[ScoredChunk]:
    return [
        replace(
            c,
            rank=i + 1,
            metadata={**c.metadata, "rerank": reason},
        )
        for i, c in enumerate(chunks)
    ]


def _short(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."
