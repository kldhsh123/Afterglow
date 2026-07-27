"""聊天连发协调器单测。"""

from __future__ import annotations

import pytest

from xuwen.chat_api.turn_coordinator import TurnCoordinator


@pytest.mark.asyncio
async def test_new_turn_cancels_previous_and_merges_unacked_inputs():
    coordinator = TurnCoordinator()

    first = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m1",
        text="第一条",
        image_shas=[],
        image_urls=[],
    )
    second = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m2",
        text="第二条",
        image_shas=[],
        image_urls=[],
    )

    assert first.cancel_event.is_set()
    assert not await coordinator.is_current(first)
    assert await coordinator.is_current(second)
    assert second.message_ids == ("m1", "m2")
    assert second.combined_text() == "第一条\n\n第二条"


@pytest.mark.asyncio
async def test_stale_turn_cannot_update_newer_active_turn():
    coordinator = TurnCoordinator()

    first = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m1",
        text="图片",
        image_shas=[],
        image_urls=["data:image/png;base64,old"],
    )
    second = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m2",
        text="新消息",
        image_shas=[],
        image_urls=[],
    )

    updated = await coordinator.update_pending_input(
        first,
        text="图片\n[图片1描述：旧请求慢返回]",
        image_shas=["old-sha"],
    )

    assert updated is False
    assert await coordinator.is_current(second)
    assert second.combined_text() == "图片\n\n新消息"
    assert second.combined_image_shas() == []


@pytest.mark.asyncio
async def test_discard_drops_stopped_inputs_without_merging_next_turn():
    coordinator = TurnCoordinator()

    first = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m1",
        text="不要再回复这条",
        image_shas=[],
        image_urls=[],
    )

    result = await coordinator.discard(
        caller_id="conv-1",
        message_ids=["m1"],
    )
    second = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m2",
        text="新话题",
        image_shas=[],
        image_urls=[],
    )

    assert first.cancel_event.is_set()
    assert result == {"discarded": 1, "cancelled_active": True, "remaining": 0}
    assert second.message_ids == ("m2",)
    assert second.combined_text() == "新话题"


@pytest.mark.asyncio
async def test_discard_old_message_does_not_cancel_newer_active_turn():
    coordinator = TurnCoordinator()

    await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m1",
        text="旧消息",
        image_shas=[],
        image_urls=[],
    )
    second = await coordinator.begin_turn(
        caller_id="conv-1",
        message_id="m2",
        text="新消息",
        image_shas=[],
        image_urls=[],
    )

    result = await coordinator.discard(
        caller_id="conv-1",
        message_ids=["m1"],
    )

    assert result == {"discarded": 1, "cancelled_active": False, "remaining": 1}
    assert await coordinator.is_current(second)
