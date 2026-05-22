"""作息画像 (circadian profile)：从历史聊天推断 TA 的清醒时段。

life 时间线系统默认假设"白天醒、晚上睡"，但夜班 / 跨时区 / 自由职业的人
作息可能完全相反。这里通过统计 friend_messages 的时间分布，识别：

- hourly_activity：24 小时活跃度直方图（按小时统计消息条数，再归一化）
- typical_awake_range：最长连续高活跃段 [start_hour, end_hour]（可跨午夜）
- night_owl_score：22:00-06:00 时段占总活跃度的比例（0=完全白天作息，1=完全夜猫子）
- weekday_vs_weekend：工作日和周末两套 hourly 数据，便于 life 小模型判断今天该用哪套

输出到 .data/persona/circadian_profile.json，由 LifeStateManager 加载用于：
1. 生成 daily_plan 兜底骨架时，把 sleep/wake 移到真实区间
2. life 小模型决策 prompt 里告诉它"TA 通常清醒时段是 X-Y"
3. _should_update_state 的"深夜熬夜词触发"区间用 profile 的 night-zone
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from xuwen.core.models import NormalizedMessage

CIRCADIAN_PROFILE_FILENAME = "circadian_profile.json"

# 用于推断"清醒时段"的活跃度阈值（归一化到 [0, 1]）：
# - 0.5 = 该小时活跃度达到峰值的 50% 才算"清醒"
_AWAKE_THRESHOLD_RATIO = 0.35

# 深夜区间用于计算 night_owl_score：22 点到次日 6 点
_NIGHT_HOURS: frozenset[int] = frozenset({22, 23, 0, 1, 2, 3, 4, 5})


@dataclass(slots=True)
class CircadianProfile:
    """TA 的作息画像。"""

    sample_size: int = 0
    hourly_activity: list[float] = field(default_factory=lambda: [0.0] * 24)
    weekday_hourly: list[float] = field(default_factory=lambda: [0.0] * 24)
    weekend_hourly: list[float] = field(default_factory=lambda: [0.0] * 24)
    typical_awake_range: list[int] = field(default_factory=lambda: [8, 23])
    night_owl_score: float = 0.0
    # 简单描述给小模型看的一句话，例如"通常深夜 1 点到下午 2 点活跃"
    summary: str = ""


def compute_circadian_profile(
    messages: Iterable[NormalizedMessage],
    *,
    min_samples: int = 30,
) -> CircadianProfile:
    """根据真人原始消息时间戳生成作息画像。

    样本太少时返回默认（8-23 清醒）；夜猫子分数 0；并标注 summary。
    """
    friend_msgs = [m for m in messages if getattr(m, "sender_role", "") == "friend"]
    sample_size = len(friend_msgs)
    if sample_size < min_samples:
        return CircadianProfile(
            sample_size=sample_size,
            summary=f"样本不足（仅 {sample_size} 条），沿用默认作息 8-23",
        )

    # 累计每小时活跃度，分工作日/周末
    all_counts = [0] * 24
    weekday_counts = [0] * 24
    weekend_counts = [0] * 24
    night_count = 0

    for m in friend_msgs:
        dt = datetime.fromtimestamp(m.timestamp_ms / 1000)
        hour = dt.hour
        all_counts[hour] += 1
        if dt.weekday() >= 5:
            weekend_counts[hour] += 1
        else:
            weekday_counts[hour] += 1
        if hour in _NIGHT_HOURS:
            night_count += 1

    hourly = _normalize(all_counts)
    weekday = _normalize(weekday_counts)
    weekend = _normalize(weekend_counts)
    awake_range = _infer_awake_range(hourly)
    night_owl_score = round(night_count / sample_size, 3) if sample_size else 0.0
    summary = _summarize(awake_range, night_owl_score)

    return CircadianProfile(
        sample_size=sample_size,
        hourly_activity=hourly,
        weekday_hourly=weekday,
        weekend_hourly=weekend,
        typical_awake_range=list(awake_range),
        night_owl_score=night_owl_score,
        summary=summary,
    )


def _normalize(counts: list[int]) -> list[float]:
    peak = max(counts) if counts else 0
    if peak == 0:
        return [0.0] * 24
    return [round(c / peak, 3) for c in counts]


def _infer_awake_range(hourly: list[float]) -> tuple[int, int]:
    """找到最长的连续高活跃段（允许跨午夜）。"""
    threshold = _AWAKE_THRESHOLD_RATIO
    # 把序列复制一份接在后面，处理跨午夜的连续段
    doubled = hourly + hourly
    best_start, best_len = 0, 0
    cur_start, cur_len = None, 0
    for i, v in enumerate(doubled):
        if v >= threshold:
            if cur_start is None:
                cur_start = i
                cur_len = 1
            else:
                cur_len += 1
            if cur_len > best_len:
                best_len = cur_len
                best_start = cur_start
        else:
            cur_start = None
            cur_len = 0
    if best_len == 0:
        return (8, 23)
    if best_len >= 24:
        # 全天活跃，退化为默认
        return (8, 23)
    start = best_start % 24
    end = (best_start + best_len - 1) % 24
    return (start, end)


def _summarize(awake: tuple[int, int], night_owl: float) -> str:
    start, end = awake
    range_text = f"{start:02d}:00 - {end:02d}:00"
    if night_owl >= 0.4:
        tone = "明显夜猫子作息"
    elif night_owl >= 0.2:
        tone = "偏晚睡型"
    else:
        tone = "常规白天作息"
    return f"通常清醒时段：{range_text}（{tone}，深夜活跃占比 {night_owl:.0%}）"


def save_circadian_profile(profile: CircadianProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_circadian_profile(path: Path) -> CircadianProfile | None:
    """加载 profile；文件不存在或解析失败返回 None（调用方走默认作息）。"""
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return CircadianProfile(
        sample_size=int(data.get("sample_size") or 0),
        hourly_activity=_coerce_hourly(data.get("hourly_activity")),
        weekday_hourly=_coerce_hourly(data.get("weekday_hourly")),
        weekend_hourly=_coerce_hourly(data.get("weekend_hourly")),
        typical_awake_range=_coerce_awake_range(data.get("typical_awake_range")),
        night_owl_score=float(data.get("night_owl_score") or 0.0),
        summary=str(data.get("summary") or ""),
    )


def _coerce_hourly(value: object) -> list[float]:
    if isinstance(value, list) and len(value) == 24:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return [0.0] * 24
    return [0.0] * 24


def _coerce_awake_range(value: object) -> list[int]:
    if isinstance(value, list) and len(value) == 2:
        try:
            start, end = int(value[0]), int(value[1])
            if 0 <= start <= 23 and 0 <= end <= 23:
                return [start, end]
        except (TypeError, ValueError):
            pass
    return [8, 23]


def is_night_hour_for_profile(hour: int, profile: CircadianProfile | None) -> bool:
    """给定小时是否属于"TA 的深夜区间"（用于 _should_update_state 等判断）。

    profile=None 或样本不足时退回默认深夜 22-06。
    否则使用 typical_awake_range 的补集（即非清醒时段视为深夜）。
    """
    if profile is None or profile.sample_size < 30:
        return hour >= 22 or hour < 6
    start, end = profile.typical_awake_range
    if start == end:
        return hour >= 22 or hour < 6
    if start <= end:
        # 清醒段不跨午夜：清醒 = [start, end]，深夜 = 其余
        return not (start <= hour <= end)
    # 跨午夜：清醒 = [start, 23] ∪ [0, end]，深夜 = (end, start)
    return end < hour < start
