"""hybrid retriever 单测：多路召回、RRF 融合、boost。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.core.errors import RetrievalError
from xuwen.core.models import RetrievalQuery, ScoredChunk
from xuwen.core.time import now_ms
from xuwen.memory.retriever import HybridRetriever


def _settings(**overrides):
    defaults = dict(
        embedding_dim=8,
        friend_top_k=5,
        window_top_k=5,
        final_context_k=4,
        rrf_k=60,
        recency_half_life_days=30,
        recency_max_boost=0.15,
        warmth_boost=0.12,
        live_source_weight=1.08,
        history_source_weight=1.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _fake_embedder(vec):
    emb = AsyncMock()
    emb.embed_one = AsyncMock(return_value=vec)
    emb.embed_texts = AsyncMock(return_value=[vec])
    return emb


def _fake_store(friend_rows, window_rows, live_rows=None, pair_rows=None):
    store = AsyncMock()
    store.search_response_pairs = AsyncMock(return_value=pair_rows or [])
    store.search_friend = AsyncMock(return_value=friend_rows)
    store.search_windows = AsyncMock(return_value=window_rows)
    store.search_live = AsyncMock(return_value=live_rows or [])
    store.recent_live = AsyncMock(return_value=live_rows or [])
    return store


def _row(
    chunk_id, text, *, distance=0.1, ts=None, source="history", warmth=0.0, **extra
):
    return {
        "id": chunk_id,
        "text": text,
        "_distance": distance,
        "timestamp_ms": ts if ts is not None else now_ms(),
        "session_id": "sess-x",
        "source": source,
        "warmth": warmth,
        **extra,
    }


def _pair_row(chunk_id, user_text, friend_reply, *, distance=0.1, ts=None):
    return _row(
        chunk_id,
        user_text,
        distance=distance,
        ts=ts,
        friend_reply=friend_reply,
        dialogue_snippet=f"Me: {user_text}\nTA: {friend_reply}",
    )


def _scored(chunk_id: str) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        kind="friend",
        text=chunk_id,
        score=1.0,
        rank=1,
        timestamp_ms=now_ms(),
    )


@pytest.mark.asyncio
async def test_retrieve_returns_top_final_k():
    settings = _settings(final_context_k=2)
    friend = [_row(f"f{i}", f"text{i}") for i in range(5)]
    windows = [_row(f"w{i}", f"win{i}") for i in range(3)]
    retriever = HybridRetriever(
        settings,
        store=_fake_store(friend, windows),
        embedder=_fake_embedder([0.1] * 8),
    )
    result = await retriever.retrieve(RetrievalQuery(query_text="hi"))
    assert len(result.fused) == 2
    assert len(result.friend_examples) == 5
    assert len(result.dialogue_windows) == 3
    # fused 顺序按分数降序
    assert result.fused[0].score >= result.fused[-1].score


@pytest.mark.asyncio
async def test_retrieve_filters_low_signal_hits():
    settings = _settings(
        self_name="Me",
        friend_name="TA",
        friend_top_k=5,
        window_top_k=5,
        final_context_k=10,
    )
    friend = [
        _row("img", "[图片]"),
        _row("qq-face", "[[呜呜呜]]"),
        _row("echo", "[图片]在干嘛"),
        _row("good", "刚在打游戏"),
    ]
    windows = [
        _row("self-only", "Me: [图片]\nMe: 在干嘛"),
        _row("friend-placeholder", "Me: 在干嘛\nTA: [图片]"),
        _row("friend-good", "Me: 在干嘛\nTA: 刚醒"),
    ]
    retriever = HybridRetriever(
        settings,
        store=_fake_store(friend, windows),
        embedder=_fake_embedder([0.1] * 8),
    )

    result = await retriever.retrieve(RetrievalQuery(query_text="你在干什么"))

    assert [c.chunk_id for c in result.friend_examples] == ["good"]
    assert [c.chunk_id for c in result.dialogue_windows] == ["friend-good"]
    assert {c.chunk_id for c in result.fused} == {"good", "friend-good"}


@pytest.mark.asyncio
async def test_retrieve_response_pairs_are_style_evidence():
    settings = _settings(self_name="Me", friend_name="TA", final_context_k=5)
    retriever = HybridRetriever(
        settings,
        store=_fake_store(
            friend_rows=[],
            window_rows=[],
            pair_rows=[_pair_row("p1", "你在干嘛", "刚吃完饭，躺着")],
        ),
        embedder=_fake_embedder([0.1] * 8),
    )

    result = await retriever.retrieve(RetrievalQuery(query_text="你在干什么"))

    assert result.response_pairs[0].chunk_id == "p1"
    assert result.friend_examples[0].kind == "response_pair"
    assert result.friend_examples[0].metadata["friend_reply"] == "刚吃完饭，躺着"
    assert result.fused[0].kind == "response_pair"


@pytest.mark.asyncio
async def test_retrieve_uses_query_rewrite_variants():
    settings = _settings(final_context_k=10)
    embedder = _fake_embedder([0.1] * 8)
    embedder.embed_texts = AsyncMock(return_value=[[0.1] * 8, [0.2] * 8])
    store = _fake_store([], [])
    store.search_friend = AsyncMock(
        side_effect=[
            [_row("a", "原始 query 命中")],
            [_row("b", "改写 query 命中")],
        ]
    )
    rewriter = AsyncMock()
    rewriter.rewrite = AsyncMock(return_value=["想你了", "历史里表达想念时怎么回复"])
    retriever = HybridRetriever(
        settings,
        store=store,
        embedder=embedder,
        query_rewriter=rewriter,
    )

    result = await retriever.retrieve(RetrievalQuery(query_text="想你了"))

    assert store.search_friend.await_count == 2
    assert {c.chunk_id for c in result.fused} >= {"a", "b"}


@pytest.mark.asyncio
async def test_retrieve_delegates_final_pool_to_reranker():
    settings = _settings(final_context_k=1)
    reranker = AsyncMock()
    reranker.candidate_limit = lambda final_k: 4
    reranker.rerank = AsyncMock(
        return_value=[
            _scored("b"),
        ]
    )
    retriever = HybridRetriever(
        settings,
        store=_fake_store([_row("a", "a"), _row("b", "b")], []),
        embedder=_fake_embedder([0.1] * 8),
        reranker=reranker,
    )

    result = await retriever.retrieve(RetrievalQuery(query_text="q"))

    reranker.rerank.assert_awaited_once()
    assert [c.chunk_id for c in result.fused] == ["b"]


@pytest.mark.asyncio
async def test_retrieve_dedupes_chunks_across_routes():
    """同一 chunk_id 在多路命中时应聚合 RRF 分数，不重复出现。"""
    settings = _settings(final_context_k=10)
    common_id = "shared"
    friend = [_row(common_id, "shared text")]
    windows = [_row(common_id, "shared text")]
    retriever = HybridRetriever(
        settings,
        store=_fake_store(friend, windows),
        embedder=_fake_embedder([0.1] * 8),
    )
    result = await retriever.retrieve(RetrievalQuery(query_text="q"))
    ids = [c.chunk_id for c in result.fused]
    assert ids.count(common_id) == 1
    # 命中 2 路应该体现在 hit_kinds 中
    fused_chunk = next(c for c in result.fused if c.chunk_id == common_id)
    assert set(fused_chunk.metadata["hit_kinds"]) == {"friend", "window"}


@pytest.mark.asyncio
async def test_recency_boost_favors_recent_chunks():
    """同样的 RRF 分下，更近的 chunk 应排到前面。"""
    settings = _settings(recency_half_life_days=10, recency_max_boost=0.5)
    now = now_ms()
    old = now - 365 * 86400 * 1000  # 一年前
    fresh = now - 1 * 86400 * 1000  # 1 天前
    friend = [
        _row("old", "x", ts=old, distance=0.1),
        _row("fresh", "y", ts=fresh, distance=0.1),
    ]
    retriever = HybridRetriever(
        settings,
        store=_fake_store(friend, []),
        embedder=_fake_embedder([0.1] * 8),
    )
    result = await retriever.retrieve(
        RetrievalQuery(query_text="q", now_ms=now)
    )
    # fresh 应在 old 前
    chunk_ids = [c.chunk_id for c in result.fused]
    assert chunk_ids.index("fresh") < chunk_ids.index("old")


@pytest.mark.asyncio
async def test_warmth_boost_favors_warm_chunks():
    settings = _settings(warmth_boost=0.5)
    now = now_ms()
    friend = [
        _row("warm", "x", ts=now, warmth=1.0),
        _row("cold", "y", ts=now, warmth=0.0),
    ]
    retriever = HybridRetriever(
        settings,
        store=_fake_store(friend, []),
        embedder=_fake_embedder([0.1] * 8),
    )
    result = await retriever.retrieve(RetrievalQuery(query_text="q", now_ms=now))
    chunk_ids = [c.chunk_id for c in result.fused]
    assert chunk_ids.index("warm") < chunk_ids.index("cold")


@pytest.mark.asyncio
async def test_recent_live_only_fetched_with_conversation_id():
    settings = _settings()
    store = _fake_store([], [], live_rows=[_row("l1", "live", source="live")])
    retriever = HybridRetriever(
        settings, store=store, embedder=_fake_embedder([0.1] * 8)
    )
    # 没 conversation_id：不调 recent_live
    await retriever.retrieve(RetrievalQuery(query_text="q"))
    store.recent_live.assert_not_awaited()
    store.search_live.assert_awaited_once()

    # 给了 conversation_id：调用
    result = await retriever.retrieve(
        RetrievalQuery(query_text="q", conversation_id="conv-1")
    )
    store.recent_live.assert_awaited_once()
    assert any(c.chunk_id == "l1" for c in result.recent_live)
    assert any(c.kind == "live" for c in result.fused)


@pytest.mark.asyncio
async def test_recent_live_filters_low_signal_and_proactive_marker():
    settings = _settings(final_context_k=10)
    live_rows = [
        _row("partial-sticker", "[sticker:e7e", source="live"),
        _row("sticker-only", "[sticker:e7e73914fab0aadf4f1d8f0a1f39271d]", source="live"),
        _row("proactive-marker", "（AI 主动开启话题）", source="live"),
        _row("assistant-old", "我之前这么回复过", source="live", role="assistant"),
        _row("assistant-ai", "这是之前 AI 说过的话", source="ai_generated", role="assistant"),
        _row("good", "昨天迷迷糊糊的", source="live"),
    ]
    retriever = HybridRetriever(
        settings,
        store=_fake_store([], [], live_rows=live_rows),
        embedder=_fake_embedder([0.1] * 8),
    )

    result = await retriever.retrieve(
        RetrievalQuery(query_text="最难忘的一次是什么", conversation_id="conv-1")
    )

    assert [c.chunk_id for c in result.recent_live] == ["assistant-ai", "good"]
    assert [c.chunk_id for c in result.fused] == ["good", "assistant-ai"]


@pytest.mark.asyncio
async def test_ai_generated_long_term_filter_defaults_to_same_conversation():
    settings = _settings(ai_generated_long_term_enabled=False)
    store = _fake_store([], [], live_rows=[])
    retriever = HybridRetriever(
        settings,
        store=store,
        embedder=_fake_embedder([0.1] * 8),
    )

    await retriever.retrieve(RetrievalQuery(query_text="q"))
    assert store.search_live.await_args.kwargs["extra_filter"] == "source != 'ai_generated'"

    await retriever.retrieve(RetrievalQuery(query_text="q", conversation_id="conv-1"))
    extra_filter = store.search_live.await_args.kwargs["extra_filter"]
    assert "source != 'ai_generated'" in extra_filter
    assert "conversation_id = 'conv-1'" in extra_filter


@pytest.mark.asyncio
async def test_ai_generated_long_term_enabled_removes_semantic_filter():
    settings = _settings(ai_generated_long_term_enabled=True)
    store = _fake_store([], [], live_rows=[])
    retriever = HybridRetriever(
        settings,
        store=store,
        embedder=_fake_embedder([0.1] * 8),
    )

    await retriever.retrieve(RetrievalQuery(query_text="q"))

    assert store.search_live.await_args.kwargs["extra_filter"] is None


@pytest.mark.asyncio
async def test_empty_query_raises():
    settings = _settings()
    retriever = HybridRetriever(
        settings,
        store=_fake_store([], []),
        embedder=_fake_embedder([0.1] * 8),
    )
    with pytest.raises(RetrievalError):
        await retriever.retrieve(RetrievalQuery(query_text="   "))
