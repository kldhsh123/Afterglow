"""陪伴状态、记忆与表情包相关的共享提示词辅助函数。"""

from __future__ import annotations

from pathlib import Path

from xuwen.chat_api.sticker_store import StickerStore, render_sticker_block_for_prompt
from xuwen.companion.life import LifeSnapshot
from xuwen.config import Settings
from xuwen.core.models import RetrievalResult, ScoredChunk
from xuwen.persona.card import load_persona_card
from xuwen.persona.prompt import ChatMessage
from xuwen.persona.style_profile import (
    load_style_profile,
    render_random_burst_block,
    render_style_profile_for_query,
)

_PERSONA_CARD_BOUNDARY = (
    "[Persona boundaries]\nThe persona is a long-term statistical reference, not evidence of "
    "today's events.\nPriority: current life state > relationship memory > genuine retrieved "
    "replies > persona.\nDo not infer today's thoughts, suspicions, games, real meetings, emoji or"
    " physical intimacy from old material."
)

_LIFE_MARKER_INSTRUCTION = (
    "[Life state updates: hidden internal protocol]\nIf this reply establishes an actual change in"
    " the character's virtual state (meal, going out, sleep, activity or mood), append one hidden"
    " block AFTER the chat text:\n<life-event>A short natural-language description of the "
    "established change</life-event>\nUse the language of the conversation. Do not invent a change"
    " just to refresh state.\nEmit the entire block at the end, never scattered through the reply."
    " No JSON.\nDescribe only changes supported by the adopted reply. The backend strips the block"
    " and normalizes it."
)

_SCHEDULE_HINT_INSTRUCTION = (
    "[Scheduled tasks: hidden internal protocol]\nOnly if the user explicitly asks to be reminded,"
    " woken or notified at a time, append a hidden block after the normal reply:\n<schedule-"
    "hint>The user's scheduling request in their original language</schedule-hint>\nPreserve the "
    "user's natural time expression, task and recurrence. Do not translate it into Chinese or "
    "English, and do not calculate ISO times, cron or RRULE yourself. A separate extractor "
    "handles that.\nCasual mentions of time are not scheduling requests. Multiple explicit tasks "
    "may use separate blocks.\nThe backend strips these blocks. In the visible reply, acknowledge "
    "the request naturally."
)


def empty_retrieval_result() -> RetrievalResult:
    return RetrievalResult(
        friend_examples=[],
        dialogue_windows=[],
        history_images=[],
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
    include_schedule_hint: bool = False,
) -> str:
    """加载人格卡片，并追加高优先级的陪伴上下文。

    `include_schedule_hint`：是否注入 <schedule-hint> 内部协议指引。
    只有真正会调用 schedule_extractor 并把结果回传给客户端的路由（目前仅
    routes/chat.py 的 ChatCompletions）才应传 True。Responses API / 主动话题
    等路由若也开启该指引，AI 会输出 hint 但调用方拿不到 schedule_tasks，
    任务会被静默丢失（Finding 5）。
    """
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
    personality_context = load_personality_prompt_context(settings)
    if personality_context:
        blocks.append(personality_context)
    experimental_context = load_experimental_prompt_context(settings)
    if experimental_context:
        blocks.append(experimental_context)
    aliases_block = _render_aliases_block(settings)
    if aliases_block:
        blocks.append(aliases_block)
    if response_policy_context:
        # 决策块放在最后，优先级最高（主模型读到这里时已经看完所有上下文）。
        blocks.append(response_policy_context)
    if settings.life_marker_update_enabled:
        blocks.append(_LIFE_MARKER_INSTRUCTION)
    if include_schedule_hint and settings.schedule_extract_enabled:
        blocks.append(_SCHEDULE_HINT_INSTRUCTION)

    sticker_store = StickerStore(settings)
    sticker_block = render_sticker_block_for_prompt(sticker_store.available_for_ai())
    if sticker_block:
        blocks.append(sticker_block)

    return (persona_card + "\n\n" + "\n\n".join(blocks)).strip()


def load_personality_prompt_context(settings: Settings) -> str:
    """按独立开关读取去证据的普通人格画像。"""
    if not settings.analysis_personality_prompt_enabled:
        return ""
    path = Path(settings.analysis_data_dir) / "personality_prompt_context.md"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return content[:12000]


def load_experimental_prompt_context(settings: Settings) -> str:
    """仅在双开关开启时读取去证据的实验人格画像。"""
    if not (
        settings.analysis_experimental_enabled and settings.analysis_experimental_prompt_enabled
    ):
        return ""
    path = Path(settings.analysis_data_dir) / "experimental" / "prompt_context.md"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return content[:12000]


def render_life_memory_context(
    retrieved: RetrievalResult,
    settings: Settings,
    *,
    max_items: int = 8,
) -> str:
    """压缩检索到的历史记录，供生活状态控制器使用。

    生活状态模型应从中学习偏好和语气，不应把这些内容当作今天已经发生之事的证据。
    """
    lines: list[str] = []
    seen: set[str] = set()
    for chunk in [
        *retrieved.response_pairs,
        *retrieved.friend_examples,
        *retrieved.dialogue_windows,
        *retrieved.history_images,
    ]:
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


def render_life_memory_context_from_recent(
    recent: list[ChatMessage],
    settings: Settings,
    *,
    max_items: int = 8,
) -> str:
    """用最近几轮对话渲染 life 模型的 memory_context。

    与 render_life_memory_context（基于 retrieved）的区别：
    - 不依赖向量检索结果，可以和 retrieve 并发执行
    - 内容是真实最近对话，对"AI 当前该是什么状态"判断同样有效
    - 缺失的只是"与当前 query 语义相似的历史片段"，但 life 决策核心信号
      （时间/作息/上一次状态/用户输入）都不在那里
    """
    if not recent:
        return ""
    self_name = settings.self_name or "User"
    friend_name = settings.friend_name or "Friend"
    lines: list[str] = []
    # 取最近 max_items 条，按时间序展示
    for msg in recent[-max_items:]:
        text = (msg.content or "").strip()
        if not text:
            continue
        speaker = self_name if msg.role == "user" else friend_name
        lines.append(f"- {speaker}: {_short(text, 140)}")
    if not lines:
        return ""
    return "\n".join(lines)


def _life_memory_line(chunk: ScoredChunk, settings: Settings) -> str:
    self_name = settings.self_name or "User"
    friend_name = settings.friend_name or "Friend"
    if chunk.kind == "response_pair":
        user_text = str(chunk.metadata.get("text") or "").strip()
        friend_reply = str(chunk.metadata.get("friend_reply") or "").strip()
        if user_text and friend_reply:
            return f"- When {self_name} said: {_short(user_text)!r}, {friend_name} replied: {_short(friend_reply)!r}"
    text = (chunk.metadata.get("dialogue_snippet") or chunk.text or "").strip()
    if not text:
        return ""
    return f"- Historical excerpt: {_short(str(text), 140)}"


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

    lines = ["[Name aliases: different names for the same people]"]
    if len(friend_names) > 1:
        lines.append(
            f"- You ({friend_names[0]}) are also called: {', '.join(friend_names[1:])}. These names refer to you; do not ask who they are."
        )
    if len(self_names) > 1:
        lines.append(
            f"- The user ({self_names[0]}) is also called: {', '.join(self_names[1:])}. Choose names naturally without frequent forced changes."
        )
    lines.append(
        "- Treat these aliases as the same people in historical conversation, not separate individuals."
    )
    return "\n".join(lines)
