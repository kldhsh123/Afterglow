"""生活时间线状态机测试。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from xuwen.companion.life import LifeStateManager
from xuwen.config import Settings


class FakeLifeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    async def complete_chat(
        self,
        messages: list[dict[str, str]],
        params: object | None = None,
        *,
        model: str | None = None,
    ) -> str:
        self.calls += 1
        self.messages.append(messages)
        return self.response


def _settings(tmp_path, **overrides: Any) -> Settings:
    values = {
        "persona_data_dir": tmp_path / "persona",
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
        "life_max_reply_delay_seconds": 45,
    }
    values.update(overrides)
    return Settings(**values)


def _state(*, date: str, slot: str, availability: str, next_update_at: str) -> dict[str, Any]:
    return {
        "date": date,
        "mood": "安静",
        "slots": {
            slot: {
                "activity": "在看消息",
                "meal": "喝了水",
                "topic": "今天怎么过",
            }
        },
        "current": {
            "time_slot": slot,
            "activity": "在看消息",
            "meal": "喝了水",
            "mood": "安静",
            "topic": "今天怎么过",
            "availability": availability,
            "next_update_at": next_update_at,
            "reply_delay_seconds": 0,
            "reply_delay_reason": "",
        },
        "timeline": [],
    }


def test_life_snapshot_prompt_includes_day_plan_and_timeline(tmp_path):
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)
    manager.path.parent.mkdir(parents=True)
    state = _state(
        date="2026-05-21",
        slot="下午",
        availability="available",
        next_update_at="2026-05-21 18:00",
    )
    state["daily_plan"] = [
        {
            "id": "afternoon",
            "from": "2026-05-21 13:30",
            "to": "2026-05-21 18:30",
            "activity": "在整理自己的东西",
            "availability": "busy",
            "topic": "问问用户下午忙不忙",
        }
    ]
    state["timeline"] = [
        {
            "at": "2026-05-21T14:20:00",
            "activity": "刚泡了杯水",
            "meal": "午饭吃得比较简单",
        }
    ]
    manager.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    prompt = manager.snapshot(datetime(2026, 5, 21, 15, 0)).render_prompt_block()

    assert "今天计划" in prompt
    assert "在整理自己的东西" in prompt
    assert "今天已经发生的状态变化" in prompt
    assert "刚泡了杯水" in prompt
    assert "历史记忆只用于语气和偏好" in prompt


@pytest.mark.asyncio
async def test_life_state_reuses_current_until_next_update(tmp_path):
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)
    manager.path.parent.mkdir(parents=True)
    manager.path.write_text(
        json.dumps(
            _state(
                date="2026-05-21",
                slot="下午",
                availability="available",
                next_update_at="2026-05-21 18:00",
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    llm = FakeLifeLLM("{}")

    snapshot = await manager.decide_for_turn(
        llm=llm,  # type: ignore[arg-type]
        model="life-small",
        current_user_text="你好",
        recent=[],
        now=datetime(2026, 5, 21, 15, 0),
    )

    assert llm.calls == 0
    assert snapshot.current_activity == "在看消息"
    assert snapshot.next_update_at == "2026-05-21 18:00"


@pytest.mark.asyncio
async def test_life_state_updates_after_configured_interval(tmp_path):
    settings = _settings(tmp_path, life_update_interval_minutes=60)
    manager = LifeStateManager(settings)
    manager.path.parent.mkdir(parents=True)
    state = _state(
        date="2026-05-21",
        slot="下午",
        availability="available",
        next_update_at="2026-05-21 18:00",
    )
    state["current"]["updated_at"] = "2026-05-21T13:30:00"
    manager.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    llm = FakeLifeLLM(
        json.dumps(
            {
                "current_activity": "刚从午后的事里缓过来",
                "recent_meal": "午饭吃得比较简单",
                "mood": "还行",
                "availability": "available",
                "topic_seed": "问问用户下午怎么样",
                "next_update_at": "2026-05-21 18:00",
                "reply_delay_seconds": 0,
            },
            ensure_ascii=False,
        )
    )

    snapshot = await manager.decide_for_turn(
        llm=llm,  # type: ignore[arg-type]
        model="life-small",
        current_user_text="你好",
        recent=[],
        now=datetime(2026, 5, 21, 15, 0),
    )

    assert llm.calls == 1
    assert snapshot.current_activity == "刚从午后的事里缓过来"


@pytest.mark.asyncio
async def test_life_decision_uses_analysis_habits_as_non_factual_prior(tmp_path):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir(parents=True)
    analysis_dir.joinpath("life_context.md").write_text(
        "【历史生活规律参考】\n- 活动偏好：夜间常和朋友打游戏",
        encoding="utf-8",
    )
    settings = _settings(
        tmp_path,
        analysis_data_dir=analysis_dir,
        analysis_life_context_enabled=True,
    )
    manager = LifeStateManager(settings)
    llm = FakeLifeLLM(
        json.dumps(
            {
                "current_activity": "在处理自己的事",
                "recent_meal": "还没特别吃什么",
                "mood": "普通",
                "availability": "available",
                "topic_seed": "问问今天安排",
                "next_update_at": "2026-05-21 16:00",
                "reply_delay_seconds": 0,
            },
            ensure_ascii=False,
        )
    )

    await manager.decide_for_turn(
        llm=llm,  # type: ignore[arg-type]
        model="life-small",
        current_user_text="在干嘛",
        recent=[],
        now=datetime(2026, 5, 21, 15, 0),
    )

    prompt = llm.messages[0][1]["content"]
    assert "夜间常和朋友打游戏" in prompt
    assert "仅作候选先验，不代表今天已经发生" in prompt
    assert "不要因为历史上常熬夜、打游戏" in prompt


@pytest.mark.asyncio
async def test_life_state_updates_when_sleeping_is_interrupted(tmp_path):
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)
    manager.path.parent.mkdir(parents=True)
    manager.path.write_text(
        json.dumps(
            _state(
                date="2026-05-21",
                slot="深夜",
                availability="sleeping",
                next_update_at="2026-05-21 08:00",
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    llm = FakeLifeLLM(
        json.dumps(
            {
                "current_activity": "被消息叫醒，半醒着看手机",
                "recent_meal": "晚饭后没再吃什么",
                "mood": "困，但还在看消息",
                "availability": "sleeping",
                "topic_seed": "问问用户怎么还没睡",
                "next_update_at": "2026-05-21 08:00",
                "reply_delay_seconds": 999,
                "reply_delay_reason": "刚被消息叫醒",
                "reason": "睡眠状态被用户消息打断",
            },
            ensure_ascii=False,
        )
    )

    snapshot = await manager.decide_for_turn(
        llm=llm,  # type: ignore[arg-type]
        model="life-small",
        current_user_text="你睡了吗",
        recent=[],
        now=datetime(2026, 5, 21, 2, 0),
    )

    assert llm.calls == 1
    assert snapshot.availability == "sleeping"
    assert snapshot.current_activity == "被消息叫醒，半醒着看手机"
    assert snapshot.reply_delay_seconds == 45
    assert snapshot.reply_delay_reason == "刚被消息叫醒"


@pytest.mark.asyncio
async def test_life_decision_prompt_treats_memory_as_tone_not_today_fact(tmp_path):
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)
    manager.path.parent.mkdir(parents=True)
    manager.path.write_text(
        json.dumps(
            _state(
                date="2026-05-21",
                slot="下午",
                availability="available",
                next_update_at="2026-05-21 14:00",
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    llm = FakeLifeLLM("{}")

    await manager.decide_for_turn(
        llm=llm,  # type: ignore[arg-type]
        model="life-small",
        current_user_text="你今天都在干什么啊",
        recent=[],
        memory_context="- 历史片段：用户曾经打游戏",
        now=datetime(2026, 5, 21, 15, 0),
    )

    prompt = llm.messages[0][1]["content"]
    assert "相关历史语气参考" in prompt
    assert "用户曾经打游戏" in prompt
    assert "不是今天事实" in prompt
    assert "不要据此生成" in prompt


def test_apply_marker_patch_updates_state(tmp_path):
    """主模型输出 life-update 标记块的 JSON 字符串应直接 patch life。"""
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)

    snapshot = manager.apply_marker_patch(
        '{"current_activity": "去吃饭了", "recent_meal": "刚吃了拉面",'
        ' "availability": "busy"}',
        now=datetime(2026, 5, 22, 12, 30),
    )

    assert snapshot is not None
    assert snapshot.current_activity == "去吃饭了"
    assert snapshot.recent_meal == "刚吃了拉面"
    assert snapshot.availability == "busy"


def test_apply_marker_patch_returns_none_on_invalid_input(tmp_path):
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)

    assert manager.apply_marker_patch("不是 JSON") is None
    assert manager.apply_marker_patch("") is None
    assert manager.apply_marker_patch("{}") is None


def test_apply_marker_patch_normalizes_invalid_availability(tmp_path):
    """无效的 availability 值应该被 _normalize_availability 兜底为 available。"""
    settings = _settings(tmp_path)
    manager = LifeStateManager(settings)
    snapshot = manager.apply_marker_patch(
        '{"current_activity": "测试", "availability": "wandering"}',
        now=datetime(2026, 5, 22, 10, 0),
    )
    assert snapshot is not None
    assert snapshot.availability == "available"
