"""cleaner + PII 脱敏单测。"""

from __future__ import annotations

import re

from xuwen.config import Settings
from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.ingestion.cleaner import Cleaner
from xuwen.ingestion.parser import parse_messages
from xuwen.persona.pii_rules import DEFAULT_RULES, redact


def test_redact_phone_email_idcard():
    text = "联系我：13812345678 / me@example.com / 110101199003070011"
    result = redact(text, list(DEFAULT_RULES))
    assert "[手机号]" in result
    assert "[邮箱]" in result
    assert "[身份证]" in result
    # 原始信息应该消失
    assert "13812345678" not in result
    assert "me@example.com" not in result
    assert "110101199003070011" not in result


def test_redact_invalid_idcard_not_substituted():
    """非法身份证号（校验位错）不应被替换。"""
    text = "工号 110101199003078212 不是身份证"
    result = redact(text, list(DEFAULT_RULES))
    assert "110101199003078212" in result


def test_redact_valid_bank_card_passes_luhn():
    text = "卡号 6222020200112233446 转账"
    result = redact(text, list(DEFAULT_RULES))
    assert "[银行卡]" in result
    assert "6222020200112233446" not in result


def test_redact_long_digits_not_bank_card():
    """长数字（订单号、时间戳）不应被误判为银行卡。"""
    text = "订单 1234567890123456789 时间戳 1753690491000"
    result = redact(text, list(DEFAULT_RULES))
    assert "1234567890123456789" in result
    assert "1753690491000" in result


def test_redact_ipv4():
    assert redact("server 192.168.0.1 down", list(DEFAULT_RULES)) == "server [IP] down"


def test_cleaner_recalled_message():
    settings = Settings()
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="r1",
        seq=1,
        timestamp_ms=1000,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.RECALLED,
        raw_type="type_1",
        text="原始内容",
        recalled=True,
    )
    assert cleaner.clean(msg).text == "[撤回]"


def test_cleaner_replaces_bracket_media(settings_for_sample, sample_payload):
    cleaner = Cleaner(settings_for_sample)
    parsed = parse_messages(sample_payload, settings_for_sample)
    cleaned = cleaner.clean_many(parsed)
    image_msgs = [m for m in cleaned if "[图片]" in m.text]
    assert len(image_msgs) > 0
    for m in image_msgs:
        assert not re.search(r"\[图片[:：]", m.text), "原始带文件名的占位应已被替换"


def test_cleaner_normalizes_qq_native_face():
    """QQ 自带文字表情 `[/汪汪]` / `[[狗狗可怜]]` 应在导入阶段被归一化为 [表情]，
    避免主模型把它当语气信号原样输出。"""
    settings = Settings(enable_pii_redaction=False)
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="f1",
        seq=1,
        timestamp_ms=1,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="今天好累[/汪汪]，晚点再聊[/呲牙][[狗狗可怜]]",
    )
    cleaned = cleaner.clean(msg).text
    assert "[/汪汪]" not in cleaned
    assert "[/呲牙]" not in cleaned
    assert "[[狗狗可怜]]" not in cleaned
    assert cleaned.count("[表情]") == 3
    assert "今天好累" in cleaned and "晚点再聊" in cleaned


def test_cleaner_normalizes_wechat_emoji():
    """微信内置 emoji `[微笑]` `[捂脸]` `[破涕为笑]` 应归一化为 [表情]，
    系统占位符 `[图片]` `[位置]` `[链接]` 必须保留原样不被错误改写。"""
    settings = Settings(enable_pii_redaction=False)
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="w1",
        seq=1,
        timestamp_ms=1,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="文本消息",
        text="今天好累[微笑]，刚发了张[图片]，等下去[位置]发[链接][捂脸][破涕为笑]",
    )
    cleaned = cleaner.clean(msg).text
    # 内置 emoji 归一
    assert "[微笑]" not in cleaned
    assert "[捂脸]" not in cleaned
    assert "[破涕为笑]" not in cleaned
    assert cleaned.count("[表情]") == 3
    # 系统占位符保留
    assert "[图片]" in cleaned
    assert "[位置]" in cleaned
    assert "[链接]" in cleaned


def test_cleaner_raw_mode_normalizes_short_bracket_faces():
    settings = Settings(enable_pii_redaction=False)
    cleaner = Cleaner(settings, json_emoji_mode="raw")
    msg = NormalizedMessage(
        message_id="raw-face",
        seq=1,
        timestamp_ms=1,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="1",
        text="也是[Toasted][123456789012][1234567890123][图片]",
    )
    assert cleaner.clean(msg).text == "也是[表情][表情][1234567890123][图片]"


