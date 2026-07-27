"""主动聊天运行时决策测试。"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from xuwen.companion.life import LifeSnapshot
from xuwen.companion.proactive import ProactiveEngine
from xuwen.config import Settings
from xuwen.persona.proactive_profile import ProactiveProfile, save_proactive_profile


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "persona_data_dir": tmp_path / "persona",
        "proactive_enabled": True,
        "proactive_quiet_hours": "",
        "proactive_min_idle_minutes": 60,
        "proactive_score_threshold": 0.55,
        "proactive_max_per_day": 1,
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
    }
    values.update(overrides)
    return Settings(**values)


def _life(availability: str = "available") -> LifeSnapshot:
    return LifeSnapshot(
        date="2026-07-04",
        time_slot="晚上",
        current_activity="刚看完消息",
        recent_meal="喝了水",
        mood="普通",
        topic_seed="问问今天怎么样",
        availability=availability,
        next_update_at="2026-07-04 23:00",
        reply_delay_seconds=0,
        reply_delay_reason="",
    )


def _strong_profile() -> ProactiveProfile:
    return ProactiveProfile(
        sample_size=12,
        positive_samples=12,
        total_sessions=20,
        hour_weights=[1.0] * 24,
        weekday_weights=[1.0] * 7,
        idle_gap_weights={
            "short": 0.1,
            "one_to_three_hours": 0.8,
            "three_to_eight_hours": 1.0,
            "overnight": 0.9,
            "multi_day": 0.7,
        },
        previous_last_speaker_weights={
            "friend": 0.8,
            "self": 1.0,
            "other": 0.4,
            "unknown": 0.5,
        },
        opening_type_weights={"life_check": 1.0},
        summary="晚上常主动开聊",
    )


def _evening_profile() -> ProactiveProfile:
    hours = [0.0] * 24
    hours[22] = 1.0
    return ProactiveProfile(
        sample_size=12,
        positive_samples=12,
        total_sessions=20,
        hour_weights=hours,
        weekday_weights=[1.0] * 7,
        idle_gap_weights={"overnight": 1.0},
        previous_last_speaker_weights={"self": 1.0},
        opening_type_weights={"life_check": 1.0},
        summary="晚上 22 点常主动开聊",
    )


@pytest.mark.asyncio
async def test_proactive_engine_sends_when_profile_and_gates_match(tmp_path):
    settings = _settings(tmp_path)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)

    decision = await engine.decide(
        conversation_id="conv-1",
        life=_life(),
        now=datetime(2026, 7, 4, 22, 0, tzinfo=UTC),
    )

    assert decision.should_send is True
    assert decision.score >= settings.proactive_score_threshold
    assert decision.skip_reasons == []
    assert "画像摘要" in decision.private_context
    assert "不要解释调度" in decision.topic_hint


@pytest.mark.asyncio
async def test_proactive_engine_respects_disabled_gate(tmp_path):
    settings = _settings(tmp_path, proactive_enabled=False)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)

    decision = await engine.decide(
        conversation_id="conv-1",
        life=_life(),
        now=datetime(2026, 7, 4, 22, 0, tzinfo=UTC),
    )

    assert decision.should_send is False
    assert "disabled" in decision.skip_reasons


@pytest.mark.asyncio
async def test_proactive_engine_skips_when_life_is_busy(tmp_path):
    settings = _settings(tmp_path)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)

    decision = await engine.decide(
        conversation_id="conv-1",
        life=_life("busy"),
        now=datetime(2026, 7, 4, 22, 0, tzinfo=UTC),
    )

    assert decision.should_send is False
    assert "life_busy" in decision.skip_reasons


@pytest.mark.asyncio
async def test_proactive_poll_schedules_then_becomes_ready(tmp_path):
    settings = _settings(tmp_path, proactive_check_interval_seconds=900)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)

    first = await engine.poll(conversation_id="conv-1", life=_life(), now=now)
    assert first.state == "scheduled"
    assert first.should_send is False
    assert first.next_poll_at_ms == int(now.timestamp() * 1000) + 900_000

    due = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=datetime(2026, 7, 4, 22, 15, tzinfo=UTC),
    )
    assert due.state == "ready"
    assert due.should_send is True
    assert "画像摘要" in due.private_context


@pytest.mark.asyncio
async def test_proactive_poll_debug_force_due(tmp_path):
    settings = _settings(tmp_path, proactive_check_interval_seconds=900)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)
    forced_at_ms = int(datetime(2026, 7, 4, 22, 1, tzinfo=UTC).timestamp() * 1000)

    first = await engine.poll(conversation_id="conv-1", life=_life(), now=now)
    forced = await engine.debug_force_candidate_due("conv-1", at_ms=forced_at_ms)
    due = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=datetime(2026, 7, 4, 22, 1, tzinfo=UTC),
    )

    assert first.state == "scheduled"
    assert forced["forced"] is True
    assert forced["previous_scheduled_for_ms"] == first.next_poll_at_ms
    assert forced["scheduled_for_ms"] == forced_at_ms
    assert due.state == "ready"
    assert due.should_send is True


@pytest.mark.asyncio
async def test_proactive_poll_cancels_when_project_recorded_user_activity(tmp_path):
    settings = _settings(tmp_path, proactive_check_interval_seconds=900)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)

    first = await engine.poll(conversation_id="conv-1", life=_life(), now=now)
    await engine.record_user_activity(
        "conv-1",
        at_ms=first.candidate_created_at_ms + 1,
    )
    cancelled = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=datetime(2026, 7, 4, 22, 1, tzinfo=UTC),
    )

    assert cancelled.state == "cancelled"
    assert cancelled.cancelled_by_user_activity is True
    assert cancelled.next_poll_at_ms > first.next_poll_at_ms


@pytest.mark.asyncio
async def test_proactive_finish_candidate_rechecks_user_activity(tmp_path):
    settings = _settings(tmp_path, proactive_check_interval_seconds=900)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)

    first = await engine.poll(conversation_id="conv-1", life=_life(), now=now)
    await engine.record_user_activity(
        "conv-1",
        at_ms=first.candidate_created_at_ms + 1,
    )

    finished = await engine.finish_candidate_if_still_valid(
        "conv-1",
        candidate_created_at_ms=first.candidate_created_at_ms,
        scheduled_for_ms=first.scheduled_for_ms,
    )

    assert finished is False


@pytest.mark.asyncio
async def test_proactive_finish_candidate_accepts_unchanged_candidate(tmp_path):
    settings = _settings(tmp_path, proactive_check_interval_seconds=900)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)

    first = await engine.poll(conversation_id="conv-1", life=_life(), now=now)

    finished = await engine.finish_candidate_if_still_valid(
        "conv-1",
        candidate_created_at_ms=first.candidate_created_at_ms,
        scheduled_for_ms=first.scheduled_for_ms,
    )

    assert finished is True


@pytest.mark.asyncio
async def test_proactive_unanswered_gate_uses_recorded_user_activity(tmp_path):
    settings = _settings(tmp_path)
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    engine._audit.append(
        {
            "conversation_id": "conv-1",
            "status": "sent",
            "ts_ms": 1000,
        }
    )
    await engine.record_user_activity("conv-1", at_ms=1001)

    assert engine._has_unanswered_proactive("conv-1", recent_live=[]) is False


@pytest.mark.asyncio
async def test_proactive_poll_uses_external_user_activity_when_first_scheduling(tmp_path):
    settings = _settings(
        tmp_path,
        proactive_check_interval_seconds=60,
        proactive_min_idle_minutes=180,
    )
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)
    now_ms = int(now.timestamp() * 1000)
    recent_user_ms = now_ms - 5 * 60_000

    result = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=now,
        last_user_message_at_ms=recent_user_ms,
    )

    assert result.state == "scheduled"
    assert "not_idle_enough" in result.skip_reasons
    assert result.next_poll_at_ms >= now_ms + 175 * 60_000


@pytest.mark.asyncio
async def test_proactive_poll_uses_external_user_activity_after_cancelling_candidate(tmp_path):
    settings = _settings(
        tmp_path,
        proactive_check_interval_seconds=60,
        proactive_min_idle_minutes=180,
    )
    save_proactive_profile(
        _strong_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)
    first_now = datetime(2026, 7, 4, 22, 0, tzinfo=UTC)
    first = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=first_now,
    )
    current = datetime(2026, 7, 4, 22, 10, tzinfo=UTC)
    current_ms = int(current.timestamp() * 1000)
    recent_user_ms = current_ms - 60_000

    cancelled = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=current,
        last_user_message_at_ms=recent_user_ms,
    )

    assert recent_user_ms > first.candidate_created_at_ms
    assert cancelled.state == "cancelled"
    assert cancelled.cancelled_by_user_activity is True
    assert cancelled.next_poll_at_ms >= current_ms + 179 * 60_000


@pytest.mark.asyncio
async def test_proactive_poll_uses_profile_hour_for_next_poll(tmp_path):
    settings = _settings(tmp_path, proactive_check_interval_seconds=3600)
    save_proactive_profile(
        _evening_profile(),
        settings.persona_data_dir / "proactive_profile.json",
    )
    engine = ProactiveEngine(settings)

    result = await engine.poll(
        conversation_id="conv-1",
        life=_life(),
        now=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
    )

    scheduled = datetime.fromtimestamp(result.next_poll_at_ms / 1000, tz=UTC)
    assert result.state == "scheduled"
    assert scheduled.hour == 22


@pytest.mark.asyncio
async def test_proactive_engine_reloads_profile_when_file_changes(tmp_path):
    settings = _settings(tmp_path)
    profile_path = settings.persona_data_dir / "proactive_profile.json"
    old_profile = _strong_profile()
    old_profile.summary = "旧画像"
    new_profile = _strong_profile()
    new_profile.summary = "新画像"
    save_proactive_profile(old_profile, profile_path)
    engine = ProactiveEngine(settings)

    snapshot = engine.snapshot()
    assert isinstance(snapshot["profile"], dict)
    assert snapshot["profile"]["summary"] == "旧画像"

    old_mtime_ns = profile_path.stat().st_mtime_ns
    save_proactive_profile(new_profile, profile_path)
    os.utime(
        profile_path,
        ns=(old_mtime_ns + 1_000_000, old_mtime_ns + 1_000_000),
    )

    snapshot = engine.snapshot()
    assert isinstance(snapshot["profile"], dict)
    assert snapshot["profile"]["summary"] == "新画像"
