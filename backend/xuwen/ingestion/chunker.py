"""混合 chunk 生成器：单条朋友发言（索引 A） + 对话窗口（索引 B）。

索引 A: friend_messages
    - 每条朋友消息一个 chunk
    - text = 朋友本条发言
    - dialogue_snippet = 含前 N 后 M 上下文的多轮对话（用于 embedding，因为单句歧义大）
    - context_before / context_after 是元数据，供前端"记忆溯源"展示
    - **上下文严格限制在所属 Session 内，不会跨越 session 边界**

索引 B: dialogue_windows
    - 对话窗口（splitter 已经切好）
    - text = "speaker: ... \n speaker: ..." 拼接
    - 含起止 seq、时间、消息数、是否含媒体等

warmth 暖度评分目前用简单的关键词启发式给一个 0~1 的分；
后续可以由 persona.analyzer 用 LLM 标注样本替换。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from xuwen.config import Settings
from xuwen.core.models import (
    ChunkBundle,
    DialogueWindowChunk,
    FriendMessageChunk,
    MessageKind,
    MessageWindow,
    NormalizedMessage,
    ResponsePairChunk,
    Session,
)

# 启发式：用于 warmth 标记的暖度词
_WARMTH_KEYWORDS = (
    "陪", "陪你", "在的", "我在", "抱抱", "别怕", "没事", "辛苦", "加油",
    "想你", "想我", "晚安", "早安", "早点睡", "记得吃饭", "照顾",
    "喜欢", "开心", "爱你", "谢谢", "感谢", "支持", "理解",
)


def build_friend_chunks(
    sessions: Sequence[Session],
    settings: Settings,
) -> list[FriendMessageChunk]:
    """从已切分好的 sessions 中提取所有朋友发言的 chunk。

    - 上下文仅取自**同一 session** 内（不跨 30 分钟边界）。
    - session_id 来自 splitter 输出，真实可追溯。
    - 跳过空文本 / 撤回 / 纯占位符（无附加语义）的消息。
    """
    chunks: list[FriendMessageChunk] = []
    for session in sessions:
        msgs = list(session.messages)
        for idx, m in enumerate(msgs):
            if m.sender_role != "friend":
                continue
            if not _is_chunkable(m):
                continue

            before = msgs[max(0, idx - settings.single_context_before) : idx]
            after = msgs[idx + 1 : idx + 1 + settings.single_context_after]
            snippet = _render_dialogue([*before, m, *after], settings)
            context_before = _render_dialogue(before, settings)
            context_after = _render_dialogue(after, settings)
            chunks.append(
                FriendMessageChunk(
                    chunk_id=_chunk_id_for_message(m),
                    message_id=m.message_id,
                    session_id=session.session_id,
                    seq=m.seq,
                    timestamp_ms=m.timestamp_ms,
                    text=m.text,
                    dialogue_snippet=snippet,
                    context_before=context_before,
                    context_after=context_after,
                    source="human_original",
                    trust_level=1.0,
                    warmth=_estimate_warmth(m.text),
                    tags=_collect_tags(m),
                )
            )
    return chunks


def build_window_chunks(
    windows: Sequence[MessageWindow],
    settings: Settings,
) -> list[DialogueWindowChunk]:
    """把 splitter 输出的窗口转为可入库的对话窗口 chunk。"""
    chunks: list[DialogueWindowChunk] = []
    for w in windows:
        text = _render_dialogue(w.messages, settings)
        if not text.strip():
            continue
        chunks.append(
            DialogueWindowChunk(
                chunk_id=_chunk_id("window", w.window_id),
                session_id=w.session_id,
                text=text,
                summary=None,
                start_seq=w.start_seq,
                end_seq=w.end_seq,
                start_time_ms=w.start_time_ms,
                end_time_ms=w.end_time_ms,
                message_count=len(w.messages),
                has_media=w.has_media,
                source="human_original",
                trust_level=1.0,
                tags=[],
            )
        )
    return chunks


def build_response_pair_chunks(
    sessions: Sequence[Session],
    settings: Settings,
) -> list[ResponsePairChunk]:
    """构造“用户输入 -> 朋友回复”索引。

    embedding 只看 user_text；召回后给 prompt 展示 friend_reply 和附近上下文。
    """
    chunks: list[ResponsePairChunk] = []
    for session in sessions:
        msgs = list(session.messages)
        idx = 0
        while idx < len(msgs):
            m = msgs[idx]
            if not m.is_self or not _is_pair_text(m):
                idx += 1
                continue

            user_msgs = [m]
            j = idx + 1
            while j < len(msgs) and msgs[j].is_self and _is_pair_text(msgs[j]):
                user_msgs.append(msgs[j])
                j += 1

            friend_msgs: list[NormalizedMessage] = []
            while j < len(msgs) and len(friend_msgs) < settings.single_context_after + 2:
                candidate = msgs[j]
                if candidate.is_self:
                    break
                if candidate.is_friend and _is_chunkable(candidate):
                    friend_msgs.append(candidate)
                j += 1

            if not friend_msgs:
                idx += 1
                continue

            context_before = msgs[max(0, idx - settings.single_context_before) : idx]
            context_after = msgs[j : j + settings.single_context_after]
            pair_msgs = [*context_before, *user_msgs, *friend_msgs, *context_after]
            user_text = "\n".join(m.text.strip() for m in user_msgs if m.text.strip())
            friend_reply = "\n".join(m.text.strip() for m in friend_msgs if m.text.strip())
            if not user_text or not friend_reply:
                idx = j
                continue
            chunks.append(
                ResponsePairChunk(
                    chunk_id=_chunk_id_for_pair(user_msgs, friend_msgs),
                    session_id=session.session_id,
                    user_message_ids=[m.message_id for m in user_msgs],
                    friend_message_ids=[m.message_id for m in friend_msgs],
                    user_text=user_text,
                    friend_reply=friend_reply,
                    dialogue_snippet=_render_dialogue(pair_msgs, settings),
                    start_seq=user_msgs[0].seq,
                    end_seq=friend_msgs[-1].seq,
                    start_time_ms=user_msgs[0].timestamp_ms,
                    end_time_ms=friend_msgs[-1].timestamp_ms,
                    source="human_original",
                    trust_level=1.0,
                    warmth=_estimate_warmth(friend_reply),
                    tags=_collect_pair_tags(user_msgs, friend_msgs),
                )
            )
            idx = j
    return chunks


def build_bundle(
    sessions: Sequence[Session],
    windows: Sequence[MessageWindow],
    settings: Settings,
) -> ChunkBundle:
    """方便 importer 一次性产出两路 chunks。"""
    return ChunkBundle(
        friend_chunks=build_friend_chunks(sessions, settings),
        window_chunks=build_window_chunks(windows, settings),
        response_pair_chunks=build_response_pair_chunks(sessions, settings),
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

_NON_TEXT_REGEX = re.compile(r"\[(图片|语音|视频|文件|表情|动画表情|撤回|系统消息)\]")


def _is_chunkable(m: NormalizedMessage) -> bool:
    """判断这条朋友消息是否值得作为单独 chunk 入库。"""
    if not m.text.strip():
        return False
    if m.kind in {MessageKind.RECALLED, MessageKind.SYSTEM}:
        return False
    if m.kind == MessageKind.PLACEHOLDER:
        # 纯占位符（无附加语义）也跳过；带文字的占位符消息会保留
        only_placeholders = _NON_TEXT_REGEX.sub("", m.text).strip()
        return bool(only_placeholders)
    return True


def _is_pair_text(m: NormalizedMessage) -> bool:
    if not m.text.strip():
        return False
    if m.kind in {MessageKind.RECALLED, MessageKind.SYSTEM}:
        return False
    if m.kind == MessageKind.PLACEHOLDER:
        only_placeholders = _NON_TEXT_REGEX.sub("", m.text).strip()
        return bool(only_placeholders)
    return True


def _render_dialogue(messages: Sequence[NormalizedMessage], settings: Settings) -> str:
    """把多条消息拼成 "speaker: text" 形式。

    speaker 解析规则：
    - self → settings.self_name 或 "我"
    - friend → settings.friend_name 或 "TA"
    - system → "系统"
    - other → 发送者名字或 "其他人"
    """
    out_lines: list[str] = []
    for m in messages:
        if m.kind == MessageKind.SYSTEM:
            continue
        speaker = _speaker_label(m, settings)
        text = m.text.strip()
        if not text:
            continue
        out_lines.append(f"{speaker}: {text}")
    return "\n".join(out_lines)


def _speaker_label(m: NormalizedMessage, settings: Settings) -> str:
    if m.sender_role == "self":
        return settings.self_name or "我"
    if m.sender_role == "friend":
        return settings.friend_name or "TA"
    if m.sender_role == "system":
        return "系统"
    return m.sender_name or "其他人"


def _chunk_id(prefix: str, source_id: str) -> str:
    h = hashlib.sha1(source_id.encode(), usedforsecurity=False)
    return f"{prefix}-{h.hexdigest()[:16]}"


def _chunk_id_for_message(m: NormalizedMessage) -> str:
    """为一条 friend 消息生成确定性 chunk_id。

    同时纳入 message_id / seq / timestamp_ms / sender_uid，避免：
    - message_id 缺失或重复时的冲突
    - 同 id 不同内容（重复导入但来源不同）误覆盖
    """
    raw = f"{m.message_id}|{m.seq}|{m.timestamp_ms}|{m.sender_uid}"
    h = hashlib.sha1(raw.encode(), usedforsecurity=False)
    return f"friend-{h.hexdigest()[:16]}"


def _chunk_id_for_pair(
    user_msgs: Sequence[NormalizedMessage],
    friend_msgs: Sequence[NormalizedMessage],
) -> str:
    raw = "|".join(
        [
            *(f"u:{m.message_id}:{m.seq}:{m.timestamp_ms}" for m in user_msgs),
            *(f"f:{m.message_id}:{m.seq}:{m.timestamp_ms}" for m in friend_msgs),
        ]
    )
    h = hashlib.sha1(raw.encode(), usedforsecurity=False)
    return f"pair-{h.hexdigest()[:16]}"


def _estimate_warmth(text: str) -> float:
    """启发式暖度评分（0~1）。

    简单累计命中的暖度关键词数量并做饱和。
    """
    if not text:
        return 0.0
    score = sum(1 for kw in _WARMTH_KEYWORDS if kw in text)
    if score == 0:
        return 0.0
    # 命中越多越高，但避免饱和过快；4 个就接近 1
    return min(1.0, score / 4.0)


def _collect_tags(m: NormalizedMessage) -> list[str]:
    tags: list[str] = []
    if m.has_media:
        tags.append("media")
    if m.reply_to_id:
        tags.append("reply")
    if m.kind == MessageKind.PLACEHOLDER:
        tags.append("placeholder")
    return tags


def _collect_pair_tags(
    user_msgs: Sequence[NormalizedMessage],
    friend_msgs: Sequence[NormalizedMessage],
) -> list[str]:
    tags: set[str] = set()
    for m in [*user_msgs, *friend_msgs]:
        tags.update(_collect_tags(m))
    return sorted(tags)
