"""WhatsApp / Telegram 文件解码、身份和消息语义回归。"""

from datetime import datetime
from pathlib import Path

import pytest

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind
from xuwen.ingestion.image_importer import _load_chat_payloads
from xuwen.ingestion.parser import detect_plugin, load_qq_json, parse_messages
from xuwen.ingestion.plugins import TEXT_CONTENT_KEY, TEXT_SUFFIX_KEY
from xuwen.ingestion.plugins.telegram_html import TelegramHTMLPlugin
from xuwen.web_ui.inspect_file import inspect_chat_file


def file(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8-sig")
    return path


def test_whatsapp_multiline_roles_media_and_system(tmp_path: Path) -> None:
    path = file(
        tmp_path,
        "chat.txt",
        """Messages are encrypted.
[10/13/26, 4:25:24 PM] Friend: hello
second line
[10/13/26, 4:25:29 PM] Friend: <图片已忽略>
[10/13/26, 4:26:05 PM] You: answer
[10/13/26, 4:33:43 PM] - [通话]
""",
    )
    inspection = inspect_chat_file(path)
    assert inspection.format == "whatsapp" and inspection.total_messages == 4
    assert {c.uid for c in inspection.candidates} == {"whatsapp:Friend", "whatsapp:You"}
    settings = Settings(
        self_uid="whatsapp:You", friend_uid="whatsapp:Friend", app_timezone="Asia/Shanghai"
    )
    messages = parse_messages(load_qq_json(path), settings)
    assert messages[0].text == "hello\nsecond line"
    assert messages[0].sender_role == "friend"
    assert messages[0].timestamp_ms == int(
        datetime.fromisoformat("2026-10-13T16:25:24+08:00").timestamp() * 1000
    )
    assert messages[1].placeholders == ["[图片]"]
    assert messages[2].sender_role == "self"
    assert messages[3].system and messages[3].sender_role == "system"


@pytest.mark.parametrize(
    "header",
    [
        "\u200e[13/10/2026, 16:25:24] Friend: hello",
        "13.10.2026, 16:25 - Friend: hello",
        "2026-10-13, 16:25 - Friend: hello",
        "[10/13/26, 4:25:24\u202fPM] Friend: hello",
    ],
)
def test_whatsapp_date_variants(tmp_path: Path, header: str) -> None:
    messages = parse_messages(load_qq_json(file(tmp_path, "chat.txt", header)), Settings())
    assert len(messages) == 1 and messages[0].sender_name == "Friend"
    assert messages[0].timestamp_ms // 86400000 == 20739


def test_whatsapp_markdown(tmp_path: Path) -> None:
    path = file(
        tmp_path,
        "chat.md",
        """# WhatsApp 聊天导出: Friend
导出日期: 2026年10月13日 16:34
---
## 2026年10月13日
[16:25] **Friend:** hello

[16:26] **You:** [图片]

[16:27] __[通话]__
""",
    )
    messages = parse_messages(load_qq_json(path), Settings())
    assert len(messages) == 3
    assert messages[0].text == "hello"
    assert messages[1].placeholders == ["[图片]"]
    assert messages[2].system


def test_whatsapp_ids_survive_upload_rename_and_preserve_duplicates(tmp_path: Path) -> None:
    source = "[10/13/26, 4:25:24 PM] Friend: hello\n" * 2
    first = parse_messages(load_qq_json(file(tmp_path, "chat.txt", source)), Settings())
    again = parse_messages(load_qq_json(file(tmp_path, "upload_123_chat.txt", source)), Settings())
    assert [m.message_id for m in first] == [m.message_id for m in again]
    assert first[0].message_id != first[1].message_id


def telegram(*nodes: str) -> str:
    return (
        '<div class="page_header"><div class="text bold">Test Chat</div></div><div class="history">'
        + "".join(nodes)
        + "</div>"
    )


def message(
    mid: int,
    content: str,
    sender: str = "",
    joined: bool = False,
    date: str = "13.10.2026 16:25:24 UTC+08:00",
) -> str:
    name = f'<div class="from_name">{sender}</div>' if sender else ""
    return f'<div class="message default clearfix {"joined" if joined else ""}" id="message{mid}"><div class="body"><div class="date details" title="{date}"></div>{name}{content}</div></div>'


def test_telegram_joined_reply_caption_timezone_and_images(tmp_path: Path) -> None:
    source = telegram(
        '<div class="message service" id="message-1"><div class="body details">13 October 2026</div></div>',
        message(1, '<div class="text">hello <a>world</a><br>下一行</div>', "Friend"),
        message(2, '<div class="text">continued</div>', joined=True),
        message(
            3,
            '<div class="reply_to"><a href="#go_to_message1">In reply</a></div><div class="media_wrap"><a class="photo_wrap" href="photos/photo.jpg"><img src="photos/thumb.jpg"></a></div><div class="text">caption</div>',
            "Me",
            date="13.10.2026 08:25:25 UTC+00:00",
        ),
        message(
            4,
            '<div class="media_wrap"><a class="sticker_wrap" href="stickers/sticker.webp"></a></div>',
            joined=True,
        ),
    )
    path = file(tmp_path, "messages.html", source)
    payload = load_qq_json(path)
    inspection = inspect_chat_file(path)
    assert inspection.format == "telegram_html" and inspection.total_messages == 4
    assert {c.uid for c in inspection.candidates} == {"telegram:Friend", "telegram:Me"}
    messages = parse_messages(
        payload, Settings(self_uid="telegram:Me", friend_uid="telegram:Friend")
    )
    by_seq = {m.seq: m for m in messages}
    assert by_seq[0].text == "hello world\n下一行"
    assert by_seq[1].sender_role == "friend"
    assert by_seq[2].sender_role == "self" and by_seq[2].text == "caption"
    assert by_seq[2].kind == MessageKind.REPLY and by_seq[2].has_media
    assert by_seq[2].reply_to_id == by_seq[0].message_id
    assert by_seq[2].timestamp_ms == by_seq[0].timestamp_ms + 1000
    assert by_seq[3].placeholders == ["[表情]"]
    refs = TelegramHTMLPlugin().extract_image_refs(payload)
    assert [r.image_name for r in refs] == ["photos/photo.jpg", "stickers/sticker.webp"]
    assert refs[0].message_id == by_seq[2].message_id
    assert len(_load_chat_payloads(tmp_path)) == 1


@pytest.mark.parametrize(
    "href",
    [
        "https://example.com/image.jpg",
        "../image.jpg",
        "%2e%2e/image.jpg",
        "/image.jpg",
        "//example.com/image.jpg",
        r"..\image.jpg",
    ],
)
def test_telegram_image_refs_reject_nonlocal_paths(href: str) -> None:
    payload = {
        TEXT_SUFFIX_KEY: ".html",
        TEXT_CONTENT_KEY: telegram(
            message(
                1,
                f'<div class="media_wrap"><a class="photo_wrap" href="{href}"></a></div>',
                "Friend",
            )
        ),
    }
    assert TelegramHTMLPlugin().extract_image_refs(payload) == []


def test_telegram_unjoined_missing_sender_is_not_inherited(tmp_path: Path) -> None:
    source = telegram(
        message(1, '<div class="text">ok</div>', "Friend"),
        message(2, '<div class="text">unknown</div>'),
    )
    assert (
        len(parse_messages(load_qq_json(file(tmp_path, "messages.html", source)), Settings())) == 1
    )


def test_unrelated_text_html_and_markdown_are_not_chat_exports(tmp_path: Path) -> None:
    for name, content in [
        ("notes.txt", "plain notes"),
        ("page.html", '<div class="text">hello</div>'),
        ("notes.md", "# Notes\n[16:25] example"),
    ]:
        path = file(tmp_path, name, content)
        assert detect_plugin(load_qq_json(path)) is None
        assert inspect_chat_file(path).format == "unknown"


def test_invalid_utf8_is_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "chat.txt"
    path.write_bytes(b"\xff")
    with pytest.raises(ParseError, match="UTF-8"):
        load_qq_json(path)
