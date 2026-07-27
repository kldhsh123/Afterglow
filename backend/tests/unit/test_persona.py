"""persona analyzer + card 单测。"""

from __future__ import annotations

from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.ingestion.splitter import split_sessions
from xuwen.persona.analyzer import analyze_persona, report_to_dict
from xuwen.persona.card import render_persona_card


def _msg(seq: int, ts: int, role: str, text: str) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m{seq}",
        seq=seq,
        timestamp_ms=ts,
        sender_uid=f"u-{role}",
        sender_name=role,
        sender_role=role,  # type: ignore[arg-type]
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text=text,
    )


def _build_settings():
    from xuwen.config import Settings
    return Settings(
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        relationship_type="friend",
        session_gap_minutes=30,
    )


def test_analyze_persona_returns_friend_stats():
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "self", "你好啊"),
        _msg(2, 2000, "friend", "嗯嗯，怎么了？"),
        _msg(3, 3000, "self", "想问你件事"),
        _msg(4, 4000, "friend", "嗯嗯，你说"),
        _msg(5, 5000, "friend", "我在的"),
        _msg(6, 6000, "self", "好"),
        _msg(7, 7000, "friend", "嗯嗯，慢慢说"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    assert report.friend_message_count == 4
    # 高频短语应包含"嗯嗯"
    assert any("嗯嗯" in t.term for t in report.top_phrases)
    # 应有典型对话样本
    assert len(report.samples) >= 1
    assert all(s.user_text for s in report.samples)


def test_render_persona_card_includes_name_and_samples():
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "self", "在干嘛"),
        _msg(2, 2000, "friend", "在看剧呢，挺好看的"),
        _msg(3, 3000, "self", "什么剧"),
        _msg(4, 4000, "friend", "你猜呀，超治愈的那种"),
        _msg(5, 5000, "self", "什么呀"),
        _msg(6, 6000, "friend", "晚安，早点睡，记得吃饭"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="Bob", self_name="Me")
    md = render_persona_card(report)
    assert "Bob" in md
    assert "## 语言节奏" in md
    assert "## 典型对话样本" in md
    assert "长期统计画像，只能作为语气参考" in md
    # 不应回显未脱敏的敏感片段格式（仅检查样本展示是否有 Me/Bob 前缀）
    assert "Me：" in md or "Bob：" in md


