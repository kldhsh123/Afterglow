"""chat_pipeline 共享 helper 单测。

聚焦那些跨路由复用且容易出错的判定函数，例如 AI 自主沉默信号识别。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from xuwen.chat_api.chat_pipeline import (
    build_policy_hint,
    effective_reply_delay_seconds,
    effective_silence_sentinel,
    extract_life_events,
    is_ai_silence_signal,
    schedule_life_events,
)
from xuwen.companion.life import LIFE_EVENT_TEXT_MAX_CHARS, LifeSnapshot
from xuwen.companion.response_policy import ResponseDecision
from xuwen.config import Settings


def _decision(**overrides: Any) -> ResponseDecision:
    """构造一份默认允许沉默的决策，方便在测试里只翻反例字段。"""
    data: dict[str, Any] = dict(
        should_reply=True,
        reply_mode="calm",
        risk_level="low",
        user_state="normal",
        retrieval_focus="human_style",
        use_image=False,
        use_sticker=False,
        reply_delay_seconds=0,
        max_length="short",
        do_not=[],
        instructions=[],
    )
    data.update(overrides)
    return ResponseDecision(**data)


def test_silence_signal_exact_match():
    assert is_ai_silence_signal(
        "[silent]",
        sentinel="[silent]",
        decision=_decision(),
    )


def test_silence_signal_allows_surrounding_whitespace():
    """sentinel 前后只有空白 → 仍判为沉默（容错主模型多了个换行）。"""
    assert is_ai_silence_signal(
        "  [silent]\n",
        sentinel="[silent]",
        decision=_decision(),
    )


def test_silence_signal_rejects_extra_content():
    """sentinel 夹在普通正文里不算沉默，避免误吞真实回复。"""
    assert not is_ai_silence_signal(
        "嗯[silent]我在的",
        sentinel="[silent]",
        decision=_decision(),
    )


def test_silence_signal_rejected_when_unsafe():
    """unsafe 场景下即使模型违规输出 sentinel，也必须当文本处理（继续回复）。"""
    assert not is_ai_silence_signal(
        "[silent]",
        sentinel="[silent]",
        decision=_decision(user_state="unsafe", risk_level="high", reply_mode="serious"),
    )


def test_silence_signal_rejected_when_rule_silence():
    """规则层已强制 silence 的情况走自己的短路，AI sentinel 不再进入此分支。"""
    assert not is_ai_silence_signal(
        "[silent]",
        sentinel="[silent]",
        decision=_decision(reply_mode="silence", should_reply=False),
    )


def test_silence_signal_rejected_for_empty_sentinel_config():
    """sentinel 配置被清空时不应触发沉默路径。"""
    assert not is_ai_silence_signal(
        "",
        sentinel="",
        decision=_decision(),
    )


def test_silence_signal_rejected_for_empty_assistant_text():
    """sanitize 后整段为空不算沉默信号，由 sticker 兜底处理。"""
    assert not is_ai_silence_signal(
        "",
        sentinel="[silent]",
        decision=_decision(),
    )


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
    }
    base.update(overrides)
    return Settings(**base)


def test_effective_silence_sentinel_default_returns_configured_value():
    """ai_silence_enabled 默认 True → 返回配置的 sentinel。"""
    settings = _settings()
    assert effective_silence_sentinel(settings) == settings.silence_response_sentinel


def test_effective_silence_sentinel_returns_empty_when_disabled():
    """关闭开关后返回空串，等价于把沉默出口从 prompt 和 pipeline 同时关掉。"""
    settings = _settings(ai_silence_enabled=False)
    assert effective_silence_sentinel(settings) == ""


def test_effective_silence_sentinel_disabled_blocks_signal_detection():
    """配合 is_ai_silence_signal：开关关 → 即使模型输出 sentinel 也不判沉默。"""
    settings = _settings(ai_silence_enabled=False)
    sentinel = effective_silence_sentinel(settings)
    assert not is_ai_silence_signal(
        settings.silence_response_sentinel,
        sentinel=sentinel,
        decision=_decision(),
    )


def test_policy_hint_includes_client_reply_delay():
    hint = build_policy_hint(
        _decision(reply_delay_seconds=12),
        reply_delay_seconds=12,
        reply_delay_reason="刚被消息叫醒",
    )
    assert hint.reply_delay_seconds == 12
    assert hint.reply_delay_reason == "刚被消息叫醒"


def test_effective_reply_delay_is_bounded_and_skips_silence():
    settings = _settings(life_max_reply_delay_seconds=20)
    life = LifeSnapshot(
        date="2026-05-23",
        time_slot="夜里",
        current_activity="半醒着看手机",
        recent_meal="喝了水",
        mood="困",
        topic_seed="早点睡",
        availability="sleeping",
        next_update_at="2026-05-23 08:00",
        reply_delay_seconds=45,
        reply_delay_reason="刚被消息叫醒",
    )
    assert effective_reply_delay_seconds(
        life=life,
        decision=_decision(reply_delay_seconds=15),
        settings=settings,
    ) == 20
    assert effective_reply_delay_seconds(
        life=life,
        decision=_decision(should_reply=False, reply_mode="silence"),
        settings=settings,
    ) == 0


# ---------- life event extraction / scheduling ----------


def test_extract_life_events_strips_new_and_legacy_protocols():
    result = extract_life_events(
        "准备吃饭<life-event>准备去吃饭，暂时忙一会儿</life-event>"
        '<life-update>{"mood":"放松"}</life-update>'
    )

    assert result.text == "准备吃饭"
    assert result.events == (
        "准备去吃饭，暂时忙一会儿",
        '{"mood":"放松"}',
    )


def test_extract_life_events_deduplicates_and_limits():
    result = extract_life_events(
        "正文<life-event>去吃饭</life-event>"
        "<life-event>去吃饭</life-event>"
        "<life-event>准备休息</life-event>",
        max_events=1,
    )

    assert result.text == "正文"
    assert result.events == ("去吃饭",)


def test_extract_life_events_respects_total_downstream_budget():
    blocks = "".join(
        f"<life-event>{str(index) * 1200}</life-event>" for index in range(10)
    )

    result = extract_life_events(blocks, max_events=10)

    assert len("\n".join(result.events)) <= LIFE_EVENT_TEXT_MAX_CHARS


@pytest.mark.asyncio
async def test_async_marker_no_marker_no_task():
    """没有事件时不创建任何 task。"""
    life = MagicMock()
    lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    schedule_life_events(
        (),
        life,
        enabled=True,
        llm=MagicMock(),
        model="life-small",
        apply_lock=lock,
        pending_tasks=pending,
    )

    assert list(pending) == []
    life.apply_event.assert_not_called()


@pytest.mark.asyncio
async def test_async_marker_disabled_strips_without_apply():
    """enabled=False 时提取仍有效，但不调用 Life 模型。"""
    life = MagicMock()
    lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    result = extract_life_events("早安<life-event>准备休息</life-event>")
    schedule_life_events(
        result.events,
        life,
        enabled=False,
        llm=MagicMock(),
        model="life-small",
        apply_lock=lock,
        pending_tasks=pending,
    )

    assert result.text == "早安"
    assert list(pending) == []
    life.apply_event.assert_not_called()


@pytest.mark.asyncio
async def test_async_marker_serializes_under_lock():
    """同一 lock 下的多个事件任务必须串行调用 Life 模型。"""
    apply_active = [False]
    call_order: list[str] = []

    async def slow_apply(**kwargs: Any) -> None:
        assert not apply_active[0], "并发 apply 同时进入，lock 失效"
        apply_active[0] = True
        await asyncio.sleep(0.02)
        call_order.append(str(kwargs["event_text"]))
        apply_active[0] = False

    life = MagicMock()
    life.settings.life_timeout_seconds = 1.0
    life.apply_event = AsyncMock(side_effect=slow_apply)
    lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    schedule_life_events(
        ("a",),
        life,
        enabled=True,
        llm=MagicMock(),
        model="life-small",
        apply_lock=lock,
        pending_tasks=pending,
    )
    schedule_life_events(
        ("b",),
        life,
        enabled=True,
        llm=MagicMock(),
        model="life-small",
        apply_lock=lock,
        pending_tasks=pending,
    )

    tasks = list(pending)
    assert len(tasks) == 2
    # 加 wait_for 兜底：lock 死锁 / 线程池饥饿等极端情况下 5s 内必须能跑完，
    # 避免某些环境（CI / WSL 共享线程池）下测试无限挂住。
    await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True),
        timeout=5.0,
    )
    assert sorted(call_order) == ["a", "b"]
    assert apply_active[0] is False


@pytest.mark.asyncio
async def test_async_marker_swallows_apply_exception():
    """Life 模型处理抛异常时 task 不向上传播。"""
    life = MagicMock()
    life.settings.life_timeout_seconds = 1.0
    life.apply_event = AsyncMock(side_effect=RuntimeError("boom"))
    lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    schedule_life_events(
        ("准备休息",),
        life,
        enabled=True,
        llm=MagicMock(),
        model="life-small",
        apply_lock=lock,
        pending_tasks=pending,
    )

    tasks = list(pending)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 异常被吞，task 本身不应抛
    assert all(not isinstance(r, BaseException) for r in results)
