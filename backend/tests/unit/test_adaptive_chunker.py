from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage, Session
from xuwen.ingestion.adaptive_chunker import build_adaptive_windows
from xuwen.ingestion.chunk_cache import AdaptiveChunkCache


def _msg(seq: int, role: str, text: str, ts: int | None = None) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m{seq}",
        seq=seq,
        timestamp_ms=seq * 60_000 if ts is None else ts,
        sender_uid=f"u-{role}",
        sender_name=role,
        sender_role=role,  # type: ignore[arg-type]
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text=text,
    )


def _session(messages: list[NormalizedMessage]) -> Session:
    return Session(
        session_id="sess-1234567890abcdef",
        messages=messages,
        start_time_ms=messages[0].timestamp_ms,
        end_time_ms=messages[-1].timestamp_ms,
    )


@pytest.mark.asyncio
async def test_adaptive_windows_use_heuristic_boundaries_and_overlap():
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_target_chars=18,
        adaptive_chunk_max_chars=40,
        adaptive_chunk_min_turns=2,
        adaptive_chunk_overlap_turns=1,
    )
    messages = [
        _msg(1, "self", "今天好累"),
        _msg(2, "friend", "先歇一会儿"),
        _msg(3, "self", "对了我明天考试"),
        _msg(4, "friend", "那早点睡"),
    ]

    windows = await build_adaptive_windows([_session(messages)], settings)

    assert len(windows) >= 2
    assert windows[0].window_id.startswith("awin-")
    assert windows[0].start_seq == 1
    # 第二个窗口带 1 个 turn overlap，包含上一段最后一句朋友回复。
    assert windows[1].start_seq == 2


@pytest.mark.asyncio
async def test_adaptive_windows_can_use_model_boundaries():
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_model_enabled=True,
        adaptive_chunk_max_messages_per_call=20,
        adaptive_chunk_overlap_turns=0,
    )
    messages = [
        _msg(1, "self", "你在干嘛"),
        _msg(2, "friend", "刚吃饭"),
        _msg(3, "self", "我有点睡不着"),
        _msg(4, "friend", "那我陪你一会儿"),
    ]
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(
        return_value='{"segments":[{"start_turn":0,"end_turn":1},{"start_turn":2,"end_turn":3}]}'
    )

    windows = await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm,
        model="glm-4-flash",
    )

    assert [(w.start_seq, w.end_seq) for w in windows] == [(1, 2), (3, 4)]
    llm.complete_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_adaptive_windows_call_model_in_batches_for_long_sessions():
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_model_enabled=True,
        adaptive_chunk_max_messages_per_call=2,
        adaptive_chunk_overlap_turns=0,
    )
    messages = [
        _msg(1, "self", "你在干嘛"),
        _msg(2, "friend", "刚吃饭"),
        _msg(3, "self", "我有点睡不着"),
        _msg(4, "friend", "那我陪你一会儿"),
    ]
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(
        side_effect=[
            '{"segments":[{"start_turn":0,"end_turn":1}]}',
            '{"segments":[{"start_turn":0,"end_turn":1}]}',
        ]
    )

    windows = await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm,
        model="glm-4-flash",
    )

    assert [(w.start_seq, w.end_seq) for w in windows] == [(1, 2), (3, 4)]
    assert llm.complete_chat.await_count == 2


@pytest.mark.asyncio
async def test_adaptive_windows_fall_back_when_model_output_invalid():
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_model_enabled=True,
        adaptive_chunk_max_messages_per_call=20,
        adaptive_chunk_target_chars=200,
    )
    messages = [_msg(1, "self", "你好"), _msg(2, "friend", "嗨")]
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(return_value="bad")

    windows = await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm,
        model="glm-4-flash",
    )

    assert len(windows) == 1
    assert windows[0].start_seq == 1
    assert windows[0].end_seq == 2


@pytest.mark.asyncio
async def test_adaptive_model_segments_cached_across_runs(tmp_path):
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_model_enabled=True,
        adaptive_chunk_max_messages_per_call=20,
        adaptive_chunk_overlap_turns=0,
    )
    messages = [
        _msg(1, "self", "你在干嘛"),
        _msg(2, "friend", "刚吃饭"),
        _msg(3, "self", "我有点睡不着"),
        _msg(4, "friend", "那我陪你一会儿"),
    ]
    cache_path = tmp_path / "chunk_cache.jsonl"
    llm = AsyncMock()
    llm.complete_chat = AsyncMock(
        return_value='{"segments":[{"start_turn":0,"end_turn":1},{"start_turn":2,"end_turn":3}]}'
    )

    windows1 = await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm,
        model="glm-4-flash",
        cache=AdaptiveChunkCache(cache_path),
    )
    assert llm.complete_chat.await_count == 1

    # 二跑（新缓存实例模拟重新导入）：命中缓存，零模型调用，结果一致
    llm2 = AsyncMock()
    llm2.complete_chat = AsyncMock(return_value="{}")
    cache2 = AdaptiveChunkCache(cache_path)
    windows2 = await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm2,
        model="glm-4-flash",
        cache=cache2,
    )
    llm2.complete_chat.assert_not_awaited()
    assert cache2.hits == 1
    assert [(w.start_seq, w.end_seq) for w in windows2] == [
        (w.start_seq, w.end_seq) for w in windows1
    ]


@pytest.mark.asyncio
async def test_adaptive_model_failure_not_cached(tmp_path):
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_model_enabled=True,
        adaptive_chunk_max_messages_per_call=20,
        adaptive_chunk_target_chars=200,
    )
    messages = [_msg(1, "self", "你好"), _msg(2, "friend", "嗨")]
    cache_path = tmp_path / "chunk_cache.jsonl"

    failing = AsyncMock()
    failing.complete_chat = AsyncMock(side_effect=RuntimeError("boom"))
    windows = await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=failing,
        model="glm-4-flash",
        cache=AdaptiveChunkCache(cache_path),
    )
    assert len(windows) == 1  # 退回启发式

    # 失败批次不写缓存：模型恢复后会真正重试而不是命中旧的退化结果
    ok = AsyncMock()
    ok.complete_chat = AsyncMock(
        return_value='{"segments":[{"start_turn":0,"end_turn":1}]}'
    )
    await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=ok,
        model="glm-4-flash",
        cache=AdaptiveChunkCache(cache_path),
    )
    ok.complete_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_adaptive_cache_key_depends_on_model(tmp_path):
    settings = Settings(
        chunking_strategy="adaptive",
        adaptive_chunk_model_enabled=True,
        adaptive_chunk_max_messages_per_call=20,
        adaptive_chunk_overlap_turns=0,
    )
    messages = [_msg(1, "self", "你在干嘛"), _msg(2, "friend", "刚吃饭")]
    cache_path = tmp_path / "chunk_cache.jsonl"
    reply = '{"segments":[{"start_turn":0,"end_turn":1}]}'

    llm = AsyncMock()
    llm.complete_chat = AsyncMock(return_value=reply)
    await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm,
        model="model-a",
        cache=AdaptiveChunkCache(cache_path),
    )

    llm2 = AsyncMock()
    llm2.complete_chat = AsyncMock(return_value=reply)
    await build_adaptive_windows(
        [_session(messages)],
        settings,
        llm=llm2,
        model="model-b",
        cache=AdaptiveChunkCache(cache_path),
    )
    llm2.complete_chat.assert_awaited_once()  # 换模型 → key 不同 → 未命中
