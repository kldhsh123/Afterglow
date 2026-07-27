from __future__ import annotations

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.core.models import ScoredChunk
from xuwen.memory.cross_reranker import CrossReranker


def _chunk(cid: str, text: str = "", *, kind: str = "friend") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=cid,
        kind=kind,  # type: ignore[arg-type]
        text=text or f"text-{cid}",
        score=1.0,
        rank=1,
        timestamp_ms=0,
        session_id="",
        source="human_original",
    )


def _settings(**overrides) -> Settings:
    base = {
        "cross_rerank_enabled": True,
        "cross_rerank_protocol": "jina",
        "cross_rerank_api_url": "https://cross.test/v1",
        "cross_rerank_api_key": "test-key",
        "cross_rerank_model": "test-reranker",
        "cross_rerank_input_k": 48,
        "cross_rerank_top_n": 3,
        "cross_rerank_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_cross_reranker_returns_passthrough_when_candidates_not_exceed_top_n():
    settings = _settings(cross_rerank_top_n=5)
    reranker = CrossReranker(settings)
    candidates = [_chunk("a"), _chunk("b")]

    result = await reranker.rerank(
        query_text="q", candidates=candidates, top_n=settings.cross_rerank_top_n
    )

    assert [c.chunk_id for c in result] == ["a", "b"]
    assert result[0].metadata["cross_rerank"] == "passthrough"
    await reranker.aclose()


@pytest.mark.asyncio
async def test_cross_reranker_jina_protocol_reorders_by_relevance():
    settings = _settings(cross_rerank_top_n=2)
    candidates = [_chunk("a"), _chunk("b"), _chunk("c"), _chunk("d")]

    with respx.mock(base_url="https://cross.test/v1") as router:
        route = router.post("/rerank").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 2, "relevance_score": 0.92},
                        {"index": 0, "relevance_score": 0.81},
                    ]
                },
            )
        )
        reranker = CrossReranker(settings)
        result = await reranker.rerank(
            query_text="q",
            candidates=candidates,
            top_n=settings.cross_rerank_top_n,
        )
        await reranker.aclose()

    assert route.called
    assert [c.chunk_id for c in result] == ["c", "a"]
    assert result[0].metadata["cross_rerank_score"] == pytest.approx(0.92)
    assert result[0].metadata["cross_rerank"] == "model"


@pytest.mark.asyncio
async def test_cross_reranker_dashscope_protocol_parses_nested_output():
    settings = _settings(
        cross_rerank_protocol="dashscope",
        cross_rerank_api_url="https://dashscope.test/api/v1",
        cross_rerank_top_n=2,
    )
    candidates = [_chunk("a"), _chunk("b"), _chunk("c")]

    with respx.mock(base_url="https://dashscope.test/api/v1") as router:
        route = router.post(
            "/services/rerank/text-rerank/text-rerank"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "output": {
                        "results": [
                            {"index": 1, "relevance_score": 0.7},
                            {"index": 2, "relevance_score": 0.5},
                        ]
                    },
                    "usage": {"total_tokens": 100},
                },
            )
        )
        reranker = CrossReranker(settings)
        result = await reranker.rerank(
            query_text="q",
            candidates=candidates,
            top_n=settings.cross_rerank_top_n,
        )
        await reranker.aclose()

    assert route.called
    assert [c.chunk_id for c in result] == ["b", "c"]


@pytest.mark.asyncio
async def test_cross_reranker_fails_open_on_http_error():
    settings = _settings(cross_rerank_top_n=2)
    candidates = [_chunk("a"), _chunk("b"), _chunk("c")]

    with respx.mock(base_url="https://cross.test/v1") as router:
        router.post("/rerank").mock(return_value=httpx.Response(500))
        reranker = CrossReranker(settings)
        result = await reranker.rerank(
            query_text="q",
            candidates=candidates,
            top_n=settings.cross_rerank_top_n,
        )
        await reranker.aclose()

    assert [c.chunk_id for c in result] == ["a", "b"]
    assert result[0].metadata["cross_rerank"] == "error"


@pytest.mark.asyncio
async def test_cross_reranker_fills_missing_indices():
    """上游漏返候选时，按原顺序补齐到 top_n，避免下游候选不够用。"""
    settings = _settings(cross_rerank_top_n=3)
    candidates = [_chunk("a"), _chunk("b"), _chunk("c"), _chunk("d")]

    with respx.mock(base_url="https://cross.test/v1") as router:
        router.post("/rerank").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"index": 3, "relevance_score": 0.9}]},
            )
        )
        reranker = CrossReranker(settings)
        result = await reranker.rerank(
            query_text="q",
            candidates=candidates,
            top_n=settings.cross_rerank_top_n,
        )
        await reranker.aclose()

    # d 第一（模型返回），a、b 按原序补齐
    assert [c.chunk_id for c in result] == ["d", "a", "b"]
    assert result[0].metadata["cross_rerank"] == "model"
    assert result[1].metadata["cross_rerank"] == "fill"


@pytest.mark.asyncio
async def test_cross_reranker_response_pair_text_includes_friend_reply():
    """response_pair 的文档文本应该把 user/TA 都拼上，cross-encoder 才能正确评分。"""
    settings = _settings(cross_rerank_top_n=1)
    pair = _chunk("p", kind="response_pair")
    pair.metadata["text"] = "我好累"
    pair.metadata["friend_reply"] = "抱抱 早点休息"
    candidates = [pair, _chunk("x"), _chunk("y")]

    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.99}]}
        )

    with respx.mock(base_url="https://cross.test/v1") as router:
        router.post("/rerank").mock(side_effect=_capture)
        reranker = CrossReranker(settings)
        await reranker.rerank(
            query_text="累",
            candidates=candidates,
            top_n=settings.cross_rerank_top_n,
        )
        await reranker.aclose()

    docs = captured["body"]["documents"]
    assert "我好累" in docs[0]
    assert "抱抱" in docs[0]
