"""splitter 单测：session 切分 + 滑动窗口。"""

from __future__ import annotations

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.ingestion.splitter import build_windows, split_sessions


def _make_msg(seq: int, ts_ms: int, role: str = "friend", text: str = "x") -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m{seq}",
        seq=seq,
        timestamp_ms=ts_ms,
        sender_uid=f"u-{role}",
        sender_name=role,
        sender_role=role,  # type: ignore[arg-type]
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text=text,
    )


def test_split_sessions_single_session_when_close():
    settings = Settings(session_gap_minutes=30)
    msgs = [_make_msg(i, i * 60_000) for i in range(5)]  # 间隔 1 分钟
    sessions = split_sessions(msgs, settings)
    assert len(sessions) == 1
    assert sessions[0].message_count == 5


def test_split_sessions_break_on_gap():
    settings = Settings(session_gap_minutes=30)
    base = 0
    # 必须严格大于 30 分钟阈值。msg2 在 base+1min，msg3 需要 > base+1min+30min。
    far = base + 60_000 + 31 * 60_000
    msgs = [
        _make_msg(1, base),
        _make_msg(2, base + 60_000),
        _make_msg(3, far),
        _make_msg(4, far + 60_000),
    ]
    sessions = split_sessions(msgs, settings)
    assert len(sessions) == 2
    assert sessions[0].message_count == 2
    assert sessions[1].message_count == 2


def test_split_sessions_id_stable():
    settings = Settings(session_gap_minutes=30)
    msgs = [_make_msg(1, 0), _make_msg(2, 60_000)]
    a = split_sessions(msgs, settings)
    b = split_sessions(msgs, settings)
    assert a[0].session_id == b[0].session_id


def test_build_windows_basic_overlap():
    settings = Settings(session_gap_minutes=30, window_size=4, window_overlap=1)
    msgs = [_make_msg(i, i * 60_000) for i in range(10)]
    sessions = split_sessions(msgs, settings)
    windows = build_windows(sessions, settings)
    assert len(windows) > 1
    # 第一窗口
    assert windows[0].start_seq == 0
    assert windows[0].end_seq == 3
    # 第二窗口起点 = 上一窗口大小 - overlap = 3
    assert windows[1].start_seq == 3


def test_build_windows_short_session_emits_single_window():
    settings = Settings(session_gap_minutes=30, window_size=12, window_overlap=3)
    msgs = [_make_msg(i, i * 60_000) for i in range(5)]
    sessions = split_sessions(msgs, settings)
    windows = build_windows(sessions, settings)
    assert len(windows) == 1
    assert windows[0].start_seq == 0
    assert windows[0].end_seq == 4


def test_build_windows_long_session_continues_sliding():
    settings = Settings(session_gap_minutes=30, window_size=10, window_overlap=2)
    msgs = [_make_msg(i, i * 60_000) for i in range(150)]
    sessions = split_sessions(msgs, settings)
    windows = build_windows(sessions, settings)
    # 150 条覆盖完毕：步长 8，覆盖 8*k + 10 >= 150 → k>=18，共 19 个窗口
    assert len(windows) >= 18
    assert windows[-1].end_seq == 149


def test_build_windows_skips_system_messages():
    settings = Settings(session_gap_minutes=30, window_size=4, window_overlap=0)
    msgs = [_make_msg(i, i * 60_000) for i in range(3)]
    msgs.append(
        NormalizedMessage(
            message_id="sys",
            seq=3,
            timestamp_ms=3 * 60_000,
            sender_uid="",
            sender_name="",
            sender_role="system",
            kind=MessageKind.SYSTEM,
            raw_type="system",
            text="撤回提示",
            system=True,
        )
    )
    msgs.append(_make_msg(4, 4 * 60_000))
    sessions = split_sessions(msgs, settings)
    windows = build_windows(sessions, settings)
    # 系统消息不进窗口
    for w in windows:
        assert all(m.kind != MessageKind.SYSTEM for m in w.messages)


def test_split_sessions_empty_input():
    assert split_sessions([], Settings()) == []
    assert build_windows([], Settings()) == []
