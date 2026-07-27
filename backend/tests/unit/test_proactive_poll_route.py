"""主动聊天 poll 路由的发送前复检测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from xuwen.chat_api.routes import companion as companion_routes
from xuwen.chat_api.routes.companion import ProactivePollRequest, ProactiveResponse
from xuwen.companion.proactive import ProactiveEngine
from xuwen.config import Settings
from xuwen.persona.proactive_profile import ProactiveProfile, save_proactive_profile

from .test_proactive_engine import _life


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "persona_data_dir": tmp_path / "persona",
        "proactive_enabled": True,
        "proactive_quiet_hours": "",
        "proactive_min_idle_minutes": 0,
        "proactive_score_threshold": 0.1,
        "proactive_check_interval_seconds": 60,
        "proactive_max_per_day": 10,
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
    }
    values.update(overrides)
    return Settings(**values)


def _profile() -> ProactiveProfile:
    return ProactiveProfile(
        sample_size=12,
        positive_samples=12,
        total_sessions=20,
        hour_weights=[1.0] * 24,
        weekday_weights=[1.0] * 7,
        idle_gap_weights={"short": 1.0},
        previous_last_speaker_weights={"unknown": 1.0},
        opening_type_weights={"life_check": 1.0},
        summary="随时可测",
    )


@pytest.mark.asyncio
async def test_proactive_poll_rechecks_activity_before_auto_send(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    save_proactive_profile(_profile(), settings.persona_data_dir / "proactive_profile.json")
    engine = ProactiveEngine(settings)
    await engine.poll(conversation_id="conv-1", life=_life())
    await engine.debug_force_candidate_due(
        "conv-1",
        at_ms=int(time.time() * 1000) - 1_000,
    )

    async def fake_generate(req, *, state, trace_id: str, persist: bool = True):
        assert persist is False
        candidate = state.proactive._candidates["conv-1"]
        await state.proactive.record_user_activity(
            "conv-1",
            at_ms=candidate.created_at_ms + 1,
        )
        return ProactiveResponse(
            message="这条不该发",
            life={},
            trace_id=trace_id,
        )

    async def fail_persist(**_kwargs):
        raise AssertionError("候选失效后不应写入主动消息")

    monkeypatch.setattr(companion_routes, "_generate_proactive_response", fake_generate)
    monkeypatch.setattr(companion_routes, "_persist_proactive_response", fail_persist)
    state = SimpleNamespace(
        life=SimpleNamespace(snapshot=_life),
        proactive=engine,
    )
    request = SimpleNamespace(state=SimpleNamespace(request_id="trace-test"))

    response = await companion_routes.proactive_poll(
        ProactivePollRequest(conversation_id="conv-1"),
        request=request,
        state=state,
    )

    assert response.state == "cancelled"
    assert response.should_send is False
    assert response.cancelled_by_user_activity is True
    assert response.proactive is None
