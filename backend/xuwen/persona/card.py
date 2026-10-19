"""把 PersonaReport 渲染为 markdown 卡片，并提供加载/保存接口。

卡片会直接喂给 prompt 模板，因此要保持简洁、贴近自然语言、不带统计学黑话。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xuwen.persona.analyzer import (
    DialogueSample,
    PersonaReport,
    TermStat,
    report_to_dict,
)


def render_persona_card(report: PersonaReport) -> str:
    """根据画像统计渲染人类可读的 markdown 卡片。

    输出会被嵌入 prompt 模板的 `{{ persona_card }}` 变量。
    """
    lines: list[str] = []
    name = report.friend_name or "Friend"
    lines.append(f"# {name} profile")
    lines.append("")
    lines.append(
        f"Based on {report.total_messages} historical messages; {name} sent {report.friend_message_count} messages."
    )
    lines.append("")
    lines.append(
        "> This statistical profile is a tone reference only. Current life state, relationship memory and genuine retrieved replies take priority. Never invent today's facts, emoji, intimate gestures or real-life experiences from this profile."
    )
    lines.append("")
    lines.append("## Speech rhythm")
    short = report.length.short_ratio
    long_ = report.length.long_ratio
    rhythm: list[str] = []
    if short > 0.6:
        rhythm.append("Mostly short messages, often sent in bursts")
    elif short > 0.4:
        rhythm.append("Prefers short messages")
    if long_ > 0.2:
        rhythm.append("Sometimes elaborates at length")
    if not rhythm:
        rhythm.append("Balanced message length")
    rhythm.append(
        f"Mean message length: {report.length.mean:.1f} characters (median {report.length.median:.1f} characters)"
    )
    lines.append("- " + "；".join(rhythm))
    lines.append("")
    lines.append("## Punctuation and emotion")
    punct_bits: list[str] = []
    if report.punctuation.no_punct_ratio > 0.4:
        punct_bits.append(
            f"About {int(report.punctuation.no_punct_ratio * 100)}% of messages have no punctuation"
        )
    if report.punctuation.question_ratio > 0.2:
        punct_bits.append("Often follows up with questions")
    if report.punctuation.exclaim_ratio > 0.2:
        punct_bits.append("Expressive, with frequent exclamation marks")
    if report.punctuation.ellipsis_ratio > 0.1:
        punct_bits.append("Uses ellipses for pauses or hesitation")
    if report.media.emoji_per_message > 0.3:
        punct_bits.append("Uses emoji")
    if not punct_bits:
        punct_bits.append("Uses punctuation sparingly")
    lines.append("- " + "；".join(punct_bits))
    lines.append("")
    if report.media.placeholder_ratio > 0.05:
        media_bits: list[str] = []
        if report.media.image_ratio > 0.03:
            media_bits.append(
                f"Images account for about {int(report.media.image_ratio * 100)}% (images convey reactions; never output historical [图片] placeholders)"
            )
        if report.media.voice_ratio > 0.02:
            media_bits.append("Sometimes sends voice messages")
        if media_bits:
            lines.append("## Media habits")
            for bit in media_bits:
                lines.append(f"- {bit}")
            lines.append("")
    phrases = [p for p in report.top_phrases if len(p.term) >= 2][:12]
    if phrases:
        lines.append("## Common phrases")
        lines.append("> Learn the context of these phrases without repeating them verbatim.")
        lines.append("")
        for p in phrases:
            lines.append(f'- "{p.term}" (used {p.count} times)')
        lines.append("")
    terms = report.top_terms[:20]
    if terms:
        lines.append("## Frequent words (reference)")
        lines.append(_format_terms(terms))
        lines.append("")
    if report.samples:
        lines.append("## Genuine dialogue examples")
        lines.append(
            f"> Genuine historical replies by {name}; use the tone when writing new replies, without copying."
        )
        lines.append("")
        for s in report.samples[:8]:
            lines.append(_format_sample(s, report.self_name or "User", name))
            lines.append("")
    lines.append(f"---\n*Generated at {datetime.now(UTC).isoformat(timespec='seconds')}*")
    return "\n".join(lines).strip() + "\n"


def save_persona_card(markdown: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def load_persona_card(path: Path) -> str:
    """加载已生成的卡片。文件不存在时返回空字符串（让 prompt 模板自行兜底）。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_persona_report(report: PersonaReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report_to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _format_terms(terms: list[TermStat]) -> str:
    return "、".join(f"{t.term}({t.count})" for t in terms)


def _format_sample(s: DialogueSample, self_name: str, friend_name: str) -> str:
    user = s.user_text[:80]
    friend = s.friend_text[:120]
    return f"{self_name}：{user}\n{friend_name}：{friend}"
