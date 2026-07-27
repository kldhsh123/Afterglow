"""writeback queue 单测：批量缓冲、阈值触发、空闲超时、drain、向量化开关。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.memory.writer import WritebackQueue, WritebackTurn


def _settings(**overrides):
    defaults = dict(
        embedding_dim=8,
        writeback_enabled=True,
        writeback_queue_size=1000,
        writeback_batch_turns=3,
        writeback_flush_interval_seconds=60,
        writeback_vectorize=True,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _fake_store():
    store = AsyncMock()
    store.append_live_messages = AsyncMock(return_value=0)
    return store


def _fake_embedder():
    emb = AsyncMock()
    # 默认返回与输入等长的固定向量
    async def _embed(texts):
        return [[0.1] * 8 for _ in texts]

    emb.embed_texts = AsyncMock(side_effect=_embed)
    return emb


def _turn(conv: str, u: str = "u", a: str = "a") -> WritebackTurn:
    return WritebackTurn(conversation_id=conv, user_text=u, assistant_text=a)


@pytest.mark.asyncio
async def test_writeback_does_not_persist_until_threshold():
    """未达 batch_turns 阈值时不应触发持久化。"""
    settings = _settings(writeback_batch_turns=5)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        # 入 3 轮，未到阈值
        for i in range(3):
            await wb.enqueue_turn(_turn("c1", f"u{i}", f"a{i}"))
        # 给点时间观察是否有意外 flush
        await asyncio.sleep(0.1)
        store.append_live_messages.assert_not_called()
        embedder.embed_texts.assert_not_called()
        assert wb.stats.pending_turns == 3
    finally:
        await wb.stop(drain=False)


@pytest.mark.asyncio
async def test_writeback_flushes_on_threshold():
    """达到 batch_turns 时一次性持久化分层 live memory + 一次 embedding 调用。"""
    settings = _settings(writeback_batch_turns=3)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        for i in range(3):
            await wb.enqueue_turn(_turn("c1", f"u{i}", f"a{i}"))
        # 等异步 flush
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await wb.stop(drain=False)

    assert wb.stats.flushed_batches == 1
    assert wb.stats.written == 6
    # embedder 只被调一次（批量），传入文本数 = 3 轮 × 2。
    embedder.embed_texts.assert_awaited_once()
    texts = embedder.embed_texts.await_args.args[0]
    assert texts == ["u0", "a0", "u1", "a1", "u2", "a2"]
    # store 一次接收 6 条 row；user_new 与 ai_generated 分层。
    store.append_live_messages.assert_awaited_once()
    rows = store.append_live_messages.await_args.args[0]
    assert len(rows) == 6
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert [r["source"] for r in rows] == [
        "user_new",
        "ai_generated",
        "user_new",
        "ai_generated",
        "user_new",
        "ai_generated",
    ]


@pytest.mark.asyncio
async def test_writeback_drain_flushes_partial_batch():
    """stop(drain=True) 时 pending 中不足 batch 的也要写出。"""
    settings = _settings(writeback_batch_turns=10)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        for i in range(2):
            await wb.enqueue_turn(_turn("c1", f"u{i}", f"a{i}"))
        await asyncio.sleep(0.05)
        store.append_live_messages.assert_not_called()
    finally:
        await wb.stop(drain=True)
    # drain 后这 2 轮也写出
    store.append_live_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_writeback_per_conversation_isolation():
    """不同 conversation 的缓冲独立计数；只有满 batch 的那个会先 flush。"""
    settings = _settings(writeback_batch_turns=3)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        # conv1 入 3 轮（满）
        for i in range(3):
            await wb.enqueue_turn(_turn("c1", f"u{i}", f"a{i}"))
        # conv2 入 1 轮（不满）
        await wb.enqueue_turn(_turn("c2", "x", "y"))
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
        # conv1 已 flush，conv2 还在缓冲
        assert wb.stats.flushed_batches == 1
        assert wb.stats.pending_turns == 1  # conv2 的 1 轮
    finally:
        await wb.stop(drain=False)


@pytest.mark.asyncio
async def test_writeback_drops_when_disabled():
    settings = _settings(writeback_enabled=False)
    wb = WritebackQueue(settings, _fake_store(), embedder=_fake_embedder())
    await wb.start()
    try:
        ok = await wb.enqueue_turn(_turn("c1"))
        assert ok is False
    finally:
        await wb.stop(drain=True)
    assert wb.stats.dropped == 1


@pytest.mark.asyncio
async def test_writeback_pause_resume():
    settings = _settings(writeback_batch_turns=2)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        wb.pause()
        ok = await wb.enqueue_turn(_turn("c1"))
        assert ok is False
        assert wb.stats.dropped == 1
        wb.resume()
        await wb.enqueue_turn(_turn("c1"))
        await wb.enqueue_turn(_turn("c1"))
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
        assert wb.stats.flushed_batches == 1
    finally:
        await wb.stop(drain=False)


@pytest.mark.asyncio
async def test_writeback_vectorize_disabled_uses_zero_vectors():
    """WRITEBACK_VECTORIZE=false 时不调 embedding，写入消息向量全 0。"""
    settings = _settings(writeback_batch_turns=2, writeback_vectorize=False)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        await wb.enqueue_turn(_turn("c1"))
        await wb.enqueue_turn(_turn("c1"))
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await wb.stop(drain=False)
    embedder.embed_texts.assert_not_called()
    rows = store.append_live_messages.await_args.args[0]
    assert len(rows) == 4
    assert {r["source"] for r in rows} == {"user_new", "ai_generated"}
    for r in rows:
        assert r["vector"] == [0.0] * 8


@pytest.mark.asyncio
async def test_writeback_persists_assistant_as_ai_generated():
    """AI 生成回复可进入 live_messages，但必须标记为 ai_generated 低信任。"""
    settings = _settings(writeback_batch_turns=1)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        await wb.enqueue_turn(
            WritebackTurn(
                conversation_id="c1",
                user_text="我今天想喝拿铁",
                assistant_text="那我陪你去买呀",
            )
        )
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await wb.stop(drain=False)

    rows = store.append_live_messages.await_args.args[0]
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[0]["text"] == "我今天想喝拿铁"
    assert rows[0]["source"] == "user_new"
    assert rows[1]["role"] == "assistant"
    assert rows[1]["text"] == "那我陪你去买呀"
    assert rows[1]["source"] == "ai_generated"
    assert rows[1]["trust_level"] < rows[0]["trust_level"]


@pytest.mark.asyncio
async def test_writeback_proactive_turn_only_persists_ai_generated():
    """主动开话题没有真实用户输入，但 AI 输出可作为连续性记忆保存。"""
    settings = _settings(writeback_batch_turns=1)
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        await wb.enqueue_turn(
            WritebackTurn(
                conversation_id="c1",
                user_text="（AI 主动开启话题）",
                assistant_text="今天有点想你",
            )
        )
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await wb.stop(drain=False)

    rows = store.append_live_messages.await_args.args[0]
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    assert rows[0]["text"] == "今天有点想你"
    assert rows[0]["source"] == "ai_generated"
    assert wb.stats.written == 1


@pytest.mark.asyncio
async def test_writeback_continues_when_embedder_fails():
    """embedder 失败时应继续用零向量入库，不影响聊天。"""
    from xuwen.core.errors import EmbeddingError

    settings = _settings(writeback_batch_turns=2)
    store = _fake_store()
    bad = AsyncMock()
    bad.embed_texts = AsyncMock(side_effect=EmbeddingError("upstream down"))
    wb = WritebackQueue(settings, store, embedder=bad)
    await wb.start()
    try:
        await wb.enqueue_turn(_turn("c1"))
        await wb.enqueue_turn(_turn("c1"))
        for _ in range(20):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await wb.stop(drain=False)
    store.append_live_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_writeback_queue_overflow_drops():
    """total pending 超过 writeback_queue_size 时丢弃。"""
    settings = _settings(writeback_batch_turns=100, writeback_queue_size=3)
    wb = WritebackQueue(settings, _fake_store(), embedder=_fake_embedder())
    await wb.start()
    try:
        # 前 3 个能入；第 4 个被丢
        results: list[bool] = []
        for i in range(5):
            results.append(await wb.enqueue_turn(_turn("c1", f"u{i}")))
    finally:
        await wb.stop(drain=False)
    # 至少有一次被 drop
    assert results.count(False) >= 1
    assert wb.stats.dropped >= 1


@pytest.mark.asyncio
async def test_writeback_idle_timeout_forces_flush():
    """若 conversation 空闲超过 flush_interval，ticker 应强制 flush 不足 batch 的轮。"""
    # 用极短的 interval 让测试快
    settings = _settings(
        writeback_batch_turns=10,
        writeback_flush_interval_seconds=1,  # 1 秒 idle
    )
    store = _fake_store()
    embedder = _fake_embedder()
    wb = WritebackQueue(settings, store, embedder=embedder)
    await wb.start()
    try:
        await wb.enqueue_turn(_turn("c1"))
        # 等 ticker 唤醒并发现超时（idle=1s, ticker interval≈1s，所以最多 2-3 秒）
        for _ in range(80):
            if wb.stats.flushed_batches >= 1:
                break
            await asyncio.sleep(0.1)
    finally:
        await wb.stop(drain=False)
    assert wb.stats.flushed_batches == 1
