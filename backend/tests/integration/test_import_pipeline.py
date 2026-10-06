"""端到端集成测试：缩减示例 JSON → parser → cleaner → splitter → chunker → embed(fake) → LanceDB。

不依赖外部 API；用 respx mock embedding 端点。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.core.errors import StoreError
from xuwen.core.models import FriendMessageChunk
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


def _fake_embedding_response(req: httpx.Request, settings: Settings) -> httpx.Response:
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


@pytest.mark.asyncio
async def test_import_douyin_chatlab_end_to_end(settings: Settings) -> None:
    sample_path = Path(__file__).resolve().parent.parent / "fixtures" / "sample_douyin_chatlab.json"

    async with httpx.AsyncClient() as raw:
        embedder = EmbeddingClient(settings, client=raw)
        store = MemoryStore(settings)
        await store.connect()
        store.ensure_tables()
        with respx.mock(base_url="https://embedding.test/v1") as router:
            router.post("/embeddings").mock(
                side_effect=lambda request: _fake_embedding_response(request, settings)
            )
            report = await import_history(
                sample_path,
                settings,
                store=store,
                embedder=embedder,
                update_circadian=False,
                update_proactive=False,
            )

    assert report.total_raw_messages == 9
    assert report.parsed_messages == 9
    assert report.friend_chunks == 2
    assert report.response_pairs == 2
    assert report.window_chunks > 0
    assert report.upserted_friend == report.friend_chunks
    assert report.upserted_window == report.window_chunks
    assert report.upserted_response_pairs == report.response_pairs


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


@pytest.mark.asyncio
async def test_import_after_embedding_dim_change_rebuilds_empty_tables(
    settings: Settings,
) -> None:
    """复现 #41：首次配置模式按默认 4096 维建了空表，向导里换成 8 维模型后导入应成功。"""
    first_boot = MemoryStore(settings.model_copy(update={"embedding_dim": 4096}))
    await first_boot.connect()
    first_boot.ensure_tables()
    sample_path = Path(__file__).resolve().parent.parent / "fixtures" / "sample_chat.json"

    async with httpx.AsyncClient() as raw:
        embedder = EmbeddingClient(settings, client=raw)
        with respx.mock(base_url="https://embedding.test/v1") as router:
            router.post("/embeddings").mock(
                side_effect=lambda request: _fake_embedding_response(request, settings)
            )
            # 不传 store：与配置向导一致，由 import_history 自建 store 并调用 ensure_tables
            report = await import_history(
                sample_path,
                settings,
                embedder=embedder,
                update_circadian=False,
                update_proactive=False,
            )

    assert report.friend_chunks > 0
    assert report.upserted_friend == report.friend_chunks
    assert report.upserted_window == report.window_chunks
    assert report.upserted_response_pairs == report.response_pairs


@pytest.mark.asyncio
async def test_import_with_existing_data_of_other_dim_fails_before_embedding(
    settings: Settings,
) -> None:
    """已有数据的表维度不一致：导入应直接说明原因，且不发出任何 embedding 请求。"""
    old_store = MemoryStore(settings.model_copy(update={"embedding_dim": 16}))
    await old_store.connect()
    old_store.ensure_tables()
    await old_store.upsert_friend_chunks(
        [
            FriendMessageChunk(
                chunk_id="old-1",
                message_id="m-old-1",
                session_id="s-old",
                seq=1,
                timestamp_ms=1000,
                text="旧模型写入的消息",
                dialogue_snippet="TestFriend: 旧模型写入的消息",
                context_before="",
                context_after="",
            )
        ],
        {"old-1": [0.1] * 16},
    )
    sample_path = Path(__file__).resolve().parent.parent / "fixtures" / "sample_chat.json"

    async with httpx.AsyncClient() as raw:
        embedder = EmbeddingClient(settings, client=raw)
        with respx.mock(
            base_url="https://embedding.test/v1",
            assert_all_called=False,
        ) as router:
            route = router.post("/embeddings").mock(
                side_effect=lambda request: _fake_embedding_response(request, settings)
            )
            with pytest.raises(StoreError, match="friend_messages.*EMBEDDING_DIM=8"):
                await import_history(
                    sample_path,
                    settings,
                    embedder=embedder,
                    update_circadian=False,
                    update_proactive=False,
                )

    assert route.call_count == 0
