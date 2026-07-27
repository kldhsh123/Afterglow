"""QQ JSON parser 单测。"""

from __future__ import annotations

from itertools import pairwise

from xuwen.config import Settings
from xuwen.core.models import MessageKind
from xuwen.ingestion.parser import detect_plugin, load_qq_json, parse_messages


def test_load_qce_jsonl(tmp_path):
    path = tmp_path / "c000001.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"seq":"1","timestamp":1000,"sender":{"uid":"u_me","name":"Me"},"content":{"text":"hi","resources":[],"elements":[]}}',
                '{"seq":"2","timestamp":2000,"sender":{"uid":"u_friend","name":"Friend"},"content":{"text":"hello","resources":[],"elements":[]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = load_qq_json(path)
    messages = parse_messages(
        payload,
        Settings(self_uid="u_me", friend_uid="u_friend"),
    )

    assert detect_plugin(payload).name == "qqexporter_v5"  # type: ignore[union-attr]
    assert len(messages) == 2
    assert [message.sender_role for message in messages] == ["self", "friend"]


def test_load_weflow_chatlab_jsonl(tmp_path):
    path = tmp_path / "wechat.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"_type":"header","chatlab":{"version":"0.0.2","generator":"WeFlow"},"meta":{"name":"Friend","platform":"wechat","type":"private"}}',
                '{"_type":"member","platformId":"wxid_me","accountName":"Me"}',
                '{"_type":"member","platformId":"wxid_friend","accountName":"Friend"}',
                '{"_type":"message","sender":"wxid_me","accountName":"Me","timestamp":1700000000,"type":0,"content":"hi","platformMessageId":"m1"}',
                '{"_type":"message","sender":"wxid_friend","accountName":"Friend","timestamp":1700000001,"type":0,"content":"hello","platformMessageId":"m2"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = load_qq_json(path)
    messages = parse_messages(
        payload,
        Settings(self_uid="wxid_me", friend_uid="wxid_friend"),
    )

    assert detect_plugin(payload).name == "wechat_weflow"  # type: ignore[union-attr]
    assert len(messages) == 2
    assert [message.sender_role for message in messages] == ["self", "friend"]
    assert messages[0].timestamp_ms == 1_700_000_000_000


def test_weflow_chatlab_type_7_uses_file_extension_and_detects_recall(tmp_path):
    path = tmp_path / "wechat-media.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"_type":"header","chatlab":{"version":"0.0.2","generator":"WeFlow"},"meta":{"name":"Friend","platform":"wechat","type":"private"}}',
                '{"_type":"message","sender":"wxid_friend","timestamp":1783737039,"type":80,"content":"You recalled a message1783737042","platformMessageId":"m1"}',
                '{"_type":"message","sender":"wxid_friend","timestamp":1783737373,"type":7,"content":"media\\\\images\\\\photo.jpg","platformMessageId":"m2"}',
                '{"_type":"message","sender":"wxid_friend","timestamp":1783738868,"type":7,"content":"media\\\\videos\\\\video.mp4","platformMessageId":"m3"}',
                '{"_type":"message","sender":"wxid_me","timestamp":1783737585,"type":25,"content":"基本都是[引用 测试文本1]","platformMessageId":"m4","replyToMessageId":"m0"}',
            ]
        ),
        encoding="utf-8",
    )

    messages = parse_messages(
        load_qq_json(path),
        Settings(self_uid="wxid_me", friend_uid="wxid_friend"),
    )

    assert messages[0].kind == MessageKind.RECALLED
    assert messages[1].placeholders == ["[图片]"]
    assert messages[1].text == ""
    assert messages[2].kind == MessageKind.REPLY
    assert messages[2].text == "基本都是"
    assert messages[3].placeholders == ["[视频]"]


def test_load_afterglow_typed_jsonl(tmp_path):
    path = tmp_path / "afterglow.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"_type":"header","afterglow":{"format":"afterglow-chat","version":"1.0"},"conversation":{"type":"private"}}',
                '{"_type":"participant","uid":"me","name":"Me","role":"self"}',
                '{"_type":"participant","uid":"friend","name":"Friend","role":"friend"}',
                '{"_type":"message","id":"m1","seq":1,"timestamp_ms":1700000000000,"sender_uid":"friend","sender_name":"Friend","kind":"text","text":"hello"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = load_qq_json(path)
    messages = parse_messages(payload, Settings(self_uid="me", friend_uid="friend"))

    assert detect_plugin(payload).name == "afterglow_v1"  # type: ignore[union-attr]
    assert len(messages) == 1
    assert messages[0].sender_role == "friend"
    assert messages[0].text == "hello"


