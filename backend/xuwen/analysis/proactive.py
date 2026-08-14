"""从完整 Session 序列生成双方主动开聊分析。"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xuwen.analysis.models import (
    ProactiveAnalysisReport,
    ProactiveOpeningRecord,
    ProactiveOpeningType,
    ProactivePeriodCount,
)
from xuwen.core.models import NormalizedMessage, Session
from xuwen.persona.proactive_profile import classify_opening


def analyze_proactive_openings(
    sessions: list[Session],
    *,
    session_gap_minutes: int = 30,
    timezone: str = "Asia/Shanghai",
) -> ProactiveAnalysisReport:
    """统计双方开始的会话，并保留首次回复前的完整开场轮次。"""
    ordered = sorted(sessions, key=lambda item: (item.start_time_ms, item.end_time_ms))
    tz = _timezone(timezone)
    openings: list[ProactiveOpeningRecord] = []
    self_started = 0
    unknown_started = 0
    eligible_sessions = 0
    previous_human_session: Session | None = None

    for session in ordered:
        first_index = _first_human_index(session.messages)
        if first_index is None:
            unknown_started += 1
            continue
        eligible_sessions += 1
        first = session.messages[first_index]
        if not (first.is_friend or first.is_self):
            unknown_started += 1
            continue

        initiator: Literal["friend", "self"] = "friend" if first.is_friend else "self"
        if initiator == "self":
            self_started += 1
        opening_messages = _opening_messages(
            session.messages[first_index:],
            initiator=initiator,
        )
        if not opening_messages:
            unknown_started += 1
            eligible_sessions -= 1
            continue
        occurred = datetime.fromtimestamp(first.timestamp_ms / 1000, tz=tz)
        gap_minutes = (
            max(
                0,
                int(
                    (session.start_time_ms - previous_human_session.end_time_ms)
                    / 60_000
                ),
            )
            if previous_human_session is not None
            else None
        )
        contents = [_message_content(message) for message in opening_messages]
        contents = [content for content in contents if content]
        combined = "\n".join(contents)
        response_excerpt = _first_response(
            session.messages[first_index:],
            initiator=initiator,
        )
        openings.append(
            ProactiveOpeningRecord(
                opening_id=_opening_id(session.session_id, first.message_id),
                session_id=session.session_id,
                initiator=initiator,
                timestamp_ms=first.timestamp_ms,
                occurred_at=occurred.isoformat(),
                hour=occurred.hour,
                weekday=occurred.weekday(),
                idle_gap_minutes=gap_minutes,
                opening_type=classify_opening(combined, hour=occurred.hour),
                messages=contents,
                content=combined,
                message_count=len(contents),
                previous_tail=_previous_tail(previous_human_session),
                response_excerpt=response_excerpt,
            )
        )
        previous_human_session = session

    friend_openings = [opening for opening in openings if opening.initiator == "friend"]
    hour_counts = [0] * 24
    weekday_counts = [0] * 7
    month_counts: Counter[str] = Counter()
    type_counts: Counter[ProactiveOpeningType] = Counter()
    active_dates: set[str] = set()
    known_gaps: list[int] = []
    for opening in friend_openings:
        hour_counts[opening.hour] += 1
        weekday_counts[opening.weekday] += 1
        occurred = datetime.fromisoformat(opening.occurred_at)
        month_counts[occurred.strftime("%Y-%m")] += 1
        active_dates.add(occurred.date().isoformat())
        type_counts[opening.opening_type] += 1
        if opening.idle_gap_minutes is not None:
            known_gaps.append(opening.idle_gap_minutes)

    range_start = ""
    range_end = ""
    span_days = 0
    average_per_30_days = 0.0
    if ordered:
        start = datetime.fromtimestamp(ordered[0].start_time_ms / 1000, tz=tz)
        end = datetime.fromtimestamp(ordered[-1].end_time_ms / 1000, tz=tz)
        range_start = start.date().isoformat()
        range_end = end.date().isoformat()
        span_days = max(1, (end.date() - start.date()).days + 1)
        average_per_30_days = round(len(friend_openings) * 30 / span_days, 2)

    return ProactiveAnalysisReport(
        session_gap_minutes=max(1, session_gap_minutes),
        source_session_count=len(ordered),
        source_message_count=sum(session.message_count for session in ordered),
        eligible_session_count=eligible_sessions,
        initiative_count=len(friend_openings),
        self_started_count=self_started,
        unknown_started_count=unknown_started,
        initiative_rate=(
            round(len(friend_openings) / eligible_sessions, 4) if eligible_sessions else 0.0
        ),
        range_start=range_start,
        range_end=range_end,
        span_days=span_days,
        active_days=len(active_dates),
        average_per_30_days=average_per_30_days,
        median_idle_gap_minutes=(round(median(known_gaps)) if known_gaps else None),
        hour_counts=hour_counts,
        weekday_counts=weekday_counts,
        monthly_counts=[
            ProactivePeriodCount(period=month, count=count)
            for month, count in sorted(month_counts.items())
        ],
        opening_type_counts=dict(type_counts),
        opening_count=len(openings),
        friend_initiative_count=len(friend_openings),
        openings=openings,
    )


def load_proactive_analysis(path: str | Path) -> ProactiveAnalysisReport | None:
    """读取主动分析产物；旧数据或损坏文件不影响主动消息生成。"""
    try:
        return ProactiveAnalysisReport.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_runtime_proactive_context(
    report: ProactiveAnalysisReport | None,
    *,
    hour: int,
    weekday: int,
    max_records: int = 6,
) -> str:
    """挑选与当前时段相近的历史主动开场，供主动 RAG 查询使用。"""
    if report is None or not report.openings:
        return ""
    candidates = sorted(
        [opening for opening in report.openings if opening.initiator == "friend"],
        key=lambda opening: (
            0 if opening.hour == hour else 1,
            0 if opening.weekday == weekday else 1,
            min(abs(opening.hour - hour), 24 - abs(opening.hour - hour)),
            -opening.timestamp_ms,
        ),
    )[: max(1, max_records)]
    lines = [
        "历史主动开聊参考（仅用于寻找相似的真人记忆，不是当前事实）：",
    ]
    for opening in candidates:
        content = " ".join(opening.content.split())[:280]
        if not content:
            continue
        reason = f"，动机={opening.reason_summary}" if opening.reason_summary else ""
        lines.append(
            f"- {opening.occurred_at[:16]}，类型={opening.opening_type}{reason}，内容={content}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


def _first_human_index(messages: list[NormalizedMessage]) -> int | None:
    for index, message in enumerate(messages):
        if message.is_self or message.is_friend:
            return index
    return None


def _opening_messages(
    messages: list[NormalizedMessage],
    *,
    initiator: str,
) -> list[NormalizedMessage]:
    output: list[NormalizedMessage] = []
    for message in messages:
        is_initiator = message.is_friend if initiator == "friend" else message.is_self
        is_response = message.is_self if initiator == "friend" else message.is_friend
        if is_response:
            break
        if is_initiator and _message_content(message):
            output.append(message)
    return output


def _first_response(
    messages: list[NormalizedMessage],
    *,
    initiator: str,
) -> str:
    for message in messages:
        is_response = message.is_self if initiator == "friend" else message.is_friend
        if is_response:
            return _message_content(message)[:300]
    return ""


def _message_content(message: NormalizedMessage) -> str:
    text = message.text.strip()
    if text:
        return text
    return " ".join(item.strip() for item in message.placeholders if item.strip())


def _previous_tail(session: Session | None) -> str:
    if session is None:
        return ""
    for message in reversed(session.messages):
        if not (message.is_self or message.is_friend):
            continue
        content = _message_content(message)
        if content:
            return content[:200]
    return ""


def _opening_id(session_id: str, message_id: str) -> str:
    digest = hashlib.sha256(f"{session_id}:{message_id}".encode()).hexdigest()[:16]
    return f"opening-{digest}"


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
