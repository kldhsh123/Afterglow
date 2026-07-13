"""Afterglow 专用导入格式 v1。

这是给第三方中间件使用的稳定中间格式，只支持私聊。图片等媒体只在文本导入
阶段保留引用；真正的图片理解由 `import-images <export-dir>` 后处理命令完成。
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


class AfterglowV1Plugin:
    """Afterglow chat JSON v1 解析插件。"""

    name = "afterglow_v1"
    display_name = "Afterglow Chat v1"

    def match(self, payload: dict[str, Any]) -> bool:
        canonical = _canonical_payload(payload)
        if canonical is None:
            return False
        afterglow = canonical.get("afterglow")
        if not isinstance(afterglow, dict):
            return False
        fmt = str(afterglow.get("format") or "").lower()
        version = str(afterglow.get("version") or "")
        return fmt == "afterglow-chat" and version.startswith("1.")

    def parse(
        self,
        payload: dict[str, Any],
        settings: Settings,
    ) -> list[NormalizedMessage]:
        canonical = _canonical_payload(payload)
        if canonical is None:
            raise ParseError("JSONL 不符合 Afterglow Chat 格式")
        conversation = canonical.get("conversation")
        if isinstance(conversation, dict):
            conv_type = str(conversation.get("type") or "private").lower()
            if conv_type != "private":
                raise ParseError("Afterglow v1 目前只支持 private 私聊导入")

        raw_messages = canonical.get("messages")
        if not isinstance(raw_messages, list):
            raise ParseError("payload 中缺少 messages 数组")

        participant_roles = _build_participant_roles(canonical.get("participants"))
        messages: list[NormalizedMessage] = []
        for idx, raw in enumerate(raw_messages):
            if not isinstance(raw, dict):
                continue
            try:
                msg = _parse_one(
                    raw,
                    settings,
                    participant_roles=participant_roles,
                    fallback_seq=idx,
                )
            except Exception:
                continue
            if msg is not None:
                messages.append(msg)

        messages.sort(key=lambda m: (m.timestamp_ms, m.seq))
        return messages

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        source_is_jsonl = jsonl_records(payload) is not None
        canonical = _canonical_payload(payload)
        if canonical is None:
            return ImportInspection("unknown", [], 0, "无法识别 Afterglow Chat 格式")
        candidates: list[ImportIdentityCandidate] = []
        participants = canonical.get("participants")
        if isinstance(participants, list):
            for item in participants:
                if not isinstance(item, dict):
                    continue
                uid = str(item.get("uid") or "")
                if not uid:
                    continue
                role_raw = str(item.get("role") or "").lower()
                role = role_raw if role_raw in {"self", "friend"} else "unknown"
                candidates.append(
                    ImportIdentityCandidate(
                        name=str(item.get("name") or uid),
                        uid=uid,
                        role_hint=role,  # type: ignore[arg-type]
                    )
                )
        if not candidates:
            seen: set[str] = set()
            for message in canonical.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                uid = str(message.get("sender_uid") or message.get("senderUid") or "")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                candidates.append(
                    ImportIdentityCandidate(
                        name=str(message.get("sender_name") or message.get("senderName") or uid),
                        uid=uid,
                    )
                )
        return ImportInspection(
            format="afterglow_jsonl" if source_is_jsonl else "afterglow_v1",
            candidates=candidates,
            total_messages=len(canonical.get("messages") or []),
            format_label="Afterglow Chat JSONL" if source_is_jsonl else "Afterglow Chat",
        )

    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]:
        canonical = _canonical_payload(payload)
        if canonical is None:
            return []
        refs: list[ImportImageRef] = []
        for idx, raw in enumerate(canonical.get("messages") or []):
            if not isinstance(raw, dict):
                continue
            message_id = str(
                raw.get("id")
                or raw.get("message_id")
                or raw.get("_jsonlSourceId")
                or f"local-{idx}"
            )
            for item in raw.get("attachments") or []:
                if not isinstance(item, dict) or str(item.get("type") or "").lower() != "image":
                    continue
                name = str(item.get("name") or item.get("filename") or "")
                if message_id and name:
                    refs.append(ImportImageRef(message_id, name))
        return refs


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    records = jsonl_records(payload)
    if records is None:
        return payload
    first = records[0]
    if first.get("_type") == "header":
        afterglow = first.get("afterglow")
        if not isinstance(afterglow, dict):
            return None
        conversation = first.get("conversation")
        participants: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []
        for record in records[1:]:
            item = {key: value for key, value in record.items() if key != "_type"}
            if record.get("_type") == "participant":
                participants.append(item)
            elif record.get("_type") == "message":
                messages.append(item)
            else:
                return None
        return {
            "afterglow": afterglow,
            "conversation": conversation if isinstance(conversation, dict) else {"type": "private"},
            "participants": participants,
            "messages": messages,
        }
    if all(
        ("sender_uid" in record or "senderUid" in record)
        and ("timestamp_ms" in record or "timestamp" in record)
        for record in records
    ):
        return {
            "afterglow": {"format": "afterglow-chat", "version": "1.0-jsonl"},
            "conversation": {"type": "private"},
            "participants": [],
            "messages": records,
        }
    return None


def _build_participant_roles(participants: Any) -> dict[str, SenderRole]:
    roles: dict[str, SenderRole] = {}
    if not isinstance(participants, list):
        return roles
    for item in participants:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid") or "")
        role = _parse_role(item.get("role"))
        if uid and role in {"self", "friend", "system", "other"}:
            roles[uid] = role
    return roles


def _parse_one(
    raw: dict[str, Any],
    settings: Settings,
    *,
    participant_roles: dict[str, SenderRole],
    fallback_seq: int,
) -> NormalizedMessage:
    sender_uid = str(raw.get("sender_uid") or raw.get("senderUid") or "")
    sender_name = str(raw.get("sender_name") or raw.get("senderName") or "")
    system = _parse_bool(raw.get("system", False))
    recalled = _parse_bool(raw.get("recalled", False))
    kind = _parse_kind(raw.get("kind"), raw_type=str(raw.get("raw_type") or ""))
    attachments = raw.get("attachments")
    placeholders = _parse_placeholders(raw.get("placeholders"))
    image_names = _extract_image_names(attachments)
    if image_names and "[图片]" not in placeholders:
        placeholders.append("[图片]")

    has_media = bool(placeholders or image_names)
    if recalled:
        kind = MessageKind.RECALLED
    elif system:
        kind = MessageKind.SYSTEM
    elif kind == MessageKind.TEXT and has_media and not str(raw.get("text") or "").strip():
        kind = MessageKind.PLACEHOLDER

    return NormalizedMessage(
        message_id=str(
            raw.get("id")
            or raw.get("message_id")
            or raw.get("_jsonlSourceId")
            or f"local-{fallback_seq}"
        ),
        seq=_parse_int(raw.get("seq"), default=fallback_seq),
        timestamp_ms=_parse_int(raw.get("timestamp_ms") or raw.get("timestamp"), default=0),
        sender_uid=sender_uid,
        sender_name=sender_name,
        sender_role=_infer_role(
            sender_uid,
            raw.get("sender_role") or raw.get("senderRole"),
            participant_roles,
            settings,
            system=system,
        ),
        kind=kind,
        raw_type=str(raw.get("raw_type") or raw.get("rawType") or kind.value),
        text=str(raw.get("text") or ""),
        placeholders=placeholders,
        reply_to_id=_optional_str(raw.get("reply_to_id") or raw.get("replyToId")),
        reply_to_summary=_optional_str(
            raw.get("reply_to_summary") or raw.get("replyToSummary")
        ),
        recalled=recalled,
        system=system,
        has_media=has_media,
        raw=raw,
    )


def _infer_role(
    uid: str,
    explicit: Any,
    participant_roles: dict[str, SenderRole],
    settings: Settings,
    *,
    system: bool,
) -> SenderRole:
    if system:
        return "system"
    explicit_role = _parse_role(explicit)
    if explicit_role:
        return explicit_role
    if uid in participant_roles:
        return participant_roles[uid]
    if uid in settings.all_self_uids:
        return "self"
    if uid in settings.all_friend_uids:
        return "friend"
    return "other"


def _parse_role(value: Any) -> SenderRole | None:
    role = str(value or "").strip().lower()
    if role in {"self", "friend", "system", "other"}:
        return role  # type: ignore[return-value]
    return None


def _parse_kind(value: Any, *, raw_type: str) -> MessageKind:
    raw = str(value or raw_type or "text").strip().lower()
    try:
        return MessageKind(raw)
    except ValueError:
        if raw in {"image", "audio", "video", "file", "sticker"}:
            return MessageKind.PLACEHOLDER
        return MessageKind.UNKNOWN


def _parse_placeholders(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v or "").strip()]


def _extract_image_names(attachments: Any) -> list[str]:
    if not isinstance(attachments, list):
        return []
    names: list[str] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").lower() != "image":
            continue
        name = str(item.get("name") or item.get("filename") or "")
        if name:
            names.append(name)
    return names


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


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
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "t"}
    return bool(value)
