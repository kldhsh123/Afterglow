"""Telegram Desktop HTML 导出：按结构解析，不依赖界面语言或目录名。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole
from xuwen.error_i18n import error_text
from xuwen.ingestion.plugins import (
    TEXT_CONTENT_KEY,
    TEXT_SUFFIX_KEY,
    ImportIdentityCandidate,
    ImportImageRef,
    ImportInspection,
)


def _source(payload: dict[str, Any]) -> str:
    value = payload.get(TEXT_CONTENT_KEY)
    return (
        value
        if isinstance(value, str) and payload.get(TEXT_SUFFIX_KEY) in {".html", ".htm"}
        else ""
    )


def _local_image(href: str) -> str | None:
    url = urlsplit(href)
    path = PurePosixPath(unquote(url.path))
    if url.scheme or url.netloc or path.is_absolute() or ".." in path.parts or "\\" in str(path):
        return None
    return (
        str(path)
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        else None
    )


def _records(source: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(source, "html.parser")
    header = soup.select_one(".page_header .text")
    chat = header.get_text(strip=True) if header else ""
    namespace = hashlib.sha256(chat.encode()).hexdigest()[:16]
    records: list[dict[str, Any]] = []
    sender = ""
    for node in soup.select(".history > .message"):
        classes = node.get("class", [])
        body = node.select_one(":scope > .body")
        if body is None:
            continue
        from_name = body.select_one(":scope > .from_name")
        if from_name:
            sender = from_name.get_text(strip=True)
        elif "joined" not in classes and "service" not in classes:
            sender = ""
        date = body.select_one(":scope > .date[title]")
        if date is None:
            # 导出中的日期分隔符与无时间的提示不作为聊天消息。
            continue
        title = str(date.get("title", ""))
        try:
            timestamp = int(
                datetime.strptime(
                    title.replace("UTC", "").strip(), "%d.%m.%Y %H:%M:%S %z"
                ).timestamp()
                * 1000
            )
        except ValueError:
            continue
        system = "service" in classes
        text_node = body.select_one(":scope > .text")
        if text_node:
            # get_text(separator='\n') 会破坏行内链接，只有 br 表示换行。
            for br in text_node.find_all("br"):
                br.replace_with("\n")
            text = text_node.get_text().strip()
        else:
            text = body.get_text(" ", strip=True) if system else ""
        media = body.select_one(":scope > .media_wrap")
        placeholders: list[str] = []
        images: list[str] = []
        if media:
            for item in media.select(".photo_wrap, .sticker_wrap, .media"):
                media_classes = " ".join(item.get("class", []))
                href = str(item.get("href", ""))
                if (
                    "sticker" in media_classes
                    or "sticker" in PurePosixPath(urlsplit(href).path).name.casefold()
                ):
                    label = "[表情]"
                elif "video" in media_classes or PurePosixPath(
                    urlsplit(href).path
                ).suffix.lower() in {".webm", ".mp4", ".mov"}:
                    label = "[视频]"
                elif "photo" in media_classes:
                    label = "[图片]"
                elif "voice" in media_classes or "audio" in media_classes:
                    label = "[语音]"
                elif "contact" in media_classes:
                    label = "[联系人]"
                elif "call" in media_classes:
                    label = "[通话]"
                else:
                    label = "[文件]"
                if label not in placeholders:
                    placeholders.append(label)
                image = _local_image(href)
                if image and image not in images:
                    images.append(image)
            if not placeholders:
                placeholders.append("[附件]")
        reply_node = body.select_one(":scope > .reply_to a[href]")
        reply = str(reply_node.get("href", "")) if reply_node else ""
        reply_match = re.search(r"(?:#message|#go_to_message|GoToMessage\()(\d+)", reply)
        native_id = str(node.get("id", ""))
        if not native_id:
            continue
        records.append(
            {
                "id": f"telegram:{namespace}:{native_id}",
                "sender": "" if system else sender,
                "timestamp": timestamp,
                "text": text,
                "system": system,
                "placeholders": placeholders,
                "images": images,
                "reply": f"telegram:{namespace}:message{reply_match[1]}" if reply_match else None,
            }
        )
    return records


class TelegramHTMLPlugin:
    name = "telegram_html"
    display_name = "Telegram Desktop HTML"

    def match(self, payload: dict[str, Any]) -> bool:
        source = _source(payload)
        return bool(
            re.search(r'class=["\'][^"\']*\bhistory\b', source)
            and re.search(r'class=["\'][^"\']*\bmessage\b', source)
            and 'id="message' in source
        )

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        if not self.match(payload):
            return ImportInspection(
                "unknown", [], 0, error_text("Cannot recognize the Telegram HTML export")
            )
        records = _records(_source(payload))
        names = list(dict.fromkeys(raw["sender"] for raw in records if raw["sender"]))
        return ImportInspection(
            self.name,
            [ImportIdentityCandidate(name, "telegram:" + quote(name, safe="")) for name in names],
            len(records),
            format_label=self.display_name,
        )

    def parse(self, payload: dict[str, Any], settings: Settings) -> list[NormalizedMessage]:
        if not self.match(payload):
            raise ParseError(
                error_text("The file does not match the Telegram Desktop HTML export format")
            )
        messages: list[NormalizedMessage] = []
        for seq, raw in enumerate(_records(_source(payload))):
            sender = raw["sender"]
            system = raw["system"]
            if not sender and not system:
                continue
            uid = "telegram:" + quote(sender, safe="") if sender else ""
            role: SenderRole = (
                "system"
                if system
                else "self"
                if uid in settings.all_self_uids
                else "friend"
                if uid in settings.all_friend_uids
                else "other"
            )
            text, placeholders, reply = raw["text"], raw["placeholders"], raw["reply"]
            messages.append(
                NormalizedMessage(
                    message_id=raw["id"],
                    seq=seq,
                    timestamp_ms=raw["timestamp"],
                    sender_uid=uid,
                    sender_name=sender,
                    sender_role=role,
                    kind=MessageKind.SYSTEM
                    if system
                    else MessageKind.REPLY
                    if reply
                    else MessageKind.TEXT
                    if text
                    else MessageKind.PLACEHOLDER,
                    raw_type="telegram_service" if system else "telegram_message",
                    text=text,
                    placeholders=placeholders,
                    reply_to_id=reply,
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
        return [
            ImportImageRef(raw["id"], image)
            for raw in _records(_source(payload))
            for image in raw["images"]
        ]
