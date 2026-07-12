"""主动开聊画像：从真人历史里学习 TA 何时、何种情况下会先发消息。"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, tzinfo
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xuwen.core.models import MessageKind, NormalizedMessage, Session
from xuwen.core.time import now_ms

PROACTIVE_PROFILE_FILENAME = "proactive_profile.json"

IdleGapBucket = Literal["short", "one_to_three_hours", "three_to_eight_hours", "overnight", "multi_day"]
OpeningType = Literal[
    "greeting",
    "life_check",
    "care",
    "continue_topic",
    "self_share",
    "playful",
    "affection",
    "wake_ping",
    "short_ping",
    "question_probe",
    "night_ping",
    "other",
]

_IDLE_BUCKETS: tuple[IdleGapBucket, ...] = (
    "short",
    "one_to_three_hours",
    "three_to_eight_hours",
    "overnight",
    "multi_day",
)
_OPENING_TYPES: tuple[OpeningType, ...] = (
    "greeting",
    "life_check",
    "care",
    "continue_topic",
    "self_share",
    "playful",
    "affection",
    "wake_ping",
    "short_ping",
    "question_probe",
    "night_ping",
    "other",
)
_SPEAKER_KEYS = ("friend", "self", "other", "unknown")

_PLACEHOLDER_RE = re.compile(r"^\[(图片|语音|视频|文件|表情|动画表情|撤回)\]$")
_CARE_RE = re.compile(r"累|难受|还好吗|咋样|怎么样|没事吧|注意|早点|休息|吃药|生病|考试|忙完")
_LIFE_CHECK_RE = re.compile(r"在吗|在不在|干嘛|干什么|忙吗|醒了|起了|睡了吗|吃了|到家|回家")
_GREETING_RE = re.compile(r"^(hi|hello|hey|嗨|哈喽|早|早安|晚安|晚上好|下午好|在吗)[呀啊嘛吗~～!！。 ]*$", re.I)
_SELF_SHARE_RE = re.compile(r"我刚|我在|我今天|我去|刚刚|睡着了|睡过了|笑死我|给你看|跟你说|我发现")
_PLAYFUL_RE = re.compile(r"哈哈|笑死|救命|草|绷不住|可恶|笨蛋|嘿嘿|？{2,}|!{2,}|！{2,}")
_AFFECTION_RE = re.compile(r"么么|啵|亲|抱抱|贴贴|爱你|想你|宝宝|宝贝|老婆|老公", re.I)
_WAKE_RE = re.compile(r"起床|起来|醒醒|醒了没|早八|早安")
_QUESTION_PROBE_RE = re.compile(r"[?？]|吗|嘛|呢|是不是|能不能|要不要|有没有|你是|行不行|可以不|可以吗")
_NIGHT_RE = re.compile(r"睡|晚安|怎么还没睡|熬夜|失眠|困|休息")


@dataclass(slots=True)
class ProactiveOpeningSample:
    """一次真人主动开聊样本。"""

    timestamp_ms: int
    hour: int
    weekday: int
    idle_gap_minutes: int
    idle_bucket: IdleGapBucket
    previous_last_speaker: str
    opening_type: OpeningType
    opening_text: str
    previous_tail: str = ""


@dataclass(slots=True)
class ProactiveProfile:
    """TA 主动开聊画像。权重均归一到 0~1，0 表示无信号。"""

    sample_size: int = 0
    positive_samples: int = 0
    self_started_sessions: int = 0
    total_sessions: int = 0
    generated_at_ms: int = 0
    min_gap_minutes: int = 120
    hour_weights: list[float] = field(default_factory=lambda: [0.0] * 24)
    weekday_weights: list[float] = field(default_factory=lambda: [0.0] * 7)
    idle_gap_weights: dict[str, float] = field(default_factory=dict)
    previous_last_speaker_weights: dict[str, float] = field(default_factory=dict)
    opening_type_weights: dict[str, float] = field(default_factory=dict)
    samples: list[ProactiveOpeningSample] = field(default_factory=list)
    summary: str = ""


@dataclass(slots=True)
class _WindowSession:
    session_id: str
    start_time_ms: int
    end_time_ms: int
    first_line: tuple[str, str] | None
    last_line: tuple[str, str] | None


def compute_proactive_profile(
    sessions: list[Session],
    *,
    min_gap_minutes: int = 120,
    sample_limit: int = 12,
    timezone: str = "UTC",
) -> ProactiveProfile:
    """从完整历史 session 学习主动开聊画像。

    session 的第一条有效消息来自 friend，且距离上一段对话超过 min_gap_minutes，
    才算一次正样本。由 self 首发的同类 session 只作为频率参考，不当强负样本。
    """
    ordered = sorted(
        [s for s in sessions if s.messages],
        key=lambda s: (s.start_time_ms, s.end_time_ms),
    )
    samples: list[ProactiveOpeningSample] = []
    self_started = 0
    tz = _timezone(timezone)

    for prev, cur in pairwise(ordered):
        first = _first_human_message(cur.messages)
        if first is None:
            continue
        gap = int((cur.start_time_ms - prev.end_time_ms) / 60_000)
        if gap < min_gap_minutes:
            continue
        if first.sender_role == "self":
            self_started += 1
            continue
        if first.sender_role != "friend" or not _usable_text(first.text):
            continue
        previous_last = _last_human_message(prev.messages)
        previous_role = previous_last.sender_role if previous_last is not None else "unknown"
        previous_tail = previous_last.text.strip() if previous_last is not None else ""
        dt = datetime.fromtimestamp(first.timestamp_ms / 1000, tz=tz)
        samples.append(
            ProactiveOpeningSample(
                timestamp_ms=first.timestamp_ms,
                hour=dt.hour,
                weekday=dt.weekday(),
                idle_gap_minutes=max(0, gap),
                idle_bucket=idle_gap_bucket(gap),
                previous_last_speaker=_speaker_key(previous_role),
                opening_type=classify_opening(first.text, hour=dt.hour),
                opening_text=_short(first.text, 80),
                previous_tail=_short(previous_tail, 80),
            )
        )

    return _build_profile(
        samples,
        self_started=self_started,
        total_sessions=len(ordered),
        min_gap_minutes=min_gap_minutes,
        sample_limit=sample_limit,
    )


def compute_proactive_profile_from_window_rows(
    rows: Iterable[dict[str, Any]],
    *,
    friend_name: str,
    self_name: str,
    min_gap_minutes: int = 120,
    sample_limit: int = 12,
    timezone: str = "UTC",
) -> ProactiveProfile:
    """从已落库 dialogue_windows 兜底重建画像。

    这是老数据兼容路径。它只能看到窗口文本，不如导入阶段基于 NormalizedMessage
    精确；但足够恢复小时、间隔、首发者和开场类型这些核心信号。
    """
    ordered = _window_rows_to_sessions(
        rows,
        friend_name=friend_name,
        self_name=self_name,
    )

    samples: list[ProactiveOpeningSample] = []
    self_started = 0
    tz = _timezone(timezone)
    for prev, cur in pairwise(ordered):
        gap = int((cur.start_time_ms - prev.end_time_ms) / 60_000)
        if gap < min_gap_minutes:
            continue
        first = cur.first_line
        if first is None:
            continue
        role, text = first
        if role == "self":
            self_started += 1
            continue
        if role != "friend" or not _usable_text(text):
            continue
        previous = prev.last_line
        previous_role = previous[0] if previous is not None else "unknown"
        previous_tail = previous[1] if previous is not None else ""
        dt = datetime.fromtimestamp(cur.start_time_ms / 1000, tz=tz)
        samples.append(
            ProactiveOpeningSample(
                timestamp_ms=cur.start_time_ms,
                hour=dt.hour,
                weekday=dt.weekday(),
                idle_gap_minutes=max(0, gap),
                idle_bucket=idle_gap_bucket(gap),
                previous_last_speaker=_speaker_key(previous_role),
                opening_type=classify_opening(text, hour=dt.hour),
                opening_text=_short(text, 80),
                previous_tail=_short(previous_tail, 80),
            )
        )

    return _build_profile(
        samples,
        self_started=self_started,
        total_sessions=len(ordered),
        min_gap_minutes=min_gap_minutes,
        sample_limit=sample_limit,
    )


def _window_rows_to_sessions(
    rows: Iterable[dict[str, Any]],
    *,
    friend_name: str,
    self_name: str,
) -> list[_WindowSession]:
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sid = str(row.get("session_id") or "")
        if not sid:
            continue
        by_session.setdefault(sid, []).append(row)

    sessions: list[_WindowSession] = []
    for sid, session_rows in by_session.items():
        ordered_rows = sorted(
            session_rows,
            key=lambda r: (
                int(r.get("start_time_ms") or 0),
                int(r.get("end_time_ms") or 0),
            ),
        )
        by_end_desc = sorted(
            session_rows,
            key=lambda r: (
                int(r.get("end_time_ms") or 0),
                int(r.get("start_time_ms") or 0),
            ),
            reverse=True,
        )
        starts = [int(row.get("start_time_ms") or 0) for row in session_rows]
        ends = [int(row.get("end_time_ms") or 0) for row in session_rows]
        first_line = _first_nonempty_speaker_line(
            ordered_rows,
            friend_name=friend_name,
            self_name=self_name,
        )
        last_line = _last_nonempty_speaker_line(
            by_end_desc,
            friend_name=friend_name,
            self_name=self_name,
        )
        sessions.append(
            _WindowSession(
                session_id=sid,
                start_time_ms=min(starts) if starts else 0,
                end_time_ms=max(ends) if ends else 0,
                first_line=first_line,
                last_line=last_line,
            )
        )

    return sorted(
        sessions,
        key=lambda s: (s.start_time_ms, s.end_time_ms, s.session_id),
    )


def _timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # datetime.UTC does not depend on the system IANA database or tzdata.
        return UTC


def save_proactive_profile(profile: ProactiveProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_proactive_profile(path: Path) -> ProactiveProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return _profile_from_dict(data)


def idle_gap_bucket(minutes: int | float) -> IdleGapBucket:
    if minutes < 60:
        return "short"
    if minutes < 180:
        return "one_to_three_hours"
    if minutes < 480:
        return "three_to_eight_hours"
    if minutes < 36 * 60:
        return "overnight"
    return "multi_day"


def classify_opening(text: str, *, hour: int | None = None) -> OpeningType:
    stripped = text.strip()
    if not stripped:
        return "other"
    if hour is not None and (hour >= 23 or hour < 6) and _NIGHT_RE.search(stripped):
        return "night_ping"
    if _GREETING_RE.search(stripped):
        return "greeting"
    if _AFFECTION_RE.search(stripped):
        return "affection"
    if _WAKE_RE.search(stripped):
        return "wake_ping"
    if _LIFE_CHECK_RE.search(stripped):
        return "life_check"
    if _CARE_RE.search(stripped):
        return "care"
    if _QUESTION_PROBE_RE.search(stripped):
        return "question_probe"
    if _SELF_SHARE_RE.search(stripped):
        return "self_share"
    if _PLAYFUL_RE.search(stripped):
        return "playful"
    if stripped.startswith("[回复") or (len(stripped) <= 24 and re.search(r"昨天|昨晚|刚才|之前|那个|上次|你说", stripped)):
        return "continue_topic"
    if _is_short_ping(stripped):
        return "short_ping"
    return "other"


def _is_short_ping(text: str) -> bool:
    if len(text) > 8:
        return False
    if text.isdigit():
        return True
    lowered = text.lower()
    if lowered in {"hi", "hey"}:
        return True
    return bool(re.fullmatch(r"[\w一-龥]{1,4}[~～!！。]?", text))


def _build_profile(
    samples: list[ProactiveOpeningSample],
    *,
    self_started: int,
    total_sessions: int,
    min_gap_minutes: int,
    sample_limit: int,
) -> ProactiveProfile:
    hour_counts = Counter(sample.hour for sample in samples)
    weekday_counts = Counter(sample.weekday for sample in samples)
    idle_counts = Counter(sample.idle_bucket for sample in samples)
    speaker_counts = Counter(sample.previous_last_speaker for sample in samples)
    opening_counts = Counter(sample.opening_type for sample in samples)

    hour_weights = _weights_for_range(hour_counts, 24)
    weekday_weights = _weights_for_range(weekday_counts, 7)
    idle_weights = _weights_for_keys(idle_counts, _IDLE_BUCKETS)
    speaker_weights = _weights_for_keys(speaker_counts, _SPEAKER_KEYS)
    opening_weights = _weights_for_keys(opening_counts, _OPENING_TYPES)

    trimmed_samples = sorted(
        samples,
        key=lambda sample: sample.timestamp_ms,
        reverse=True,
    )[:sample_limit]
    summary = _summary(
        samples,
        self_started=self_started,
        total_sessions=total_sessions,
        hour_counts=hour_counts,
        idle_counts=idle_counts,
        opening_counts=opening_counts,
    )
    return ProactiveProfile(
        sample_size=len(samples),
        positive_samples=len(samples),
        self_started_sessions=self_started,
        total_sessions=total_sessions,
        generated_at_ms=now_ms(),
        min_gap_minutes=min_gap_minutes,
        hour_weights=hour_weights,
        weekday_weights=weekday_weights,
        idle_gap_weights=idle_weights,
        previous_last_speaker_weights=speaker_weights,
        opening_type_weights=opening_weights,
        samples=trimmed_samples,
        summary=summary,
    )


def _weights_for_range(counts: Counter[int], size: int) -> list[float]:
    if not counts:
        return [0.0] * size
    peak = max(counts.values())
    return [round((counts.get(i, 0) + 1) / (peak + 1), 3) for i in range(size)]


def _weights_for_keys[T: str](counts: Counter[T], keys: tuple[T, ...]) -> dict[str, float]:
    if not counts:
        return {key: 0.0 for key in keys}
    peak = max(counts.values())
    return {key: round((counts.get(key, 0) + 1) / (peak + 1), 3) for key in keys}


def _summary(
    samples: list[ProactiveOpeningSample],
    *,
    self_started: int,
    total_sessions: int,
    hour_counts: Counter[int],
    idle_counts: Counter[IdleGapBucket],
    opening_counts: Counter[OpeningType],
) -> str:
    if not samples:
        return "历史中没有足够的 TA 主动开聊样本，运行时会走保守低频策略。"
    active_hours = [f"{hour:02d}:00" for hour, _ in hour_counts.most_common(3)]
    gap = idle_counts.most_common(1)[0][0]
    opening = opening_counts.most_common(1)[0][0]
    return (
        f"共识别 {len(samples)} 次 TA 主动开聊样本（用户首发长间隔会话 {self_started} 次，"
        f"总 session {total_sessions} 段）；高频时间：{', '.join(active_hours)}；"
        f"常见空闲间隔：{gap}；常见开场类型：{opening}。"
    )


def _first_human_message(messages: list[NormalizedMessage]) -> NormalizedMessage | None:
    for msg in messages:
        if msg.sender_role not in {"self", "friend"}:
            continue
        if msg.kind in {MessageKind.SYSTEM, MessageKind.RECALLED}:
            continue
        if _usable_text(msg.text):
            return msg
    return None


def _last_human_message(messages: list[NormalizedMessage]) -> NormalizedMessage | None:
    for msg in reversed(messages):
        if msg.sender_role not in {"self", "friend"}:
            continue
        if msg.kind in {MessageKind.SYSTEM, MessageKind.RECALLED}:
            continue
        if _usable_text(msg.text):
            return msg
    return None


def _usable_text(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and _PLACEHOLDER_RE.fullmatch(stripped) is None


def _first_speaker_line(
    text: str,
    *,
    friend_name: str,
    self_name: str,
) -> tuple[str, str] | None:
    for line in text.splitlines():
        parsed = _parse_speaker_line(line, friend_name=friend_name, self_name=self_name)
        if parsed is not None and _usable_text(parsed[1]):
            return parsed
    return None


def _first_nonempty_speaker_line(
    rows: Iterable[dict[str, Any]],
    *,
    friend_name: str,
    self_name: str,
) -> tuple[str, str] | None:
    for row in rows:
        first = _first_speaker_line(
            str(row.get("text") or ""),
            friend_name=friend_name,
            self_name=self_name,
        )
        if first is not None:
            return first
    return None


def _last_speaker_line(
    text: str,
    *,
    friend_name: str,
    self_name: str,
) -> tuple[str, str] | None:
    for line in reversed(text.splitlines()):
        parsed = _parse_speaker_line(line, friend_name=friend_name, self_name=self_name)
        if parsed is not None and _usable_text(parsed[1]):
            return parsed
    return None


def _last_nonempty_speaker_line(
    rows: Iterable[dict[str, Any]],
    *,
    friend_name: str,
    self_name: str,
) -> tuple[str, str] | None:
    for row in rows:
        last = _last_speaker_line(
            str(row.get("text") or ""),
            friend_name=friend_name,
            self_name=self_name,
        )
        if last is not None:
            return last
    return None


def _parse_speaker_line(
    line: str,
    *,
    friend_name: str,
    self_name: str,
) -> tuple[str, str] | None:
    if ":" not in line and "：" not in line:
        return None
    speaker, content = re.split(r"[:：]", line, maxsplit=1)
    speaker = speaker.strip()
    content = content.strip()
    friend_labels = {friend_name.strip(), "TA", "ta"}
    self_labels = {self_name.strip(), "我", "用户", "Me"}
    if speaker in friend_labels:
        return ("friend", content)
    if speaker in self_labels:
        return ("self", content)
    return ("other", content)


def _speaker_key(value: str) -> str:
    if value in {"friend", "self", "other"}:
        return value
    return "unknown"


def _short(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _profile_from_dict(data: dict[str, Any]) -> ProactiveProfile:
    samples: list[ProactiveOpeningSample] = []
    raw_samples = data.get("samples")
    if isinstance(raw_samples, list):
        for item in raw_samples:
            if not isinstance(item, dict):
                continue
            samples.append(
                ProactiveOpeningSample(
                    timestamp_ms=int(item.get("timestamp_ms") or 0),
                    hour=_bounded_int(item.get("hour"), 0, 23),
                    weekday=_bounded_int(item.get("weekday"), 0, 6),
                    idle_gap_minutes=max(0, int(item.get("idle_gap_minutes") or 0)),
                    idle_bucket=_coerce_idle_bucket(item.get("idle_bucket")),
                    previous_last_speaker=_speaker_key(str(item.get("previous_last_speaker") or "")),
                    opening_type=_coerce_opening_type(item.get("opening_type")),
                    opening_text=str(item.get("opening_text") or ""),
                    previous_tail=str(item.get("previous_tail") or ""),
                )
            )
    return ProactiveProfile(
        sample_size=max(0, int(data.get("sample_size") or data.get("positive_samples") or 0)),
        positive_samples=max(0, int(data.get("positive_samples") or data.get("sample_size") or 0)),
        self_started_sessions=max(0, int(data.get("self_started_sessions") or 0)),
        total_sessions=max(0, int(data.get("total_sessions") or 0)),
        generated_at_ms=max(0, int(data.get("generated_at_ms") or 0)),
        min_gap_minutes=max(1, int(data.get("min_gap_minutes") or 120)),
        hour_weights=_coerce_float_list(data.get("hour_weights"), 24),
        weekday_weights=_coerce_float_list(data.get("weekday_weights"), 7),
        idle_gap_weights=_coerce_weight_dict(data.get("idle_gap_weights"), _IDLE_BUCKETS),
        previous_last_speaker_weights=_coerce_weight_dict(
            data.get("previous_last_speaker_weights"),
            _SPEAKER_KEYS,
        ),
        opening_type_weights=_coerce_weight_dict(data.get("opening_type_weights"), _OPENING_TYPES),
        samples=samples,
        summary=str(data.get("summary") or ""),
    )


def _bounded_int(value: object, low: int, high: int) -> int:
    try:
        out = int(str(value))
    except (TypeError, ValueError):
        return low
    return min(high, max(low, out))


def _coerce_float_list(value: object, size: int) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        return [0.0] * size
    out: list[float] = []
    for item in value:
        try:
            out.append(max(0.0, min(1.0, float(item))))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _coerce_weight_dict[T: str](value: object, keys: tuple[T, ...]) -> dict[str, float]:
    raw = value if isinstance(value, dict) else {}
    out: dict[str, float] = {}
    for key in keys:
        try:
            out[key] = max(0.0, min(1.0, float(raw.get(key, 0.0))))
        except (TypeError, ValueError):
            out[key] = 0.0
    return out


def _coerce_idle_bucket(value: object) -> IdleGapBucket:
    text = str(value or "")
    if text in _IDLE_BUCKETS:
        return text
    return "overnight"


def _coerce_opening_type(value: object) -> OpeningType:
    text = str(value or "")
    if text in _OPENING_TYPES:
        return text
    return "other"
