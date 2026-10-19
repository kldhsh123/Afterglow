"""正文发送不等待定时任务解析，但任务仍须在收尾块中交付。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from xuwen.chat_api.routes.chat import _pseudo_stream_chunks
from xuwen.chat_api.schemas import PolicyHint, ScheduleTask


@pytest.mark.parametrize("tasks", [[], [ScheduleTask(
    id="t-test", trigger_at="2026-10-20T07:00:00+08:00", message="该起床了"
)]])
async def test_pseudo_stream_delivers_body_before_loading_tasks(tasks):
    loader = AsyncMock(return_value=tasks)
    stream = _pseudo_stream_chunks(
        model_name="test", trace_id="trace", assistant_text="明早叫你",
        policy=PolicyHint(should_reply=True, reply_mode="normal", user_state="normal", risk_level="low"),
        schedule_loader=loader,
    )
    await anext(stream)  # role/policy
    body = json.loads((await anext(stream)).decode().removeprefix("data: "))
    assert body["choices"][0]["delta"]["content"] == "明早叫你"
    loader.assert_not_awaited()
    final = json.loads((await anext(stream)).decode().removeprefix("data: "))
    loader.assert_awaited_once()
    assert final["choices"][0]["finish_reason"] == "stop"
    if tasks:
        assert final["schedule_tasks"][0]["id"] == "t-test"
    else:
        assert "schedule_tasks" not in final
    assert await anext(stream) == b"data: [DONE]\n\n"
