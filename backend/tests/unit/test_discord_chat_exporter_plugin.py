"""DiscordChatExporter JSON：身份、引用、附件和容错。"""

from copy import deepcopy

import pytest

from xuwen.config import Settings
from xuwen.core.models import MessageKind
from xuwen.ingestion.parser import detect_plugin, parse_messages
from xuwen.ingestion.plugins.discord_chat_exporter import DiscordChatExporterPlugin


@pytest.fixture()
def payload():
    return {
        "guild": {"id": None, "name": "Direct Messages"},
        "channel": {"id": "123", "type": "DirectTextChat", "name": "朋友"},
        "exportedAt": "2026-10-13T16:00:00+08:00",
        "messages": [
            {
                "id": "1",
                "type": "Default",
                "timestamp": "2026-10-13T16:00:00.789+08:00",
                "author": {"id": "me", "name": "Me"},
                "content": "你好 / hello",
                "attachments": [],
            },
            {
                "id": "2",
                "type": "Reply",
                "timestamp": "2026-10-13T08:00:01.789+00:00",
                "author": {"id": "friend", "name": "Friend", "nickname": "朋友"},
                "content": "回答 / reply",
                "reference": {"messageId": "1"},
                "attachments": [
                    {"fileName": "image.png", "url": "https://cdn.example.com/image.png"}
                ],
            },
            {
                "id": "3",
                "type": "Default",
                "timestamp": "2026-10-13T16:00:02+08:00",
                "author": {"id": "friend", "name": "Friend"},
                "content": "",
                "attachments": [{"fileName": "image.jpg", "url": "assets/image.jpg"}],
            },
            {
                "id": "4",
                "type": "Call",
                "timestamp": "2026-10-13T16:00:03+08:00",
                "author": {"id": "me", "name": "Me"},
                "content": "Call",
            },
        ],
    }


def test_identifies_authors_without_assuming_owner(payload):
    plugin = detect_plugin(payload)
    assert plugin is not None and plugin.name == "discord_chat_exporter"
    inspection = plugin.inspect(payload)
    assert inspection.total_messages == 4
    assert {c.uid for c in inspection.candidates} == {"me", "friend"}
    assert all(c.role_hint == "unknown" for c in inspection.candidates)
    translated = deepcopy(payload)
    translated["guild"]["name"] = "私信"
    translated["channel"]["name"] = "Friend"
    assert plugin.match(translated)
    assert not plugin.match({"channel": {"id": "123"}, "messages": []})


def test_roles_replies_timestamps_and_captioned_attachments(payload):
    messages = parse_messages(payload, Settings(self_uid="me", friend_uid="friend"))
    assert messages[0].sender_role == "self"
    assert messages[1].sender_role == "friend"
    assert messages[1].kind == MessageKind.REPLY
    assert messages[1].reply_to_id == messages[0].message_id
    assert messages[1].timestamp_ms == messages[0].timestamp_ms + 1000
    assert messages[1].text == "回答 / reply" and messages[1].has_media
    assert messages[2].kind == MessageKind.PLACEHOLDER
    assert messages[2].placeholders == ["[图片]"]
    assert messages[3].system and messages[3].sender_role == "system"
    refs = DiscordChatExporterPlugin().extract_image_refs(payload)
    assert [(r.message_id, r.image_name) for r in refs] == [
        (messages[2].message_id, "assets/image.jpg")
    ]


def test_malformed_records_are_skipped(payload):
    payload["messages"] += [None, {}, {"id": "5", "author": {"id": "friend"}, "timestamp": "bad"}]
    assert len(parse_messages(payload, Settings())) == 4


@pytest.mark.parametrize(
    "url",
    [
        "../image.jpg",
        "%2e%2e/image.jpg",
        "/image.jpg",
        "https://cdn.example.com/image.jpg",
        "//cdn.example.com/image.jpg",
    ],
)
def test_rejects_unsafe_or_remote_image_refs(payload, url):
    payload["messages"] = [payload["messages"][2]]
    payload["messages"][0]["attachments"][0]["url"] = url
    assert DiscordChatExporterPlugin().extract_image_refs(payload) == []
