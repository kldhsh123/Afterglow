"""抖音 douyin-chat-export ChatLab 导入 plugin 单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xuwen.config import Settings
from xuwen.core.errors import ParseError
from xuwen.core.models import MessageKind
from xuwen.ingestion.chunker import (
    build_friend_chunks,
    build_response_pair_chunks,
    build_window_chunks,
)
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.parser import detect_plugin, load_qq_json, parse_messages
from xuwen.ingestion.plugins.douyin_chat_export import DouyinChatExportPlugin
from xuwen.ingestion.splitter import build_windows, split_sessions


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_douyin_chatlab.json"


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        self_name="Me",
        self_uid="uid-self-001",
        friend_name="TestFriend",
        friend_uid="uid-friend-001",
    )


def test_match_is_strict_to_douyin_generator_and_platform() -> None:
    plugin = DouyinChatExportPlugin()
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert plugin.match(payload) is True

    payload["chatlab"]["generator"] = "WeFlow"
    payload["meta"]["platform"] = "wechat"
    assert plugin.match(payload) is False


def test_parse_maps_douyin_message_semantics(settings: Settings) -> None:
    messages = parse_messages(load_qq_json(FIXTURE), settings)
    by_id = {message.message_id: message for message in messages}

    assert detect_plugin(load_qq_json(FIXTURE)).name == "douyin_chat_export"  # type: ignore[union-attr]
    assert by_id["dy-001"].sender_role == "self"
    assert by_id["dy-002"].sender_role == "friend"
    assert by_id["dy-002"].kind == MessageKind.REPLY
    assert by_id["dy-002"].reply_to_id == "dy-001"
    assert by_id["dy-002"].reply_to_summary == "Me: 昨天那个视频你看了吗？"
    assert by_id["dy-003"].kind == MessageKind.PLACEHOLDER
    assert by_id["dy-003"].text == ""
    assert by_id["dy-003"].placeholders == ["[图片]"]
    assert by_id["dy-004"].placeholders == ["[表情]"]
    assert by_id["dy-005"].kind == MessageKind.TEXT
    assert "https://www.douyin.com/video/123456" in by_id["dy-005"].text
    assert by_id["dy-006"].placeholders == ["[语音]"]
    assert by_id["dy-007"].placeholders == ["[链接]"]
    assert by_id["dy-008"].kind == MessageKind.UNKNOWN
    assert by_id["dy-008"].text == "未知类型里的可读正文"
    assert by_id["dy-009"].kind == MessageKind.SYSTEM
    assert by_id["dy-009"].sender_role == "system"
    assert by_id["dy-001"].timestamp_ms == 1_754_363_160_000


def test_pure_media_is_filtered_from_reply_samples_but_kept_in_windows(
    settings: Settings,
) -> None:
    parsed = parse_messages(load_qq_json(FIXTURE), settings)
    cleaned = Cleaner(settings).clean_many(parsed)
    sessions = split_sessions(cleaned, settings)
    windows = build_windows(sessions, settings)

    friend_chunks = build_friend_chunks(sessions, settings)
    response_pairs = build_response_pair_chunks(sessions, settings)
    window_chunks = build_window_chunks(windows, settings)

    assert {chunk.message_id for chunk in friend_chunks} == {"dy-002", "dy-008"}
    assert {message_id for pair in response_pairs for message_id in pair.friend_message_ids} == {
        "dy-002",
        "dy-008",
    }
    window_text = "\n".join(chunk.text for chunk in window_chunks)
    assert "[图片]" in window_text
    assert "[表情]" in window_text
    assert "[语音]" in window_text
    assert "[链接]" in window_text
    assert "https://www.douyin.com/video/123456" in window_text
    assert "我们已互相关注，可以开始聊天了" not in window_text


def test_parse_jsonl_preserves_file_order_for_equal_timestamps(
    settings: Settings,
    tmp_path: Path,
) -> None:
    path = tmp_path / "douyin.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"_type":"header","chatlab":{"version":"0.0.2","generator":"douyin-chat-export"},"meta":{"platform":"douyin","type":"private","ownerId":"uid-self-001"}}',
                '{"_type":"member","platformId":"uid-self-001","accountName":"Me"}',
                '{"_type":"member","platformId":"uid-friend-001","accountName":"TestFriend"}',
                '{"_type":"message","sender":"uid-friend-001","accountName":"TestFriend","timestamp":1754363160,"type":0,"content":"先发","platformMessageId":"same-1"}',
                '{"_type":"message","sender":"uid-friend-001","accountName":"TestFriend","timestamp":1754363160,"type":0,"content":"后发","platformMessageId":"same-2"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = load_qq_json(path)
    messages = parse_messages(payload, settings)

    assert detect_plugin(payload).name == "douyin_chat_export"  # type: ignore[union-attr]
    assert [message.message_id for message in messages] == ["same-1", "same-2"]
    assert [message.seq for message in messages] == [0, 1]


def test_inspect_uses_owner_and_most_active_other_member() -> None:
    plugin = DouyinChatExportPlugin()
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    inspection = plugin.inspect(payload)

    assert inspection.format == "douyin_chat_export"
    assert inspection.format_label == "Douyin Chat Export"
    assert inspection.total_messages == 9
    assert [(candidate.uid, candidate.role_hint) for candidate in inspection.candidates] == [
        ("uid-self-001", "self"),
        ("uid-friend-001", "friend"),
    ]


def test_parse_rejects_non_private_conversation(settings: Settings) -> None:
    plugin = DouyinChatExportPlugin()
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["meta"]["type"] = "group"

    with pytest.raises(ParseError, match="private"):
        plugin.parse(payload, settings)


def test_parse_skips_malformed_and_empty_unknown_records(settings: Settings) -> None:
    plugin = DouyinChatExportPlugin()
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["messages"] = [
        None,
        {"sender": "uid-friend-001", "timestamp": "bad", "type": 0, "content": "bad"},
        {"sender": "uid-friend-001", "timestamp": 1754363160, "type": 99, "content": ""},
        {
            "sender": "uid-friend-001",
            "timestamp": 1754363161,
            "type": "new_type",
            "content": "仍然可读",
            "platformMessageId": "valid-unknown",
        },
    ]

    messages = plugin.parse(payload, settings)

    assert len(messages) == 1
    assert messages[0].message_id == "valid-unknown"
    assert messages[0].kind == MessageKind.UNKNOWN
