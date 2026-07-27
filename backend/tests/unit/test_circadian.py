"""circadian profile 单测。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.persona.circadian import (
    CircadianProfile,
    compute_circadian_profile,
    is_night_hour_for_profile,
    load_circadian_profile,
    save_circadian_profile,
)


def _msg(ts: datetime, *, role: str = "friend") -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m-{ts.timestamp()}",
        seq=int(ts.timestamp()),
        timestamp_ms=int(ts.timestamp() * 1000),
        sender_uid="u-friend" if role == "friend" else "u-self",
        sender_name="TA" if role == "friend" else "Me",
        sender_role=role,
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="hi",
    )


def test_low_sample_falls_back_to_default():
    """样本太少时画像保持 8-23 默认，不强行推断。"""
    msgs = [_msg(datetime(2026, 5, 22, 14, 0))] * 5
    profile = compute_circadian_profile(msgs)
    assert profile.sample_size == 5
    assert profile.typical_awake_range == [8, 23]
    assert "样本不足" in profile.summary


def test_night_owl_pattern_detected():
    """凌晨 1-5 点活跃、白天稀疏的样本应识别为夜猫子作息。"""
    night_hours = [1, 2, 3, 4]
    daytime_hours = [10, 14, 16]
    msgs: list[NormalizedMessage] = []
    # 100 条夜间 + 10 条零星白天 = 明显夜猫子
    for _ in range(25):
        for h in night_hours:
            msgs.append(_msg(datetime(2026, 5, 22, h, 0)))
    for h in daytime_hours:
        msgs.append(_msg(datetime(2026, 5, 22, h, 0)))

    profile = compute_circadian_profile(msgs)
    assert profile.sample_size > 30
    # 夜猫子分数应明显高
    assert profile.night_owl_score > 0.5
    # 推断的清醒时段应该落在夜间区间附近
    start, end = profile.typical_awake_range
    # 至少包含凌晨小时之一
    nights = {1, 2, 3, 4}
    if start <= end:
        covered = set(range(start, end + 1))
    else:
        covered = set(range(start, 24)) | set(range(0, end + 1))
    assert covered & nights, f"清醒段 {start}-{end} 未覆盖凌晨夜间小时"


def test_daytime_pattern_keeps_standard_hours():
    """常规白天作息：清醒段应大致落在 8-23 范围内。"""
    msgs: list[NormalizedMessage] = []
    for h in range(8, 23):
        for _ in range(5):
            msgs.append(_msg(datetime(2026, 5, 22, h, 0)))
    profile = compute_circadian_profile(msgs)
    start, end = profile.typical_awake_range
    assert 6 <= start <= 12
    assert 19 <= end <= 23
    assert profile.night_owl_score < 0.2


def test_save_and_load_roundtrip(tmp_path: Path):
    profile = CircadianProfile(
        sample_size=100,
        typical_awake_range=[1, 14],
        night_owl_score=0.6,
        summary="夜猫子样例",
    )
    path = tmp_path / "circadian.json"
    save_circadian_profile(profile, path)
    loaded = load_circadian_profile(path)
    assert loaded is not None
    assert loaded.sample_size == 100
    assert loaded.typical_awake_range == [1, 14]
    assert abs(loaded.night_owl_score - 0.6) < 1e-6
    assert loaded.summary == "夜猫子样例"


def test_load_returns_none_when_missing(tmp_path: Path):
    assert load_circadian_profile(tmp_path / "nonexistent.json") is None


def test_is_night_hour_for_profile_night_owl():
    """夜猫子的"深夜"应该是别人的白天（10-14 点）。"""
    profile = CircadianProfile(
        sample_size=100,
        typical_awake_range=[22, 14],  # 跨午夜：晚上 10 点到下午 2 点清醒
        night_owl_score=0.6,
        summary="",
    )
    # 凌晨 2 点对夜猫子是清醒
    assert not is_night_hour_for_profile(2, profile)
    # 下午 4 点对夜猫子是深夜
    assert is_night_hour_for_profile(16, profile)
    # 凌晨 12 点（24-1）边界
    assert not is_night_hour_for_profile(0, profile)


def test_is_night_hour_for_profile_fallback_to_default():
    """无 profile / 样本太少时退回默认 22-06。"""
    assert is_night_hour_for_profile(23, None) is True
    assert is_night_hour_for_profile(3, None) is True
    assert is_night_hour_for_profile(14, None) is False
    low_sample = CircadianProfile(sample_size=10, typical_awake_range=[1, 14])
    assert is_night_hour_for_profile(23, low_sample) is True
    assert is_night_hour_for_profile(14, low_sample) is False
