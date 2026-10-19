"""Web UI 导入任务辅助逻辑测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xuwen.config import Settings
from xuwen.persona.proactive_profile import (
    PROACTIVE_PROFILE_FILENAME,
    load_proactive_profile,
)
from xuwen.web_ui import import_tasks
from xuwen.web_ui.import_tasks import _rebuild_proactive_profile_from_store


class _FakeStore:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.limit: int | None = None

    async def list_dialogue_windows(self, limit: int = 10_000) -> list[dict[str, object]]:
        self.limit = limit
        return self.rows


async def test_rebuild_proactive_profile_from_store_uses_all_imported_windows(tmp_path) -> None:
    settings = Settings(
        persona_data_dir=tmp_path / "persona",
        proactive_profile_window_limit=80,
        proactive_learning_min_gap_minutes=120,
        self_name="我",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
    )
    store = _FakeStore(
        [
            {
                "session_id": "qq-session",
                "text": "我: 晚安\nTA: 晚安",
                "start_time_ms": 1_700_000_000_000,
                "end_time_ms": 1_700_000_060_000,
                "deleted": False,
            },
            {
                "session_id": "wechat-session",
                "text": "TA: 起床\n我: 醒了",
                "start_time_ms": 1_700_008_700_000,
                "end_time_ms": 1_700_008_760_000,
                "deleted": False,
            },
        ]
    )

    summary = await _rebuild_proactive_profile_from_store(settings, store)

    assert store.limit == 80
    assert "Identified 1 proactive openings by the other person" in summary
    saved = load_proactive_profile(
        settings.persona_data_dir / PROACTIVE_PROFILE_FILENAME
    )
    assert saved is not None
    assert saved.sample_size == 1


@pytest.mark.parametrize("index_result", ["built (1000 rows)", "error: training failed", "raise"])
async def test_web_import_builds_indexes_once_after_all_files(tmp_path, monkeypatch, index_result):
    manager = import_tasks.ImportTaskManager()
    task = manager.create([tmp_path / "a.json", tmp_path / "b.json"], ["a", "b"])
    events = []

    async def import_one(**kwargs):
        events.append(kwargs["display_name"])
        return SimpleNamespace(friend_chunks=1, window_chunks=1, response_pairs=1)

    async def build_indexes():
        events.append("index")
        assert task.status != "done"
        if index_result == "raise":
            raise RuntimeError("index unavailable")
        return {"friend_messages": index_result}

    store = SimpleNamespace(connect=AsyncMock(), ensure_vector_indices=AsyncMock(side_effect=build_indexes))
    monkeypatch.setattr(import_tasks, "get_manager", lambda: manager)
    monkeypatch.setattr(import_tasks, "_process_one_file", import_one)
    monkeypatch.setattr(import_tasks, "_run_persona_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(import_tasks, "_rebuild_proactive_profile_from_store", AsyncMock(return_value="ok"))
    monkeypatch.setattr("xuwen.memory.store.MemoryStore", lambda settings: store)

    await import_tasks.run_import_task(task.task_id, Settings(persona_data_dir=tmp_path))

    assert events == ["a", "b", "index"]
    assert task.status == "done"
    assert task.progress == 1.0
    assert task.report is not None
    if index_result.startswith("built"):
        assert task.error == ""
        assert task.report["vector_indices"]["friend_messages"] == index_result
    else:
        assert task.error


async def test_web_import_respects_disabled_auto_indexing(tmp_path, monkeypatch):
    manager = import_tasks.ImportTaskManager()
    task = manager.create([Path("a.json")], ["a"])
    store_factory = AsyncMock()
    monkeypatch.setattr(import_tasks, "get_manager", lambda: manager)
    monkeypatch.setattr(import_tasks, "_process_one_file", AsyncMock(return_value={}))
    monkeypatch.setattr(import_tasks, "_run_persona_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(import_tasks, "_rebuild_proactive_profile_from_store", AsyncMock(return_value="ok"))
    monkeypatch.setattr("xuwen.memory.store.MemoryStore", store_factory)
    await import_tasks.run_import_task(task.task_id, Settings(lance_index_min_rows=0))
    assert task.status == "done"
    store_factory.assert_not_called()
