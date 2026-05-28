"""Optional LLM-assisted query rewrite and semantic reranking.

Both helpers are deliberately fail-open: if the model call fails or returns
invalid JSON, retrieval falls back to the existing vector + RRF ranking.
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
    """Rewrite short, colloquial user input into retrieval-friendly variants."""

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
        """Return query variants, always including the original query first."""
        original = query_text.strip()
        if not original or not self.settings.query_rewrite_enabled:
            return [original]

        messages = [
            {
                "role": "system",
                "content": (
                    "你是 RAG 检索 query 改写器。你只输出 JSON，不要 markdown。"
                    "目标是把用户当前的短句、口语、情绪表达改写成 1-3 个适合检索历史聊天记录的查询。"
                    "不要添加用户没表达的新事实；不要改写成回答。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "用户本轮输入：\n"
                    f"{original}\n\n"
                    "输出格式：\n"
                    '{"queries":["..."],"intent":"一句短标签"}'
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
    """Rerank fused retrieval candidates with an OpenAI-compatible chat model."""

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
                    "你是聊天记忆语义重排器。你只输出 JSON，不要 markdown。"
                    "任务：对每条历史候选独立打 0~1 分，表示它作为本轮回复证据的贴合度。"
                    "评分依据（优先级从高到低）：是真实聊天 > response_pair（用户→朋友回复对）> "
                    "语境相符 > 排除低信号寒暄（仅『嗯』、『在』、纯表情等）。"
                    "严禁所有候选打同一个分数；不要生成回复正文；不要改写候选内容。"
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
        "【当前用户输入】",
        query_text.strip(),
        "",
        "【候选记忆】",
    ]
    for idx, chunk in enumerate(candidates, 1):
        meta = chunk.metadata
        if chunk.kind == "response_pair":
            text = (
                f"用户曾说：{meta.get('text') or ''}\n"
                f"TA 曾回：{meta.get('friend_reply') or ''}\n"
                f"上下文：{chunk.text}"
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
            "请输出 JSON。**格式示例（数字仅为示意，不要照抄）**：",
            '{"ranked":[{"id":"first_id","score":0.92},{"id":"second_id","score":0.45},{"id":"third_id","score":0.08}]}',
            "score 范围 0~1，**根据每条候选的真实贴合度独立打分**：",
            "  - 0.7-0.95：特别贴合，是本轮的强证据（如同样话题的真实 response_pair）",
            "  - 0.3-0.6：一般相关，可作为背景",
            "  - 0-0.2：无关、低信号寒暄、或主要是用户自己说的话",
            "必须为【所有】候选 id 给出一个分数，不要遗漏；",
            "严禁所有候选打相同分数，严禁照抄上面示例里的 0.92/0.45/0.08；",
            "也不要返回候选集合之外的 id。只输出这一段 JSON，不要解释、不要 markdown。",
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
