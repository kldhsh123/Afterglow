"""ingestion CLI 辅助逻辑测试。"""

from __future__ import annotations

from xuwen.config import Settings
from xuwen.ingestion.cli import (
    _auto_build_vector_indices,
    _rebuild_proactive_profile_from_store,
)
from xuwen.persona.proactive_profile import (
    PROACTIVE_PROFILE_FILENAME,
    load_proactive_profile,
)


class _FakeStore:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.limit: int | None = None

    async def list_dialogue_windows(self, limit: int = 10_000) -> list[dict[str, object]]:
        self.limit = limit
        return self.rows


async def test_rebuild_proactive_profile_from_store_saves_combined_rows(tmp_path) -> None:
    settings = Settings(
        persona_data_dir=tmp_path / "persona",
        proactive_profile_window_limit=50,
        proactive_learning_min_gap_minutes=120,
        self_name="我",
        self_uid="u-self",
        friend_name="TA",
        friend_uid="u-friend",
    )
    rows = [
        {
            "id": "w1",
            "session_id": "s1",
            "start_time_ms": 1_700_000_000_000,
            "end_time_ms": 1_700_000_060_000,
            "text": "我: 晚安\nTA: 晚安",
            "deleted": False,
        },
        {
            "id": "w2",
            "session_id": "s2",
            "start_time_ms": 1_700_008_700_000,
            "end_time_ms": 1_700_008_760_000,
            "text": "TA: 起床\n我: 醒了",
            "deleted": False,
        },
    ]
    store = _FakeStore(rows)

    summary = await _rebuild_proactive_profile_from_store(settings, store)

    assert store.limit == 50
    assert "共识别 1 次" in summary
    saved = load_proactive_profile(
        settings.persona_data_dir / PROACTIVE_PROFILE_FILENAME
    )
    assert saved is not None
    assert saved.sample_size == 1


async def test_auto_build_vector_indices_runs_when_enabled(monkeypatch) -> None:
    calls: list[str] = []

    class FakeStore:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def connect(self) -> None:
            calls.append("connect")

        def ensure_tables(self) -> None:
            calls.append("ensure_tables")

        async def ensure_vector_indices(self) -> dict[str, str]:
            calls.append("ensure_vector_indices")
            return {"history_images": "built IVF_FLAT (100 rows)"}

    monkeypatch.setattr("xuwen.ingestion.cli.MemoryStore", FakeStore)

    await _auto_build_vector_indices(Settings(lance_index_min_rows=1))

    assert calls == ["connect", "ensure_tables", "ensure_vector_indices"]


async def test_auto_build_vector_indices_skips_when_disabled(monkeypatch) -> None:
    class BombStore:
        def __init__(self, _settings: Settings) -> None:
            raise AssertionError("MemoryStore should not be constructed")

    monkeypatch.setattr("xuwen.ingestion.cli.MemoryStore", BombStore)

    await _auto_build_vector_indices(Settings(lance_index_min_rows=0))
