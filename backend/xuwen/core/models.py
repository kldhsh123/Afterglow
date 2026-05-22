"""核心领域模型。

整个 ingestion / memory / chat_api 流水线都基于本模块定义的数据结构。
保持纯数据类，不依赖任何 IO 或第三方框架。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

# 发送者角色
SenderRole = Literal["self", "friend", "system", "other"]


class MessageKind(StrEnum):
    """消息的语义类型，统一离散值，便于过滤与统计。"""

    TEXT = "text"           # 普通文本
    REPLY = "reply"         # 引用回复
    PLACEHOLDER = "placeholder"  # 仅含占位符（图片 / 语音 / 视频 / 文件等）
    RECALLED = "recalled"   # 撤回
    SYSTEM = "system"       # 系统事件
    UNKNOWN = "unknown"     # 未识别


@dataclass(slots=True)
class NormalizedMessage:
    """标准化后的单条消息。

    所有上游解析器（目前是 QQChatExporter V5）都要把原始数据转成这个结构。
    """

    message_id: str
    seq: int
    timestamp_ms: int
    sender_uid: str
    sender_name: str
    sender_role: SenderRole
    kind: MessageKind
    raw_type: str           # 原始 type 字段（如 "type_1" / "type_17"）
    text: str               # 经过 cleaner 处理后的可读文本
    placeholders: list[str] = field(default_factory=list)  # [图片]/[语音]/[视频]/[文件]
    reply_to_id: str | None = None
    reply_to_summary: str | None = None
    recalled: bool = False
    system: bool = False
    has_media: bool = False
    raw: dict[str, Any] | None = None  # 可选保留原始 JSON 引用，用于调试

    @property
    def is_friend(self) -> bool:
        """是否来自需要模仿的"朋友"。"""
        return self.sender_role == "friend"

    @property
    def is_self(self) -> bool:
        """是否为用户自己发送。"""
        return self.sender_role == "self"


@dataclass(slots=True)
class Session:
    """一段连续对话。

    切分规则由 splitter.split_sessions 决定，默认相邻消息间隔 > 30 分钟即切断。
    """

    session_id: str
    messages: list[NormalizedMessage]
    start_time_ms: int
    end_time_ms: int

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def friend_message_count(self) -> int:
        return sum(1 for m in self.messages if m.is_friend)

    @property
    def self_message_count(self) -> int:
        return sum(1 for m in self.messages if m.is_self)

    @property
    def duration_ms(self) -> int:
        return self.end_time_ms - self.start_time_ms


@dataclass(slots=True)
class MessageWindow:
    """从某个 session 切出的滑动窗口（多轮对话）。"""

    window_id: str
    session_id: str
    messages: list[NormalizedMessage]
    start_seq: int
    end_seq: int
    start_time_ms: int
    end_time_ms: int
    has_media: bool = False


@dataclass(slots=True)
class FriendMessageChunk:
    """索引 A：单条朋友发言 chunk。"""

    chunk_id: str
    message_id: str
    session_id: str
    seq: int
    timestamp_ms: int
    text: str                   # 朋友这条发言的文本
    dialogue_snippet: str       # 前文 + 当前发言（用于 embedding）
    context_before: str         # 前 N 条上下文（仅元数据）
    context_after: str          # 后 M 条上下文（仅元数据）
    source: Literal["history", "live", "human_original", "user_new", "ai_generated"] = "history"
    trust_level: float = 1.0    # 0~1，越高越信任
    warmth: float = 0.0         # 暖度评分，影响检索 boost
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DialogueWindowChunk:
    """索引 B：多轮对话窗口 chunk。"""

    chunk_id: str
    session_id: str
    text: str                   # speaker: ... 格式拼接
    summary: str | None
    start_seq: int
    end_seq: int
    start_time_ms: int
    end_time_ms: int
    message_count: int
    has_media: bool
    source: Literal["history", "live", "human_original", "user_new", "ai_generated"] = "history"
    trust_level: float = 1.0
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResponsePairChunk:
    """索引 C：用户输入 -> 朋友回复。

    用于回答“用户现在这样说时，对方历史上通常怎么回”，避免只召回到用户自己的原话。
    """

    chunk_id: str
    session_id: str
    user_message_ids: list[str]
    friend_message_ids: list[str]
    user_text: str
    friend_reply: str
    dialogue_snippet: str
    start_seq: int
    end_seq: int
    start_time_ms: int
    end_time_ms: int
    source: Literal["history", "live", "human_original", "user_new", "ai_generated"] = "history"
    trust_level: float = 1.0
    warmth: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ChunkBundle:
    """一次 ingestion 产出的所有 chunk。"""

    friend_chunks: list[FriendMessageChunk]
    window_chunks: list[DialogueWindowChunk]
    response_pair_chunks: list[ResponsePairChunk] = field(default_factory=list)


@dataclass(slots=True)
class ImportReport:
    """importer 完成后的执行报告。"""

    total_raw_messages: int
    parsed_messages: int
    skipped_messages: int
    sessions: int
    friend_chunks: int
    window_chunks: int
    response_pairs: int
    embedded_friend: int
    embedded_window: int
    embedded_response_pairs: int
    upserted_friend: int
    upserted_window: int
    upserted_response_pairs: int
    duration_seconds: float
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScoredChunk:
    """召回结果（带分数）。

    显式字段方便前端直接消费（特别是"记忆溯源"浮窗需要的 sender_name / timestamp / session_id）。
    无显式字段对应的额外信息可放在 `metadata`。
    """

    chunk_id: str
    kind: Literal["friend", "window", "live", "response_pair"]
    text: str
    score: float
    rank: int
    timestamp_ms: int
    session_id: str = ""
    sender_name: str = ""
    sender_role: SenderRole = "other"
    source: Literal["history", "live", "human_original", "user_new", "ai_generated"] = "history"
    warmth: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievalQuery:
    """检索请求。"""

    query_text: str
    conversation_id: str | None = None
    top_k_friend: int | None = None
    top_k_window: int | None = None
    final_k: int | None = None
    now_ms: int | None = None


@dataclass(slots=True)
class RetrievalResult:
    """retriever 返回的融合结果。"""

    friend_examples: list[ScoredChunk]
    dialogue_windows: list[ScoredChunk]
    recent_live: list[ScoredChunk]
    response_pairs: list[ScoredChunk]
    fused: list[ScoredChunk]
