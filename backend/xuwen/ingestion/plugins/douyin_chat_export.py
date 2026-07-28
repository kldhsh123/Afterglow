"""douyin-chat-export 的 ChatLab v0.0.2 JSON / JSONL 导入 plugin。

上游项目：https://github.com/TeamBreakerr/douyin-chat-export

该导出器把抖音私信规范化为 ChatLab：文本、图片、表情、分享和引用关系均使用
统一字段。图片内容是临时 CDN URL 或 data URL，本 plugin 只保留 ``[图片]``
占位符，不在文本导入阶段读取网络或把大段媒体数据写入向量库。
"""

from __future__ import annotations

import re
from typing import Any

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole
from xuwen.ingestion.plugins import (
    ImportIdentityCandidate,
    ImportInspection,
    jsonl_records,
)

_GENERATOR = "douyin-chat-export"
_PLATFORM = "douyin"

_SYSTEM_TEXTS = {
    "我们已互相关注，可以开始聊天了",
    "你们已互相关注，可以开始聊天了",
    "[系统消息]",
}
_SYSTEM_PREFIXES = (
    "[系统]",
    "[通话成功]",
    "[视频通话邀请]",
    "[一起看视频]",
)
_VOICE_TEXT_RE = re.compile(r"^\[语音(?:\s+\d+(?:\.\d+)?秒)?\]$")
_VIDEO_TEXT_RE = re.compile(r"^\[视频(?:\s+\d+(?:\.\d+)?秒)?\]$")


class DouyinChatExportPlugin:
    """解析 douyin-chat-export 生成的 ChatLab 私聊记录。"""

    name = "douyin_chat_export"
    display_name = "Douyin Chat Export"

    def match(self, payload: dict[str, Any]) -> bool:
        canonical = _canonical_payload(payload)
        return canonical is not None and _looks_like_douyin_chatlab(canonical)

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        canonical = _canonical_payload(payload)
        if canonical is None or not _looks_like_douyin_chatlab(canonical):
            raise ParseError("JSON / JSONL 不符合 douyin-chat-export ChatLab 格式")

        meta = canonical.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        conversation_type = str(meta.get("type") or "private").strip().lower()
        if conversation_type != "private":
            raise ParseError("douyin-chat-export plugin 目前只支持 private 私聊导入")

        raw_messages = canonical.get("messages")
        if not isinstance(raw_messages, list):
            raise ParseError("ChatLab payload 中缺少 messages 数组")

        messages: list[NormalizedMessage] = []
        for idx, raw in enumerate(raw_messages):
            if not isinstance(raw, dict):
                continue
            try:
                message = _parse_one(raw, settings, fallback_seq=idx)
            except (TypeError, ValueError):
                continue
            if message is not None:
                messages.append(message)

        messages.sort(key=lambda message: (message.timestamp_ms, message.seq))
        return messages

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        source_is_jsonl = jsonl_records(payload) is not None
        canonical = _canonical_payload(payload)
        if canonical is None or not _looks_like_douyin_chatlab(canonical):
            return ImportInspection("unknown", [], 0, "无法识别 Douyin Chat Export 格式")

        meta = canonical.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        owner_uid = str(meta.get("ownerId") or "").strip()
        names: dict[str, str] = {}
        counts: dict[str, int] = {}

        for member in canonical.get("members") or []:
            if not isinstance(member, dict):
                continue
            uid = str(member.get("platformId") or "").strip()
            if uid:
                names[uid] = str(member.get("accountName") or uid).strip() or uid

        raw_messages = canonical.get("messages")
        raw_messages = raw_messages if isinstance(raw_messages, list) else []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            uid = str(raw.get("sender") or "").strip()
            if not uid:
                continue
            counts[uid] = counts.get(uid, 0) + 1
            name = str(raw.get("accountName") or "").strip()
            if name:
                names.setdefault(uid, name)
            else:
                names.setdefault(uid, uid)

        if owner_uid:
            names.setdefault(owner_uid, owner_uid)
        friend_uids = [uid for uid in names if uid != owner_uid]
        friend_uid = (
            max(friend_uids, key=lambda uid: counts.get(uid, 0))
            if owner_uid and friend_uids
            else ""
        )

        ordered_uids = sorted(
            names,
            key=lambda uid: (
                0 if uid == owner_uid else 1 if uid == friend_uid else 2,
                -counts.get(uid, 0),
            ),
        )
        candidates = [
            ImportIdentityCandidate(
                name=names[uid],
                uid=uid,
                role_hint=(
                    "self" if uid == owner_uid else "friend" if uid == friend_uid else "unknown"
                ),
            )
            for uid in ordered_uids
        ]
        conversation_type = str(meta.get("type") or "private").strip().lower()
        error = "" if conversation_type == "private" else "仅支持 private 私聊导入"
        return ImportInspection(
            format="douyin_chat_export_jsonl" if source_is_jsonl else "douyin_chat_export",
            candidates=candidates,
            total_messages=len(raw_messages),
            error=error,
            format_label=(
                "Douyin Chat Export JSONL" if source_is_jsonl else "Douyin Chat Export"
            ),
        )


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    records = jsonl_records(payload)
    if records is None:
        return payload
    first = records[0]
    if first.get("_type") != "header":
        return None
    chatlab = first.get("chatlab")
    meta = first.get("meta")
    if not isinstance(chatlab, dict) or not isinstance(meta, dict):
        return None

    members: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for record in records[1:]:
        item = {key: value for key, value in record.items() if key != "_type"}
        if record.get("_type") == "member":
            members.append(item)
        elif record.get("_type") == "message":
            messages.append(item)
        else:
            return None
    return {
        "chatlab": chatlab,
        "meta": meta,
        "members": members,
        "messages": messages,
        "sourceFormat": "douyin_chat_export_jsonl",
    }


