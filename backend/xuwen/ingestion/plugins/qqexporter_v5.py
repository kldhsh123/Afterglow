"""QQChatExporter 导入 plugin。

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
from xuwen.ingestion.plugins import (
    ImportIdentityCandidate,
    ImportImageRef,
    ImportInspection,
    jsonl_records,
)


class QQExporterV5Plugin:
    """QQChatExporter 导出 JSON / JSONL 的解析插件。"""

    name = "qqexporter_v5"
    display_name = "QQChatExporter"

    def match(self, payload: dict[str, Any]) -> bool:
        """识别 QQChatExporter 的特征字段。"""
        canonical = _canonical_payload(payload)
        if canonical is None:
            return False
        metadata = canonical.get("metadata")
        if isinstance(metadata, dict):
            name = str(metadata.get("name") or "").lower()
            if "qqchatexporter" in name or "qq-chat-exporter" in name:
                return True
        chat_info = canonical.get("chatInfo")
        if isinstance(chat_info, dict) and "selfUid" in chat_info:
            return True
        return False

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        canonical = _canonical_payload(payload)
        if canonical is None:
            raise ParseError("JSONL 不符合 QQChatExporter 消息格式")
        if "messages" not in canonical or not isinstance(canonical["messages"], list):
            raise ParseError("payload 中缺少 messages 数组")

        messages: list[NormalizedMessage] = []
        for idx, raw in enumerate(canonical["messages"]):
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

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        source_is_jsonl = jsonl_records(payload) is not None
        canonical = _canonical_payload(payload)
        if canonical is None:
            return ImportInspection("unknown", [], 0, "无法识别 QQChatExporter 格式")
        info = canonical.get("chatInfo")
        info = info if isinstance(info, dict) else {}
        self_uid = str(info.get("selfUid") or "")
        candidates: list[ImportIdentityCandidate] = []
        if self_uid:
            candidates.append(
                ImportIdentityCandidate(
                    name=str(info.get("selfName") or info.get("name") or "我"),
                    uid=self_uid,
                    role_hint="self",
                )
            )
        counts: dict[tuple[str, str], int] = {}
        for message in canonical.get("messages") or []:
            if not isinstance(message, dict):
                continue
            sender = message.get("sender")
            if not isinstance(sender, dict):
                continue
            uid = str(sender.get("uid") or sender.get("uin") or "")
            if not uid or uid == self_uid:
                continue
            name = str(sender.get("remark") or sender.get("name") or sender.get("nickname") or uid)
            key = (uid, name)
            counts[key] = counts.get(key, 0) + 1
        for index, ((uid, name), _count) in enumerate(
            sorted(counts.items(), key=lambda item: -item[1])[:20]
        ):
            candidates.append(
                ImportIdentityCandidate(
                    name=name,
                    uid=uid,
                    role_hint="friend" if self_uid and index == 0 else "unknown",
                )
            )
        return ImportInspection(
            format="qce_jsonl" if source_is_jsonl else "qqexporter_v5",
            candidates=candidates,
            total_messages=len(canonical.get("messages") or []),
            format_label="QQChatExporter JSONL" if source_is_jsonl else "QQChatExporter",
        )

    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]:
        canonical = _canonical_payload(payload)
        if canonical is None:
            return []
        refs: list[ImportImageRef] = []
        seen: set[tuple[str, str]] = set()
        for raw in canonical.get("messages") or []:
            if not isinstance(raw, dict):
                continue
            message_id = str(raw.get("id") or raw.get("_jsonlSourceId") or "")
            content = raw.get("content")
            if not message_id or not isinstance(content, dict):
                continue
            candidates: list[str] = []
            for resource in content.get("resources") or []:
                if isinstance(resource, dict) and str(resource.get("type") or "").lower() == "image":
                    candidates.append(_resource_path(resource))
            for element in content.get("elements") or []:
                if not isinstance(element, dict) or str(element.get("type") or "").lower() != "image":
                    continue
                data = element.get("data")
                if isinstance(data, dict):
                    candidates.append(_resource_path(data))
            for image_name in candidates:
                key = (message_id, image_name.lower())
                if image_name and key not in seen:
                    seen.add(key)
                    refs.append(ImportImageRef(message_id, image_name))
        return refs


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    records = jsonl_records(payload)
    if records is None:
        return payload
    if not all(
        isinstance(record.get("sender"), dict)
        and isinstance(record.get("content"), dict)
        and "timestamp" in record
        and "_type" not in record
        for record in records
    ):
        return None
    return {
        "metadata": {"name": "QQChatExporter / chunked-jsonl"},
        "chatInfo": {},
        "messages": records,
        "sourceFormat": "qce_jsonl",
    }


def _resource_path(raw: dict[str, Any]) -> str:
    return str(raw.get("localPath") or raw.get("filename") or "")


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

    sender_uid = str(sender.get("uid") or sender.get("uin") or "")
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
        message_id=str(raw.get("id") or raw.get("_jsonlSourceId") or f"local-{fallback_seq}"),
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
