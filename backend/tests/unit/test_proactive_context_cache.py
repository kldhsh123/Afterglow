"""主动开场最近上下文缓存测试。"""

from __future__ import annotations

import pytest

from xuwen.companion.proactive_context import (
    ProactiveContextCache,
    render_proactive_context_cache,
)
from xuwen.config import Settings


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "persona_data_dir": tmp_path / "persona",
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_proactive_context_cache_indexes_by_caller_and_conversation(tmp_path) -> None:
    cache = ProactiveContextCache(_settings(tmp_path))

    await cache.append_turn(
        caller_id="caller-1",
        conversation_id="conv-1",
        user_text="我明天要去办手续",
        assistant_text="那你记得早点睡",
    )

    by_caller = await cache.recent(caller_id="caller-1", conversation_id=None)
    by_conversation = await cache.recent(caller_id=None, conversation_id="conv-1")

    assert [item.text for item in by_caller] == ["我明天要去办手续", "那你记得早点睡"]
    assert [item.text for item in by_conversation] == [
        "我明天要去办手续",
        "那你记得早点睡",
    ]
    assert "用户: 我明天要去办手续" in render_proactive_context_cache(by_caller)


@pytest.mark.asyncio
async def test_proactive_context_cache_trims_per_key(tmp_path) -> None:
    cache = ProactiveContextCache(
        _settings(tmp_path, proactive_context_cache_max_items=3)
    )

    for i in range(5):
        await cache.append_turn(
            caller_id="caller-1",
            conversation_id="conv-1",
            user_text=f"消息 {i}",
            assistant_text="",
        )

    items = await cache.recent(caller_id="caller-1", conversation_id=None, limit=10)

    assert [item.text for item in items] == ["消息 2", "消息 3", "消息 4"]
