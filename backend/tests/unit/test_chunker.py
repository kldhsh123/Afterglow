"""chunker 单测：混合 chunk 生成。"""

from __future__ import annotations

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.ingestion.chunker import (
    build_friend_chunks,
    build_response_pair_chunks,
    build_window_chunks,
)
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.parser import parse_messages
from xuwen.ingestion.splitter import build_windows, split_sessions


def _msg(seq: int, ts: int, role: str, text: str, kind: MessageKind = MessageKind.TEXT) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m{seq}",
        seq=seq,
        timestamp_ms=ts,
        sender_uid=f"u-{role}",
        sender_name=role,
        sender_role=role,  # type: ignore[arg-type]
        kind=kind,
        raw_type="type_1",
        text=text,
    )


def test_build_friend_chunks_only_friend_messages():
    settings = Settings(single_context_before=2, single_context_after=1)
    msgs = [
        _msg(1, 1000, "self", "你好"),
        _msg(2, 2000, "friend", "嗨"),
        _msg(3, 3000, "self", "在吗"),
        _msg(4, 4000, "friend", "在"),
    ]
    sessions = split_sessions(msgs, settings)
    chunks = build_friend_chunks(sessions, settings)
    assert len(chunks) == 2
    assert all(c.source == "human_original" for c in chunks)
    assert chunks[0].text == "嗨"
    assert chunks[1].text == "在"


def test_build_friend_chunks_includes_context():
    settings = Settings(
        self_name="Me",
        friend_name="TA",
        single_context_before=2,
        single_context_after=1,
    )
    msgs = [
        _msg(1, 1000, "self", "你好"),
        _msg(2, 2000, "self", "在吗"),
        _msg(3, 3000, "friend", "在"),
        _msg(4, 4000, "self", "好的"),
    ]
    sessions = split_sessions(msgs, settings)
    chunks = build_friend_chunks(sessions, settings)
    assert len(chunks) == 1
    c = chunks[0]
    assert "你好" in c.context_before
    assert "在吗" in c.context_before
    assert "好的" in c.context_after
    assert "TA: 在" in c.dialogue_snippet


def test_build_response_pair_chunks_maps_user_to_friend_reply():
    settings = Settings(self_name="Me", friend_name="TA")
    msgs = [
        _msg(1, 1000, "self", "你在干嘛"),
        _msg(2, 2000, "friend", "刚吃完饭"),
        _msg(3, 3000, "friend", "准备躺会儿"),
    ]
    sessions = split_sessions(msgs, settings)
    chunks = build_response_pair_chunks(sessions, settings)

    assert len(chunks) == 1
    assert chunks[0].user_text == "你在干嘛"
    assert chunks[0].friend_reply == "刚吃完饭\n\n准备躺会儿"
    assert "Me: 你在干嘛" in chunks[0].dialogue_snippet
    assert "TA: 刚吃完饭" in chunks[0].dialogue_snippet


def test_build_friend_chunks_uses_real_session_id():
    """friend chunk 的 session_id 必须是 splitter 输出的真实值。"""
    settings = Settings()
    msgs = [
        _msg(1, 1000, "self", "你好"),
        _msg(2, 2000, "friend", "在"),
    ]
    sessions = split_sessions(msgs, settings)
    chunks = build_friend_chunks(sessions, settings)
    assert chunks[0].session_id == sessions[0].session_id
    assert chunks[0].session_id.startswith("sess-")  # 不是 pseudo


def test_build_friend_chunks_does_not_cross_session_boundary():
    """两个不同 session 中的 friend chunk 上下文不应互相串。"""
    settings = Settings(session_gap_minutes=30, single_context_before=5, single_context_after=5)
    # session 1
    msgs = [
        _msg(1, 0, "self", "session1-self"),
        _msg(2, 60_000, "friend", "session1-friend"),
    ]
    # session 2（间隔超过 30 分钟）
    msgs += [
        _msg(3, 60_000 + 32 * 60_000, "self", "session2-self"),
        _msg(4, 60_000 + 33 * 60_000, "friend", "session2-friend"),
    ]
    sessions = split_sessions(msgs, settings)
    assert len(sessions) == 2

    chunks = build_friend_chunks(sessions, settings)
    s1_chunk = next(c for c in chunks if c.text == "session1-friend")
    s2_chunk = next(c for c in chunks if c.text == "session2-friend")

    # session 2 的 chunk 不应包含 session 1 的内容
    assert "session1" not in s2_chunk.context_before
    assert "session1" not in s2_chunk.dialogue_snippet
    # 反向亦然
    assert "session2" not in s1_chunk.context_after


def test_build_friend_chunks_skips_recalled_and_system():
    settings = Settings()
    msgs = [
        _msg(1, 1000, "friend", "[撤回]", kind=MessageKind.RECALLED),
        NormalizedMessage(
            message_id="s",
            seq=2,
            timestamp_ms=2000,
            sender_uid="",
            sender_name="",
            sender_role="system",
            kind=MessageKind.SYSTEM,
            raw_type="system",
            text="撤回提示",
            system=True,
        ),
        _msg(3, 3000, "friend", "ok"),
    ]
    sessions = split_sessions(msgs, settings)
    chunks = build_friend_chunks(sessions, settings)
    assert [c.text for c in chunks] == ["ok"]


def test_warmth_estimation():
    settings = Settings()
    warm = _msg(1, 1000, "friend", "辛苦啦，早点睡，记得吃饭")
    cold = _msg(2, 2000, "friend", "嗯")
    sessions = split_sessions([warm, cold], settings)
    chunks = build_friend_chunks(sessions, settings)
    assert chunks[0].warmth > 0.5
    assert chunks[1].warmth == 0.0


def test_chunk_id_includes_seq_and_timestamp():
    """chunk_id 应纳入 seq/timestamp/sender_uid，避免 message_id 重复时冲突。"""
    settings = Settings()
    m1 = _msg(1, 1000, "friend", "x")
    m2 = NormalizedMessage(
        message_id="m1",  # 故意与 m1 同 id
        seq=2,
        timestamp_ms=2000,
        sender_uid="u-friend",
        sender_name="friend",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="y",
    )
    sessions = split_sessions([m1, m2], settings)
    chunks = build_friend_chunks(sessions, settings)
    assert chunks[0].chunk_id != chunks[1].chunk_id


def test_build_window_chunks_renders_dialogue(settings_for_sample, sample_payload):
    cleaner = Cleaner(settings_for_sample)
    parsed = parse_messages(sample_payload, settings_for_sample)
    cleaned = cleaner.clean_many(parsed)
    sessions = split_sessions(cleaned, settings_for_sample)
    windows = build_windows(sessions, settings_for_sample)
    chunks = build_window_chunks(windows, settings_for_sample)
    assert len(chunks) > 0
    # 检查 speaker 标签存在
    sample = chunks[0]
    assert ":" in sample.text
    assert sample.message_count > 0
    assert sample.start_seq <= sample.end_seq


def test_build_window_chunks_marks_media():
    settings = Settings(window_size=4, window_overlap=0)
    msgs = [
        NormalizedMessage(
            message_id="m1",
            seq=1,
            timestamp_ms=1000,
            sender_uid="u-friend",
            sender_name="friend",
            sender_role="friend",
            kind=MessageKind.PLACEHOLDER,
            raw_type="type_1",
            text="[图片]",
            placeholders=["[图片]"],
            has_media=True,
        ),
        _msg(2, 2000, "self", "好可爱"),
    ]
    sessions = split_sessions(msgs, settings)
    windows = build_windows(sessions, settings)
    chunks = build_window_chunks(windows, settings)
    assert chunks[0].has_media is True
