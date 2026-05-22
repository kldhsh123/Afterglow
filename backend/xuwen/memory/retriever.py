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

import logging
import re
from dataclasses import dataclass
from typing import Any

from xuwen.config import Settings
from xuwen.core.errors import RetrievalError
from xuwen.core.models import RetrievalQuery, RetrievalResult, ScoredChunk
from xuwen.core.time import now_ms, recency_weight
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_RETRIEVAL_OVERFETCH = 4
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
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """执行混合检索并返回融合结果。"""
        if not query.query_text.strip():
            raise RetrievalError("query_text 不能为空")

        # 1) 把查询文本向量化
        try:
            vector = await self.embedder.embed_one(query.query_text)
        except Exception as e:
            # 把 embedder 的失败包装成 RetrievalError，方便上层统一处理
            raise RetrievalError(f"查询向量化失败：{type(e).__name__}") from e

        top_k_friend = query.top_k_friend or self.settings.friend_top_k
        top_k_pair = self.settings.response_pair_top_k
        top_k_window = query.top_k_window or self.settings.window_top_k
        final_k = query.final_k or self.settings.final_context_k
        now = query.now_ms or now_ms()

        # 2) 三路召回
        pair_rows = _filter_response_pair_rows(
            await self.store.search_response_pairs(
                vector,
                top_k_pair * _RETRIEVAL_OVERFETCH,
            ),
            limit=top_k_pair,
        )
        friend_rows = _filter_friend_rows(
            await self.store.search_friend(
                vector,
                top_k_friend * _RETRIEVAL_OVERFETCH,
            ),
            query_text=query.query_text,
            limit=top_k_friend,
        )
        window_rows = _filter_window_rows(
            await self.store.search_windows(
                vector,
                top_k_window * _RETRIEVAL_OVERFETCH,
            ),
            friend_names=self.settings.all_friend_names or ["TA"],
            limit=top_k_window,
        )
        # live 层语义召回：跨会话排除 ai_generated（除非 long_term_enabled）
        live_top_k = self.settings.live_top_k
        live_filter = _compute_live_filter(query, self.settings)
        live_semantic_rows = await self.store.search_live(
            vector,
            live_top_k * _RETRIEVAL_OVERFETCH,
            extra_filter=live_filter,
        )
        live_rows: list[dict[str, Any]] = []
        if query.conversation_id:
            live_rows = _filter_live_rows(
                await self.store.recent_live(query.conversation_id, limit=20),
                limit=20,
            )
        # 把语义召回的 live 行合并到 live_rows（保证 fused 也能命中 live）
        live_semantic = _filter_live_rows(live_semantic_rows, limit=live_top_k)
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

        # 4) RRF 融合 + boost
        fused = _fuse(
            response_pairs=response_pairs,
            friend_examples=friend_examples,
            dialogue_windows=dialogue_windows,
            recent_live=recent_live,
            settings=self.settings,
            now_ms=now,
            final_k=final_k,
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
