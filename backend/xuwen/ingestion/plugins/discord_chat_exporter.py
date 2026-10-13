"""DiscordChatExporter JSON 导入，不依赖文件名或导出界面语言。"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole
from xuwen.ingestion.plugins import ImportIdentityCandidate, ImportImageRef, ImportInspection


class DiscordChatExporterPlugin:
    name = "discord_chat_exporter"
    display_name = "DiscordChatExporter"

    def match(self, payload: dict[str, Any]) -> bool:
        channel = payload.get("channel")
        return (
            isinstance(channel, dict)
            and bool(channel.get("id"))
            and isinstance(channel.get("type"), str)
            and isinstance(payload.get("messages"), list)
            and "exportedAt" in payload
            and "guild" in payload
        )

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        if not self.match(payload):
            return ImportInspection("unknown", [], 0, "无法识别 DiscordChatExporter JSON")
        authors: dict[str, str] = {}
        for raw in payload["messages"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("author"), dict):
                continue
            author = raw["author"]
            uid = str(author.get("id") or "").strip()
            if uid:
                authors[uid] = str(author.get("nickname") or author.get("name") or uid)
        return ImportInspection(
            self.name,
            [ImportIdentityCandidate(name, uid) for uid, name in authors.items()],
            len(payload["messages"]),
            format_label=self.display_name,
        )

    def parse(self, payload: dict[str, Any], settings: Settings) -> list[NormalizedMessage]:
        if not self.match(payload):
            raise ParseError("JSON 不符合 DiscordChatExporter 格式")
        messages: list[NormalizedMessage] = []
        for seq, raw in enumerate(payload["messages"]):
            if not isinstance(raw, dict):
                continue
            author = raw.get("author")
            if not isinstance(author, dict):
                continue
            uid = str(author.get("id") or "").strip()
            native_id = str(raw.get("id") or "").strip()
            if not uid or not native_id:
                continue
            try:
                date = datetime.fromisoformat(str(raw.get("timestamp") or ""))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=ZoneInfo(settings.app_timezone))
                timestamp = int(date.timestamp() * 1000)
            except (ValueError, OverflowError):
                continue
            content = raw.get("content")
            text = content.strip() if isinstance(content, str) else ""
            raw_type = str(raw.get("type") or "Default")
            system = raw_type not in {"Default", "Reply", "ChatInputCommand", "ContextMenuCommand"}
            role: SenderRole = (
                "system"
                if system
                else "self"
                if uid in settings.all_self_uids
                else "friend"
                if uid in settings.all_friend_uids
                else "other"
            )
            placeholders: list[str] = []
            attachments = raw.get("attachments")
            for attachment in attachments if isinstance(attachments, list) else []:
                if not isinstance(attachment, dict):
                    continue
                ext = PurePosixPath(
                    str(
                        attachment.get("fileName")
                        or urlsplit(str(attachment.get("url") or "")).path
                    )
                ).suffix.lower()
                label = (
                    "[图片]"
                    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
                    else "[视频]"
                    if ext in {".mp4", ".webm", ".mov"}
                    else "[语音]"
                    if ext in {".mp3", ".ogg", ".wav", ".m4a"}
                    else "[文件]"
                )
                if label not in placeholders:
                    placeholders.append(label)
            if isinstance(raw.get("stickers"), list) and raw["stickers"]:
                placeholders.append("[表情]")
            embeds = raw.get("embeds")
            if not text and not placeholders and isinstance(embeds, list) and embeds:
                placeholders.append("[链接]")
            reference = raw.get("reference")
            reply_id = str(reference.get("messageId") or "") if isinstance(reference, dict) else ""
            if not text and not placeholders and not system:
                continue
            messages.append(
                NormalizedMessage(
                    message_id=f"discord:{native_id}",
                    seq=seq,
                    timestamp_ms=timestamp,
                    sender_uid=uid,
                    sender_name=str(author.get("nickname") or author.get("name") or uid),
                    sender_role=role,
                    kind=MessageKind.SYSTEM
                    if system
                    else MessageKind.REPLY
                    if reply_id
                    else MessageKind.TEXT
                    if text
                    else MessageKind.PLACEHOLDER,
                    raw_type=f"discord_{raw_type}",
                    text=text,
                    placeholders=placeholders,
                    reply_to_id=f"discord:{reply_id}" if reply_id else None,
                    system=system,
                    has_media=bool(placeholders),
                    raw=raw,
                )
            )
        messages.sort(key=lambda m: (m.timestamp_ms, m.seq))
        return messages

    def extract_image_refs(self, payload: dict[str, Any]) -> list[ImportImageRef]:
        if not self.match(payload):
            return []
        refs: list[ImportImageRef] = []
        for raw in payload["messages"]:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            attachments = raw.get("attachments")
            for attachment in attachments if isinstance(attachments, list) else []:
                if not isinstance(attachment, dict):
                    continue
                url = urlsplit(str(attachment.get("url") or ""))
                path = PurePosixPath(unquote(url.path))
                if (
                    url.scheme
                    or url.netloc
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in str(path)
                    or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
                ):
                    continue
                refs.append(ImportImageRef(f"discord:{raw['id']}", str(path)))
        return refs