def test_cleaner_normalized_mode_only_recognizes_canonical_marker():
    settings = Settings(enable_pii_redaction=False)
    cleaner = Cleaner(settings, json_emoji_mode="normalized")
    msg = NormalizedMessage(
        message_id="normalized-face",
        seq=1,
        timestamp_ms=1,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="1",
        text="保留[Toasted][重点]，转换[/表情]，保留[[爱心]][图片]",
    )
    assert cleaner.clean(msg).text == "保留[Toasted][重点]，转换[表情]，保留[[爱心]][图片]"


def test_cleaner_rejects_unknown_json_emoji_mode():
    settings = Settings(enable_pii_redaction=False)
    try:
        Cleaner(settings, json_emoji_mode="other")
    except ValueError as exc:
        assert "json_emoji_mode" in str(exc)
    else:
        raise AssertionError("未知 JSON emoji 模式应被拒绝")


def test_cleaner_normalizes_short_bracket_faces_in_any_language():
    """方括号内 1-12 个字符统一视为表情，超过 12 个字符则保留。"""
    settings = Settings(enable_pii_redaction=False)
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="bracket-face",
        seq=1,
        timestamp_ms=1,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="1",
        text="也是[Toasted][emoji][123456789012][1234567890123][图片]",
    )

    cleaned = cleaner.clean(msg).text

    assert cleaned == "也是[表情][表情][表情][1234567890123][图片]"


def test_cleaner_replaces_multiple_self_uids_via_uids_list():
    """SELF_UIDS / FRIEND_UIDS 列表里的所有 UID 都应被替换为 @你 / @我。

    场景：跨平台一个人有 QQ + 微信两个号，消息里 @ 了不同号都要识别。
    """
    settings = Settings(
        self_uid="u_qq_main_account_aaaa",
        self_uids=["u_qq_alt_account_bbbb"],
        self_name="Me",
        friend_uid="u_friend_main_account_x",
        friend_uids=["u_friend_alt_account_y"],
        friend_name="TA",
        enable_pii_redaction=False,
    )
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="m1",
        seq=1,
        timestamp_ms=1,
        sender_uid="u_friend_main_account_x",
        sender_name="TA",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text=(
            "@u_qq_main_account_aaaa 主号在不，"
            "@u_qq_alt_account_bbbb 小号也戳一下，"
            "@u_friend_alt_account_y 我自己的微信号"
        ),
    )
    cleaned = cleaner.clean(msg).text
    # 三个 uid 都不应残留
    assert "u_qq_main_account_aaaa" not in cleaned
    assert "u_qq_alt_account_bbbb" not in cleaned
    assert "u_friend_alt_account_y" not in cleaned
    # 主号和小号都应转成 @你（self）
    assert cleaned.count("@你") == 2
    # 朋友小号转成 @我
    assert "@我" in cleaned


def test_cleaner_redacts_phone_in_real_message(settings_for_sample, sample_payload):
    cleaner = Cleaner(settings_for_sample)
    parsed = parse_messages(sample_payload, settings_for_sample)
    cleaned = cleaner.clean_many(parsed)
    # fixture 中 m8 含 13800138000 与 me@example.com
    msg = next(m for m in cleaned if m.message_id == "m8")
    assert "13800138000" not in msg.text
    assert "me@example.com" not in msg.text
    assert "[手机号]" in msg.text
    assert "[邮箱]" in msg.text


def test_cleaner_can_disable_pii_redaction():
    settings = Settings(enable_pii_redaction=False)
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u1",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="phone 13800138000",
    )
    assert "13800138000" in cleaner.clean(msg).text


def test_cleaner_replaces_mentions_perspective():
    """@对方 应替换为 @我（站在朋友视角生成训练样本）。"""
    settings = Settings(
        self_uid="uid-self-001",
        self_name="Me",
        friend_uid="uid-friend-001",
        friend_name="TestFriend",
    )
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="uid-self-001",
        sender_name="Me",
        sender_role="self",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="@TestFriend 是谁？",
    )
    cleaned = cleaner.clean(msg)
    assert "@我" in cleaned.text
    assert "@TestFriend" not in cleaned.text


