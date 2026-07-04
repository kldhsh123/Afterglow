"""主动聊天用户活动记录工具。"""

from __future__ import annotations

from typing import Any


async def record_proactive_user_activity(
    state: Any,
    *scope_ids: str | None,
) -> None:
    """按所有可用作用域记录用户活动，避免漏取消 pending 主动候选。"""
    seen: set[str] = set()
    for raw in scope_ids:
        scope_id = (raw or "").strip()
        if not scope_id or scope_id in seen:
            continue
        seen.add(scope_id)
        await state.proactive.record_user_activity(scope_id)