def test_parse_messages_returns_non_empty(sample_payload, settings_for_sample):
    msgs = parse_messages(sample_payload, settings_for_sample)
    assert len(msgs) > 0


def test_parse_messages_sorted_by_timestamp_and_seq(sample_payload, settings_for_sample):
    msgs = parse_messages(sample_payload, settings_for_sample)
    for prev, curr in pairwise(msgs):
        assert (prev.timestamp_ms, prev.seq) <= (curr.timestamp_ms, curr.seq)


def test_parse_messages_distinguishes_roles(sample_payload, settings_for_sample):
    msgs = parse_messages(sample_payload, settings_for_sample)
    roles = {m.sender_role for m in msgs}
    assert "self" in roles
    assert "friend" in roles


def test_parse_messages_extracts_image_placeholder(sample_payload, settings_for_sample):
    msgs = parse_messages(sample_payload, settings_for_sample)
    image_msgs = [m for m in msgs if "[图片]" in m.placeholders]
    assert len(image_msgs) > 0
    sample = image_msgs[0]
    assert sample.has_media is True


def test_parse_messages_text_kind(sample_payload, settings_for_sample):
    msgs = parse_messages(sample_payload, settings_for_sample)
    text_msgs = [m for m in msgs if m.kind == MessageKind.TEXT]
    assert len(text_msgs) > 0
    assert any(m.text.strip() for m in text_msgs)


def test_parse_messages_unknown_uid_falls_back_to_other():
    """如果没在 settings 里配置 uid，所有人都应归到 other（system 除外）。"""
    payload = {
        "messages": [
            {
                "id": "1",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": "uA", "name": "A"},
                "type": "type_1",
                "content": {"text": "hi"},
            },
            {
                "id": "2",
                "seq": "2",
                "timestamp": 2000,
                "sender": {"uid": "uB", "name": "B"},
                "type": "type_1",
                "content": {"text": "hello"},
            },
        ]
    }
    settings = Settings()
    msgs = parse_messages(payload, settings, plugin_name="qqexporter_v5")
    assert all(m.sender_role == "other" for m in msgs)


def test_parse_messages_recalled_message():
    payload = {
        "messages": [
            {
                "id": "r1",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": "uA"},
                "type": "type_1",
                "content": {"text": ""},
                "recalled": True,
            }
        ]
    }
    settings = Settings()
    msgs = parse_messages(payload, settings, plugin_name="qqexporter_v5")
    assert msgs[0].kind == MessageKind.RECALLED
    assert msgs[0].recalled is True


def test_parse_messages_system_message():
    payload = {
        "messages": [
            {
                "id": "s1",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": ""},
                "type": "system",
                "content": {"text": "撤回提示"},
                "system": True,
            }
        ]
    }
    settings = Settings()
    msgs = parse_messages(payload, settings, plugin_name="qqexporter_v5")
    assert msgs[0].kind == MessageKind.SYSTEM
    assert msgs[0].sender_role == "system"


def test_parse_messages_skips_malformed_items():
    """非 dict 条目应被跳过，不应中断整批。"""
    payload = {
        "messages": [
            None,
            "not a dict",
            123,
            {
                "id": "ok",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": "u1"},
                "type": "type_1",
                "content": {"text": "hello"},
            },
        ]
    }
    settings = Settings()
    msgs = parse_messages(payload, settings, plugin_name="qqexporter_v5")
    assert len(msgs) == 1
    assert msgs[0].message_id == "ok"


def test_parse_messages_handles_string_bools():
    """recalled / system 字段是字符串时也要正确解析。"""
    payload = {
        "messages": [
            {
                "id": "r1",
                "seq": "1",
                "timestamp": 1000,
                "sender": {"uid": "u1"},
                "type": "type_1",
                "content": {"text": "x"},
                "recalled": "false",  # 字符串 false
                "system": "false",
            },
            {
                "id": "r2",
                "seq": "2",
                "timestamp": 2000,
                "sender": {"uid": "u1"},
                "type": "type_1",
                "content": {"text": "y"},
                "recalled": "true",  # 字符串 true
                "system": False,
            },
        ]
    }
    settings = Settings()
    msgs = parse_messages(payload, settings, plugin_name="qqexporter_v5")
    assert msgs[0].recalled is False
    assert msgs[1].recalled is True
