from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.core.models import ScoredChunk
from xuwen.memory.reranker import QueryRewriter, SemanticReranker


def _chunk(cid: str, score: float, *, session_id: str = "") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=cid,
        kind="friend",
        text=f"text-{cid}",
        score=score,
        rank=1,
        timestamp_ms=0,
        session_id=session_id,
        source="human_original",
    )


@pytest.mark.asyncio
async def test_query_rewriter_keeps_original_first_and_dedupes():
    settings = Settings(
        query_rewrite_enabled=True,
        query_rewrite_max_variants=2,
    )
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(
        return_value='{"queries":["陪伴 安慰 历史回复","想你时怎么回复","想你时怎么回复"]}'
    )
    rewriter = QueryRewriter(settings, llm)

    queries = await rewriter.rewrite("想你了")

    assert queries == ["想你了", "陪伴 安慰 历史回复", "想你时怎么回复"]


@pytest.mark.asyncio
async def test_semantic_reranker_blends_model_scores_and_reassigns_ranks():
    settings = Settings(
        rerank_enabled=True,
        rerank_mode="always",
        rerank_min_candidates=1,
        rerank_top_k=3,
        rerank_weight=0.8,
    )
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(return_value='{"ranked":[{"id":"b","score":1.0},{"id":"a","score":0.1}]}')
    reranker = SemanticReranker(settings, llm)

    result = await reranker.rerank(
        query_text="我好累",
        candidates=[_chunk("a", 1.0), _chunk("b", 0.5), _chunk("c", 0.4)],
        final_k=2,
    )

    assert [c.chunk_id for c in result] == ["b", "a"]
    assert [c.rank for c in result] == [1, 2]
    assert result[0].metadata["rerank"] == "model"


@pytest.mark.asyncio
async def test_semantic_reranker_falls_back_on_invalid_json():
    settings = Settings(
        rerank_enabled=True,
        rerank_mode="always",
        rerank_min_candidates=1,
    )
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(return_value="not json")
    reranker = SemanticReranker(settings, llm)

    result = await reranker.rerank(
        query_text="q",
        candidates=[_chunk("a", 1.0), _chunk("b", 0.5)],
        final_k=2,
    )

    assert [c.chunk_id for c in result] == ["a", "b"]
    assert result[0].metadata["rerank"] == "invalid_json"


@pytest.mark.asyncio
async def test_semantic_reranker_auto_skips_when_candidates_too_few():
    settings = Settings(
        rerank_enabled=True,
        rerank_mode="auto",
        rerank_min_candidates=3,
    )
    llm = AsyncMock()
    llm.complete_chat = AsyncMock()
    reranker = SemanticReranker(settings, llm)

    result = await reranker.rerank(
        query_text="想你了",
        candidates=[_chunk("a", 1.0), _chunk("b", 0.5)],
        final_k=2,
    )

    llm.complete_chat.assert_not_awaited()
    assert [c.chunk_id for c in result] == ["a", "b"]
    assert result[0].metadata["rerank"] == "skipped"
