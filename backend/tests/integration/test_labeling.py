"""端到端打标：导入 → label_all_unlabeled → 检查 LanceDB 字段写入。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from xuwen.config import Settings
from xuwen.core.models import FriendMessageChunk
from xuwen.memory.store import MemoryStore
from xuwen.persona.labeler import ChunkLabel
from xuwen.persona.labeling import label_all_unlabeled


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        embedding_dim=8,
        lance_db_path=tmp_path / "lancedb",
        labeling_enabled=True,
        label_api_url="https://label.test/v1",
        label_api_key="sk-test",  # type: ignore[arg-type]
        label_model="glm-4-flash",
        label_batch_size=2,
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
    )


def _vec() -> list[float]:
    return [0.1] * 8


def _chunk(cid: str, text: str) -> FriendMessageChunk:
    return FriendMessageChunk(
        chunk_id=cid,
        message_id=f"m-{cid}",
        session_id="s",
        seq=1,
        timestamp_ms=1000,
        text=text,
        dialogue_snippet=text,
        context_before="",
        context_after="",
    )


@pytest.mark.asyncio
async def test_label_all_unlabeled_writes_fields(settings: Settings):
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()

    # 写入 3 个未打标 chunk
    chunks = [
        _chunk("a", "在干嘛"),
        _chunk("b", "辛苦啦"),
        _chunk("c", "哈哈哈"),
    ]
    await store.upsert_friend_chunks(chunks, {c.chunk_id: _vec() for c in chunks})

    pending = await store.list_unlabeled_friend_chunks()
    assert len(pending) == 3

    # mock 打标 API：batch=2，应当被切成 2 次调用（2+1）
    seq = iter(
        [
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "labels": [
                                            {"mood": "调侃", "topic": "玩笑", "importance": 1},
                                            {"mood": "安慰", "topic": "鼓励", "importance": 2},
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            ),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"labels": [{"mood": "日常", "topic": "", "importance": 1}]}
                                )
                            }
                        }
                    ]
                },
            ),
        ]
    )

    with respx.mock(base_url="https://label.test/v1") as router:
        router.post("/chat/completions").mock(side_effect=lambda req: next(seq))
        report = await label_all_unlabeled(settings, store=store)

    assert report.total_unlabeled == 3
    assert report.labeled == 3
    assert report.batches == 2

    # 再查应该全标完了
    pending_after = await store.list_unlabeled_friend_chunks()
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_label_all_unlabeled_respects_max_concurrency(settings: Settings):
    settings = settings.model_copy(
        update={
            "label_batch_size": 1,
            "label_max_concurrency": 2,
        }
    )
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()

    chunks = [_chunk(str(i), f"msg-{i}") for i in range(5)]
    await store.upsert_friend_chunks(chunks, {c.chunk_id: _vec() for c in chunks})

    class SlowLabeler:
        active = 0
        max_active = 0

        async def label_messages(self, texts: list[str]) -> list[ChunkLabel]:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.02)
                return [
                    ChunkLabel(mood="日常", topic="", importance=1)
                    for _ in texts
                ]
            finally:
                self.active -= 1

    labeler = SlowLabeler()
    report = await label_all_unlabeled(settings, store=store, labeler=labeler)  # type: ignore[arg-type]

    assert report.labeled == 5
    assert report.batches == 5
    assert labeler.max_active == 2


@pytest.mark.asyncio
async def test_label_all_unlabeled_keeps_failed_batches_unlabeled(settings: Settings):
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()

    chunks = [_chunk("a", "在吗"), _chunk("b", "今天累了")]
    await store.upsert_friend_chunks(chunks, {c.chunk_id: _vec() for c in chunks})

    class FailingLabeler:
        async def label_messages(self, texts: list[str]) -> list[ChunkLabel]:
            raise RuntimeError("rate limited")

    report = await label_all_unlabeled(
        settings,
        store=store,
        labeler=FailingLabeler(),  # type: ignore[arg-type]
    )

    assert report.labeled == 0
    assert report.failed == 2
    pending_after = await store.list_unlabeled_friend_chunks()
    assert len(pending_after) == 2


@pytest.mark.asyncio
async def test_label_all_unlabeled_incremental(settings: Settings):
    """跑过一次后再加新 chunk，应该只标新的。"""
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()

    # 第一次：1 条
    c1 = _chunk("a", "在吗")
    await store.upsert_friend_chunks([c1], {c1.chunk_id: _vec()})
    with respx.mock(base_url="https://label.test/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"labels": [{"mood": "日常", "topic": "", "importance": 1}]}
                                )
                            }
                        }
                    ]
                },
            )
        )
        r1 = await label_all_unlabeled(settings, store=store)
    assert r1.labeled == 1

    # 第二次：再加 2 条
    c2 = _chunk("b", "今天累了")
    c3 = _chunk("c", "想睡觉")
    await store.upsert_friend_chunks(
        [c2, c3], {c2.chunk_id: _vec(), c3.chunk_id: _vec()}
    )

    call_count = [0]
    with respx.mock(base_url="https://label.test/v1") as router:
        def handler(req):
            call_count[0] += 1
            body = json.loads(req.read())
            user_text = body["messages"][-1]["content"]
            count = user_text.count("[")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "labels": [
                                            {"mood": "日常", "topic": "x", "importance": 1}
                                            for _ in range(count)
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        router.post("/chat/completions").mock(side_effect=handler)
        r2 = await label_all_unlabeled(settings, store=store)

    # 应该只标了新增的 2 条，老的 a 不重标
    assert r2.total_unlabeled == 2
    assert r2.labeled == 2


@pytest.mark.asyncio
async def test_label_all_unlabeled_disabled(settings: Settings):
    settings = settings.model_copy(update={"labeling_enabled": False})
    store = MemoryStore(settings)
    await store.connect()
    store.ensure_tables()
    report = await label_all_unlabeled(settings, store=store)
    assert report.total_unlabeled == 0
    assert report.batches == 0
