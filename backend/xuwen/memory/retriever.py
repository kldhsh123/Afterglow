"""混合检索器：多路召回 → RRF 融合 → 时间衰减 / source 权重 / warmth boost。

设计：
- 三路召回
    A. friend_messages 向量召回（top_k = settings.friend_top_k）
    B. dialogue_windows 向量召回（top_k = settings.window_top_k）
    C. live_messages 最近若干条（按 conversation_id 过滤）—— 非向量，无需 embedding
- 融合：RRF（Reciprocal Rank Fusion），按 chunk_id 去重；同一 chunk 在多路出现得分相加
- 后处理 boost：
    final = rrf * source_weight * recency * (1 + warmth * settings.warmth_boost)
- 输出 top final_context_k 条 ScoredChunk
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from xuwen.config import Settings
from xuwen.core.errors import RetrievalError
from xuwen.core.metrics import MetricsRecorder
from xuwen.core.models import RetrievalQuery, RetrievalResult, ScoredChunk
from xuwen.core.time import now_ms, recency_weight
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.cross_reranker import CrossReranker
from xuwen.memory.reranker import QueryRewriter, SemanticReranker
from xuwen.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_PLACEHOLDER_TOKEN_RE = re.compile(
    r"\[(图片|语音|视频|文件|表情|动画表情|撤回|系统消息)\]|\[\[[^\]]+\]\]"
)
_STICKER_TOKEN_RE = re.compile(r"\[sticker(?::|=)[^\]\s]+\]")
_TRAILING_PARTIAL_STICKER_RE = re.compile(r"\s*\[sticker(?::|=)[^\]\s]*$")
_NOISE_RE = re.compile(r"[\s,，.。!！?？~～、…·:：;；'\"“”‘’`]+")
_PROACTIVE_USER_MARKERS = {"（AI 主动开启话题）", "(AI 主动开启话题)"}
_QUESTION_ALIAS = {
    "你在干什么": "在干嘛",
    "在干什么": "在干嘛",
    "你在干嘛": "在干嘛",
    "干嘛呢": "在干嘛",
}
_ECHO_RISK_QUERIES = {"在干嘛", "在吗"}


@dataclass(slots=True, frozen=True)
class _RawHit:
    """召回的原始行 + 来源信息。"""

    row: dict[str, Any]
    rank: int
    kind: str  # "response_pair" / "friend" / "window" / "live"


class HybridRetriever:
    """组合多路向量召回 + 时间衰减 + warmth boost 的检索器。"""

    def __init__(
        self,
        settings: Settings,
        store: MemoryStore,
        embedder: EmbeddingClient,
        query_rewriter: QueryRewriter | None = None,
        reranker: SemanticReranker | None = None,
        cross_reranker: CrossReranker | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.query_rewriter = query_rewriter
        self.reranker = reranker
        self.cross_reranker = cross_reranker

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        metrics: MetricsRecorder | None = None,
        trace_id: str = "",
    ) -> RetrievalResult:
        """执行混合检索并返回融合结果。"""
        total_start = time.perf_counter()
        if not query.query_text.strip():
            raise RetrievalError("query_text 不能为空")

        # 1) query 改写（可选）+ 向量化。第一个永远是原始 query。
        query_texts = [query.query_text]
        if self.query_rewriter is not None:
            rewrite_start = time.perf_counter()
            query_texts = await self.query_rewriter.rewrite(query.query_text)
            _record_retrieval_metric(
                metrics,
                "retrieval.query_rewrite",
                rewrite_start,
                trace_id=trace_id,
                detail=f"variants={len(query_texts)}",
            )
        query_texts = _dedupe_query_texts(query_texts)
        try:
            embed_start = time.perf_counter()
            if len(query_texts) == 1:
                vectors = [await self.embedder.embed_one(query_texts[0])]
            else:
                vectors = await self.embedder.embed_texts(query_texts)
            _record_retrieval_metric(
                metrics,
                "retrieval.embed",
                embed_start,
                trace_id=trace_id,
                detail=f"variants={len(query_texts)},dim={len(vectors[0]) if vectors else 0}",
            )
        except Exception as e:
            _record_retrieval_metric(
                metrics,
                "retrieval.embed",
                embed_start,
                trace_id=trace_id,
                error=type(e).__name__,
            )
            # 把 embedder 的失败包装成 RetrievalError，方便上层统一处理
            raise RetrievalError(f"查询向量化失败：{type(e).__name__}") from e

        top_k_friend = query.top_k_friend or self.settings.friend_top_k
        top_k_pair = self.settings.response_pair_top_k
        top_k_window = query.top_k_window or self.settings.window_top_k
        final_k = query.final_k or self.settings.final_context_k
        now = query.now_ms or now_ms()

        # 2) 五路召回：以前是串行 await，现在 asyncio.gather 真并发（store.search_* 用了 to_thread）。
        #    store 的 _vector_search 是同步 LanceDB 调用，靠线程池让多路真正并行执行。
        live_top_k = self.settings.live_top_k
        live_filter = _compute_live_filter(query, self.settings)
        # overfetch 从 settings 读，便于大库用户调高（默认 2 适配 < 10k 行）
        overfetch = max(1, self.settings.retrieval_overfetch)

        pair_raw_task = _search_variants(
            self.store.search_response_pairs,
            vectors,
            top_k_pair * overfetch,
        )
        friend_raw_task = _search_variants(
            self.store.search_friend,
            vectors,
            top_k_friend * overfetch,
        )
        window_raw_task = _search_variants(
            self.store.search_windows,
            vectors,
            top_k_window * overfetch,
        )
        live_semantic_task = _search_variants(
            self.store.search_live,
            vectors,
            live_top_k * overfetch,
            extra_filter=live_filter,
        )
        # recent_live 不依赖 query vector，但跟其它四路同根（只依赖 conversation_id），可一起并发
        recent_live_task: Any
        if query.conversation_id:
            recent_live_task = self.store.recent_live(query.conversation_id, limit=20)
        else:
            recent_live_task = _noop_rows()

        (
            pair_raw,
            friend_raw,
            window_raw,
            live_semantic_raw,
            recent_live_raw,
        ) = await _timed_gather(
            metrics,
            "retrieval.vector_search",
            pair_raw_task,
            friend_raw_task,
            window_raw_task,
            live_semantic_task,
            recent_live_task,
            trace_id=trace_id,
        )

        fuse_start = time.perf_counter()
        pair_rows = _filter_response_pair_rows(pair_raw, limit=top_k_pair)
        friend_rows = _filter_friend_rows(
            friend_raw,
            query_text=query.query_text,
            limit=top_k_friend,
        )
        window_rows = _filter_window_rows(
            window_raw,
            friend_names=self.settings.all_friend_names or ["TA"],
            limit=top_k_window,
        )
        live_rows: list[dict[str, Any]] = _filter_live_rows(recent_live_raw, limit=20)
        # 把语义召回的 live 行合并到 live_rows（保证 fused 也能命中 live）
        live_semantic = _filter_live_rows(live_semantic_raw, limit=live_top_k)
        seen_ids = {r.get("id") for r in live_rows}
        for r in live_semantic:
            if r.get("id") not in seen_ids:
                live_rows.append(r)
                seen_ids.add(r.get("id"))

        # 3) 归一化为 ScoredChunk（RRF 前先各自 rank）
        response_pairs = [
            _row_to_scored(r, rank=i + 1, kind="response_pair")
            for i, r in enumerate(pair_rows)
        ]
        friend_examples = [
            _row_to_scored(r, rank=i + 1, kind="friend") for i, r in enumerate(friend_rows)
        ]
        dialogue_windows = [
            _row_to_scored(r, rank=i + 1, kind="window") for i, r in enumerate(window_rows)
        ]
        recent_live = [
            _row_to_scored(r, rank=i + 1, kind="live") for i, r in enumerate(live_rows)
        ]

        # 4) RRF 融合 + boost；按下游需要的最大候选数保留池子。
        final_pool_k = final_k
        if self.cross_reranker is not None and self.settings.cross_rerank_enabled:
            final_pool_k = max(final_pool_k, self.settings.cross_rerank_input_k)
        if self.reranker is not None:
            final_pool_k = max(final_pool_k, self.reranker.candidate_limit(final_k))
        fused_pool = _fuse(
            response_pairs=response_pairs,
            friend_examples=friend_examples,
            dialogue_windows=dialogue_windows,
            recent_live=recent_live,
            settings=self.settings,
            now_ms=now,
            final_k=final_pool_k,
        )
        _record_retrieval_metric(
            metrics,
            "retrieval.fuse",
            fuse_start,
            trace_id=trace_id,
            detail=(
                f"pair={len(pair_rows)},friend={len(friend_rows)},"
                f"window={len(window_rows)},live={len(live_rows)},pool={len(fused_pool)}"
            ),
        )

        # 5) Cross-encoder 粗排（可选）：48 → 16，砍掉纯噪声候选给 LLM 精排
        candidates_for_rerank = fused_pool
        if self.cross_reranker is not None and self.settings.cross_rerank_enabled:
            cross_start = time.perf_counter()
            candidates_for_rerank = await self.cross_reranker.rerank(
                query_text=query.query_text,
                candidates=fused_pool,
                top_n=self.settings.cross_rerank_top_n,
                trace_id=trace_id,
                metrics=metrics,
            )
            _record_retrieval_metric(
                metrics,
                "retrieval.cross_rerank.total",
                cross_start,
                trace_id=trace_id,
                detail=f"in={len(fused_pool)},out={len(candidates_for_rerank)}",
            )

        # 6) LLM 语义精排（可选）：在 cross 粗排后的候选里做"风格证据"判断
        if self.reranker is not None:
            rerank_start = time.perf_counter()
            fused = await self.reranker.rerank(
                query_text=query.query_text,
                candidates=candidates_for_rerank,
                final_k=final_k,
            )
            _record_retrieval_metric(
                metrics,
                "retrieval.semantic_rerank",
                rerank_start,
                trace_id=trace_id,
                detail=f"in={len(candidates_for_rerank)},out={len(fused)}",
            )
        else:
            fused = candidates_for_rerank[:final_k]

        _record_retrieval_metric(
            metrics,
            "retrieval.total",
            total_start,
            trace_id=trace_id,
            detail=f"final={len(fused)}",
        )
        return RetrievalResult(
            friend_examples=[*response_pairs[:4], *friend_examples],
            dialogue_windows=dialogue_windows,
            recent_live=recent_live,
            response_pairs=response_pairs,
            fused=fused,
        )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


async def _search_variants(
    search_fn: Any,
    vectors: list[list[float]],
    top_k: int,
    *,
    extra_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Run vector search for one or more query variants and merge by row id."""
    if not vectors:
        return []
    tasks = [
        search_fn(vector, top_k, extra_filter=extra_filter)
        for vector in vectors
    ]
    results = await asyncio.gather(*tasks)
    return _merge_variant_rows(results)


