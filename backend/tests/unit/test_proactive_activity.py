"""主动聊天用户活动记录测试。"""

from __future__ import annotations

from xuwen.chat_api.proactive_activity import record_proactive_user_activity


class _FakeProactive:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def record_user_activity(self, scope_id: str) -> None:
        self.calls.append(scope_id)


class _FakeState:
    def __init__(self) -> None:
        self.proactive = _FakeProactive()


async def test_record_proactive_user_activity_records_all_unique_scope_ids() -> None:
    state = _FakeState()

    await record_proactive_user_activity(
        state,
        "conversation-1",
        "caller-1",
        "conversation-1",
        "",
        None,
        " caller-1 ",
    )

    assert state.proactive.calls == ["conversation-1", "caller-1"]