def _looks_like_douyin_chatlab(payload: dict[str, Any]) -> bool:
    chatlab = payload.get("chatlab")
    meta = payload.get("meta")
    return (
        isinstance(chatlab, dict)
        and str(chatlab.get("generator") or "").strip().lower() == _GENERATOR
        and isinstance(meta, dict)
        and str(meta.get("platform") or "").strip().lower() == _PLATFORM
        and isinstance(payload.get("messages"), list)
    )


def _parse_one(
    raw: dict[str, Any],
    settings: Settings,
    *,
    fallback_seq: int,
) -> NormalizedMessage | None:
    timestamp_ms = _parse_timestamp_ms(raw.get("timestamp"))
    if timestamp_ms is None:
        return None

    sender_uid = str(raw.get("sender") or "").strip()
    sender_name = str(raw.get("accountName") or sender_uid).strip()
    content = raw.get("content")
    content = content.strip() if isinstance(content, str) else ""
    chatlab_type = _parse_type(raw.get("type"))
    raw_type = f"chatlab_{raw.get('type', 'unknown')}"
    system = chatlab_type == 0 and _is_system_text(content)
    if not sender_uid and not system:
        return None

    reply_to_id, reply_to_summary, has_reply = _parse_reply(raw.get("replyTo"))
    placeholders: list[str] = []
    text = content

    if chatlab_type == 1:
        placeholders = ["[图片]"]
        text = ""
    elif chatlab_type == 5:
        placeholders = ["[表情]"]
        text = ""
    elif chatlab_type == 0 and _VOICE_TEXT_RE.fullmatch(content):
        placeholders = ["[语音]"]
        text = ""
    elif chatlab_type == 0 and _VIDEO_TEXT_RE.fullmatch(content):
        placeholders = ["[视频]"]
        text = ""
    elif chatlab_type == 0 and content == "[分享内容]":
        placeholders = ["[链接]"]
        text = ""
    elif chatlab_type == 24 and not content:
        placeholders = ["[链接]"]

    if system:
        kind = MessageKind.SYSTEM
    elif placeholders:
        kind = MessageKind.PLACEHOLDER
    elif has_reply:
        kind = MessageKind.REPLY
    elif chatlab_type in {0, 24} and text:
        kind = MessageKind.TEXT
    elif text:
        kind = MessageKind.UNKNOWN
    else:
        return None

    return NormalizedMessage(
        message_id=str(raw.get("platformMessageId") or f"local-{fallback_seq}"),
        seq=fallback_seq,
        timestamp_ms=timestamp_ms,
        sender_uid=sender_uid,
        sender_name=sender_name,
        sender_role=_infer_role(sender_uid, settings, system=system),
        kind=kind,
        raw_type=raw_type,
        text=text,
        placeholders=placeholders,
        reply_to_id=reply_to_id,
        reply_to_summary=reply_to_summary,
        system=system,
        has_media=bool(placeholders),
        raw=raw,
    )


def _infer_role(uid: str, settings: Settings, *, system: bool) -> SenderRole:
    if system:
        return "system"
    if uid in settings.all_self_uids:
        return "self"
    if uid in settings.all_friend_uids:
        return "friend"
    return "other"


def _parse_reply(value: Any) -> tuple[str | None, str | None, bool]:
    if not isinstance(value, dict):
        return None, None, False
    reply_id = str(value.get("replyTo") or "").strip() or None
    author = str(value.get("replyToAuthor") or "").strip()
    content = str(value.get("replyToContent") or "").strip()
    if author and content:
        summary = f"{author}: {content}"
    else:
        summary = content or author
    return reply_id, summary[:120] or None, bool(reply_id or summary)


def _is_system_text(content: str) -> bool:
    return content in _SYSTEM_TEXTS or content.startswith(_SYSTEM_PREFIXES)


def _parse_timestamp_ms(value: Any) -> int | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp <= 0:
        return None
    return timestamp if timestamp >= 1_000_000_000_000 else timestamp * 1000


def _parse_type(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None
