"""Afterglow v1 专用导入格式测试。"""

from __future__ import annotations

import pytest

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind
from xuwen.ingestion.parser import detect_plugin, parse_messages
from xuwen.ingestion.plugins.afterglow_v1 import AfterglowV1Plugin


def _payload() -> dict:
    return {
        "afterglow": {"format": "afterglow-chat", "version": "1.0"},
        "conversation": {"id": "c1", "type": "private"},
        "participants": [
            {"uid": "me", "name": "Me", "role": "self"},
            {"uid": "friend", "name": "Friend", "role": "friend"},
        ],
        "messages": [
            {
                "id": "m2",
                "seq": 2,
                "timestamp_ms": 2000,
                "sender_uid": "friend",
                "kind": "placeholder",
                "text": "[图片: a.jpg]",
                "attachments": [{"type": "image", "name": "a.jpg"}],
            },
            {
                "id": "m1",
                "seq": 1,
                "timestamp_ms": 1000,
                "sender_uid": "me",
                "kind": "text",
                "text": "hi",
            },
        ],
    }


def test_afterglow_v1_detects_plugin() -> None:
    detected = detect_plugin(_payload())
    assert detected is not None
    assert detected.name == "afterglow_v1"


def test_afterglow_v1_parse_private_chat() -> None:
    msgs = parse_messages(_payload(), Settings())
    assert [m.message_id for m in msgs] == ["m1", "m2"]
    assert msgs[0].sender_role == "self"
    assert msgs[1].sender_role == "friend"
    assert msgs[1].kind == MessageKind.PLACEHOLDER
    assert msgs[1].placeholders == ["[图片]"]
    assert msgs[1].has_media is True


def test_afterglow_v1_rejects_group_chat() -> None:
    payload = _payload()
    payload["conversation"]["type"] = "group"
    with pytest.raises(ParseError):
        AfterglowV1Plugin().parse(payload, Settings())
