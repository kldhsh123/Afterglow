"""主动开聊画像学习测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from xuwen.core.models import MessageKind, NormalizedMessage, Session
from xuwen.persona.proactive_profile import (
    classify_opening,
    compute_proactive_profile,
    compute_proactive_profile_from_window_rows,
)


def _ts(hour: int, minute: int = 0, day: int = 1) -> int:
    return int(datetime(2026, 1, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _utc_ts(hour: int, minute: int = 0, day: int = 1) -> int:
    return int(datetime(2026, 1, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _msg(seq: int, role: str, text: str, ts: int) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m{seq}",
        seq=seq,
        timestamp_ms=ts,
        sender_uid=f"u-{role}",
        sender_name=role,
        sender_role=role,  # type: ignore[arg-type]
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text=text,
    )


def _session(session_id: str, messages: list[NormalizedMessage]) -> Session:
    return Session(
        session_id=session_id,
        messages=messages,
        start_time_ms=messages[0].timestamp_ms,
        end_time_ms=messages[-1].timestamp_ms,
    )


def test_compute_proactive_profile_learns_friend_initiated_sessions():
    sessions = [
        _session(
            "s1",
            [
                _msg(1, "self", "我先忙了", _ts(10)),
                _msg(2, "friend", "好", _ts(10, 1)),
            ],
        ),
        _session(
            "s2",
            [
                _msg(3, "friend", "忙完没", _ts(22)),
                _msg(4, "self", "刚忙完", _ts(22, 2)),
            ],
        ),
        _session(
            "s3",
            [
                _msg(5, "self", "早", _ts(9, day=2)),
                _msg(6, "friend", "早呀", _ts(9, 1, day=2)),
            ],
        ),
    ]

    profile = compute_proactive_profile(sessions, min_gap_minutes=120)

    assert profile.positive_samples == 1
    assert profile.self_started_sessions == 1
    assert profile.hour_weights[22] == 1.0
    assert profile.idle_gap_weights["overnight"] == 1.0
    assert profile.previous_last_speaker_weights["friend"] == 1.0
    assert profile.opening_type_weights["care"] == 1.0
    assert "TA 主动开聊样本" in profile.summary


def test_compute_proactive_profile_uses_configured_timezone():
    sessions = [
        _session(
            "s1",
            [
                _msg(1, "self", "我先忙了", _utc_ts(10)),
                _msg(2, "friend", "好", _utc_ts(10, 1)),
            ],
        ),
        _session(
            "s2",
            [
                _msg(3, "friend", "忙完没", _utc_ts(16)),
                _msg(4, "self", "刚忙完", _utc_ts(16, 2)),
            ],
        ),
    ]

    profile = compute_proactive_profile(
        sessions,
        min_gap_minutes=120,
        timezone="Asia/Shanghai",
    )

    assert profile.positive_samples == 1
    assert profile.hour_weights[0] == 1.0
    assert profile.hour_weights[0] > profile.hour_weights[16]
    assert profile.samples[0].weekday == 4


def test_compute_proactive_profile_from_window_rows_is_usable_for_old_data():
    rows = [
        {
            "session_id": "s1",
            "text": "Me: 我先忙了\nTA: 好",
            "start_time_ms": _ts(10),
            "end_time_ms": _ts(10, 1),
        },
        {
            "session_id": "s2",
            "text": "TA: 在干嘛\nMe: 看书",
            "start_time_ms": _ts(14),
            "end_time_ms": _ts(14, 3),
        },
    ]

    profile = compute_proactive_profile_from_window_rows(
        rows,
        friend_name="TA",
        self_name="Me",
        min_gap_minutes=120,
    )

    assert profile.positive_samples == 1
    assert profile.hour_weights[14] == 1.0
    assert profile.opening_type_weights["life_check"] == 1.0


def test_window_rows_profile_uses_configured_timezone():
    rows = [
        {
            "session_id": "s1",
            "text": "Me: 我先忙了\nTA: 好",
            "start_time_ms": _utc_ts(10),
            "end_time_ms": _utc_ts(10, 1),
        },
        {
            "session_id": "s2",
            "text": "TA: 在干嘛\nMe: 看书",
            "start_time_ms": _utc_ts(16),
            "end_time_ms": _utc_ts(16, 3),
        },
    ]

    profile = compute_proactive_profile_from_window_rows(
        rows,
        friend_name="TA",
        self_name="Me",
        min_gap_minutes=120,
        timezone="Asia/Shanghai",
    )

    assert profile.positive_samples == 1
    assert profile.hour_weights[0] == 1.0
    assert profile.hour_weights[0] > profile.hour_weights[16]
    assert profile.samples[0].weekday == 4


def test_window_rows_profile_merges_session_end_before_measuring_gap():
    rows = [
        {
            "session_id": "s1",
            "text": "Me: 我先忙了\nTA: 好",
            "start_time_ms": _ts(10),
            "end_time_ms": _ts(10, 10),
        },
        {
            "session_id": "s1",
            "text": "TA: 后面又聊了一会\nMe: 收到",
            "start_time_ms": _ts(12),
            "end_time_ms": _ts(12, 30),
        },
        {
            "session_id": "s2",
            "text": "TA: 在干嘛\nMe: 看书",
            "start_time_ms": _ts(14),
            "end_time_ms": _ts(14, 3),
        },
    ]

    profile = compute_proactive_profile_from_window_rows(
        rows,
        friend_name="TA",
        self_name="Me",
        min_gap_minutes=180,
    )

    assert profile.positive_samples == 0
    assert profile.total_sessions == 2


def test_window_rows_profile_uses_latest_window_tail_for_previous_speaker():
    rows = [
        {
            "session_id": "s1",
            "text": "Me: 我先忙了\nTA: 好",
            "start_time_ms": _ts(10),
            "end_time_ms": _ts(10, 10),
        },
        {
            "session_id": "s1",
            "text": "TA: 后面又聊了一会\nMe: 最后一句",
            "start_time_ms": _ts(12),
            "end_time_ms": _ts(12, 30),
        },
        {
            "session_id": "s2",
            "text": "TA: 在干嘛\nMe: 看书",
            "start_time_ms": _ts(15),
            "end_time_ms": _ts(15, 3),
        },
    ]

    profile = compute_proactive_profile_from_window_rows(
        rows,
        friend_name="TA",
        self_name="Me",
        min_gap_minutes=120,
    )

    assert profile.positive_samples == 1
    assert profile.previous_last_speaker_weights["self"] == 1.0
    assert profile.samples[0].idle_gap_minutes == 150
    assert profile.samples[0].previous_tail == "最后一句"


def test_classify_opening_types():
    assert classify_opening("在干嘛") == "life_check"
    assert classify_opening("早点休息", hour=23) == "night_ping"
    assert classify_opening("我刚看到一个东西") == "self_share"
    assert classify_opening("抱抱") == "affection"
    assert classify_opening("起床") == "wake_ping"
    assert classify_opening("你是不是已经出门了") == "question_probe"
    assert classify_opening("abc") == "short_ping"
    assert classify_opening("[回复 @我: [图片]]") == "continue_topic"
    assert classify_opening("我去昨晚又睡着了") == "self_share"
