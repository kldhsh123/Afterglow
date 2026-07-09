"""LanceDB 表的 pyarrow schema 定义。

- friend_messages：索引 A，单条朋友发言 chunk
- dialogue_windows：索引 B，多轮对话窗口 chunk
- response_pairs：索引 C，用户输入 -> 朋友回复
- history_images：索引 D，历史聊天图片的离线视觉摘要
- live_messages：运行时累积的对话记忆（live 层）
- relationship_memories：新关系长期记忆（用户喜好 / 重要事实 / 待追问事项）

设计原则：
- 向量列固定使用 fixed_size_list<float32>[settings.embedding_dim]，避免 LanceDB 升级时类型不一致。
- 软删除：所有表都带 `deleted` 字段，检索默认过滤 deleted=false。
- timestamp 单位统一毫秒（int64），便于按时间排序与衰减计算。
"""

from __future__ import annotations

import pyarrow as pa


def friend_messages_schema(dim: int) -> pa.Schema:
    """单条朋友发言 chunk 表 schema。"""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
            pa.field("text", pa.string(), nullable=False),
            pa.field("dialogue_snippet", pa.string()),
            pa.field("context_before", pa.string()),
            pa.field("context_after", pa.string()),
            pa.field("message_id", pa.string()),
            pa.field("session_id", pa.string()),
            pa.field("seq", pa.int64()),
            pa.field("timestamp_ms", pa.int64()),
            pa.field("source", pa.string()),       # history / live
            pa.field("trust_level", pa.float32()),
            pa.field("warmth", pa.float32()),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("deleted", pa.bool_()),
            pa.field("created_at_ms", pa.int64()),
            # 语义标签（由 labeler 异步填充；空字符串表示"还没标过"）
            pa.field("mood", pa.string()),
            pa.field("topic", pa.string()),
            pa.field("importance", pa.int8()),
        ]
    )


def dialogue_windows_schema(dim: int) -> pa.Schema:
    """多轮对话窗口 chunk 表 schema。"""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
            pa.field("text", pa.string(), nullable=False),
            pa.field("summary", pa.string()),
            pa.field("session_id", pa.string()),
            pa.field("start_seq", pa.int64()),
            pa.field("end_seq", pa.int64()),
            pa.field("start_time_ms", pa.int64()),
            pa.field("end_time_ms", pa.int64()),
            pa.field("message_count", pa.int32()),
            pa.field("has_media", pa.bool_()),
            pa.field("source", pa.string()),
            pa.field("trust_level", pa.float32()),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("deleted", pa.bool_()),
            pa.field("created_at_ms", pa.int64()),
        ]
    )


def response_pairs_schema(dim: int) -> pa.Schema:
    """用户输入 -> 朋友回复 pair 表 schema。"""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
            pa.field("text", pa.string(), nullable=False),  # user_text，用于向量召回
            pa.field("friend_reply", pa.string(), nullable=False),
            pa.field("dialogue_snippet", pa.string()),
            pa.field("user_message_ids", pa.list_(pa.string())),
            pa.field("friend_message_ids", pa.list_(pa.string())),
            pa.field("session_id", pa.string()),
            pa.field("start_seq", pa.int64()),
            pa.field("end_seq", pa.int64()),
            pa.field("start_time_ms", pa.int64()),
            pa.field("end_time_ms", pa.int64()),
            pa.field("source", pa.string()),
            pa.field("trust_level", pa.float32()),
            pa.field("warmth", pa.float32()),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("deleted", pa.bool_()),
            pa.field("created_at_ms", pa.int64()),
        ]
    )


def history_images_schema(dim: int) -> pa.Schema:
    """历史聊天图片摘要表 schema。"""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
            pa.field("description", pa.string(), nullable=False),
            pa.field("message_id", pa.string()),
            pa.field("session_id", pa.string()),
            pa.field("seq", pa.int64()),
            pa.field("timestamp_ms", pa.int64()),
            pa.field("sender_uid", pa.string()),
            pa.field("sender_name", pa.string()),
            pa.field("sender_role", pa.string()),
            pa.field("image_sha", pa.string()),
            pa.field("image_name", pa.string()),
            pa.field("mime", pa.string()),
            pa.field("size", pa.int64()),
            pa.field("context_before", pa.string()),
            pa.field("context_after", pa.string()),
            pa.field("vision_model", pa.string()),
            pa.field("vision_prompt_version", pa.string()),
            pa.field("source", pa.string()),
            pa.field("trust_level", pa.float32()),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("deleted", pa.bool_()),
            pa.field("created_at_ms", pa.int64()),
        ]
    )


def live_messages_schema(dim: int) -> pa.Schema:
    """运行时对话记忆表 schema。

    vector 列也保留，但允许 placeholder（全 0 向量）以便未向量化的消息也能落库。
    attachments：用户消息附带的图片 sha 列表（按图片去重存到 .data/images/<sha>.<ext>）。
    """
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
            pa.field("text", pa.string(), nullable=False),
            pa.field("role", pa.string(), nullable=False),  # user / assistant
            pa.field("conversation_id", pa.string()),
            pa.field("confirmed", pa.bool_()),
            pa.field("source", pa.string()),  # 始终 "live"
            pa.field("trust_level", pa.float32()),
            pa.field("deleted", pa.bool_()),
            pa.field("created_at_ms", pa.int64()),
            pa.field("attachments", pa.list_(pa.string())),
        ]
    )


def relationship_memories_schema(dim: int) -> pa.Schema:
    """新关系长期记忆表 schema。"""
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
            pa.field("text", pa.string(), nullable=False),
            pa.field("kind", pa.string()),  # preference / fact / plan / boundary / rhythm / note
            pa.field("importance", pa.int8()),
            pa.field("source", pa.string()),  # chat / proactive / manual
            pa.field("conversation_id", pa.string()),
            pa.field("created_at_ms", pa.int64()),
            pa.field("updated_at_ms", pa.int64()),
            pa.field("deleted", pa.bool_()),
        ]
    )


# 表名常量，避免在业务代码里散落字符串
TABLE_FRIEND_MESSAGES = "friend_messages"
TABLE_DIALOGUE_WINDOWS = "dialogue_windows"
TABLE_RESPONSE_PAIRS = "response_pairs"
TABLE_HISTORY_IMAGES = "history_images"
TABLE_LIVE_MESSAGES = "live_messages"
TABLE_RELATIONSHIP_MEMORIES = "relationship_memories"
