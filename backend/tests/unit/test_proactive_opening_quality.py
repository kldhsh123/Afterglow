"""主动开场质量门控测试。"""

from __future__ import annotations

from xuwen.chat_api.routes.companion import (
    _fallback_proactive_opening,
    _parse_proactive_opening_judgement,
    _parse_proactive_topic_hook,
    _proactive_opening_hard_violation,
    _ProactiveTopicHook,
)
from xuwen.companion.life import LifeSnapshot


def _life(
    *,
    current_activity: str = "在床上看手机",
    availability: str = "available",
    topic_seed: str = "刚准备睡但还没睡，随手看看消息",
) -> LifeSnapshot:
    return LifeSnapshot(
        date="2026-07-05",
        time_slot="深夜",
        current_activity=current_activity,
        recent_meal="晚饭吃得普通",
        mood="普通",
        topic_seed=topic_seed,
        availability=availability,
        next_update_at="2026-07-05 00:45",
        reply_delay_seconds=0,
        reply_delay_reason="",
    )


def test_proactive_opening_hard_violation_keeps_only_safety_edges() -> None:
    assert _proactive_opening_hard_violation("") == "empty_message"
    assert _proactive_opening_hard_violation("还没睡") == "assumes_user_current_state"
    assert _proactive_opening_hard_violation("我还没睡，突然想找你说两句") == ""
    assert _proactive_opening_hard_violation("abc") == ""
    assert _proactive_opening_hard_violation("在吗") == "assumes_user_current_state"
    assert _proactive_opening_hard_violation("刚准备睡但还在刷手机") == ""
    assert _proactive_opening_hard_violation("还没睡\n\n刷手机刷得眼睛快睁不开了\n\n你睡") == "multi_line_opening"


def test_proactive_opening_hard_violation_catches_user_state_assumption() -> None:
    assert _proactive_opening_hard_violation("还没睡啊？") == "assumes_user_current_state"
    assert _proactive_opening_hard_violation("你在干嘛") == "assumes_user_current_state"
    assert _proactive_opening_hard_violation("看到你消息了") == "assumes_user_current_state"


def test_parse_proactive_opening_judgement() -> None:
    parsed = _parse_proactive_opening_judgement(
        '{"should_rewrite": true, "reason": "只是状态播报", '
        '"rewrite_instruction": "补一个可回复点"}'
    )

    assert parsed.should_rewrite is True
    assert parsed.reason == "只是状态播报"
    assert parsed.rewrite_instruction == "补一个可回复点"


def test_parse_proactive_opening_judgement_fail_closed_on_bad_json() -> None:
    parsed = _parse_proactive_opening_judgement("not json")

    assert parsed.should_rewrite is True
    assert parsed.reason == "judge_parse_failed"


def test_proactive_opening_fallback_does_not_assume_user_state() -> None:
    fallback = _fallback_proactive_opening(_life())

    assert "你还没睡" not in fallback
    assert "看到你" not in fallback
    assert "在干嘛" not in fallback
    assert "有空的话" in fallback


def test_proactive_opening_fallback_uses_relationship_hook() -> None:
    fallback = _fallback_proactive_opening(
        _life(),
        relationship_context="- [2026-05-28] (note, 1) 用户说：最近在准备考试",
    )

    assert "准备考试" in fallback
    assert "后来怎么样" in fallback


def test_proactive_opening_fallback_prefers_topic_hook() -> None:
    fallback = _fallback_proactive_opening(
        _life(),
        relationship_context="- [2026-05-28] (note, 1) 用户说：最近在准备考试",
        topic_hook=_ProactiveTopicHook(
            found=True,
            hook="明天办手续",
            source="context_cache",
        ),
    )

    assert fallback == "你有空的话，我想接着问问明天办手续"


def test_parse_proactive_topic_hook_rejects_low_information_hook() -> None:
    parsed = _parse_proactive_topic_hook(
        '{"found": true, "hook": "最近怎么样", "source": "relationship", "reason": "弱记忆"}'
    )

    assert parsed.found is False
    assert parsed.reason == "low_information_topic_hook"


def test_parse_proactive_topic_hook_rejects_generic_previous_topic() -> None:
    parsed = _parse_proactive_topic_hook(
        '{"found": true, "hook": "之前那个话题", "source": "retrieval", "reason": "太泛"}'
    )

    assert parsed.found is False
    assert parsed.reason == "low_information_topic_hook"
