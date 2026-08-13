"""embedder 单测：批处理、维度校验、错误重试。

用 respx 拦截 httpx 请求，避免真实网络。
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.core.errors import EmbeddingError
from xuwen.ingestion.embedder import EmbeddingClient, _AsyncSlidingWindowRateLimiter

EMB_BASE = "https://embedding.test/v1"


def _settings(dim: int = 8) -> Settings:
    return Settings(
        embedding_api_url=EMB_BASE,
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        embedding_model="Qwen3-Embedding-8B",
        embedding_dim=dim,
    )


def _embedding_response(batch: list[str], dim: int = 8) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": [0.1 * (i + 1)] * dim}
            for i in range(len(batch))
        ],
        "model": "Qwen3-Embedding-8B",
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@pytest.mark.asyncio
async def test_embed_texts_returns_in_order():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            router.post("/embeddings").mock(
                side_effect=lambda req: httpx.Response(
                    200, json=_embedding_response(["a", "b", "c"], dim=8)
                )
            )
            vecs = await client.embed_texts(["a", "b", "c"], batch_size=10)
        assert len(vecs) == 3
        assert all(len(v) == 8 for v in vecs)


@pytest.mark.asyncio
async def test_embed_sends_dimensions_field_by_default():
    """默认 EMBEDDING_SEND_DIMENSIONS=true → 请求体里必须有 dimensions=embedding_dim。
    这是为了支持 MRL 服务（OpenAI text-embedding-3 / DashScope / Qwen3 网关）
    显式指定输出维度，避免上游用默认值降到 1024 之类。"""
    settings = _settings(dim=4096)
    captured: list[dict] = []
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            def _handler(req: httpx.Request) -> httpx.Response:
                import json as _json
                captured.append(_json.loads(req.content.decode("utf-8")))
                return httpx.Response(200, json=_embedding_response(["a"], dim=4096))

            router.post("/embeddings").mock(side_effect=_handler)
            await client.embed_texts(["a"], batch_size=1)

    assert len(captured) == 1
    assert captured[0].get("dimensions") == 4096


@pytest.mark.asyncio
async def test_embed_omits_dimensions_when_disabled():
    """老网关不识别 dimensions 字段时，关掉开关 → 请求体里不再含该字段。"""
    settings = Settings(
        embedding_api_url=EMB_BASE,
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        embedding_model="Qwen3-Embedding-8B",
        embedding_dim=1024,
        embedding_send_dimensions=False,
    )
    captured: list[dict] = []
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            def _handler(req: httpx.Request) -> httpx.Response:
                import json as _json
                captured.append(_json.loads(req.content.decode("utf-8")))
                return httpx.Response(200, json=_embedding_response(["a"], dim=1024))

            router.post("/embeddings").mock(side_effect=_handler)
            await client.embed_texts(["a"], batch_size=1)

    assert "dimensions" not in captured[0]


@pytest.mark.asyncio
async def test_embed_texts_batches_correctly():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            calls: list[int] = []

            def handle(req: httpx.Request) -> httpx.Response:
                body = req.read()
                import json as _json

                inputs = _json.loads(body)["input"]
                calls.append(len(inputs))
                return httpx.Response(200, json=_embedding_response(inputs))

            router.post("/embeddings").mock(side_effect=handle)
            await client.embed_texts(["t"] * 7, batch_size=3)
        # 7 条按 batch_size=3 切分：3 + 3 + 1
        assert sorted(calls) == [1, 3, 3]


@pytest.mark.asyncio
async def test_embed_uses_configured_max_concurrency():
    settings = Settings(
        embedding_api_url=EMB_BASE,
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        embedding_model="Qwen3-Embedding-8B",
        embedding_dim=8,
        embedding_max_concurrency=1,
    )
    active = 0
    max_active = 0

    async def handle(req: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        inputs = json.loads(req.content.decode("utf-8"))["input"]
        batch = inputs if isinstance(inputs, list) else [inputs]

        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

        return httpx.Response(200, json=_embedding_response(batch))

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EmbeddingClient(settings, client=raw)
        await client.embed_texts(["a", "b", "c"], batch_size=1)

    assert max_active == 1


@pytest.mark.asyncio
async def test_embedding_rate_limiter_waits_after_window_is_full():
    limiter = _AsyncSlidingWindowRateLimiter(max_requests=2, window_seconds=0.05)

    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()

    assert time.monotonic() - start >= 0.04


@pytest.mark.asyncio
async def test_embed_dim_mismatch_raises():
    settings = _settings(dim=8)
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            router.post("/embeddings").mock(
                return_value=httpx.Response(200, json=_embedding_response(["x"], dim=4))
            )
            with pytest.raises(EmbeddingError):
                await client.embed_texts(["x"])


@pytest.mark.asyncio
async def test_embed_retries_on_5xx_then_succeeds():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            seq = iter(
                [
                    httpx.Response(503, text="upstream busy"),
                    httpx.Response(200, json=_embedding_response(["x"])),
                ]
            )
            router.post("/embeddings").mock(side_effect=lambda req: next(seq))
            vecs = await client.embed_texts(["x"])
        assert len(vecs) == 1


@pytest.mark.asyncio
async def test_embed_4xx_not_retried():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            route = router.post("/embeddings").mock(
                return_value=httpx.Response(400, text="bad input")
            )
            with pytest.raises(EmbeddingError):
                await client.embed_texts(["x"])
            assert route.call_count == 1  # 4xx 不重试


@pytest.mark.asyncio
async def test_embed_empty_input_returns_empty():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        assert await client.embed_texts([]) == []


def test_resolve_endpoint_does_not_double_append():
    """如果用户在 EMBEDDING_API_URL 里已经写了 /embeddings，不应重复拼接。"""
    from xuwen.ingestion.embedder import _resolve_endpoint

    assert (
        _resolve_endpoint("https://ai.gitee.com/v1", "/embeddings")
        == "https://ai.gitee.com/v1/embeddings"
    )
    assert (
        _resolve_endpoint("https://ai.gitee.com/v1/embeddings", "/embeddings")
        == "https://ai.gitee.com/v1/embeddings"
    )
    assert (
        _resolve_endpoint("https://ai.gitee.com/v1/embeddings/", "/embeddings")
        == "https://ai.gitee.com/v1/embeddings"
    )


@pytest.mark.asyncio
async def test_embed_does_not_leak_response_body_on_4xx():
    """4xx 错误信息不应包含响应正文（避免泄露 input 或 key 详情）。"""
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            sensitive = "INVALID_KEY_sk_abc123_DO_NOT_LEAK"
            router.post("/embeddings").mock(
                return_value=httpx.Response(401, text=sensitive)
            )
            with pytest.raises(EmbeddingError) as exc:
                await client.embed_texts(["x"])
            assert sensitive not in str(exc.value)
            assert sensitive not in str(exc.value.detail or "")


@pytest.mark.asyncio
async def test_embed_non_json_response_wrapped():
    """非 JSON 响应应该被包装成 EmbeddingError，不抛裸 JSONDecodeError。"""
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            router.post("/embeddings").mock(
                return_value=httpx.Response(200, text="<html>404 not found</html>")
            )
            with pytest.raises(EmbeddingError) as exc:
                await client.embed_texts(["x"])
            assert "非 JSON" in str(exc.value)


@pytest.mark.asyncio
async def test_embed_malformed_data_array():
    """data 字段不是数组时应该报清晰错误。"""
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            router.post("/embeddings").mock(
                return_value=httpx.Response(200, json={"data": "not-a-list"})
            )
            with pytest.raises(EmbeddingError):
                await client.embed_texts(["x"])


@pytest.mark.asyncio
async def test_embed_texts_tolerant_skips_failed_batch():
    """中间批次重试耗尽 → 对应位置为 None，其余批次正常返回，不抛异常。"""
    settings = Settings(
        embedding_api_url=EMB_BASE,
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        embedding_model="Qwen3-Embedding-8B",
        embedding_dim=8,
        embedding_retry_attempts=1,  # 立即耗尽，避免测试等待退避
    )
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            def handle(req: httpx.Request) -> httpx.Response:
                inputs = json.loads(req.content.decode("utf-8"))["input"]
                if "bad" in inputs:
                    return httpx.Response(500, text="boom")
                return httpx.Response(200, json=_embedding_response(inputs))

            router.post("/embeddings").mock(side_effect=handle)
            result = await client.embed_texts_tolerant(
                ["a", "b", "bad", "bad2", "c", "d"], batch_size=2
            )

    assert result.total_batches == 3
    assert result.failed_batches == 1
    assert result.last_error is not None
    assert len(result.vectors) == 6
    assert result.vectors[2] is None and result.vectors[3] is None
    ok = [v for i, v in enumerate(result.vectors) if i not in (2, 3)]
    assert all(v is not None and len(v) == 8 for v in ok)


@pytest.mark.asyncio
async def test_embed_texts_tolerant_4xx_batch_skipped_without_retry():
    """4xx 在 tolerant 模式下同样跳过（不重试），交由调用方的连续失败判定兜底。"""
    settings = _settings()
    calls = 0
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            def handle(req: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                return httpx.Response(401, text="bad key")

            router.post("/embeddings").mock(side_effect=handle)
            result = await client.embed_texts_tolerant(["x"], batch_size=1)

    assert calls == 1  # 4xx 不重试
    assert result.failed_batches == 1 and result.total_batches == 1
    assert result.vectors == [None]


@pytest.mark.asyncio
async def test_embed_texts_tolerant_all_success_matches_embed_texts():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url=EMB_BASE) as router:
            def handle(req: httpx.Request) -> httpx.Response:
                inputs = json.loads(req.content.decode("utf-8"))["input"]
                return httpx.Response(200, json=_embedding_response(inputs))

            router.post("/embeddings").mock(side_effect=handle)
            strict_vecs = await client.embed_texts(["a", "b", "c"], batch_size=2)
            result = await client.embed_texts_tolerant(["a", "b", "c"], batch_size=2)

    assert result.failed_batches == 0
    assert result.total_batches == 2
    assert result.vectors == strict_vecs


@pytest.mark.asyncio
async def test_embed_texts_tolerant_empty_input():
    settings = _settings()
    async with httpx.AsyncClient() as raw:
        client = EmbeddingClient(settings, client=raw)
        result = await client.embed_texts_tolerant([])
    assert result.vectors == []
    assert result.total_batches == 0 and result.failed_batches == 0
