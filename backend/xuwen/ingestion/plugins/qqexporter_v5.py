"""QQChatExporter V5 导入 plugin。

参考：https://github.com/shuakami/qq-chat-exporter

数据结构（顶层）：
    {
        "metadata": {"name": "QQChatExporter V5 / ...", "version": ...},
        "chatInfo": {"type": "private", "selfUid": "u_xxx", "selfName": "...", ...},
        "messages": [...]
    }

每条 message：
    {
        "id": "...", "seq": "...", "timestamp": ms, "time": "...",
        "sender": {"uid": "u_xxx", "name": "...", "remark": "..."},
        "type": "type_1" / "system" / ...,
        "content": {"text": "...", "elements": [...], "resources": [...]},
        "recalled": false/true, "system": false/true
    }
"""

from __future__ import annotations

from typing import Any

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole


class QQExporterV5Plugin:
    """QQChatExporter V5 导出 JSON 的解析插件。"""

    name = "qqexporter_v5"
    display_name = "QQChatExporter V5"

    def match(self, payload: dict[str, Any]) -> bool:
        """识别 QQChatExporter 的特征字段。"""
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            name = str(metadata.get("name") or "").lower()
            if "qqchatexporter" in name or "qq-chat-exporter" in name:
                return True
        chat_info = payload.get("chatInfo")
        if isinstance(chat_info, dict) and "selfUid" in chat_info:
            return True
        return False

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        if "messages" not in payload or not isinstance(payload["messages"], list):
            raise ParseError("payload 中缺少 messages 数组")

        messages: list[NormalizedMessage] = []
        for idx, raw in enumerate(payload["messages"]):
            if not isinstance(raw, dict):
                # 跳过非 dict 项（null / 字符串），避免一颗鼠屎坏了一锅粥
                continue
            try:
                msg = _parse_one(raw, settings, fallback_seq=idx)
            except Exception:
                # 异常 detail 只保留可追溯的 id/seq，不带入聊天原文
                continue
            if msg is not None:
                messages.append(msg)

        messages.sort(key=lambda m: (m.timestamp_ms, m.seq))
        return messages


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _parse_one(
    raw: dict[str, Any],
    settings: Settings,
    fallback_seq: int,
) -> NormalizedMessage | None:
    """解析单条消息。"""
    sender = raw.get("sender") or {}
    if not isinstance(sender, dict):
        sender = {}

    sender_uid = str(sender.get("uid") or "")
    sender_name = str(sender.get("remark") or sender.get("name") or sender.get("nickname") or "")
    raw_type = str(raw.get("type") or "")
    recalled = _parse_bool(raw.get("recalled", False))
    system = _parse_bool(raw.get("system", False))

    role = _infer_role(sender_uid, settings, system=system)

    content = raw.get("content") or {}
    if not isinstance(content, dict):
        content = {}
    text_field = str(content.get("text") or "")
    resources = content.get("resources") or []
    if not isinstance(resources, list):
        resources = []
    elements = content.get("elements") or []
    if not isinstance(elements, list):
        elements = []

    placeholders = _extract_placeholders(resources, elements)
    has_media = bool(placeholders)

    reply_info = _extract_reply(raw)

    kind = _classify_kind(
        raw_type=raw_type,
        recalled=recalled,
        system=system,
        has_text=bool(text_field.strip()),
        has_media=has_media,
        is_reply=reply_info is not None,
    )

    return NormalizedMessage(
        message_id=str(raw.get("id") or f"local-{fallback_seq}"),
        seq=_parse_int(raw.get("seq"), default=fallback_seq),
        timestamp_ms=_parse_int(raw.get("timestamp"), default=0),
        sender_uid=sender_uid,
        sender_name=sender_name,
        sender_role=role,
        kind=kind,
        raw_type=raw_type,
        text=text_field,
        placeholders=placeholders,
        reply_to_id=reply_info[0] if reply_info else None,
        reply_to_summary=reply_info[1] if reply_info else None,
        recalled=recalled,
        system=system,
        has_media=has_media,
        raw=raw,
    )


def _infer_role(uid: str, settings: Settings, *, system: bool) -> SenderRole:
    if system:
        return "system"
    if not uid:
        return "other"
    # 用集合判定：同一个人可能跨平台 / 跨账号，settings.all_self_uids 把所有
    # self UID（主 + SELF_UIDS 列表）合并；friend 同理。
    if uid in settings.all_self_uids:
        return "self"
    if uid in settings.all_friend_uids:
        return "friend"
    return "other"


def _classify_kind(
    *,
    raw_type: str,
    recalled: bool,
    system: bool,
    has_text: bool,
    has_media: bool,
    is_reply: bool,
) -> MessageKind:
    if recalled:
        return MessageKind.RECALLED
    if system or raw_type == "system":
        return MessageKind.SYSTEM
    if is_reply:
        return MessageKind.REPLY
    if has_text:
        return MessageKind.TEXT
    if has_media:
        return MessageKind.PLACEHOLDER
    if raw_type in {"type_17", "forward", "json", "type_19", "video", "audio", "file"}:
        return MessageKind.PLACEHOLDER
    return MessageKind.UNKNOWN


def _extract_placeholders(
    resources: list[Any],
    elements: list[Any],
) -> list[str]:
    tags: list[str] = []
    for r in resources:
        if not isinstance(r, dict):
            continue
        t = str(r.get("type") or "").lower()
        if t == "image":
            tags.append("[图片]")
        elif t == "audio":
            tags.append("[语音]")
        elif t == "video":
            tags.append("[视频]")
        elif t == "file":
            tags.append("[文件]")
        else:
            tags.append(f"[{t or '附件'}]")
    return tags


def _extract_reply(raw: dict[str, Any]) -> tuple[str, str] | None:
    content = raw.get("content")
    if isinstance(content, dict):
        reply = content.get("reply")
        if isinstance(reply, dict):
            return (
                str(reply.get("sourceMsgId") or reply.get("id") or ""),
                str(reply.get("text") or reply.get("summary") or "")[:120],
            )
    src = raw.get("sourceMsgInfo")
    if isinstance(src, dict):
        return (
            str(src.get("sourceMsgId") or ""),
            str(src.get("text") or "")[:120],
        )
    return None


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _parse_bool(value: Any) -> bool:
    """QQ 导出偶尔会把布尔写成字符串 "false"，直接 bool("false") 会得到 True。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "t"}
    return bool(value)
