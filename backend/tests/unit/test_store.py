"""LanceDB store 单测：建表、upsert、检索、统计、软删除。

直接打到磁盘（tmp_path），用低维度向量避免拖慢测试。
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from xuwen.config import Settings
from xuwen.core.models import (
    DialogueWindowChunk,
    FriendMessageChunk,
    HistoryImageChunk,
    ResponsePairChunk,
)
from xuwen.memory.schema import (
    TABLE_DIALOGUE_WINDOWS,
    TABLE_FRIEND_MESSAGES,
    TABLE_HISTORY_IMAGES,
    TABLE_LIVE_MESSAGES,
    TABLE_RESPONSE_PAIRS,
)
from xuwen.memory.store import MemoryStore


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        embedding_dim=8,  # 测试用低维度
        lance_db_path=tmp_path / "lancedb",
        lance_upsert_batch_size=64,
        enable_pii_redaction=False,
    )


@pytest.fixture()
async def store(settings: Settings) -> MemoryStore:
    s = MemoryStore(settings)
    await s.connect()
    s.ensure_tables()
    return s


def _vec(v: float, dim: int = 8) -> list[float]:
    return [v] * dim


def _friend_chunk(chunk_id: str, text: str, ts: int = 1000) -> FriendMessageChunk:
    return FriendMessageChunk(
        chunk_id=chunk_id,
        message_id=f"m-{chunk_id}",
        session_id="sess-x",
        seq=1,
        timestamp_ms=ts,
        text=text,
        dialogue_snippet=f"TA: {text}",
        context_before="",
        context_after="",
        warmth=0.0,
    )


def _window_chunk(chunk_id: str, text: str) -> DialogueWindowChunk:
    return DialogueWindowChunk(
        chunk_id=chunk_id,
        session_id="sess-x",
        text=text,
        summary=None,
        start_seq=1,
        end_seq=10,
        start_time_ms=1,
        end_time_ms=2,
        message_count=10,
        has_media=False,
    )


def _pair_chunk(chunk_id: str, user_text: str, friend_reply: str) -> ResponsePairChunk:
    return ResponsePairChunk(
        chunk_id=chunk_id,
        session_id="sess-x",
        user_message_ids=["u1"],
        friend_message_ids=["f1"],
        user_text=user_text,
        friend_reply=friend_reply,
        dialogue_snippet=f"Me: {user_text}\nTA: {friend_reply}",
        start_seq=1,
        end_seq=2,
        start_time_ms=1,
        end_time_ms=2,
    )


def _history_image_chunk(chunk_id: str, sha: str, description: str) -> HistoryImageChunk:
    return HistoryImageChunk(
        chunk_id=chunk_id,
        message_id=f"m-{chunk_id}",
        session_id="sess-x",
        seq=1,
        timestamp_ms=1000,
        sender_uid="friend",
        sender_name="Friend",
        sender_role="friend",
        image_sha=sha,
        image_name="a.jpg",
        mime="image/jpeg",
        size=8,
        description=description,
    )


@pytest.mark.asyncio
async def test_ensure_tables_creates_memory_tables(store: MemoryStore):
    from xuwen.memory.store import _list_table_names

    db = store._require_db()
    names = set(_list_table_names(db))
    assert TABLE_FRIEND_MESSAGES in names
    assert TABLE_DIALOGUE_WINDOWS in names
    assert TABLE_RESPONSE_PAIRS in names
    assert TABLE_HISTORY_IMAGES in names
    assert TABLE_LIVE_MESSAGES in names


@pytest.mark.asyncio
async def test_upsert_friend_chunks_and_search(store: MemoryStore):
    c1 = _friend_chunk("c1", "你好")
    c2 = _friend_chunk("c2", "晚安")
    embs = {"c1": _vec(0.1), "c2": _vec(0.9)}
    n = await store.upsert_friend_chunks([c1, c2], embs)
    assert n == 2
    results = await store.search_friend(_vec(0.1), top_k=5)
    assert len(results) >= 2
    ids = {r["id"] for r in results}
    assert {"c1", "c2"} <= ids


@pytest.mark.asyncio
async def test_upsert_window_chunks_and_search(store: MemoryStore):
    w = _window_chunk("w1", "你好\n你也好")
    await store.upsert_window_chunks([w], {"w1": _vec(0.5)})
    results = await store.search_windows(_vec(0.5), top_k=5)
    assert any(r["id"] == "w1" for r in results)


@pytest.mark.asyncio
async def test_list_dialogue_windows_orders_without_vectors(store: MemoryStore):
    early = _window_chunk("w1", "TA: 早")
    early.start_time_ms = 1000
    early.end_time_ms = 2000
    late = _window_chunk("w2", "TA: 晚")
    late.start_time_ms = 3000
    late.end_time_ms = 4000
    await store.upsert_window_chunks(
        [late, early],
        {"w1": _vec(0.1), "w2": _vec(0.2)},
    )

    rows = await store.list_dialogue_windows(limit=10)

    assert [row["id"] for row in rows] == ["w1", "w2"]
    assert "vector" not in rows[0]


@pytest.mark.asyncio
async def test_list_dialogue_windows_uses_query_select_without_table_to_arrow(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeQuery:
        def __init__(self) -> None:
            self.where_clause = ""
            self.selected: list[str] = []
            self.limit_value = 0

        def where(self, clause: str) -> FakeQuery:
            self.where_clause = clause
            return self

        def limit(self, limit: int) -> FakeQuery:
            self.limit_value = limit
            return self

        def select(self, cols: list[str]) -> FakeQuery:
            self.selected = cols
            return self

        def to_arrow(self) -> pa.Table:
            assert self.where_clause == "deleted = false"
            assert self.limit_value == 2
            assert "vector" not in self.selected
            return pa.table(
                {
                    "id": ["late", "early"],
                    "session_id": ["s2", "s1"],
                    "text": ["TA: 晚", "TA: 早"],
                    "start_time_ms": [3000, 1000],
                    "end_time_ms": [4000, 2000],
                    "deleted": [False, False],
                }
            )

    class FakeTable:
        def __init__(self) -> None:
            self.query = FakeQuery()
            self.schema = pa.schema(
                [
                    pa.field("id", pa.string()),
                    pa.field("session_id", pa.string()),
                    pa.field("text", pa.string()),
                    pa.field("start_time_ms", pa.int64()),
                    pa.field("end_time_ms", pa.int64()),
                    pa.field("deleted", pa.bool_()),
                    pa.field("vector", pa.list_(pa.float32())),
                ]
            )

        def search(self) -> FakeQuery:
            return self.query

        def to_arrow(self) -> pa.Table:
            raise AssertionError("list_dialogue_windows 不应该直接物化整表")

    fake_table = FakeTable()
    store = MemoryStore(settings)
    monkeypatch.setattr(store, "_table", lambda _name: fake_table)

    rows = await store.list_dialogue_windows(limit=2)

    assert [row["id"] for row in rows] == ["early", "late"]


@pytest.mark.asyncio
async def test_upsert_response_pair_chunks_and_search(store: MemoryStore):
    pair = _pair_chunk("p1", "你在干嘛", "刚吃完饭")
    await store.upsert_response_pair_chunks([pair], {"p1": _vec(0.4)})
    results = await store.search_response_pairs(_vec(0.4), top_k=5)
    assert any(r["id"] == "p1" and r["friend_reply"] == "刚吃完饭" for r in results)


@pytest.mark.asyncio
async def test_history_image_batch_lookup_and_soft_delete(store: MemoryStore):
    good = _history_image_chunk("img-good", "sha-good", "已有有效摘要")
    failed = _history_image_chunk("img-failed", "sha-failed", "[图片：识别失败]")
    await store.upsert_history_image_chunks(
        [good, failed],
        {"img-good": _vec(0.2), "img-failed": _vec(0.3)},
    )

    rows = await store.list_history_images_by_ids(["img-good", "img-failed", "missing"])

    assert {row["id"] for row in rows} == {"img-good", "img-failed"}
    assert all("vector" not in row for row in rows)

    deleted = await store.soft_delete_ids(TABLE_HISTORY_IMAGES, ["img-failed"])
    assert deleted == 1
    rows_by_sha = await store.list_history_images_by_sha("sha-failed")
    assert rows_by_sha == []


@pytest.mark.asyncio
async def test_append_live_messages(store: MemoryStore):
    rows = [
        {
            "id": "live-1",
            "vector": _vec(0.0),
            "text": "你好",
            "role": "user",
            "conversation_id": "conv-1",
        }
    ]
    n = await store.append_live_messages(rows)
    assert n == 1
    res = await store.recent_live("conv-1", limit=5)
    assert len(res) == 1
    assert res[0]["text"] == "你好"
    perf = store.db_perf_snapshot()
    assert "upsert:live_messages" in perf["by_operation"]
    assert "recent_live:live_messages" in perf["by_operation"]


@pytest.mark.asyncio
async def test_dimension_mismatch_raises(store: MemoryStore):
    from xuwen.core.errors import StoreError

    c = _friend_chunk("c1", "x")
    with pytest.raises(StoreError):
        await store.upsert_friend_chunks([c], {"c1": [0.1] * 4})


@pytest.mark.asyncio
async def test_stats(store: MemoryStore):
    s0 = await store.stats()
    assert s0.friend_messages == 0
    assert s0.response_pairs == 0
    await store.upsert_friend_chunks(
        [_friend_chunk("c1", "hi")], {"c1": _vec(0.1)}
    )
    s1 = await store.stats()
    assert s1.friend_messages == 1


@pytest.mark.asyncio
async def test_soft_delete_friend(store: MemoryStore):
    await store.upsert_friend_chunks(
        [_friend_chunk("c1", "x")], {"c1": _vec(0.2)}
    )
    ok = await store.soft_delete(TABLE_FRIEND_MESSAGES, "c1")
    assert ok is True
    # 检索时 deleted=false 过滤后查不到
    res = await store.search_friend(_vec(0.2), top_k=5)
    assert all(r["id"] != "c1" for r in res)


@pytest.mark.asyncio
async def test_soft_delete_handles_quote_in_id(store: MemoryStore):
    """row_id 含单引号也不应导致 LanceDB SQL 解析错误（防注入）。"""
    weird_id = "c1' OR 1=1 --"
    await store.upsert_friend_chunks(
        [_friend_chunk(weird_id, "x")], {weird_id: _vec(0.3)}
    )
    ok = await store.soft_delete(TABLE_FRIEND_MESSAGES, weird_id)
    assert ok is True


@pytest.mark.asyncio
async def test_update_labels_bulk_preserves_existing_columns(store: MemoryStore):
    """批量回填标签只应修改标签列，不应清空 vector/text，也不应插入陌生 id。"""
    c1 = _friend_chunk("c1", "你好")
    c2 = _friend_chunk("c2", "晚安")
    await store.upsert_friend_chunks([c1, c2], {"c1": _vec(0.1), "c2": _vec(0.2)})

    written = await store.update_labels(
        TABLE_FRIEND_MESSAGES,
        [
            {"id": "c1", "mood": "日常", "topic": "问候", "importance": 2},
            {"id": "missing", "mood": "调侃", "topic": "不存在", "importance": 3},
        ],
    )

    assert written == 2
    rows = store._table(TABLE_FRIEND_MESSAGES).to_arrow().to_pylist()
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"c1", "c2"}
    assert by_id["c1"]["text"] == "你好"
    assert by_id["c1"]["vector"] == pytest.approx(_vec(0.1))
    assert by_id["c1"]["mood"] == "日常"
    assert by_id["c1"]["topic"] == "问候"
    assert by_id["c1"]["importance"] == 2
    assert by_id["c2"]["mood"] == ""


@pytest.mark.asyncio
async def test_recent_live_handles_quote_in_conversation_id(store: MemoryStore):
    """conversation_id 含单引号时不应崩溃。"""
    cid = "conv-1' OR 1=1 --"
    await store.append_live_messages(
        [
            {
                "id": "l1",
                "vector": _vec(0.0),
                "text": "x",
                "role": "user",
                "conversation_id": cid,
            }
        ]
    )
    res = await store.recent_live(cid, limit=5)
    assert len(res) == 1


@pytest.mark.asyncio
async def test_append_live_messages_does_not_mutate_input(store: MemoryStore):
    """传入的 rows 不应被原地修改（默认字段补全应作用于副本）。"""
    rows: list[dict] = [
        {
            "id": "l-orig",
            "vector": _vec(0.0),
            "text": "x",
            "role": "user",
            "conversation_id": "c",
        }
    ]
    before_keys = set(rows[0].keys())
    await store.append_live_messages(rows)
    after_keys = set(rows[0].keys())
    assert before_keys == after_keys, "调用方传入的 dict 不应被加入默认字段"


@pytest.mark.asyncio
async def test_cleanup_ai_generated_soft_deletes_only_matching_rows(store: MemoryStore):
    await store.append_live_messages(
        [
            {
                "id": "ai-old",
                "vector": _vec(0.0),
                "text": "旧 AI 回复",
                "role": "assistant",
                "conversation_id": "c1",
                "source": "ai_generated",
                "created_at_ms": 1,
            },
            {
                "id": "ai-other",
                "vector": _vec(0.0),
                "text": "别的会话 AI 回复",
                "role": "assistant",
                "conversation_id": "c2",
                "source": "ai_generated",
                "created_at_ms": 1,
            },
            {
                "id": "user-old",
                "vector": _vec(0.0),
                "text": "用户输入",
                "role": "user",
                "conversation_id": "c1",
                "source": "user_new",
                "created_at_ms": 1,
            },
        ]
    )

    dry = await store.cleanup_ai_generated(
        older_than_days=0,
        conversation_id="c1",
        dry_run=True,
    )
    assert dry == 1
    deleted = await store.cleanup_ai_generated(
        older_than_days=0,
        conversation_id="c1",
    )
    assert deleted == 1

    rows = store._table(TABLE_LIVE_MESSAGES).to_arrow().to_pylist()
    by_id = {r["id"]: r for r in rows}
    assert by_id["ai-old"]["deleted"] is True
    assert by_id["ai-other"]["deleted"] is False
    assert by_id["user-old"]["deleted"] is False


@pytest.mark.asyncio
async def test_upsert_batching_handles_large_input(store: MemoryStore):
    """upsert 应能按配置分批处理超过单批上限的输入。"""
    n = store.settings.lance_upsert_batch_size + 10
    chunks = [_friend_chunk(f"c{i}", f"text-{i}") for i in range(n)]
    embs = {c.chunk_id: _vec(float(i) / n) for i, c in enumerate(chunks)}
    written = await store.upsert_friend_chunks(chunks, embs)
    assert written == n
    stats = await store.stats()
    assert stats.friend_messages == n


@pytest.mark.asyncio
async def test_ensure_vector_indices_skips_small_tables(store: MemoryStore):
    """表行数低于 min_rows 阈值时应该 skip_small 而不是真的建索引。

    小数据集 IVF_PQ KMeans 会出空集群，强行建反而浪费 + 易失败。
    """
    chunks = [_friend_chunk(f"c{i}", f"t{i}") for i in range(5)]
    embs = {c.chunk_id: _vec(float(i) / 5) for i, c in enumerate(chunks)}
    await store.upsert_friend_chunks(chunks, embs)

    report = await store.ensure_vector_indices(min_rows=1000)
    assert report[TABLE_FRIEND_MESSAGES].startswith("skip_small")


@pytest.mark.asyncio
async def test_ensure_vector_indices_not_found_for_unused_tables(store: MemoryStore):
    """空表（行数 0）也应该走 skip_small 分支，不抛异常。"""
    report = await store.ensure_vector_indices(min_rows=1)
    # response_pairs / live_messages / dialogue_windows / friend_messages 都还没写
    for table, status in report.items():
        assert status.startswith("skip_small") or status == "not_found", (
            f"{table}: {status}"
        )


@pytest.mark.asyncio
async def test_optimize_all_tables_handles_empty_db(store: MemoryStore):
    """optimize 空库应不抛异常，每张表返回 'optimized' 或可解释状态。"""
    report = await store.optimize_all_tables()
    for status in report.values():
        # 空表 optimize 在某些版本返回 ok，某些版本抛 IO 错误 → 都允许
        assert status.startswith("optimized") or status.startswith("error"), status


def test_choose_num_sub_vectors_returns_divisor():
    """num_sub_vectors 必须能整除 embedding_dim。"""
    from xuwen.memory.store import _choose_num_sub_vectors

    for dim in (4096, 1024, 768, 512, 384, 256, 8):
        sub = _choose_num_sub_vectors(dim)
        assert dim % sub == 0, f"dim={dim} sub={sub}"
        assert sub >= 1
