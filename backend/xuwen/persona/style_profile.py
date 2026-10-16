"""基于真实响应对构建的特定场景角色风格配置文件。

基于真实响应对构建的特定场景角色风格配置文件。普通人物卡片是一个广泛的统计摘要。该模块保持
更精确地说，查询可选配置文件：“当用户说这种话时，这位朋友通常怎么回答？"

"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from xuwen.content_i18n import content_text
from xuwen.core.models import MessageKind, NormalizedMessage, Session

_PLACEHOLDER_RE = re.compile(r"\[(图片|语音|视频|文件|表情|动画表情|撤回)\]")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F☀-➿⌀-⏿]")
_QUESTION_RE = re.compile(r"[?？]|吗|嘛|呢$")
_INTIMACY_RE = re.compile(
    "想你|抱抱|抱住|亲|爱你|喜欢你|贴贴|老婆|宝宝|宝贝|\\b(?:miss you|love you|hug|hugs|kiss|cuddle|babe|baby)\\b",
    re.I,
)
_RANDOM_BURST_RE = re.compile(
    "(啊{3,}|哈{3,}|呜{3,}|哇{3,}|救命|绷不住|笑死|草|卧槽|发疯|疯了|我要闹了|不活了|爆炸|阴暗|扭曲|爬行|尖叫|胡言乱语|可恶|受不了了)|\\b(?:aaaa+|haha(?:ha)+|lmao|losing my mind|screaming|i can\\x27t take it)\\b",
    re.I,
)
_LIGHT_TONE_RE = re.compile(
    "(哈哈|笑死|草|救命|可爱|想你|抱抱|贴贴|发疯|疯了|怎么这样|笨蛋|嘿嘿|？{2,}|!{2,}|！{2,})|\\b(?:haha|lol|lmao|cute|miss you|hugs|silly|hehe)\\b",
    re.I,
)
_SERIOUS_TONE_RE = re.compile(
    "(为什么|怎么解决|解释|原因|报错|失败|难受|崩溃|怎么办|帮我|求助|在干嘛|在干什么|吃了吗|睡了吗|今天|昨天|现实|钱|医院|自杀|伤害)|\\b(?:why|how to fix|explain|error|failed|upset|overwhelmed|help me|hospital|suicide|harm)\\b",
    re.I,
)


@dataclass(slots=True, frozen=True)
class StyleSceneSample:
    user_text: str
    friend_reply: str
    timestamp_ms: int


@dataclass(slots=True, frozen=True)
class StyleSceneProfile:
    scene_id: str
    title: str
    evidence_count: int
    confidence: str
    avg_reply_chars: float
    short_reply_ratio: float
    question_reply_ratio: float
    emoji_reply_ratio: float
    image_placeholder_ratio: float
    intimacy_reply_ratio: float
    samples: list[StyleSceneSample] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class RandomBurstProfile:
    evidence_count: int
    confidence: str
    avg_reply_chars: float
    question_reply_ratio: float
    emoji_reply_ratio: float
    image_placeholder_ratio: float
    samples: list[StyleSceneSample] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class PersonaStyleProfile:
    friend_name: str
    self_name: str
    scenes: list[StyleSceneProfile]
    random_burst: RandomBurstProfile | None = None


@dataclass(slots=True, frozen=True)
class RandomBurstDecision:
    allowed: bool
    probability: float
    reason: str
    profile: RandomBurstProfile | None


@dataclass(slots=True, frozen=True)
class _SceneRule:
    scene_id: str
    title: str
    patterns: tuple[str, ...]


_SCENE_RULES: tuple[_SceneRule, ...] = (
    _SceneRule(
        "life_check",
        "Small talk / checking in",
        ("在干嘛", "在干什么", "干嘛呢", "忙吗", "在吗", "你呢", "醒了吗", "起了吗"),
    ),
    _SceneRule(
        "miss_you",
        "Missing someone / affection",
        ("想你", "想我", "抱抱", "抱住", "亲", "爱你", "喜欢你", "贴贴"),
    ),
    _SceneRule(
        "sleep",
        "Good night / sleep",
        ("晚安", "睡觉", "睡了吗", "睡了没", "困", "熬夜", "失眠", "怎么还没睡"),
    ),
    _SceneRule(
        "comfort",
        "Comfort / low mood",
        ("难受", "好累", "累死", "烦", "不开心", "哭", "崩溃", "委屈", "emo"),
    ),
    _SceneRule(
        "meal",
        "Meals / everyday needs",
        ("吃了吗", "吃饭", "吃什么", "饿", "外卖", "奶茶", "咖啡", "早饭", "午饭", "晚饭"),
    ),
    _SceneRule(
        "game",
        "Games / entertainment",
        ("游戏", "打游戏", "开黑", "上号", "原神", "王者", "lol", "LOL", "steam", "Steam"),
    ),
)

_DEFAULT_SCENE = _SceneRule("casual", "Casual conversation", ())

_EN_SCENE_PATTERNS = {
    "life_check": (
        "what are you doing",
        "what are you up to",
        "are you busy",
        "are you there",
        "how about you",
    ),
    "miss_you": ("miss you", "love you", "hug", "hugs", "kiss", "cuddle"),
    "sleep": ("good night", "sleep", "sleepy", "still awake", "insomnia"),
    "comfort": ("feeling down", "so tired", "upset", "unhappy", "crying", "overwhelmed", "anxious"),
    "meal": (
        "have you eaten",
        "food",
        "hungry",
        "takeout",
        "coffee",
        "breakfast",
        "lunch",
        "dinner",
    ),
    "game": ("game", "gaming", "play together", "genshin", "league of legends", "steam"),
}


def build_style_profile(
    sessions: list[Session],
    *,
    friend_name: str,
    self_name: str,
    sample_limit_per_scene: int = 8,
) -> PersonaStyleProfile:
    buckets: dict[str, list[StyleSceneSample]] = {
        rule.scene_id: [] for rule in (*_SCENE_RULES, _DEFAULT_SCENE)
    }
    for user_msg, friend_msg in _iter_response_pairs(sessions):
        user_text = _clean_text(user_msg.text)
        friend_reply = friend_msg.text.strip()
        if not user_text or not friend_reply:
            continue
        if _PLACEHOLDER_RE.fullmatch(friend_reply):
            continue
        sample = StyleSceneSample(
            user_text=user_text,
            friend_reply=friend_reply,
            timestamp_ms=friend_msg.timestamp_ms,
        )
        buckets[_scene_for_text(user_text).scene_id].append(sample)

    scenes: list[StyleSceneProfile] = []
    for rule in (*_SCENE_RULES, _DEFAULT_SCENE):
        samples = buckets[rule.scene_id]
        if not samples:
            continue
        scenes.append(
            _build_scene_profile(
                rule,
                samples,
                sample_limit=sample_limit_per_scene,
            )
        )

    return PersonaStyleProfile(
        friend_name=friend_name,
        self_name=self_name,
        scenes=scenes,
        random_burst=_build_random_burst_profile(
            [
                StyleSceneSample(
                    user_text=_clean_text(user.text),
                    friend_reply=friend.text.strip(),
                    timestamp_ms=friend.timestamp_ms,
                )
                for user, friend in _iter_response_pairs(sessions)
                if _is_random_burst(friend.text)
            ],
            sample_limit=sample_limit_per_scene,
        ),
    )


def save_style_profile(profile: PersonaStyleProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_style_profile(path: Path) -> PersonaStyleProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return _profile_from_dict(data)


def render_style_profile_for_query(
    profile: PersonaStyleProfile | None,
    query: str,
    *,
    max_scenes: int = 2,
) -> str:
    if profile is None or not profile.scenes:
        return ""
    scene_by_id = {scene.scene_id: scene for scene in profile.scenes}
    selected: list[StyleSceneProfile] = []
    primary = scene_by_id.get(_scene_for_text(query).scene_id)
    if primary is not None:
        selected.append(primary)
    casual = scene_by_id.get(_DEFAULT_SCENE.scene_id)
    if casual is not None and casual not in selected:
        selected.append(casual)
    if not selected:
        selected = sorted(
            profile.scenes,
            key=lambda scene: scene.evidence_count,
            reverse=True,
        )

    rendered = [_render_scene(scene, profile) for scene in selected[:max_scenes]]
    return "\n\n".join(part for part in rendered if part)


def render_random_burst_block(profile: PersonaStyleProfile | None, query: str) -> str:
    decision = decide_random_burst(profile, query)
    if decision.profile is None:
        return ""
    p = decision.profile
    lines = [
        "[Chaotic/playful style gate]",
        "These samples show speech habits, not facts, and cannot replace a proper answer.",
        f"- Allowed this turn: {('yes' if decision.allowed else 'no')}",
        f"- Suggested probability: {round(decision.probability * 100)}%",
        f"- Reason: {decision.reason}",
        f"- Evidence count: {p.evidence_count}; confidence: {p.confidence}",
        f"- Mean length: {p.avg_reply_chars:.1f} characters; question ratio: {_pct(p.question_reply_ratio)}; emoji ratio: {_pct(p.emoji_reply_ratio)}",
        "Borrow this style sparingly only when the user is relaxed, playful, affectionate or chaotic. Never use it for factual/state questions, explanations, comfort or requests for help.",
    ]
    if decision.allowed:
        lines.append("- Genuine samples: ")
        for sample in p.samples[:4]:
            lines.append(f"  - {_short(sample.friend_reply, 64)}")
    return "\n".join(lines)


def decide_random_burst(profile: PersonaStyleProfile | None, query: str) -> RandomBurstDecision:
    p = profile.random_burst if profile is not None else None
    if p is None or p.evidence_count == 0:
        return RandomBurstDecision(False, 0.0, "No available samples", p)
    if _SERIOUS_TONE_RE.search(query):
        return RandomBurstDecision(
            False, 0.0, "This request needs facts, current state, explanation or comfort", p
        )
    if _RANDOM_BURST_RE.search(query):
        return RandomBurstDecision(
            True, 0.45, "The user is also expressing a chaotic/playful mood", p
        )
    if _LIGHT_TONE_RE.search(query):
        return RandomBurstDecision(
            True, 0.18, "The tone is relaxed; occasional borrowing is allowed", p
        )
    return RandomBurstDecision(True, 0.05, "Casual chat: very rare stylistic touches only", p)


def _iter_response_pairs(
    sessions: list[Session],
) -> list[tuple[NormalizedMessage, NormalizedMessage]]:
    pairs: list[tuple[NormalizedMessage, NormalizedMessage]] = []
    for session in sessions:
        messages = session.messages
        for i in range(1, len(messages)):
            prev, curr = messages[i - 1], messages[i]
            if not prev.is_self or not curr.is_friend:
                continue
            if not _usable_message(prev) or not _usable_message(curr):
                continue
            pairs.append((prev, curr))
    return pairs


def _usable_message(message: NormalizedMessage) -> bool:
    if message.kind in {MessageKind.RECALLED, MessageKind.SYSTEM}:
        return False
    return bool(message.text.strip())


def _scene_for_text(text: str) -> _SceneRule:
    lowered = text.lower()
    for rule in _SCENE_RULES:
        if any(pattern.lower() in lowered for pattern in rule.patterns) or any(
            re.search(r"(?<!\w)" + re.escape(pattern) + r"(?!\w)", lowered)
            for pattern in _EN_SCENE_PATTERNS.get(rule.scene_id, ())
        ):
            return rule
    return _DEFAULT_SCENE


def _build_scene_profile(
    rule: _SceneRule,
    samples: list[StyleSceneSample],
    *,
    sample_limit: int,
) -> StyleSceneProfile:
    replies = [s.friend_reply for s in samples]
    count = len(replies)
    lengths = [len(_clean_text(reply)) for reply in replies]
    chosen = _choose_samples(samples, sample_limit)
    return StyleSceneProfile(
        scene_id=rule.scene_id,
        title=content_text(rule.title),
        evidence_count=count,
        confidence=_confidence(count),
        avg_reply_chars=round(sum(lengths) / count, 2) if lengths else 0.0,
        short_reply_ratio=_ratio(length < 8 for length in lengths),
        question_reply_ratio=_ratio(bool(_QUESTION_RE.search(reply)) for reply in replies),
        emoji_reply_ratio=_ratio(bool(_EMOJI_RE.search(reply)) for reply in replies),
        image_placeholder_ratio=_ratio("[图片]" in reply for reply in replies),
        intimacy_reply_ratio=_ratio(bool(_INTIMACY_RE.search(reply)) for reply in replies),
        samples=chosen,
    )


def _build_random_burst_profile(
    samples: list[StyleSceneSample],
    *,
    sample_limit: int,
) -> RandomBurstProfile | None:
    if not samples:
        return None
    replies = [s.friend_reply for s in samples]
    lengths = [len(_clean_text(reply)) for reply in replies]
    count = len(replies)
    return RandomBurstProfile(
        evidence_count=count,
        confidence=_confidence(count),
        avg_reply_chars=round(sum(lengths) / count, 2) if lengths else 0.0,
        question_reply_ratio=_ratio(bool(_QUESTION_RE.search(reply)) for reply in replies),
        emoji_reply_ratio=_ratio(bool(_EMOJI_RE.search(reply)) for reply in replies),
        image_placeholder_ratio=_ratio("[图片]" in reply for reply in replies),
        samples=_choose_samples(samples, sample_limit),
    )


def _choose_samples(
    samples: list[StyleSceneSample],
    limit: int,
) -> list[StyleSceneSample]:
    if len(samples) <= limit:
        return list(samples)
    step = len(samples) / limit
    chosen = [samples[int(i * step)] for i in range(limit)]
    seen: set[str] = set()
    out: list[StyleSceneSample] = []
    for sample in chosen:
        key = sample.friend_reply[:12]
        if key in seen:
            continue
        seen.add(key)
        out.append(sample)
    return out


def _render_scene(scene: StyleSceneProfile, profile: PersonaStyleProfile) -> str:
    lines = [
        f"[Scene profile: {scene.title}]",
        f"- Evidence count: {scene.evidence_count}; confidence: {scene.confidence}",
        f"- Mean reply length: {scene.avg_reply_chars:.1f} characters; short-reply ratio: {_pct(scene.short_reply_ratio)}",
        f"- Question/follow-up ratio: {_pct(scene.question_reply_ratio)}",
        f"- Emoji ratio: {_pct(scene.emoji_reply_ratio)}; image-placeholder ratio: {_pct(scene.image_placeholder_ratio)}",
        f"- Intimacy-expression ratio: {_pct(scene.intimacy_reply_ratio)}",
    ]
    if scene.confidence == "low":
        lines.append("- Weak evidence: use lightly, without strong style conclusions.")
    lines.append("- Genuine samples: ")
    for sample in scene.samples[:4]:
        lines.append(
            f"  - {profile.self_name}：{_short(sample.user_text, 48)} / {profile.friend_name}：{_short(sample.friend_reply, 64)}"
        )
    return "\n".join(lines)


def _profile_from_dict(data: object) -> PersonaStyleProfile | None:
    if not isinstance(data, dict):
        return None
    scenes_raw = data.get("scenes")
    if not isinstance(scenes_raw, list):
        return None
    scenes: list[StyleSceneProfile] = []
    for item in scenes_raw:
        if not isinstance(item, dict):
            continue
        samples_raw = item.get("samples")
        samples: list[StyleSceneSample] = []
        if isinstance(samples_raw, list):
            for sample in samples_raw:
                if not isinstance(sample, dict):
                    continue
                samples.append(
                    StyleSceneSample(
                        user_text=str(sample.get("user_text") or ""),
                        friend_reply=str(sample.get("friend_reply") or ""),
                        timestamp_ms=int(sample.get("timestamp_ms") or 0),
                    )
                )
        scenes.append(
            StyleSceneProfile(
                scene_id=str(item.get("scene_id") or ""),
                title=str(item.get("title") or ""),
                evidence_count=int(item.get("evidence_count") or 0),
                confidence=str(item.get("confidence") or "low"),
                avg_reply_chars=float(item.get("avg_reply_chars") or 0.0),
                short_reply_ratio=float(item.get("short_reply_ratio") or 0.0),
                question_reply_ratio=float(item.get("question_reply_ratio") or 0.0),
                emoji_reply_ratio=float(item.get("emoji_reply_ratio") or 0.0),
                image_placeholder_ratio=float(item.get("image_placeholder_ratio") or 0.0),
                intimacy_reply_ratio=float(item.get("intimacy_reply_ratio") or 0.0),
                samples=samples,
            )
        )
    return PersonaStyleProfile(
        friend_name=str(data.get("friend_name") or "TA"),
        self_name=str(data.get("self_name") or "用户"),
        scenes=scenes,
        random_burst=_random_burst_from_dict(data.get("random_burst")),
    )


def _random_burst_from_dict(data: object) -> RandomBurstProfile | None:
    if not isinstance(data, dict):
        return None
    samples_raw = data.get("samples")
    samples: list[StyleSceneSample] = []
    if isinstance(samples_raw, list):
        for sample in samples_raw:
            if not isinstance(sample, dict):
                continue
            samples.append(
                StyleSceneSample(
                    user_text=str(sample.get("user_text") or ""),
                    friend_reply=str(sample.get("friend_reply") or ""),
                    timestamp_ms=int(sample.get("timestamp_ms") or 0),
                )
            )
    return RandomBurstProfile(
        evidence_count=int(data.get("evidence_count") or 0),
        confidence=str(data.get("confidence") or "low"),
        avg_reply_chars=float(data.get("avg_reply_chars") or 0.0),
        question_reply_ratio=float(data.get("question_reply_ratio") or 0.0),
        emoji_reply_ratio=float(data.get("emoji_reply_ratio") or 0.0),
        image_placeholder_ratio=float(data.get("image_placeholder_ratio") or 0.0),
        samples=samples,
    )


def _confidence(count: int) -> str:
    if count >= 20:
        return "high"
    if count >= 8:
        return "medium"
    return "low"


def _ratio(values: Iterable[bool]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(1 for value in items if value) / len(items), 3)


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _clean_text(text: str) -> str:
    return " ".join(_PLACEHOLDER_RE.sub(" ", text).split())


def _is_random_burst(text: str) -> bool:
    cleaned = _clean_text(text)
    if _RANDOM_BURST_RE.search(cleaned):
        return True
    if len(cleaned) <= 12 and re.search(r"([啊哈呜哇])\1{2,}", cleaned):
        return True
    return False


def _short(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
