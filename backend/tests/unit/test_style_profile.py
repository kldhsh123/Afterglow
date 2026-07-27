"""Scene-specific persona style profile tests."""

from __future__ import annotations

from xuwen.core.models import MessageKind, NormalizedMessage
from xuwen.ingestion.splitter import split_sessions
from xuwen.persona.style_profile import (
    build_style_profile,
    load_style_profile,
    render_random_burst_block,
    render_style_profile_for_query,
    save_style_profile,
)


def _msg(seq: int, role: str, text: str) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=f"m{seq}",
        seq=seq,
        timestamp_ms=seq * 1000,
        sender_uid=f"u-{role}",
        sender_name=role,
        sender_role=role,  # type: ignore[arg-type]
        kind=MessageKind.TEXT,
        raw_type="type_1",
        text=text,
    )


def test_build_style_profile_groups_response_pairs_by_scene(tmp_path):
    messages = [
        _msg(1, "self", "在干嘛"),
        _msg(2, "friend", "刚吃完饭"),
        _msg(3, "self", "想你了"),
        _msg(4, "friend", "抱抱"),
        _msg(5, "self", "今天好累"),
        _msg(6, "friend", "先歇一会儿"),
    ]
    sessions = split_sessions(messages, _settings())

    profile = build_style_profile(sessions, friend_name="TA", self_name="Me")

    scenes = {scene.scene_id: scene for scene in profile.scenes}
    assert scenes["life_check"].samples[0].friend_reply == "刚吃完饭"
    assert scenes["miss_you"].intimacy_reply_ratio == 1.0
    assert scenes["comfort"].confidence == "low"

    path = tmp_path / "persona_style_profile.json"
    save_style_profile(profile, path)
    loaded = load_style_profile(path)
    rendered = render_style_profile_for_query(loaded, "你在干什么")

    assert "场景画像：寒暄 / 在干嘛" in rendered
    assert "刚吃完饭" in rendered
    assert "场景画像：想念 / 亲密表达" not in rendered


def test_random_burst_profile_is_gated_by_query_tone():
    messages = [
        _msg(1, "self", "哈哈"),
        _msg(2, "friend", "啊啊啊救命笑死"),
        _msg(3, "self", "怎么这样"),
        _msg(4, "friend", "我要闹了"),
        _msg(5, "self", "草"),
        _msg(6, "friend", "绷不住了"),
    ]
    sessions = split_sessions(messages, _settings())

    profile = build_style_profile(sessions, friend_name="TA", self_name="Me")
    assert profile.random_burst is not None
    assert profile.random_burst.evidence_count == 3

    light = render_random_burst_block(profile, "哈哈救命")
    serious = render_random_burst_block(profile, "你今天在干什么")

    assert "本轮允许：是" in light
    assert "啊啊啊救命笑死" in light
    assert "本轮允许：否" in serious
    assert "事实/状态/解释或安慰" in serious


def _settings():
    from xuwen.config import Settings

    return Settings(
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
        relationship_type="friend",
        session_gap_minutes=30,
    )
