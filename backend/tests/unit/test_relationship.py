"""关系长期记忆持久化边界测试。"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from xuwen.companion.relationship import (
    RelationshipMemoryEntry,
    RelationshipMemoryManager,
)
from xuwen.config import Settings


def _manager(tmp_path: Any) -> RelationshipMemoryManager:
    settings = Settings(
        persona_data_dir=tmp_path,
        self_name="Me",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
    )
    return RelationshipMemoryManager(
        settings,
        store=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
    )


def test_load_markdown_ignores_legacy_unverified_notes(tmp_path: Any) -> None:
    manager = _manager(tmp_path)
    manager.path.write_text(
        "# 关系记忆\n\n"
        "- [2026-01-01] (note, 1) 用户说：普通寒暄\n"
        "- [2026-01-02] (boundary, 2) 用户不希望讨论某个话题\n",
        encoding="utf-8",
    )

    context = manager.load_markdown()

    assert "普通寒暄" not in context
    assert "用户不希望讨论某个话题" in context


@pytest.mark.asyncio
async def test_remember_turn_preserves_filtered_legacy_notes(tmp_path: Any) -> None:
    manager = _manager(tmp_path)
    manager.store = MagicMock()
    manager.store.upsert_relationship_memories = AsyncMock()
    manager.embedder = MagicMock()
    manager.embedder.embed_texts = AsyncMock(return_value=[[0.1, 0.2]])
    manager.path.write_text(
        "# 关系记忆\n\n"
        "- [2026-01-01] (note, 1) 用户说：旧版普通记录\n",
        encoding="utf-8",
    )

    await manager.remember_turn(
        conversation_id="conv-test",
        entry=RelationshipMemoryEntry(
            text="用户明确喜欢某种甜点",
            kind="preference",
            importance=2,
        ),
    )

    raw = manager.path.read_text(encoding="utf-8")
    assert "(note, 1) 用户说：旧版普通记录" in raw
    assert "(preference, 2) 用户明确喜欢某种甜点" in raw
    assert "旧版普通记录" not in manager.load_markdown()
