"""Web UI 导入任务辅助逻辑测试。"""

from __future__ import annotations

from xuwen.config import Settings
from xuwen.persona.proactive_profile import (
    PROACTIVE_PROFILE_FILENAME,
    load_proactive_profile,
)
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
    assert "共识别 1 次" in summary
    saved = load_proactive_profile(
        settings.persona_data_dir / PROACTIVE_PROFILE_FILENAME
    )
    assert saved is not None
    assert saved.sample_size == 1