def test_cleaner_replaces_uid_mentions():
    """QQ 偶尔会把 @ 提及存为 uid 形式（@u_xxx），需要识别并替换。"""
    settings = Settings(
        self_uid="u_ExampleSelfUid00000-w",
        self_name="Me",
        friend_uid="u_ExampleFriendUidBBBB",
        friend_name="TA",
    )
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u_ExampleFriendUidBBBB",
        sender_name="TA",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="@u_ExampleSelfUid00000-w 你在嘛",
    )
    cleaned = cleaner.clean(msg)
    assert "u_ExampleSelfUid00000" not in cleaned.text
    assert "@你" in cleaned.text


def test_cleaner_replaces_uid_body_only():
    """uid 的 base64 主体（去掉 u_ 前缀和 -x 尾缀）单独出现也应该被替换。"""
    settings = Settings(
        self_uid="u_ExampleSelfUid00000-w",
        self_name="Me",
        friend_uid="u_ExampleFriendUidBBBB",
        friend_name="TA",
    )
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u_ExampleFriendUidBBBB",
        sender_name="TA",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="刚才看到 ExampleSelfUid00000 出现",
    )
    cleaned = cleaner.clean(msg)
    assert "ExampleSelfUid00000" not in cleaned.text
    assert "@你" in cleaned.text


def test_cleaner_replaces_unknown_uid_with_placeholder():
    """未配置的第三方 uid 应被兜底替换为 @某人，避免泄漏。"""
    settings = Settings()
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u-other",
        sender_name="X",
        sender_role="other",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="@u_StrangerXXXXXXXXXXXXXX-z 你好",
    )
    cleaned = cleaner.clean(msg)
    assert "u_StrangerXXXXXXXXXXXXXX" not in cleaned.text
    assert "@某人" in cleaned.text


def test_cleaner_url_preserved():
    """URL 与域名应原样保留，不做简化（用户决策）。"""
    settings = Settings()
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="看这个 https://www.bilibili.com/video/BV1234?token=abc 很好玩",
    )
    cleaned = cleaner.clean(msg)
    assert "https://www.bilibili.com/video/BV1234?token=abc" in cleaned.text
    assert "[链接" not in cleaned.text


def test_cleaner_system_message_normalized():
    settings = Settings()
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="s",
        seq=1,
        timestamp_ms=1,
        sender_uid="",
        sender_name="",
        sender_role="system",
        kind=MessageKind.SYSTEM,
        raw_type="system",
        text="",
        system=True,
    )
    assert cleaner.clean(msg).text == "[系统消息]"


def test_cleaner_placeholder_appended_when_missing():
    settings = Settings()
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="p",
        seq=1,
        timestamp_ms=1,
        sender_uid="u",
        sender_name="X",
        sender_role="friend",
        kind=MessageKind.PLACEHOLDER,
        raw_type="type_1",
        text="",
        placeholders=["[图片]"],
        has_media=True,
    )
    assert cleaner.clean(msg).text == "[图片]"


def test_cleaner_normalizes_all_aliases_as_mentions():
    """SELF_ALIASES / FRIEND_ALIASES 里的任意别名 @ 提及都应被视角转换为 @我 / @你。"""
    settings = Settings(
        self_uid="u-self",
        self_name="Me",
        self_aliases=["测试别名", "Mike"],
        friend_uid="u-friend",
        friend_name="TA",
        friend_aliases=["阿巴", "ABBaa"],
    )
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u-friend",
        sender_name="TA",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="@Me 在吗 @测试别名 @Mike 都是你；@ABBaa 是我自己，@阿巴 也是我",
    )
    cleaned = cleaner.clean(msg).text
    # 用户的所有别名都视为视角对方 → @你
    assert "@Me" not in cleaned
    assert "@测试别名" not in cleaned
    assert "@Mike" not in cleaned
    assert cleaned.count("@你") == 3
    # 朋友自己的别名 → @我
    assert "@ABBaa" not in cleaned
    assert "@阿巴" not in cleaned
    assert cleaned.count("@我") == 2


def test_cleaner_alias_longer_name_wins_over_short():
    """优先匹配较长的别名，避免短名称提前吃掉长别名。"""
    settings = Settings(
        self_uid="u-self",
        self_name="别名",
        self_aliases=["测试别名"],
        friend_uid="u-friend",
        friend_name="TA",
    )
    cleaner = Cleaner(settings)
    msg = NormalizedMessage(
        message_id="x",
        seq=1,
        timestamp_ms=1,
        sender_uid="u-friend",
        sender_name="TA",
        sender_role="friend",
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text="@测试别名 你好",
    )
    cleaned = cleaner.clean(msg).text
    # 不应留下长别名的前缀残留
    assert cleaned.startswith("@你")
    assert "测试" not in cleaned
