"""从保留逐条时间戳的 Session 构建确定性分析块。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xuwen.analysis.models import AnalysisBlock
from xuwen.core.models import NormalizedMessage, Session


def build_analysis_blocks(
    sessions: list[Session],
    *,
    self_name: str,
    friend_name: str,
    char_budget: int = 10_000,
    timezone: str = "Asia/Shanghai",
) -> list[AnalysisBlock]:
    """按字符预算聚合连续会话，超长会话在消息边界拆分。"""
    budget = max(1_000, char_budget)
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    units: list[tuple[str, int, int, str, int]] = []
    for session in sessions:
        rendered = [
            _render_message(message, self_name=self_name, friend_name=friend_name, timezone=tz)
            for message in session.messages
        ]
        current: list[str] = []
        current_chars = 0
        current_start = session.start_time_ms
        current_end = session.start_time_ms
        current_count = 0
        for message, line in zip(session.messages, rendered, strict=True):
            line_chars = len(line) + 1
            if current and current_chars + line_chars > budget:
                units.append(
                    (
                        session.session_id,
                        current_start,
                        current_end,
                        "\n".join(current),
                        current_count,
                    )
                )
                current = []
                current_chars = 0
                current_start = message.timestamp_ms
                current_end = message.timestamp_ms
                current_count = 0
            current.append(line)
            current_chars += line_chars
            current_count += 1
            current_end = message.timestamp_ms
        if current:
            units.append(
                (
                    session.session_id,
                    current_start,
                    current_end,
                    "\n".join(current),
                    current_count,
                )
            )

    blocks: list[AnalysisBlock] = []
    pending: list[tuple[str, int, int, str, int]] = []
    pending_chars = 0
    for unit in units:
        if pending and pending_chars + len(unit[3]) > budget:
            blocks.append(_make_block(pending))
            pending = []
            pending_chars = 0
        pending.append(unit)
        pending_chars += len(unit[3])
    if pending:
        blocks.append(_make_block(pending))
    return blocks


def _make_block(units: list[tuple[str, int, int, str, int]]) -> AnalysisBlock:
    text = "\n\n".join(unit[3] for unit in units)
    session_ids = list(dict.fromkeys(unit[0] for unit in units))
    digest_input = f"{units[0][1]}\n{units[-1][2]}\n{text}".encode()
    block_id = "blk-" + hashlib.sha256(digest_input).hexdigest()[:20]
    return AnalysisBlock(
        block_id=block_id,
        start_time_ms=units[0][1],
        end_time_ms=units[-1][2],
        session_ids=session_ids,
        message_count=sum(unit[4] for unit in units),
        text=text,
    )


def _render_message(
    message: NormalizedMessage,
    *,
    self_name: str,
    friend_name: str,
    timezone: ZoneInfo,
) -> str:
    timestamp = datetime.fromtimestamp(message.timestamp_ms / 1000, tz=timezone)
    role = (self_name or "我") if message.is_self else (friend_name or "对方")
    content = message.text.strip() or " ".join(message.placeholders) or f"[{message.kind}]"
    return f"[{timestamp:%Y-%m-%d %H:%M}] {role}: {content}"
