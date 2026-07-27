from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage, Session
from xuwen.ingestion.adaptive_chunker import build_adaptive_windows


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
