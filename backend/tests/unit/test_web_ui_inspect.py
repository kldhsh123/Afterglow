"""web_ui.inspect_file 嗅探聊天文件元数据测试。

覆盖：
- QQChatExporter V5 格式识别 self/friend
- 微信 WeFlow arkme-json 用 isSend=1 反查 self
- 未知格式返回 error
- 不存在文件 / 非 JSON 返回 error
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xuwen.web_ui.inspect_file import inspect_chat_file


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_inspect_douyin_chat_export_returns_owner_and_friend() -> None:
    result = inspect_chat_file(FIXTURES / "sample_douyin_chatlab.json")

    assert result.format == "douyin_chat_export"
    assert result.format_label == "Douyin Chat Export"
    assert result.error == ""
    assert result.total_messages == 9
    assert [(candidate.uid, candidate.role_hint) for candidate in result.candidates] == [
        ("uid-self-001", "self"),
        ("uid-friend-001", "friend"),
    ]


def test_inspect_qq_returns_self_and_friend() -> None:
    result = inspect_chat_file(FIXTURES / "sample_chat.json")
    assert result.format == "qqexporter_v5"
    assert result.error == ""
    assert result.total_messages > 0

    roles = {c.role_hint for c in result.candidates}
    assert "self" in roles
    assert "friend" in roles

    self_c = next(c for c in result.candidates if c.role_hint == "self")
    friend_c = next(c for c in result.candidates if c.role_hint == "friend")
    assert self_c.uid != friend_c.uid


def test_inspect_qce_jsonl_returns_sender_candidates(tmp_path: Path) -> None:
    path = tmp_path / "c000001.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"timestamp":1000,"sender":{"uid":"u_me","name":"Me"},"content":{"text":"hi"}}',
                '{"timestamp":2000,"sender":{"uid":"u_friend","name":"Friend"},"content":{"text":"hello"}}',
                '{"timestamp":3000,"sender":{"uid":"u_friend","name":"Friend"},"content":{"text":"again"}}',
            ]
        ),
        encoding="utf-8",
    )

    result = inspect_chat_file(path)

    assert result.format == "qce_jsonl"
    assert result.error == ""
    assert result.total_messages == 3
    assert [candidate.uid for candidate in result.candidates] == ["u_friend", "u_me"]
    assert all(candidate.role_hint == "unknown" for candidate in result.candidates)


def test_inspect_weflow_chatlab_jsonl_returns_members(tmp_path: Path) -> None:
    path = tmp_path / "wechat.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"_type":"header","chatlab":{"generator":"WeFlow"},"meta":{"name":"Friend","platform":"wechat","type":"private"}}',
                '{"_type":"member","platformId":"wxid_me","accountName":"Me"}',
                '{"_type":"member","platformId":"wxid_friend","accountName":"Friend"}',
                '{"_type":"message","sender":"wxid_friend","accountName":"Friend","timestamp":1700000000,"type":0,"content":"hello"}',
            ]
        ),
        encoding="utf-8",
    )

    result = inspect_chat_file(path)

    assert result.format == "weflow_chatlab_jsonl"
    assert result.total_messages == 1
    assert [candidate.uid for candidate in result.candidates] == [
        "wxid_me",
        "wxid_friend",
    ]


def test_inspect_afterglow_jsonl_uses_participant_roles(tmp_path: Path) -> None:
    path = tmp_path / "afterglow.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"_type":"header","afterglow":{"format":"afterglow-chat","version":"1.0"},"conversation":{"type":"private"}}',
                '{"_type":"participant","uid":"me","name":"Me","role":"self"}',
                '{"_type":"participant","uid":"friend","name":"Friend","role":"friend"}',
                '{"_type":"message","timestamp_ms":1000,"sender_uid":"friend","kind":"text","text":"hi"}',
            ]
        ),
        encoding="utf-8",
    )

    result = inspect_chat_file(path)

    assert result.format == "afterglow_jsonl"
    assert result.total_messages == 1
    assert [candidate.role_hint for candidate in result.candidates] == [
        "self",
        "friend",
    ]


def test_inspect_wechat_uses_isSend_to_identify_self() -> None:
    result = inspect_chat_file(FIXTURES / "sample_wechat_weflow.json")
    assert result.format == "wechat_weflow"
    assert result.error == ""

    roles = {c.role_hint for c in result.candidates}
    assert "self" in roles
    assert "friend" in roles


def test_inspect_self_listed_first_in_wechat(tmp_path: Path) -> None:
    """微信场景：self 排在 candidates[0]，方便前端默认展示。"""
    data = {
        "weflow": {"format": "arkme-json"},
        "senders": [
            {"senderID": 1, "wxid": "wxid_friend", "displayName": "Friend"},
            {"senderID": 2, "wxid": "wxid_me", "displayName": "Me"},
        ],
        "messages": [{"isSend": 1, "senderID": 2}],
    }
    p = tmp_path / "wx.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    result = inspect_chat_file(p)
    assert result.candidates[0].role_hint == "self"
    assert result.candidates[0].uid == "wxid_me"


def test_inspect_unknown_format_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "random.json"
    p.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    result = inspect_chat_file(p)
    assert result.format == "unknown"
    assert "无法识别" in result.error


def test_inspect_missing_file_returns_error(tmp_path: Path) -> None:
    result = inspect_chat_file(tmp_path / "nope.json")
    assert result.format == "unknown"
    assert result.candidates == []
    assert result.error != ""


def test_inspect_invalid_json_returns_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    result = inspect_chat_file(p)
    assert result.format == "unknown"
    assert result.error != ""


def test_inspect_qq_dedupes_senders(tmp_path: Path) -> None:
    """同一个 sender uid 在 messages 出现多次只应作为单个候选返回一次。"""
    data = {
        "chatInfo": {"selfUid": "u_me", "selfName": "Me"},
        "messages": [
            {"sender": {"uid": "u_friend", "name": "Friend"}},
            {"sender": {"uid": "u_friend", "name": "Friend"}},
            {"sender": {"uid": "u_friend", "name": "Friend"}},
        ],
    }
    p = tmp_path / "qq.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = inspect_chat_file(p)
    friend_candidates = [c for c in result.candidates if c.uid == "u_friend"]
    assert len(friend_candidates) == 1


def test_inspect_qq_returns_self_even_without_messages(tmp_path: Path) -> None:
    data = {"chatInfo": {"selfUid": "u_me", "selfName": "Me"}, "messages": []}
    p = tmp_path / "qq.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = inspect_chat_file(p)
    assert result.format == "qqexporter_v5"
    assert len(result.candidates) == 1
    assert result.candidates[0].role_hint == "self"
