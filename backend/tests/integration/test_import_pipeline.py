"""端到端集成测试：缩减示例 JSON → parser → cleaner → splitter → chunker → embed(fake) → LanceDB。

不依赖外部 API；用 respx mock embedding 端点。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.ingestion.embedder import EmbeddingClient
from xuwen.ingestion.importer import import_history
from xuwen.memory.store import MemoryStore


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        self_name="Me",
        self_uid="uid-self-001",
        friend_name="TestFriend",
        friend_uid="uid-friend-001",
        relationship_type="friend",
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        embedding_api_url="https://embedding.test/v1",
        embedding_api_key="sk-test",  # type: ignore[arg-type]
        chat_model="dummy",
        openai_api_key="sk-test",  # type: ignore[arg-type]
        window_size=4,
        window_overlap=1,
        single_context_before=2,
        single_context_after=1,
        enable_pii_redaction=True,
    )


@pytest.mark.asyncio
async def test_import_history_end_to_end(settings: Settings, tmp_path):
    """端到端跑通：fixtures/sample_chat.json → LanceDB。"""
    sample_path = (
        tmp_path.parent.parent.parent
        / "fixtures"
        / "sample_chat.json"
    )
    # 上面的相对路径不稳，直接用绝对路径
    from pathlib import Path as _P

    sample_path = _P(__file__).resolve().parent.parent / "fixtures" / "sample_chat.json"

    def _fake_embedding(req: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(req.read())
        inputs = body["input"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "index": i,
                        "embedding": [float(i + 1) * 0.01] * settings.embedding_dim,
                    }
                    for i in range(len(inputs))
                ],
                "model": settings.embedding_model,
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )

    async with httpx.AsyncClient() as raw:
        embedder = EmbeddingClient(settings, client=raw)
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
        with respx.mock(base_url="https://embedding.test/v1") as router:
            router.post("/embeddings").mock(side_effect=_fake_embedding)
            report = await import_history(
                sample_path,
                settings,
                store=store,
                embedder=embedder,
            )

    assert report.parsed_messages > 0
    assert report.sessions >= 1
    assert report.friend_chunks > 0
    assert report.upserted_friend > 0
    assert report.upserted_window > 0
    assert report.upserted_response_pairs > 0
    # 双索引数量与 chunk 数量一致
    assert report.embedded_friend == report.friend_chunks
    assert report.embedded_window == report.window_chunks
    assert report.embedded_response_pairs == report.response_pairs

    stats = await store.stats()
    assert stats.friend_messages == report.upserted_friend
    assert stats.dialogue_windows == report.upserted_window
    assert stats.response_pairs == report.upserted_response_pairs


@pytest.mark.asyncio
async def test_import_yields_no_friend_chunks_when_friend_uid_wrong(settings: Settings, tmp_path):
    """若把 friend_uid 故意设错，friend chunks 应为 0，对话窗口仍正常产出。"""
    from pathlib import Path as _P

    sample_path = _P(__file__).resolve().parent.parent / "fixtures" / "sample_chat.json"
    settings = settings.model_copy(update={"friend_uid": "uid-not-exist"})

    def _fake_embedding(req: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(req.read())
        inputs = body["input"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": [0.1] * settings.embedding_dim}
                    for i in range(len(inputs))
                ],
                "model": settings.embedding_model,
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )

    async with httpx.AsyncClient() as raw:
        embedder = EmbeddingClient(settings, client=raw)
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
        with respx.mock(base_url="https://embedding.test/v1") as router:
            router.post("/embeddings").mock(side_effect=_fake_embedding)
            report = await import_history(
                sample_path,
                settings,
                store=store,
                embedder=embedder,
            )
    assert report.friend_chunks == 0
    assert report.upserted_friend == 0
    assert report.window_chunks > 0
    assert report.response_pairs == 0