def test_render_persona_card_does_not_encourage_image_placeholder_output():
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "self", "看看"),
        _msg(2, 2000, "friend", "[图片] 这个好可爱"),
        _msg(3, 3000, "self", "还有吗"),
        _msg(4, 4000, "friend", "[图片] 这个也行"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    md = render_persona_card(report)

    assert "模型不能直接输出 [图片] 占位符" in md
    assert "以 [图片] 代替文字也是自然的" not in md


def test_report_to_dict_roundtrip():
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "self", "hi"),
        _msg(2, 2000, "friend", "hello"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    d = report_to_dict(report)
    assert d["friend_name"] == "TA"
    assert d["total_messages"] == 2
    assert "samples" in d


def test_persona_skips_recalled_and_system():
    settings = _build_settings()
    msgs = [
        NormalizedMessage(
            message_id="r",
            seq=1,
            timestamp_ms=1000,
            sender_uid="u-friend",
            sender_name="TA",
            sender_role="friend",
            kind=MessageKind.RECALLED,
            raw_type="type_1",
            text="[撤回]",
            recalled=True,
        ),
        _msg(2, 2000, "friend", "正常消息"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    assert report.friend_message_count == 1


def test_persona_skips_placeholder_messages():
    """PLACEHOLDER 类型（type_17 / json 卡片等）不应进入词频统计。"""
    settings = _build_settings()
    msgs = [
        # 一条真实文本
        _msg(1, 1000, "friend", "嘿嘿好可爱"),
        # 一条 json 类卡片，text 字段里有 title/desc 等噪声字段
        NormalizedMessage(
            message_id="p",
            seq=2,
            timestamp_ms=2000,
            sender_uid="u-friend",
            sender_name="TA",
            sender_role="friend",
            kind=MessageKind.PLACEHOLDER,
            raw_type="type_17",
            text='{"title":"分享标题","desc":"分享内容预览"}',
        ),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    # 只统计 1 条朋友消息
    assert report.friend_message_count == 1
    top_terms_str = " ".join(t.term for t in report.top_terms)
    assert "title" not in top_terms_str
    assert "desc" not in top_terms_str


def test_persona_excludes_extended_stopwords():
    """扩展停用词（一下/看看/怎么/title/回复等）不应进入高频。"""
    settings = _build_settings()
    msgs = [
        _msg(i, i * 1000, "friend", text)
        for i, text in enumerate(
            [
                "在干嘛 一下",
                "看看 怎么了",
                "一下 看看 一下",
                "回复 title desc",
                "嘿嘿 笨蛋",
            ],
            start=1,
        )
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    forbidden = {"一下", "看看", "怎么", "回复", "title", "desc"}
    top_terms = {t.term for t in report.top_terms}
    top_phrases = {t.term for t in report.top_phrases}
    assert top_terms.isdisjoint(forbidden), (
        f"扩展停用词不应进入 top_terms：{top_terms & forbidden}"
    )
    assert top_phrases.isdisjoint(forbidden), (
        f"扩展停用词不应进入 top_phrases：{top_phrases & forbidden}"
    )


def test_persona_filters_uid_like_tokens():
    """长 base64 串（uid 残留）不应进入词频。"""
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "friend", "ExampleSelfUid00000 嘿嘿"),
        _msg(2, 2000, "friend", "ExampleSelfUid00000 好可爱"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")
    terms_str = " ".join(t.term for t in report.top_terms + report.top_phrases)
    assert "ExampleSelfUid00000" not in terms_str


def test_persona_excludes_short_bracket_faces_from_text_stats():
    """历史数据中的任意短方括号表情不应污染词频、短语或对话样本。"""
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "self", "怎么了[Question]"),
        _msg(2, 2000, "friend", "也是[Toasted][Toasted]正常聊天"),
        _msg(3, 3000, "friend", "保留[1234567890123]"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me")

    stats_text = " ".join(t.term for t in report.top_terms + report.top_phrases)
    assert "Toasted" not in stats_text
    assert all(part not in stats_text for part in ("To", "as", "st", "te", "ted"))
    assert "1234567890123" not in stats_text  # 长数字仍由 token 规则排除
    assert any("正常聊天" in sample.friend_text for sample in report.samples)
    assert all("Toasted" not in sample.friend_text for sample in report.samples)


def test_persona_normalized_mode_preserves_noncanonical_brackets():
    settings = _build_settings()
    msgs = [
        _msg(1, 1000, "self", "说说看"),
        _msg(2, 2000, "friend", "保留[Toasted]文本[/表情]"),
    ]
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(
        sessions,
        friend_name="TA",
        self_name="Me",
        json_emoji_mode="normalized",
    )
    stats_text = " ".join(t.term for t in report.top_terms + report.top_phrases)
    assert "Toasted" in stats_text
    assert all("[/表情]" not in sample.friend_text for sample in report.samples)
    assert any("[Toasted]" in sample.friend_text for sample in report.samples)


def test_sample_pairs_covers_length_buckets():
    """样本应该覆盖短/中/长三档朋友回复，不应全是同一长度。"""
    settings = _build_settings()
    msgs: list[NormalizedMessage] = []
    seq = 0
    short_pool = [f"哈{i}" for i in range(20)]
    mid_pool = [f"嘿嘿好可爱{i}" for i in range(20)]
    long_pool = [f"哎呀这种事情我也不太清楚啊要不要等一下再说呢{i}" for i in range(20)]
    for friend_text in short_pool + mid_pool + long_pool:
        seq += 1
        msgs.append(_msg(seq, seq * 1000, "self", "在吗"))
        seq += 1
        msgs.append(_msg(seq, seq * 1000, "friend", friend_text))
    sessions = split_sessions(msgs, settings)
    report = analyze_persona(sessions, friend_name="TA", self_name="Me", sample_count=12)
    lengths = [len(s.friend_text) for s in report.samples]
    short = sum(1 for L in lengths if L < 8)
    long_ = sum(1 for L in lengths if L > 20)
    # 至少各桶都有命中
    assert short >= 1, f"应该至少有一条短样本，实际长度分布：{lengths}"
    assert long_ >= 1, f"应该至少有一条长样本，实际长度分布：{lengths}"
