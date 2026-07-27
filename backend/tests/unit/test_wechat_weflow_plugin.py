"""WeChat WeFlow plugin 单测。

聚焦：
- match 识别（weflow.format / senders+session 兜底）
- 角色映射（wxid 命中 settings vs isSend 兜底）
- 类型分类（文本/引用/动画表情/图片/语音/撤回/系统）
- 引用消息正文裁剪 + reply_to_summary 提取
- timestamp 单位换算（秒 → 毫秒）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.ingestion.parser import detect_plugin, parse_messages
from xuwen.ingestion.plugins.wechat_weflow import WeChatWeFlowPlugin

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_wechat_weflow.json"


@pytest.fixture(scope="module")
def wechat_payload() -> dict[str, Any]:
    with FIXTURE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "self_name": "Me",
        "self_uid": "wxid_me",
        "friend_name": "Friend",
        "friend_uid": "wxid_friend",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


def test_match_via_weflow_format_field():
    plugin = WeChatWeFlowPlugin()
    assert plugin.match(
        {"weflow": {"format": "arkme-json", "version": "1.0.3"}}
    )


def test_match_via_generator_field():
    plugin = WeChatWeFlowPlugin()
    assert plugin.match({"weflow": {"generator": "WeFlow"}})


def test_match_via_session_senders_fallback():
    plugin = WeChatWeFlowPlugin()
    assert plugin.match(
        {
            "session": {"type": "私聊"},
            "senders": [{"senderID": 1, "wxid": "wxid_x"}],
            "messages": [],
        }
    )


def test_match_rejects_unrelated_payload():
    plugin = WeChatWeFlowPlugin()
    assert not plugin.match({"messages": []})
    assert not plugin.match({"metadata": {"name": "QQChatExporter"}})


def test_detect_plugin_returns_wechat(wechat_payload: dict[str, Any]) -> None:
    detected = detect_plugin(wechat_payload)
    assert detected is not None
    assert detected.name == "wechat_weflow"


# ---------------------------------------------------------------------------
# parse 整体形状
# ---------------------------------------------------------------------------


def test_parse_returns_normalized_messages(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    assert len(msgs) == 8
    for m in msgs:
        assert isinstance(m, NormalizedMessage)
    # 时间戳应该按 createTime*1000 升序
    timestamps = [m.timestamp_ms for m in msgs]
    assert timestamps == sorted(timestamps)
    assert all(t >= 1_000_000_000_000 for t in timestamps), "createTime 必须从秒换算到毫秒"


# ---------------------------------------------------------------------------
# 角色映射
# ---------------------------------------------------------------------------


def test_role_uses_wxid_when_settings_match(wechat_payload: dict[str, Any]) -> None:
    """settings.self_uid='wxid_me' / friend_uid='wxid_friend' → 全部按 wxid 命中。"""
    msgs = parse_messages(wechat_payload, _settings())
    by_id = {m.message_id: m for m in msgs}

    # localId 2: senderID=1 (Friend wxid) → friend
    friend_text = by_id["1328215055331550326"]
    assert friend_text.sender_role == "friend"

    # localId 4: senderID=2 (Me wxid) → self
    self_text = by_id["5496543081854318712"]
    assert self_text.sender_role == "self"


def test_role_falls_back_to_is_send_when_uid_missing(
    wechat_payload: dict[str, Any],
) -> None:
    """settings 没配 wxid 时也要正确：用 isSend 兜底。"""
    settings = _settings(self_uid="", friend_uid="")
    msgs = parse_messages(wechat_payload, settings)
    by_id = {m.message_id: m for m in msgs}

    # isSend=0 → friend
    assert by_id["1328215055331550326"].sender_role == "friend"
    # isSend=1 → self
    assert by_id["5496543081854318712"].sender_role == "self"


# ---------------------------------------------------------------------------
# 消息类型分类
# ---------------------------------------------------------------------------


def test_text_message_is_text_kind(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    text_msg = next(m for m in msgs if m.message_id == "1328215055331550326")
    assert text_msg.kind == MessageKind.TEXT
    assert text_msg.text == "你真是个好人"
    assert not text_msg.has_media
    assert text_msg.placeholders == []


def test_animated_emoji_is_placeholder_kind(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    emoji = next(m for m in msgs if m.message_id == "48033046049388297")
    # "动画表情" 因为 content 是 "[表情包]" 字符串，has_text=True → 仍可能归 TEXT。
    # 但占位符列表必须把 [表情] 加上，让下游 cleaner 能识别。
    assert "[表情]" in emoji.placeholders
    assert emoji.has_media


def test_image_message_is_placeholder_kind(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    img = next(m for m in msgs if m.message_id == "2358560893161328023")
    assert img.kind == MessageKind.PLACEHOLDER
    assert img.placeholders == ["[图片]"]
    assert img.has_media
    assert img.text == ""


def test_voice_message_is_placeholder_kind(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    voice = next(m for m in msgs if m.message_id == "9999999999999999")
    assert voice.kind == MessageKind.PLACEHOLDER
    assert voice.placeholders == ["[语音]"]


def test_system_message_is_system_kind(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    sys_msg = next(m for m in msgs if m.message_id == "5937896762021067022")
    assert sys_msg.kind == MessageKind.SYSTEM
    assert sys_msg.system is True
    assert sys_msg.sender_role == "system"
    assert not sys_msg.recalled


def test_recall_system_message_is_recalled_kind(wechat_payload: dict[str, Any]) -> None:
    """'你撤回了一条消息' 这种系统消息应该提升为 RECALLED 类型。"""
    msgs = parse_messages(wechat_payload, _settings())
    recall = next(m for m in msgs if m.message_id == "4808686759457900069")
    assert recall.kind == MessageKind.RECALLED
    assert recall.recalled is True
    # 撤回是动作而非系统事件，不应再标 system=True，避免被同时算两类
    assert recall.system is False


# ---------------------------------------------------------------------------
# 引用消息
# ---------------------------------------------------------------------------


def test_reply_message_extracts_reply_info(wechat_payload: dict[str, Any]) -> None:
    msgs = parse_messages(wechat_payload, _settings())
    reply = next(m for m in msgs if m.message_id == "6903820443222478858")
    assert reply.kind == MessageKind.REPLY
    assert reply.reply_to_id == "582407163894838653"
    assert reply.reply_to_summary is not None
    assert "Friend" in reply.reply_to_summary
    assert "你在县城吗" in reply.reply_to_summary


def test_reply_message_strips_quoted_tail(wechat_payload: dict[str, Any]) -> None:
    """WeFlow 把 '[引用 X：Y]' 拼到正文末尾；归一化后 text 只留正文。"""
    msgs = parse_messages(wechat_payload, _settings())
    reply = next(m for m in msgs if m.message_id == "6903820443222478858")
    assert reply.text == "1"
    assert "[引用" not in reply.text


# ---------------------------------------------------------------------------
# 健壮性
# ---------------------------------------------------------------------------


def test_parse_skips_non_dict_messages() -> None:
    """messages 里有 null / 字符串项不应让整批失败。"""
    plugin = WeChatWeFlowPlugin()
    payload: dict[str, Any] = {
        "weflow": {"format": "arkme-json"},
        "senders": [{"senderID": 1, "wxid": "w_a", "displayName": "A"}],
        "messages": [
            None,
            "not a dict",
            {
                "localId": 1,
                "createTime": 1000,
                "type": "文本消息",
                "localType": 1,
                "content": "hi",
                "isSend": 0,
                "senderID": 1,
                "platformMessageId": "p1",
            },
        ],
    }
    msgs = plugin.parse(payload, _settings())
    assert len(msgs) == 1
    assert msgs[0].text == "hi"


def test_parse_raises_when_messages_missing() -> None:
    plugin = WeChatWeFlowPlugin()
    with pytest.raises(Exception):
        plugin.parse({"weflow": {"format": "arkme-json"}}, _settings())


# ---------------------------------------------------------------------------
# 单发微信内置 emoji
# ---------------------------------------------------------------------------


def _make_payload_with_text(content: str, raw_type: str = "文本消息") -> dict[str, Any]:
    """造一份只含一条消息的最小 WeFlow payload，方便测试不同 content 的归类。"""
    return {
        "weflow": {"format": "arkme-json"},
        "senders": [{"senderID": 1, "wxid": "wxid_friend", "displayName": "Friend"}],
        "messages": [
            {
                "localId": 1,
                "createTime": 1000,
                "type": raw_type,
                "localType": 1,
                "content": content,
                "isSend": 0,
                "senderID": 1,
                "platformMessageId": "p1",
            }
        ],
    }


def test_emoji_only_text_becomes_placeholder() -> None:
    """整条只发了 [微笑] → 转为 PLACEHOLDER 类型，has_media=True，text 清空。"""
    plugin = WeChatWeFlowPlugin()
    msgs = plugin.parse(_make_payload_with_text("[微笑]"), _settings())
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.kind == MessageKind.PLACEHOLDER
    assert msg.placeholders == ["[表情]"]
    assert msg.has_media is True
    assert msg.text == ""


def test_emoji_only_multiple_tokens_becomes_placeholder() -> None:
    """整条由多个 emoji 组成 [捂脸][破涕为笑][害羞] → 同样归 PLACEHOLDER。"""
    plugin = WeChatWeFlowPlugin()
    msgs = plugin.parse(_make_payload_with_text("[捂脸][破涕为笑][害羞]"), _settings())
    assert msgs[0].kind == MessageKind.PLACEHOLDER
    assert msgs[0].placeholders == ["[表情]"]


def test_emoji_mixed_with_text_stays_text() -> None:
    """文本里夹杂 emoji 时 → 仍是 TEXT，emoji 留给 cleaner 归一化。"""
    plugin = WeChatWeFlowPlugin()
    msgs = plugin.parse(_make_payload_with_text("今天好累[微笑]"), _settings())
    assert msgs[0].kind == MessageKind.TEXT
    assert msgs[0].text == "今天好累[微笑]"
    assert msgs[0].has_media is False


def test_reserved_placeholder_in_text_is_not_emoji_only() -> None:
    """整条只有 `[图片]` 这种系统占位符 → 不应被当成 emoji-only。

    实际上这种 case 不会发生（图片在 WeFlow 里 type='图片消息'），
    这里防御性测试以防未来格式漂移破坏判定。"""
    plugin = WeChatWeFlowPlugin()
    msgs = plugin.parse(_make_payload_with_text("[图片]"), _settings())
    # 不应该触发 emoji-only 转换：placeholders 为空且 has_media=False
    assert msgs[0].placeholders == []
    assert msgs[0].has_media is False


# ---------------------------------------------------------------------------
# 多 UID（跨平台 / 跨账号）
# ---------------------------------------------------------------------------


def test_multi_uid_identifies_self_and_friend_via_uids_list() -> None:
    """SELF_UIDS / FRIEND_UIDS 复数列表里的任一 UID 都应被识别为对应角色。

    场景：用户在微信有主号 wxid_me_main + 小号 wxid_me_alt；
    朋友也有 QQ + 微信两个号。
    """
    settings = _settings(
        # 主 self_uid 是 QQ，复数 UID 加上微信主号 + 小号
        self_uid="u_my_qq",
        self_uids=["wxid_me_main", "wxid_me_alt"],
        # 朋友主号是微信主号，复数 UID 加上 QQ
        friend_uid="wxid_friend_main",
        friend_uids=["u_friend_qq"],
    )
    plugin = WeChatWeFlowPlugin()

    payload: dict[str, Any] = {
        "weflow": {"format": "arkme-json"},
        "senders": [
            {"senderID": 1, "wxid": "wxid_friend_main", "displayName": "Friend"},
            {"senderID": 2, "wxid": "wxid_me_alt", "displayName": "Me-alt"},
        ],
        "messages": [
            # 注意：isSend 故意填错（=0 但 wxid 是 self 小号）→ 应优先按 UID 集合判定为 self
            {
                "localId": 1, "createTime": 1000, "type": "文本消息",
                "localType": 1, "content": "from-alt", "isSend": 0,
                "senderID": 2, "platformMessageId": "p1",
            },
            {
                "localId": 2, "createTime": 2000, "type": "文本消息",
                "localType": 1, "content": "from-friend", "isSend": 0,
                "senderID": 1, "platformMessageId": "p2",
            },
        ],
    }
    msgs = plugin.parse(payload, settings)
    by_id = {m.message_id: m for m in msgs}
    # 小号 wxid 在 self_uids 里 → 角色为 self（不受 isSend=0 误导）
    assert by_id["p1"].sender_role == "self"
    # friend 主 wxid 在 friend_uid 里 → 角色为 friend
    assert by_id["p2"].sender_role == "friend"
