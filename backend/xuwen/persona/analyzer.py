"""离线人格画像分析。

从已切分好的 sessions 中抽取朋友（friend）的语言特征：
- 高频词 / 高频字 n-gram
- 句长分布
- 标点习惯
- emoji / 占位符使用频率
- 典型回复样本（按 self → friend 对的形式）

输出 `PersonaReport`（dataclass），由 card.py 渲染成 markdown。

设计原则：纯统计，不调用任何 API；可在导入完成后立即跑。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

import jieba

from xuwen.core.models import MessageKind, NormalizedMessage, Session

# jieba 启动时会加载词典，第一次调用偏慢；提前初始化让导入阶段就完成
jieba.initialize()

# 中文标点
_PUNCTUATION = "，。！？、；：""''…—（）《》【】"
# emoji 与表情占位（粗略匹配 Unicode 表情区段）
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F☀-➿⌀-⏿]"
)
_PLACEHOLDER_RE = re.compile(r"\[(图片|语音|视频|文件|表情|动画表情|撤回)\]")
# 兼容未重新导入的历史数据：腾讯可能把表情写成任意中英文方括号名称。
# 与导入清洗规则一致，方括号内 1-12 个字符一律不参与 persona 文本统计。
_SHORT_BRACKET_PLACEHOLDER_RE = re.compile(r"\[[^\[\]\n]{1,12}\]")
_TOKEN_RE = re.compile(r"[一-鿿]+|[A-Za-z]+")
# 长串 base64url（>= 15 字符）通常是 uid 或 url 片段，统计时跳过
_LONG_BASE64_RE = re.compile(r"^[A-Za-z0-9_-]{15,}$")
# jieba 分词忽略的"虚词/无信息词"集合
_STOPWORDS = {
    # 语言学虚词
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "也", "就",
    "都", "和", "与", "或", "把", "被", "让", "去", "来", "啊", "呢",
    "吧", "啦", "哦", "嘛", "嗯", "诶", "哈", "呀", "哇", "唉",
    # 高频但不刻画风格的中性词（语气填充 / 通用动词 / 疑问代词 / QQ 元数据噪声）
    "一下", "看看", "怎么", "什么", "可以", "不是", "知道", "时候",
    "现在", "东西", "准备", "回复", "已经", "还是", "应该", "然后",
    "这种", "那种", "这个", "那个", "这样", "那样", "这里", "那里",
    "出来", "起来", "过来", "过去", "出去",
    # QQ 分享卡片字段名（type_17 / json 类消息泄漏）
    "title", "desc", "summary", "preview", "content", "url",
}


@dataclass(slots=True, frozen=True)
class TermStat:
    """词/短语统计。"""

    term: str
    count: int


@dataclass(slots=True, frozen=True)
class LengthStats:
    """句长统计（按消息字符数）。"""

    mean: float
    median: float
    short_ratio: float   # < 8 字
    long_ratio: float    # > 30 字


@dataclass(slots=True, frozen=True)
class PunctuationStats:
    """标点习惯。"""

    counts: dict[str, int] = field(default_factory=dict)
    ellipsis_ratio: float = 0.0       # 含 "…" 或 "..." 的消息比例
    question_ratio: float = 0.0       # 含 "？" / "?" 的消息比例
    exclaim_ratio: float = 0.0        # 含 "！" / "!" 的消息比例
    no_punct_ratio: float = 0.0       # 完全无标点的消息比例


@dataclass(slots=True, frozen=True)
class MediaStats:
    """媒体 / 占位符使用频率。"""

    emoji_per_message: float
    placeholder_ratio: float
    image_ratio: float
    voice_ratio: float


@dataclass(slots=True, frozen=True)
class DialogueSample:
    """一对真实对话样本：你说 → 朋友回。"""

    user_text: str
    friend_text: str
    timestamp_ms: int


@dataclass(slots=True, frozen=True)
class PersonaReport:
    """朋友画像报告。"""

    friend_name: str
    self_name: str
    total_messages: int
    friend_message_count: int

    top_terms: list[TermStat]
    top_phrases: list[TermStat]
    length: LengthStats
    punctuation: PunctuationStats
    media: MediaStats
    samples: list[DialogueSample]


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def analyze_persona(
    sessions: list[Session],
    *,
    friend_name: str,
    self_name: str,
    top_n_terms: int = 60,
    top_n_phrases: int = 30,
    sample_count: int = 20,
    json_emoji_mode: str = "raw",
) -> PersonaReport:
    """
    Analyze friend messages from pre-segmented sessions and build a persona report.
    
    Parameters:
        json_emoji_mode (str): Placeholder handling mode, either ``"raw"`` or
            ``"normalized"``.
    
    Returns:
        PersonaReport: Aggregated language, punctuation, media, and dialogue
            sample statistics.
    
    Raises:
        ValueError: If ``json_emoji_mode`` is not ``"raw"`` or ``"normalized"``.
    """
    if json_emoji_mode not in {"raw", "normalized"}:
        raise ValueError("json_emoji_mode 必须是 raw 或 normalized")
    flat: list[NormalizedMessage] = [m for s in sessions for m in s.messages]
    friend_msgs = [m for m in flat if m.is_friend and _is_useful_for_persona(m)]

    return PersonaReport(
        friend_name=friend_name,
        self_name=self_name,
        total_messages=len(flat),
        friend_message_count=len(friend_msgs),
        top_terms=_top_terms(friend_msgs, top_n_terms, json_emoji_mode),
        top_phrases=_top_phrases(friend_msgs, top_n_phrases, json_emoji_mode),
        length=_length_stats(friend_msgs),
        punctuation=_punctuation_stats(friend_msgs),
        media=_media_stats(friend_msgs),
        samples=_sample_pairs(sessions, sample_count, json_emoji_mode),
    )


# ---------------------------------------------------------------------------
# 内部统计
# ---------------------------------------------------------------------------


def _is_useful_for_persona(m: NormalizedMessage) -> bool:
    """
    Determine whether a message is suitable for language-style statistics.
    
    Parameters:
    	m (NormalizedMessage): The message to evaluate.
    
    Returns:
    	`true` if the message has usable text and is not recalled, system-generated, or a placeholder message; `false` otherwise.
    """
    if m.kind in {MessageKind.RECALLED, MessageKind.SYSTEM, MessageKind.PLACEHOLDER}:
        return False
    return bool(m.text.strip())


def _top_terms(
    messages: list[NormalizedMessage], n: int, json_emoji_mode: str
) -> list[TermStat]:
    """
    Return the most frequently occurring meaningful terms in the messages.
    
    Parameters:
    	messages (list[NormalizedMessage]): Messages from which to extract terms.
    	n (int): Maximum number of terms to return.
    	json_emoji_mode (str): Placeholder and emoji-cleaning mode.
    
    Returns:
    	list[TermStat]: Terms ordered by descending frequency.
    """
    counter: Counter[str] = Counter()
    for m in messages:
        text = _strip_placeholders(m.text, json_emoji_mode)
        for token in jieba.cut(text):
            token = token.strip()
            if not token or token in _STOPWORDS or token in _PUNCTUATION:
                continue
            if len(token) < 2:
                continue
            if not _TOKEN_RE.search(token):
                continue
            # 跳过看起来像 uid / token / 长 base64 串的内容
            if _LONG_BASE64_RE.match(token):
                continue
            counter[token] += 1
    return [TermStat(term=t, count=c) for t, c in counter.most_common(n)]


def _top_phrases(
    messages: list[NormalizedMessage], n: int, json_emoji_mode: str
) -> list[TermStat]:
    """
    Identify frequently repeated two- to four-character phrases in friend messages.
    
    Parameters:
    	messages (list[NormalizedMessage]): Friend messages to analyze.
    	n (int): Maximum number of phrases to return.
    	json_emoji_mode (str): Placeholder and emoji-cleaning mode used before phrase extraction.
    
    Returns:
    	list[TermStat]: Phrases occurring at least twice, ordered by descending frequency.
    """
    counter: Counter[str] = Counter()
    for m in messages:
        text = _strip_placeholders(m.text, json_emoji_mode)
        # 仅在汉字 token 上做 n-gram，避免标点干扰
        for run in _TOKEN_RE.findall(text):
            if len(run) < 2:
                continue
            # 跳过看起来像 uid 的英数长串
            if _LONG_BASE64_RE.match(run):
                continue
            for size in (2, 3, 4):
                for i in range(len(run) - size + 1):
                    phrase = run[i : i + size]
                    # 短语本身也要过停用词（如"一下"、"看看"会出现在 n-gram 里）
                    if phrase in _STOPWORDS:
                        continue
                    counter[phrase] += 1
    # 过滤掉只出现一次的短语
    return [TermStat(term=t, count=c) for t, c in counter.most_common(n) if c >= 2]


def _length_stats(messages: list[NormalizedMessage]) -> LengthStats:
    lengths = [len(m.text) for m in messages if m.text]
    if not lengths:
        return LengthStats(mean=0.0, median=0.0, short_ratio=0.0, long_ratio=0.0)
    n = len(lengths)
    short = sum(1 for x in lengths if x < 8) / n
    long = sum(1 for x in lengths if x > 30) / n
    return LengthStats(
        mean=round(mean(lengths), 2),
        median=round(median(lengths), 2),
        short_ratio=round(short, 3),
        long_ratio=round(long, 3),
    )


def _punctuation_stats(messages: list[NormalizedMessage]) -> PunctuationStats:
    if not messages:
        return PunctuationStats()
    counts: Counter[str] = Counter()
    ellipsis = question = exclaim = no_punct = 0
    n = len(messages)
    for m in messages:
        text = m.text
        for ch in text:
            if ch in _PUNCTUATION or ch in ".!?,;:":
                counts[ch] += 1
        if "…" in text or "..." in text:
            ellipsis += 1
        if "？" in text or "?" in text:
            question += 1
        if "！" in text or "!" in text:
            exclaim += 1
        # 完全无标点
        if not any(ch in _PUNCTUATION or ch in ".!?,;:" for ch in text):
            no_punct += 1
    return PunctuationStats(
        counts=dict(counts.most_common(15)),
        ellipsis_ratio=round(ellipsis / n, 3),
        question_ratio=round(question / n, 3),
        exclaim_ratio=round(exclaim / n, 3),
        no_punct_ratio=round(no_punct / n, 3),
    )


def _media_stats(messages: list[NormalizedMessage]) -> MediaStats:
    """
    Summarize emoji and media-marker usage across messages.
    
    Parameters:
    	messages (list[NormalizedMessage]): Messages to analyze.
    
    Returns:
    	MediaStats: Average emoji usage and ratios for placeholders, images, and voice messages.
    """
    if not messages:
        return MediaStats(0.0, 0.0, 0.0, 0.0)
    n = len(messages)
    emoji_total = sum(len(_EMOJI_RE.findall(m.text)) for m in messages)
    placeholder = sum(1 for m in messages if _PLACEHOLDER_RE.search(m.text))
    image = sum(1 for m in messages if "[图片]" in m.text)
    voice = sum(1 for m in messages if "[语音]" in m.text)
    return MediaStats(
        emoji_per_message=round(emoji_total / n, 3),
        placeholder_ratio=round(placeholder / n, 3),
        image_ratio=round(image / n, 3),
        voice_ratio=round(voice / n, 3),
    )


def _sample_pairs(
    sessions: list[Session], n: int, json_emoji_mode: str
) -> list[DialogueSample]:
    """
    Extracts representative adjacent self-to-friend dialogue samples.
    
    Samples are cleaned according to the selected emoji mode, exclude friend replies
    shorter than four characters, and are distributed across reply-length categories
    before near-duplicate replies are removed and the results are ordered by timestamp.
    
    Parameters:
        sessions (list[Session]): Sessions containing the messages to examine.
        n (int): Maximum number of samples to return.
        json_emoji_mode (str): Placeholder-cleaning mode, either ``"raw"`` or
            ``"normalized"``.
    
    Returns:
        list[DialogueSample]: Selected dialogue samples in ascending timestamp order.
    """
    pairs: list[DialogueSample] = []
    for session in sessions:
        msgs = session.messages
        for i in range(1, len(msgs)):
            prev, curr = msgs[i - 1], msgs[i]
            if not prev.is_self or not curr.is_friend:
                continue
            user_text = _strip_placeholders(prev.text, json_emoji_mode).strip()
            friend_text = _strip_placeholders(curr.text, json_emoji_mode).strip()
            if len(friend_text) < 4:
                continue
            pairs.append(
                DialogueSample(
                    user_text=user_text or prev.text.strip(),
                    friend_text=friend_text,
                    timestamp_ms=curr.timestamp_ms,
                )
            )

    if len(pairs) <= n:
        return pairs

    # 分桶
    short_bucket: list[DialogueSample] = []
    mid_bucket: list[DialogueSample] = []
    long_bucket: list[DialogueSample] = []
    for p in pairs:
        L = len(p.friend_text)
        if L < 8:
            short_bucket.append(p)
        elif L <= 20:
            mid_bucket.append(p)
        else:
            long_bucket.append(p)

    def _evenly(items: list[DialogueSample], k: int) -> list[DialogueSample]:
        if k <= 0 or not items:
            return []
        if len(items) <= k:
            return list(items)
        step = len(items) / k
        return [items[int(i * step)] for i in range(k)]

    # 按比例分配名额：希望短:中:长 = 3:4:3，至少保留每桶 1 条
    quota_short = max(1, n * 3 // 10) if short_bucket else 0
    quota_long = max(1, n * 3 // 10) if long_bucket else 0
    quota_mid = max(1, n - quota_short - quota_long) if mid_bucket else 0
    # 如果某桶空，分给其它桶
    spare = n - quota_short - quota_mid - quota_long
    if spare > 0:
        if mid_bucket:
            quota_mid += spare
        elif short_bucket:
            quota_short += spare
        elif long_bucket:
            quota_long += spare

    chosen = (
        _evenly(short_bucket, quota_short)
        + _evenly(mid_bucket, quota_mid)
        + _evenly(long_bucket, quota_long)
    )

    # 去重：以前 10 字作 key
    seen: set[str] = set()
    out: list[DialogueSample] = []
    for p in chosen:
        key = p.friend_text[:10]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    # 按时间排序，让样本以时间顺序呈现
    out.sort(key=lambda s: s.timestamp_ms)
    return out[:n]


def _strip_placeholders(text: str, json_emoji_mode: str = "raw") -> str:
    """
    Remove placeholder and emoji-marker text from a message.
    
    Parameters:
        text (str): Message text to clean.
        json_emoji_mode (str): Cleaning mode; `"normalized"` removes `[/表情]`, while `"raw"` removes short bracketed placeholders.
    
    Returns:
        str: Message text with recognized placeholders replaced by spaces.
    """
    text = _PLACEHOLDER_RE.sub(" ", text)
    if json_emoji_mode == "normalized":
        return text.replace("[/表情]", " ")
    return _SHORT_BRACKET_PLACEHOLDER_RE.sub(" ", text)


# ---------------------------------------------------------------------------
# 序列化（供 card.py 与 scripts/analyze_persona.py 使用）
# ---------------------------------------------------------------------------


def report_to_dict(report: PersonaReport) -> dict[str, Any]:
    """把 PersonaReport 转为可 JSON 序列化的字典。"""
    return {
        "friend_name": report.friend_name,
        "self_name": report.self_name,
        "total_messages": report.total_messages,
        "friend_message_count": report.friend_message_count,
        "top_terms": [{"term": t.term, "count": t.count} for t in report.top_terms],
        "top_phrases": [{"term": t.term, "count": t.count} for t in report.top_phrases],
        "length": {
            "mean": report.length.mean,
            "median": report.length.median,
            "short_ratio": report.length.short_ratio,
            "long_ratio": report.length.long_ratio,
        },
        "punctuation": {
            "counts": report.punctuation.counts,
            "ellipsis_ratio": report.punctuation.ellipsis_ratio,
            "question_ratio": report.punctuation.question_ratio,
            "exclaim_ratio": report.punctuation.exclaim_ratio,
            "no_punct_ratio": report.punctuation.no_punct_ratio,
        },
        "media": {
            "emoji_per_message": report.media.emoji_per_message,
            "placeholder_ratio": report.media.placeholder_ratio,
            "image_ratio": report.media.image_ratio,
            "voice_ratio": report.media.voice_ratio,
        },
        "samples": [
            {
                "user_text": s.user_text,
                "friend_text": s.friend_text,
                "timestamp_ms": s.timestamp_ms,
            }
            for s in report.samples
        ],
    }