async def _timed_gather(
    metrics: MetricsRecorder | None,
    kind: str,
    *aws: Any,
    trace_id: str = "",
) -> tuple[Any, ...]:
    start = time.perf_counter()
    try:
        result = await asyncio.gather(*aws)
    except Exception as e:
        _record_retrieval_metric(metrics, kind, start, trace_id=trace_id, error=type(e).__name__)
        raise
    _record_retrieval_metric(metrics, kind, start, trace_id=trace_id)
    return tuple(result)


def _record_retrieval_metric(
    metrics: MetricsRecorder | None,
    kind: str,
    start: float,
    *,
    trace_id: str = "",
    detail: str = "",
    error: str | None = None,
) -> None:
    if metrics is None:
        return
    prefix = f"trace={trace_id}" if trace_id else ""
    if prefix and detail:
        detail = f"{prefix},{detail}"
    elif prefix:
        detail = prefix
    metrics.record(kind, (time.perf_counter() - start) * 1000, error=error, detail=detail)


async def _noop_rows() -> list[dict[str, Any]]:
    """gather 占位：没有 conversation_id 时的 recent_live 替代。"""
    return []


def _merge_variant_rows(result_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for variant_idx, rows in enumerate(result_sets):
        for rank, row in enumerate(rows, 1):
            row_id = str(row.get("id") or "")
            if not row_id:
                continue
            existing = merged.get(row_id)
            if existing is None:
                copied = dict(row)
                copied["query_variant_hits"] = [variant_idx]
                copied["query_variant_best_rank"] = rank
                merged[row_id] = copied
                continue
            best_rank = min(
                int(existing.get("query_variant_best_rank") or rank),
                rank,
            )
            hits = list(existing.get("query_variant_hits") or [])
            if variant_idx not in hits:
                hits.append(variant_idx)
            existing["query_variant_hits"] = hits
            existing["query_variant_best_rank"] = best_rank
            old_distance = existing.get("_distance")
            new_distance = row.get("_distance")
            if (
                isinstance(old_distance, (int, float))
                and isinstance(new_distance, (int, float))
                and new_distance < old_distance
            ):
                for key, value in row.items():
                    existing[key] = value
                existing["query_variant_hits"] = hits
                existing["query_variant_best_rank"] = best_rank
    # 按 best_rank（任一 variant 内的最小排名）升序输出，让 variant-only 命中也有机会
    # 进入后续 top_k 截断；原先按"原 query 在前、追加 variant"顺序会让 query_rewrite 的
    # 命中实质被截掉，等于该功能没生效。同 rank 用 distance 兜底。
    def _sort_key(row: dict[str, Any]) -> tuple[int, float]:
        rank = int(row.get("query_variant_best_rank") or 999_999)
        distance = row.get("_distance")
        dist_val = float(distance) if isinstance(distance, (int, float)) else 999_999.0
        return (rank, dist_val)

    return sorted(merged.values(), key=_sort_key)


def _dedupe_query_texts(texts: list[str]) -> list[str]:
    out: list[str] = []
    for raw in texts:
        text = " ".join(str(raw or "").split()).strip()
        if not text or text in out:
            continue
        out.append(text)
    return out or [""]


def _filter_friend_rows(
    rows: list[dict[str, Any]],
    *,
    query_text: str,
    limit: int,
) -> list[dict[str, Any]]:
    """过滤不能作为“朋友回复示例”的低信号单句。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text") or "")
        if _is_low_signal_text(text):
            continue
        if _is_echo_of_risky_query(query_text=query_text, candidate=text):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _filter_response_pair_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """过滤 friend_reply 低信号的 pair。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        reply = str(row.get("friend_reply") or "")
        if _is_low_signal_text(reply):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _filter_window_rows(
    rows: list[dict[str, Any]],
    *,
    friend_names: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """过滤没有目标对象有效发言的窗口，避免把用户自己的问句当证据。

    friend_names 包含 friend_name 和所有别名（真名/网名/昵称等），任一匹配即视为友方发言。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not _window_has_friend_signal(str(row.get("text") or ""), friend_names):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _filter_live_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """过滤 live 层低信号 + 旧库污染。

    - 占位符（"（AI 主动开启话题）"等）丢弃
    - 低信号文本（纯 sticker / 表情等）丢弃
    - 旧库残留 source=live + role=assistant 丢弃
      （新写入规则下，source=live 只可能是旧库残留；新 AI 回复用 source=ai_generated）
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text") or "").strip()
        if text in _PROACTIVE_USER_MARKERS:
            continue
        if _is_low_signal_text(text):
            continue
        source = str(row.get("source") or "")
        role = str(row.get("role") or "")
        if source == "live" and role == "assistant":
            # 旧库污染：早期把 AI 回复也写成 source=live。新检索侧主动跳过，避免再被召回。
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _compute_live_filter(
    query: RetrievalQuery,
    settings: Settings,
) -> str | None:
    """生成 search_live 的 extra_filter。

    - ai_generated_long_term_enabled=True：完全不限制（允许 AI 历史跨会话长期累积）
    - 否则：跨会话排除 ai_generated；如果有 conversation_id，同会话允许
    """
    if settings.ai_generated_long_term_enabled:
        return None
    base = "source != 'ai_generated'"
    if query.conversation_id:
        cid_quoted = query.conversation_id.replace("'", "''")
        return f"{base} OR conversation_id = '{cid_quoted}'"
    return base


def _window_has_friend_signal(text: str, friend_names: list[str]) -> bool:
    # 兼容旧测试/旧数据：没有 speaker 前缀的窗口只能按低信号文本过滤。
    if ":" not in text and "：" not in text:
        return not _is_low_signal_text(text)

    prefixes = [f"{name}:" for name in friend_names if name]
    if not prefixes:
        return False
    for line in text.splitlines():
        line = line.strip()
        matched_prefix = next((p for p in prefixes if line.startswith(p)), None)
        if matched_prefix is None:
            continue
        content = line[len(matched_prefix):].strip()
        if not _is_low_signal_text(content):
            return True
    return False


def _is_low_signal_text(text: str) -> bool:
    """纯媒体/表情占位符不能作为语气或回复证据。"""
    stripped = _PLACEHOLDER_TOKEN_RE.sub("", text)
    stripped = _STICKER_TOKEN_RE.sub("", stripped)
    stripped = _TRAILING_PARTIAL_STICKER_RE.sub("", stripped).strip()
    stripped = _NOISE_RE.sub("", stripped)
    return not stripped


def _is_echo_of_risky_query(*, query_text: str, candidate: str) -> bool:
    query_norm = _normalize_short_query(query_text)
    candidate_norm = _normalize_short_query(_PLACEHOLDER_TOKEN_RE.sub("", candidate))
    if query_norm not in _ECHO_RISK_QUERIES:
        return False
    return candidate_norm == query_norm


def _normalize_short_query(text: str) -> str:
    text = _NOISE_RE.sub("", text.strip())
    for old, new in _QUESTION_ALIAS.items():
        text = text.replace(old, new)
    return text


def _row_to_scored(row: dict[str, Any], *, rank: int, kind: str) -> ScoredChunk:
    """把 LanceDB 返回的行转为 ScoredChunk。

    LanceDB 在向量检索时返回 `_distance` 列，越小越近。
    """
    distance = row.get("_distance")
    if distance is None:
        base_score = 1.0
    else:
        base_score = max(0.0, 1.0 - float(distance))

    metadata = {k: v for k, v in row.items() if k not in {"vector"}}

    if kind == "response_pair":
        text = str(
            row.get("dialogue_snippet")
            or f"用户: {row.get('text') or ''}\n回复: {row.get('friend_reply') or ''}"
        )
    else:
        text = str(row.get("text") or "")

    return ScoredChunk(
        chunk_id=str(row.get("id") or ""),
        kind=kind,  # type: ignore[arg-type]
        text=text,
        score=base_score,
        rank=rank,
        timestamp_ms=int(
            row.get("timestamp_ms")
            or row.get("end_time_ms")
            or row.get("created_at_ms")
            or 0
        ),
        session_id=str(row.get("session_id") or ""),
        sender_name=str(row.get("sender_name") or ""),
        source=str(row.get("source") or "history"),  # type: ignore[arg-type]
        warmth=float(row.get("warmth") or 0.0),
        metadata=metadata,
    )


def _fuse(
    *,
    response_pairs: list[ScoredChunk],
    friend_examples: list[ScoredChunk],
    dialogue_windows: list[ScoredChunk],
    recent_live: list[ScoredChunk],
    settings: Settings,
    now_ms: int,
    final_k: int,
) -> list[ScoredChunk]:
    """RRF 融合三路结果。

    公式：
        rrf  = Σ 1 / (rrf_k + rank_i)（同一 chunk 在多路命中时加和）
        final = rrf * source_weight * recency * (1 + warmth * warmth_boost)
    """
    k = settings.rrf_k
    # 用 chunk_id 聚合
    aggregated: dict[str, dict[str, Any]] = {}

    def _add(chunk: ScoredChunk) -> None:
        agg = aggregated.setdefault(
            chunk.chunk_id,
            {
                "chunk": chunk,
                "rrf": 0.0,
                "ranks": [],
                "kinds": set(),
            },
        )
        agg["rrf"] += 1.0 / (k + chunk.rank)
        agg["ranks"].append(chunk.rank)
        agg["kinds"].add(chunk.kind)

    for c in response_pairs:
        _add(c)
    for c in friend_examples:
        _add(c)
    for c in dialogue_windows:
        _add(c)
    # live 语义召回也参与 fused，让"刚才聊到哪了"能被主 prompt 拿到。
    # 但权重受 source_weight 控制，ai_generated 默认 0.25 远低于真人原始。
    for c in recent_live:
        _add(c)

    fused: list[ScoredChunk] = []
    for entry in aggregated.values():
        chunk: ScoredChunk = entry["chunk"]
        rrf = entry["rrf"]

        # source weight
        if chunk.source == "ai_generated":
            src_w = settings.ai_generated_source_weight
        elif chunk.source in {"live", "user_new"}:
            src_w = settings.live_source_weight
        else:
            src_w = settings.history_source_weight

        # recency
        rec_w = recency_weight(
            chunk.timestamp_ms,
            half_life_days=settings.recency_half_life_days,
            max_boost=settings.recency_max_boost,
            now=now_ms,
        )

        # warmth
        warm = 1.0 + chunk.warmth * settings.warmth_boost

        pair_w = 1.35 if chunk.kind == "response_pair" else 1.0
        final_score = rrf * src_w * rec_w * warm * pair_w

        fused.append(
            ScoredChunk(
                chunk_id=chunk.chunk_id,
                kind=chunk.kind,
                text=chunk.text,
                score=final_score,
                rank=chunk.rank,  # 暂保留原始 rank，最终排序后会重新分配
                timestamp_ms=chunk.timestamp_ms,
                session_id=chunk.session_id,
                sender_name=chunk.sender_name,
                sender_role=chunk.sender_role,
                source=chunk.source,
                warmth=chunk.warmth,
                metadata={
                    **chunk.metadata,
                    "rrf_raw": round(rrf, 6),
                    "source_weight": src_w,
                    "recency_weight": round(rec_w, 4),
                    "warmth_factor": round(warm, 4),
                    "pair_weight": pair_w,
                    "hit_kinds": sorted(entry["kinds"]),
                },
            )
        )

    fused.sort(key=lambda c: c.score, reverse=True)
    # 重新分配最终 rank
    final = fused[:final_k]
    return [
        ScoredChunk(
            chunk_id=c.chunk_id,
            kind=c.kind,
            text=c.text,
            score=c.score,
            rank=i + 1,
            timestamp_ms=c.timestamp_ms,
            session_id=c.session_id,
            sender_name=c.sender_name,
            sender_role=c.sender_role,
            source=c.source,
            warmth=c.warmth,
            metadata=c.metadata,
        )
        for i, c in enumerate(final)
    ]
