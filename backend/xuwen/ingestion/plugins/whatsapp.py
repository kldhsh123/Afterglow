"""WhatsApp TXT（iOS/Android）和 Markdown 聊天导入。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind, NormalizedMessage, SenderRole
from xuwen.error_i18n import error_text
from xuwen.ingestion.plugins import (
    TEXT_CONTENT_KEY,
    TEXT_SUFFIX_KEY,
    ImportIdentityCandidate,
    ImportInspection,
)

# 日期数字和结构用于识别，聊天正文不受语言限制。
_HEADER = re.compile(
    r"^\[?(?P<date>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]?\s*(?:-\s*)?(?P<body>.*)$"
)
_MD_DATE = re.compile(r"^##\s+(\d{4})年(\d{1,2})月(\d{1,2})日\s*$")
_MD_HEADER = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+(.*)$")
_MEDIA = {
    "[图片]": "[图片]",
    "<图片已忽略>": "[图片]",
    "<image omitted>": "[图片]",
    "<media omitted>": "[附件]",
    "<媒体已忽略>": "[附件]",
    "[视频]": "[视频]",
    "<视频已忽略>": "[视频]",
    "<video omitted>": "[视频]",
    "[语音]": "[语音]",
    "<音频已忽略>": "[语音]",
    "<audio omitted>": "[语音]",
    "[表情]": "[表情]",
    "<贴图已忽略>": "[表情]",
    "<sticker omitted>": "[表情]",
    "<album消息>": "[图片]",
    "[album消息]": "[图片]",
    "<document omitted>": "[文件]",
    "<文件已忽略>": "[文件]",
}
_CONTROLS = str.maketrans("", "", "\u200e\u200f\u202a\u202b\u202c\u2066\u2067\u2068\u2069")


def _source(payload: dict[str, Any]) -> str:
    value = payload.get(TEXT_CONTENT_KEY)
    return (
        value if isinstance(value, str) and payload.get(TEXT_SUFFIX_KEY) in {".txt", ".md"} else ""
    )


def _records(source: str, *, markdown: bool) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    day = ""
    for line in (
        source.translate(_CONTROLS).replace("\u202f", " ").replace("\u00a0", " ").splitlines()
    ):
        if markdown:
            date_match = _MD_DATE.match(line)
            if date_match:
                day = "-".join(date_match.groups())
                continue
            match = _MD_HEADER.match(line) if day else None
            if match:
                clock, body = match.groups()
                sender_match = re.match(r"^\*\*(.+?):\*\*\s?(.*)$", body)
                sender, text = (
                    sender_match.groups()
                    if sender_match
                    else ("", body.removeprefix("__").removesuffix("__"))
                )
                records.append({"date": day, "time": clock, "sender": sender.strip(), "text": text})
            elif records and line and line != "---" and not line.startswith("#"):
                records[-1]["text"] += "\n" + line
        else:
            header = _HEADER.match(line)
            if header:
                raw = header.groupdict()
                body = raw["body"]
                sender, sep, text = body.partition(": ")
                records.append(
                    {
                        "date": raw["date"],
                        "time": raw["time"],
                        "sender": sender.strip() if sep else "",
                        "text": text if sep else body,
                    }
                )
            elif records:
                records[-1]["text"] += "\n" + line
    return records


def _timestamp(raw: dict[str, str], zone: ZoneInfo, day_first: bool) -> int:
    parts = re.split(r"[/.\-]", raw["date"])
    if len(parts[0]) == 4:
        year, month, day = map(int, parts)
    else:
        first, second, year = map(int, parts)
        day, month = (first, second) if day_first else (second, first)
        year = year + (2000 if year < 69 else 1900) if year < 100 else year
    clock = raw["time"].upper().strip()
    fmt = "%I:%M:%S %p" if clock.count(":") == 2 else "%I:%M %p"
    if clock.endswith(("AM", "PM")):
        clock = re.sub(r"\s*([AP]M)$", r" \1", clock)
    else:
        fmt = "%H:%M:%S" if clock.count(":") == 2 else "%H:%M"
    time = datetime.strptime(clock, fmt).time()
    return int(
        datetime(year, month, day, time.hour, time.minute, time.second, tzinfo=zone).timestamp()
        * 1000
    )


class WhatsAppPlugin:
    name = "whatsapp"
    display_name = "WhatsApp"

    def match(self, payload: dict[str, Any]) -> bool:
        source = _source(payload)
        if payload.get(TEXT_SUFFIX_KEY) == ".md":
            return source.startswith("# WhatsApp") and bool(_records(source, markdown=True))
        return any(
            _HEADER.match(line.translate(_CONTROLS).replace("\u202f", " "))
            for line in source.splitlines()
        )

    def inspect(self, payload: dict[str, Any]) -> ImportInspection:
        if not self.match(payload):
            return ImportInspection(
                "unknown", [], 0, error_text("Cannot recognize the WhatsApp export")
            )
        records = _records(_source(payload), markdown=payload.get(TEXT_SUFFIX_KEY) == ".md")
        names = list(dict.fromkeys(raw["sender"] for raw in records if raw["sender"]))
        candidates = [
            ImportIdentityCandidate(
                name,
                "whatsapp:" + quote(name, safe=""),
                "self" if name in {"你", "You"} else "unknown",
            )
            for name in names
        ]
        return ImportInspection(self.name, candidates, len(records), format_label=self.display_name)

    def parse(self, payload: dict[str, Any], settings: Settings) -> list[NormalizedMessage]:
        if not self.match(payload):
            raise ParseError(
                error_text("The file does not match the WhatsApp TXT / Markdown chat format")
            )
        records = _records(_source(payload), markdown=payload.get(TEXT_SUFFIX_KEY) == ".md")
        # 点分隔日期使用日/月；斜杠默认月/日，有明确大于 12 的首项时按日/月。
        day_first = any(
            "." in raw["date"]
            or (
                len(re.split(r"[/.\-]", raw["date"])[0]) < 4
                and int(re.split(r"[/.\-]", raw["date"])[0]) > 12
            )
            for raw in records
        )
        zone = ZoneInfo(settings.app_timezone)
        messages: list[NormalizedMessage] = []
        occurrences: dict[str, int] = {}
        for seq, raw in enumerate(records):
            try:
                timestamp = _timestamp(raw, zone, day_first)
            except ValueError:
                continue
            sender = raw["sender"].strip()
            uid = "whatsapp:" + quote(sender, safe="") if sender else ""
            role: SenderRole = (
                "system"
                if not sender
                else "self"
                if uid in settings.all_self_uids
                else "friend"
                if uid in settings.all_friend_uids
                else "other"
            )
            text = raw["text"].strip()
            placeholder = _MEDIA.get(text.casefold())
            digest = hashlib.sha256(f"{timestamp}\0{uid}\0{text}".encode()).hexdigest()[:24]
            occurrence = occurrences.get(digest, 0)
            occurrences[digest] = occurrence + 1
            messages.append(
                NormalizedMessage(
                    message_id=f"whatsapp:{digest}:{occurrence}",
                    seq=seq,
                    timestamp_ms=timestamp,
                    sender_uid=uid,
                    sender_name=sender,
                    sender_role=role,
                    kind=MessageKind.SYSTEM
                    if not sender
                    else MessageKind.PLACEHOLDER
                    if placeholder
                    else MessageKind.TEXT,
                    raw_type="whatsapp_system" if not sender else "whatsapp_message",
                    text="" if placeholder else text,
                    placeholders=[placeholder] if placeholder else [],
                    system=not sender,
                    has_media=bool(placeholder),
                    raw=raw,
                )
            )
        messages.sort(key=lambda m: (m.timestamp_ms, m.seq))
        return messages
