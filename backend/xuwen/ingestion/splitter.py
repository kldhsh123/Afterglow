"""会话切分 + 滑动窗口生成。

- `split_sessions`：按 `session_gap_minutes` 把消息序列切成 Session。
- `build_windows`：对每个 Session 用滑动窗口切成 MessageWindow。
- 超长 session 自动按窗口大小继续切，避免单 chunk 过大。

system 消息默认不参与窗口（避免污染对话），但会保留在原 Session.messages 中作为元数据。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import timedelta

from xuwen.config import Settings
from xuwen.core.models import MessageKind, MessageWindow, NormalizedMessage, Session
from xuwen.core.time import is_session_break
from xuwen.error_i18n import error_text


def split_sessions(
    messages: list[NormalizedMessage],
    settings: Settings,
) -> list[Session]:
    """按时间间隔切分会话。

    规则：
    - 相邻消息间隔 > `session_gap_minutes` 即切断。
    - system 消息不强制切断 session，但保留在原 Session.messages 内。
    """
    if not messages:
        return []

    gap = timedelta(minutes=settings.session_gap_minutes)
    sessions: list[Session] = []
    current: list[NormalizedMessage] = []
    prev_ts: int | None = None

    for msg in messages:
        if prev_ts is not None and is_session_break(prev_ts, msg.timestamp_ms, gap):
            sessions.append(_finalize_session(current))
            current = []
        current.append(msg)
        prev_ts = msg.timestamp_ms

    if current:
        sessions.append(_finalize_session(current))

    return sessions


def build_windows(
    sessions: list[Session],
    settings: Settings,
) -> list[MessageWindow]:
    """对每个 session 应用滑动窗口切分。

    窗口大小 = settings.window_size，重叠 = settings.window_overlap。
    超长 session 会一直滑动直到覆盖完全部消息。
    """
    size = settings.window_size
    overlap = settings.window_overlap
    step = max(1, size - overlap)
    if size <= 0:
        return []

    windows: list[MessageWindow] = []
    for session in sessions:
        usable = _filter_for_window(session.messages)
        if not usable:
            continue
        for win_msgs in _slide(usable, size=size, step=step):
            windows.append(_make_window(session.session_id, win_msgs))
    return windows


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _finalize_session(msgs: list[NormalizedMessage]) -> Session:
    if not msgs:
        raise ValueError(error_text("Cannot finalize an empty session"))
    start_ts = msgs[0].timestamp_ms
    end_ts = msgs[-1].timestamp_ms
    sid = _session_id(start_ts, end_ts, msgs[0].message_id)
    return Session(
        session_id=sid,
        messages=list(msgs),
        start_time_ms=start_ts,
        end_time_ms=end_ts,
    )


def _session_id(start_ms: int, end_ms: int, first_id: str) -> str:
    """生成确定性的 session_id。"""
    h = hashlib.sha1(f"{start_ms}-{end_ms}-{first_id}".encode(), usedforsecurity=False)
    return f"sess-{h.hexdigest()[:16]}"


def _window_id(session_id: str, start_seq: int, end_seq: int) -> str:
    return f"win-{session_id[5:13]}-{start_seq}-{end_seq}"


def _filter_for_window(messages: list[NormalizedMessage]) -> list[NormalizedMessage]:
    """窗口不包含 system 消息（保留在 Session.messages 用于元数据）。"""
    return [m for m in messages if m.kind != MessageKind.SYSTEM]


def _slide(
    messages: list[NormalizedMessage],
    *,
    size: int,
    step: int,
) -> Iterator[list[NormalizedMessage]]:
    """按窗口大小与步长切片。

    特殊情况：消息数 <= size 时仍输出一个完整窗口。
    """
    if not messages:
        return
    n = len(messages)
    if n <= size:
        yield list(messages)
        return
    i = 0
    while i < n:
        end = min(i + size, n)
        yield list(messages[i:end])
        if end == n:
            break
        i += step


def _make_window(session_id: str, messages: list[NormalizedMessage]) -> MessageWindow:
    start_seq = messages[0].seq
    end_seq = messages[-1].seq
    start_ts = messages[0].timestamp_ms
    end_ts = messages[-1].timestamp_ms
    has_media = any(m.has_media for m in messages)
    return MessageWindow(
        window_id=_window_id(session_id, start_seq, end_seq),
        session_id=session_id,
        messages=list(messages),
        start_seq=start_seq,
        end_seq=end_seq,
        start_time_ms=start_ts,
        end_time_ms=end_ts,
        has_media=has_media,
    )
