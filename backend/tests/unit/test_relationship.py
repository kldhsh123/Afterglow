"""关系长期记忆持久化边界测试。"""

from typing import Any

from xuwen.companion.relationship import RelationshipMemoryManager
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
