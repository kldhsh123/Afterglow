"""Shared prompt helpers for companion state, memory, and stickers."""

from __future__ import annotations

from xuwen.chat_api.sticker_store import StickerStore, render_sticker_block_for_prompt
from xuwen.companion.life import LifeSnapshot
from xuwen.config import Settings
from xuwen.core.models import RetrievalResult, ScoredChunk
from xuwen.persona.card import load_persona_card
from xuwen.persona.style_profile import (
    load_style_profile,
    render_random_burst_block,
    render_style_profile_for_query,
)

_PERSONA_CARD_BOUNDARY = """【画像使用边界】
persona 画像是长期统计参考，不是今天发生的事实。
回复优先级：当前生活状态 > 关系记忆 > 本轮检索到的真实回复样本 > persona 画像。
不要凭画像或历史片段发明今天在想谁、怀疑用户、正在打游戏、现实见面、emoji 或亲密动作。"""

_LIFE_MARKER_INSTRUCTION = """【生活状态自更新（内部协议，不要向用户解释）】
如果在这一轮回复中你的真实生活状态发生了变化（如吃饭/出门/睡觉/换活动/心情转变），
在回复正文之后**追加**一个隐藏标记块（只输出**有变化**的字段）：

<life-update>
{"current_activity": "...", "recent_meal": "...", "mood": "...", "availability": "available|busy|sleeping|away"}
</life-update>

规则：
- 没有真实变化时不要输出标记块；不要为了刷新状态而编造改变。
- 标记块只能放在回复最后，且整体一次性输出，不要分散在正文中。
- 字段值都是短语，不要超过 30 字；availability 只能是上述四个值之一。
- 标记块会被后端解析并从对外回复中**剥离**，用户看不到。"""


def empty_retrieval_result() -> RetrievalResult:
    return RetrievalResult(
        friend_examples=[],
        dialogue_windows=[],
        recent_live=[],
        response_pairs=[],
        fused=[],
    )


def build_persona_card_with_companion_context(
    *,
    settings: Settings,
    life: LifeSnapshot,
    relationship_context: str,
    style_query: str = "",
    response_policy_context: str = "",
) -> str:
    """Load persona card and append high-priority companion context."""
    persona_card = load_persona_card(settings.persona_data_dir / "persona_card.md")
    blocks = [_PERSONA_CARD_BOUNDARY]
    style_profile = load_style_profile(settings.persona_data_dir / "persona_style_profile.json")
    style_block = render_style_profile_for_query(style_profile, style_query)
    if style_block:
        blocks.append(style_block)
    random_burst_block = render_random_burst_block(style_profile, style_query)
    if random_burst_block:
        blocks.append(random_burst_block)
    blocks.append(life.render_prompt_block())
    if relationship_context:
        blocks.append(relationship_context)
    aliases_block = _render_aliases_block(settings)
    if aliases_block:
        blocks.append(aliases_block)
    if response_policy_context:
        # 决策块放在最后，优先级最高（主模型读到这里时已经看完所有上下文）。
        blocks.append(response_policy_context)
    if settings.life_marker_update_enabled:
        blocks.append(_LIFE_MARKER_INSTRUCTION)

    sticker_store = StickerStore(settings)
    sticker_block = render_sticker_block_for_prompt(sticker_store.available_for_ai())
    if sticker_block:
        blocks.append(sticker_block)

    return (persona_card + "\n\n" + "\n\n".join(blocks)).strip()


def render_life_memory_context(
    retrieved: RetrievalResult,
    settings: Settings,
    *,
    max_items: int = 8,
) -> str:
    """Compress retrieved history for the life-state controller.

    The life model should learn preferences and tone from this text, not treat it
    as evidence about what happened today.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for chunk in [*retrieved.response_pairs, *retrieved.friend_examples, *retrieved.dialogue_windows]:
        line = _life_memory_line(chunk, settings)
        if not line or line in seen:
            continue
        lines.append(line)
        seen.add(line)
        if len(lines) >= max_items:
            break
    if not lines:
        return ""
    return "\n".join(lines)


def _life_memory_line(chunk: ScoredChunk, settings: Settings) -> str:
    self_name = settings.self_name or "用户"
    friend_name = settings.friend_name or "TA"
    if chunk.kind == "response_pair":
        user_text = str(chunk.metadata.get("text") or "").strip()
        friend_reply = str(chunk.metadata.get("friend_reply") or "").strip()
        if user_text and friend_reply:
            return f"- 当 {self_name} 说「{_short(user_text)}」时，{friend_name} 曾回「{_short(friend_reply)}」"
    text = (chunk.metadata.get("dialogue_snippet") or chunk.text or "").strip()
    if not text:
        return ""
    return f"- 历史片段：{_short(str(text), 140)}"


def _short(text: str, limit: int = 80) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _render_aliases_block(settings: Settings) -> str:
    """渲染"你和对方的别名"教学块。

    当没有任何别名时返回空，避免给主模型增加无意义的上下文。
    """
    friend_names = settings.all_friend_names
    self_names = settings.all_self_names
    if not (len(friend_names) > 1 or len(self_names) > 1):
        return ""

    lines = ["【你和对方的别名（重要，否则会闹笑话）】"]
    if len(friend_names) > 1:
        primary = friend_names[0]
        others = "、".join(friend_names[1:])
        lines.append(
            f"- 你（{primary}）也会被用户这样称呼：{others}。"
            "看到这些名字时不要追问\"...是谁\"，那都是叫你。"
        )
    if len(self_names) > 1:
        primary = self_names[0]
        others = "、".join(self_names[1:])
        lines.append(
            f"- 用户（{primary}）也可能被叫成：{others}。"
            "你可以根据语境选用其中一个名字回应；不要刻意频繁切换。"
        )
    lines.append(
        "- 历史记忆里如果出现这些别名，都按真人对话理解，不要把同一个人当成两个人。"
    )
    return "\n".join(lines)
