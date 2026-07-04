"""运行时主动聊天决策：画像评分、防打扰门控与审计日志。"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from xuwen.companion.life import LifeSnapshot
from xuwen.config import Settings
from xuwen.core.time import local_now
from xuwen.memory.store import MemoryStore
from xuwen.persona.proactive_profile import (
    PROACTIVE_PROFILE_FILENAME,
    ProactiveProfile,
    compute_proactive_profile_from_window_rows,
    idle_gap_bucket,
    load_proactive_profile,
    save_proactive_profile,
)


@dataclass(slots=True)
class ProactiveDecision:
    """一次主动聊天候选时机的解释性决策。"""

    should_send: bool
    score: float
    threshold: float
    reason: str
    skip_reasons: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    private_context: str = ""
    topic_hint: str = ""
    profile_summary: str = ""
    next_check_seconds: int = 0


@dataclass(slots=True)
class ProactivePollResult:
    """轮询式主动聊天状态机结果。"""

    state: str
    should_send: bool
    score: float
    threshold: float
    reason: str
    skip_reasons: list[str]
    features: dict[str, float]
    private_context: str
    topic_hint: str
    profile_summary: str
    next_poll_at_ms: int
    scheduled_for_ms: int
    candidate_created_at_ms: int = 0
    cancelled_by_user_activity: bool = False


@dataclass(slots=True)
class _PollCandidate:
    conversation_id: str
    created_at_ms: int
    scheduled_for_ms: int
    reason: str


class ProactiveEngine:
    """主动聊天学习画像的运行时决策器。

    它不负责投递消息，只回答“现在是否适合主动找用户”以及应该给
    `/v1/companion/proactive` 的内部上下文。这样外部 bot、前端轮询或后续
    后台调度器都能复用同一套门控逻辑。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        store: MemoryStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.profile_path = settings.persona_data_dir / PROACTIVE_PROFILE_FILENAME
        self.audit_path = settings.persona_data_dir / "proactive_audit.jsonl"
        self.state_path = settings.persona_data_dir / "proactive_poll_state.json"
        self._profile: ProactiveProfile | None = None
        self._profile_loaded = False
        self._audit_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._audit: deque[dict[str, Any]] = deque(maxlen=settings.proactive_audit_max_records)
        self._candidates: dict[str, _PollCandidate] = {}
        self._user_activity_ms: dict[str, int] = {}
        self._load_audit()
        self._load_poll_state()

    async def decide(
        self,
        *,
        conversation_id: str | None,
        life: LifeSnapshot | None = None,
        now: datetime | None = None,
        reason: str = "learned_rhythm",
    ) -> ProactiveDecision:
        current = now or local_now(self.settings.app_timezone)
        profile = await self._profile_for_decision()
        recent_live = await self._recent_live(conversation_id)
        last_live = recent_live[0] if recent_live else None
        cid = conversation_id or ""
        in_memory_user_at = self._user_activity_ms.get(cid, 0)
        idle_minutes = _idle_minutes(last_live, current)
        last_role = str(last_live.get("role") or "unknown") if last_live else "unknown"
        last_live_at = int(last_live.get("created_at_ms") or 0) if last_live else 0
        if in_memory_user_at > last_live_at:
            idle_minutes = max(0.0, (int(current.timestamp() * 1000) - in_memory_user_at) / 60_000)
            last_role = "user"

        skip_reasons = self._gates(
            conversation_id=conversation_id,
            life=life,
            now=current,
            recent_live=recent_live,
            idle_minutes=idle_minutes,
        )
        features = self._features(
            profile,
            now=current,
            idle_minutes=idle_minutes,
            last_role=last_role,
            life=life,
        )
        score = _weighted_score(features)
        threshold = max(0.0, min(1.0, self.settings.proactive_score_threshold))
        should_send = not skip_reasons and score >= threshold
        reason_text = "matched_learned_rhythm" if should_send else _decision_reason(skip_reasons, score, threshold)
        private_context = _private_context(
            profile,
            now=current,
            idle_minutes=idle_minutes,
            last_role=last_role,
            life=life,
            reason=reason,
            score=score,
            features=features,
        )
        topic_hint = _topic_hint(profile, life=life)
        return ProactiveDecision(
            should_send=should_send,
            score=round(score, 3),
            threshold=threshold,
            reason=reason_text,
            skip_reasons=skip_reasons,
            features={k: round(v, 3) for k, v in features.items()},
            private_context=private_context,
            topic_hint=topic_hint,
            profile_summary=profile.summary,
            next_check_seconds=max(60, int(self.settings.proactive_check_interval_seconds)),
        )

    async def poll(
        self,
        *,
        conversation_id: str | None,
        life: LifeSnapshot | None = None,
        now: datetime | None = None,
        reason: str = "learned_rhythm",
        last_user_message_at_ms: int | None = None,
    ) -> ProactivePollResult:
        """轮询主动聊天状态机。

        第一次调用通常只创建 pending candidate 并返回 next_poll_at_ms。
        到点后再次调用；如果 candidate 创建后用户没有先发消息，且当前门控通过，
        返回 state=ready。路由层可在 auto_send=true 时复用 /proactive 生成内容。
        """
        current = now or local_now(self.settings.app_timezone)
        current_ms = int(current.timestamp() * 1000)
        cid = conversation_id or ""
        async with self._state_lock:
            candidate = self._candidates.get(cid)
            last_user_at = max(
                self._user_activity_ms.get(cid, 0),
                int(last_user_message_at_ms or 0),
            )
            if candidate is not None and _user_activity_after_candidate(
                last_user_at,
                candidate,
            ):
                self._candidates.pop(cid, None)
                self._save_poll_state()
                next_at = self._next_schedule_at_ms(current, idle_minutes=None)
                new_candidate = _PollCandidate(
                    conversation_id=cid,
                    created_at_ms=current_ms,
                    scheduled_for_ms=next_at,
                    reason=reason,
                )
                self._candidates[cid] = new_candidate
                self._save_poll_state()
                return self._poll_scheduled_result(
                    new_candidate,
                    state="cancelled",
                    reason="cancelled_by_user_activity",
                    cancelled=True,
                )

            if candidate is not None and current_ms < candidate.scheduled_for_ms:
                return self._poll_scheduled_result(candidate, state="scheduled")

        decision = await self.decide(
            conversation_id=conversation_id,
            life=life,
            now=current,
            reason=reason,
        )

        async with self._state_lock:
            candidate = self._candidates.get(cid)
            if candidate is None:
                if "disabled" in decision.skip_reasons:
                    return ProactivePollResult(
                        state="skipped",
                        should_send=False,
                        score=decision.score,
                        threshold=decision.threshold,
                        reason=decision.reason,
                        skip_reasons=decision.skip_reasons,
                        features=decision.features,
                        private_context=decision.private_context,
                        topic_hint=decision.topic_hint,
                        profile_summary=decision.profile_summary,
                        next_poll_at_ms=int(
                            (
                                current.timestamp()
                                + max(60, int(self.settings.proactive_check_interval_seconds))
                            )
                            * 1000
                        ),
                        scheduled_for_ms=0,
                    )
                candidate = _PollCandidate(
                    conversation_id=cid,
                    created_at_ms=current_ms,
                    scheduled_for_ms=self._next_schedule_at_ms(
                        current,
                        idle_minutes=_idle_minutes_from_decision(decision),
                    ),
                    reason=reason,
                )
                self._candidates[cid] = candidate
                self._save_poll_state()
                return self._poll_scheduled_result(candidate, decision=decision)

            if not decision.should_send:
                self._candidates.pop(cid, None)
                next_candidate = _PollCandidate(
                    conversation_id=cid,
                    created_at_ms=current_ms,
                    scheduled_for_ms=self._next_schedule_at_ms(current, idle_minutes=None),
                    reason=reason,
                )
                self._candidates[cid] = next_candidate
                self._save_poll_state()
                return ProactivePollResult(
                    state="skipped",
                    should_send=False,
                    score=decision.score,
                    threshold=decision.threshold,
                    reason=decision.reason,
                    skip_reasons=decision.skip_reasons,
                    features=decision.features,
                    private_context=decision.private_context,
                    topic_hint=decision.topic_hint,
                    profile_summary=decision.profile_summary,
                    next_poll_at_ms=next_candidate.scheduled_for_ms,
                    scheduled_for_ms=candidate.scheduled_for_ms,
                    candidate_created_at_ms=candidate.created_at_ms,
                )

            return ProactivePollResult(
                state="ready",
                should_send=True,
                score=decision.score,
                threshold=decision.threshold,
                reason=decision.reason,
                skip_reasons=[],
                features=decision.features,
                private_context=decision.private_context,
                topic_hint=decision.topic_hint,
                profile_summary=decision.profile_summary,
                next_poll_at_ms=current_ms,
                scheduled_for_ms=candidate.scheduled_for_ms,
                candidate_created_at_ms=candidate.created_at_ms,
            )

    async def record_user_activity(
        self,
        conversation_id: str | None,
        *,
        at_ms: int | None = None,
    ) -> None:
        """记录项目内部观察到的用户发言时间。

        普通 chat/responses 请求会调用这里；poll 因此能自己判断“候选时间之前
        用户是否已经主动开聊”，调用方不需要再传 last_user_message_at_ms。
        """
        cid = conversation_id or ""
        if not cid:
            return
        ts = at_ms if at_ms is not None else int(local_now(self.settings.app_timezone).timestamp() * 1000)
        if ts <= 0:
            return
        async with self._state_lock:
            self._user_activity_ms[cid] = max(ts, self._user_activity_ms.get(cid, 0))
            self._save_poll_state()

    async def finish_candidate(self, conversation_id: str | None) -> None:
        """主动消息已经发送/沉默后，清掉当前 pending candidate。"""
        cid = conversation_id or ""
        async with self._state_lock:
            self._candidates.pop(cid, None)
            self._save_poll_state()

    async def debug_force_candidate_due(
        self,
        conversation_id: str | None,
        *,
        at_ms: int | None = None,
    ) -> dict[str, Any]:
        """调试用：把已有 pending candidate 的 scheduled_for_ms 改成已到期。

        只应从 /debug/* 端点暴露，用于本地 smoke test 验证完整 poll 流程；
        正常业务路径仍必须等待 poll 返回的 next_poll_at_ms。
        """
        cid = conversation_id or ""
        if not cid:
            return {"forced": False, "reason": "missing_conversation_id"}
        current_ms = at_ms if at_ms is not None else int(
            local_now(self.settings.app_timezone).timestamp() * 1000
        )
        if current_ms <= 0:
            return {"forced": False, "reason": "invalid_at_ms"}
        async with self._state_lock:
            candidate = self._candidates.get(cid)
            if candidate is None:
                return {"forced": False, "reason": "no_pending_candidate"}
            updated = _PollCandidate(
                conversation_id=candidate.conversation_id,
                created_at_ms=candidate.created_at_ms,
                scheduled_for_ms=current_ms,
                reason=candidate.reason,
            )
            self._candidates[cid] = updated
            self._save_poll_state()
            return {
                "forced": True,
                "conversation_id": cid,
                "previous_scheduled_for_ms": candidate.scheduled_for_ms,
                "scheduled_for_ms": updated.scheduled_for_ms,
                "candidate_created_at_ms": updated.created_at_ms,
            }

    async def record_decision(
        self,
        *,
        conversation_id: str | None,
        decision: ProactiveDecision,
        status: str,
        message_preview: str = "",
    ) -> None:
        record = {
            "ts_ms": int(local_now(self.settings.app_timezone).timestamp() * 1000),
            "conversation_id": conversation_id or "",
            "status": status,
            "should_send": decision.should_send,
            "score": decision.score,
            "threshold": decision.threshold,
            "reason": decision.reason,
            "skip_reasons": decision.skip_reasons,
            "features": decision.features,
            "message_preview": _short(message_preview, 120),
        }
        async with self._audit_lock:
            self._audit.append(record)
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._append_audit_line, record)

    def snapshot(self) -> dict[str, Any]:
        profile = self._load_profile_sync()
        return {
            "enabled": self.settings.proactive_enabled,
            "profile_path": str(self.profile_path),
            "poll_state_path": str(self.state_path),
            "profile_exists": self.profile_path.exists(),
            "profile": _profile_to_dict(profile),
            "pending_candidates": {
                cid: asdict(candidate) for cid, candidate in self._candidates.items()
            },
            "user_activity_ms": dict(self._user_activity_ms),
            "recent_audit": list(self._audit)[-30:],
        }

    async def _profile_for_decision(self) -> ProactiveProfile:
        profile = self._load_profile_sync()
        if profile is not None and profile.sample_size > 0:
            return profile
        if self.store is None:
            return profile or ProactiveProfile()
        try:
            rows = await self.store.list_dialogue_windows(
                limit=self.settings.proactive_profile_window_limit
            )
        except Exception:
            return profile or ProactiveProfile()
        rebuilt = compute_proactive_profile_from_window_rows(
            rows,
            friend_name=self.settings.friend_name or "TA",
            self_name=self.settings.self_name or "我",
            min_gap_minutes=self.settings.proactive_learning_min_gap_minutes,
        )
        if rebuilt.sample_size > 0:
            save_proactive_profile(rebuilt, self.profile_path)
            self._profile = rebuilt
            self._profile_loaded = True
            return rebuilt
        return profile or rebuilt

    def _load_profile_sync(self) -> ProactiveProfile | None:
        if self._profile_loaded:
            return self._profile
        self._profile = load_proactive_profile(self.profile_path)
        self._profile_loaded = True
        return self._profile

    async def _recent_live(self, conversation_id: str | None) -> list[dict[str, Any]]:
        if not conversation_id or self.store is None:
            return []
        try:
            return await self.store.recent_live(conversation_id, limit=30)
        except Exception:
            return []

    def _gates(
        self,
        *,
        conversation_id: str | None,
        life: LifeSnapshot | None,
        now: datetime,
        recent_live: list[dict[str, Any]],
        idle_minutes: float | None,
    ) -> list[str]:
        reasons: list[str] = []
        if not self.settings.proactive_enabled:
            reasons.append("disabled")
        if _in_quiet_hours(now, self.settings.proactive_quiet_hours):
            reasons.append("quiet_hours")
        if (
            idle_minutes is not None
            and idle_minutes < self.settings.proactive_min_idle_minutes
        ):
            reasons.append("not_idle_enough")
        if self._sent_today(conversation_id, now) >= self.settings.proactive_max_per_day:
            reasons.append("daily_limit")
        if self._has_unanswered_proactive(conversation_id, recent_live):
            reasons.append("previous_proactive_unanswered")
        if (
            self.settings.proactive_skip_when_life_busy
            and life is not None
            and life.availability in {"busy", "sleeping", "away", "unavailable"}
        ):
            reasons.append(f"life_{life.availability}")
        return reasons

    def _features(
        self,
        profile: ProactiveProfile,
        *,
        now: datetime,
        idle_minutes: float | None,
        last_role: str,
        life: LifeSnapshot | None,
    ) -> dict[str, float]:
        hour_weight = _list_weight(profile.hour_weights, now.hour, fallback=0.35)
        weekday_weight = _list_weight(profile.weekday_weights, now.weekday(), fallback=0.4)
        idle_weight = (
            profile.idle_gap_weights.get(idle_gap_bucket(idle_minutes), 0.35)
            if idle_minutes is not None
            else 0.35
        )
        speaker_key = last_role if last_role in {"user", "assistant"} else "unknown"
        historical_speaker = "self" if speaker_key == "user" else "friend" if speaker_key == "assistant" else "unknown"
        speaker_weight = profile.previous_last_speaker_weights.get(historical_speaker, 0.35)
        opening_weight = max(profile.opening_type_weights.values(), default=0.35)
        availability_weight = _availability_weight(life)
        evidence_weight = min(1.0, profile.sample_size / 12.0) if profile.sample_size else 0.25
        return {
            "time_match": hour_weight,
            "weekday_match": weekday_weight,
            "idle_gap_match": idle_weight,
            "previous_speaker_match": speaker_weight,
            "opening_confidence": opening_weight,
            "availability": availability_weight,
            "evidence": evidence_weight,
        }

    def _next_schedule_at_ms(
        self,
        now: datetime,
        *,
        idle_minutes: float | None,
    ) -> int:
        base_delay = max(60, int(self.settings.proactive_check_interval_seconds))
        if idle_minutes is not None and idle_minutes < self.settings.proactive_min_idle_minutes:
            remaining = int((self.settings.proactive_min_idle_minutes - idle_minutes) * 60)
            base_delay = max(base_delay, remaining)
        earliest = int((now.timestamp() + base_delay) * 1000)
        quiet_end = _quiet_hours_end(now, self.settings.proactive_quiet_hours)
        if quiet_end is not None:
            earliest = max(earliest, quiet_end)
        profile = self._load_profile_sync()
        if profile is None or profile.sample_size <= 0:
            return earliest
        return _next_profile_time_ms(profile, now=now, earliest_ms=earliest)

    def _poll_scheduled_result(
        self,
        candidate: _PollCandidate,
        *,
        state: str = "scheduled",
        decision: ProactiveDecision | None = None,
        reason: str = "scheduled",
        cancelled: bool = False,
    ) -> ProactivePollResult:
        return ProactivePollResult(
            state=state,
            should_send=False,
            score=decision.score if decision is not None else 0.0,
            threshold=decision.threshold if decision is not None else self.settings.proactive_score_threshold,
            reason=reason if decision is None else "scheduled",
            skip_reasons=decision.skip_reasons if decision is not None else [],
            features=decision.features if decision is not None else {},
            private_context=decision.private_context if decision is not None else "",
            topic_hint=decision.topic_hint if decision is not None else "",
            profile_summary=decision.profile_summary if decision is not None else "",
            next_poll_at_ms=candidate.scheduled_for_ms,
            scheduled_for_ms=candidate.scheduled_for_ms,
            candidate_created_at_ms=candidate.created_at_ms,
            cancelled_by_user_activity=cancelled,
        )

    def _sent_today(self, conversation_id: str | None, now: datetime) -> int:
        cid = conversation_id or ""
        count = 0
        for record in self._audit:
            if record.get("conversation_id", "") != cid:
                continue
            if record.get("status") not in {"sent", "silenced"}:
                continue
            ts = int(record.get("ts_ms") or 0)
            if ts <= 0:
                continue
            dt = datetime.fromtimestamp(ts / 1000, tz=now.tzinfo)
            if dt.date() == now.date():
                count += 1
        return count

    def _has_unanswered_proactive(
        self,
        conversation_id: str | None,
        recent_live: list[dict[str, Any]],
    ) -> bool:
        cid = conversation_id or ""
        last_sent = 0
        for record in reversed(self._audit):
            if record.get("conversation_id", "") == cid and record.get("status") == "sent":
                last_sent = int(record.get("ts_ms") or 0)
                break
        if last_sent <= 0:
            return False
        for row in recent_live:
            if str(row.get("role") or "") == "user" and int(row.get("created_at_ms") or 0) > last_sent:
                return False
        return True

    def _load_audit(self) -> None:
        path = Path(self.audit_path)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-self.settings.proactive_audit_max_records :]:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                self._audit.append(data)

    def _load_poll_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        raw_candidates = data.get("candidates")
        if isinstance(raw_candidates, dict):
            for cid, raw in raw_candidates.items():
                if not isinstance(cid, str) or not isinstance(raw, dict):
                    continue
                try:
                    scheduled_for_ms = int(raw.get("scheduled_for_ms") or 0)
                    created_at_ms = int(raw.get("created_at_ms") or 0)
                except (TypeError, ValueError):
                    continue
                if scheduled_for_ms <= 0 or created_at_ms <= 0:
                    continue
                self._candidates[cid] = _PollCandidate(
                    conversation_id=cid,
                    created_at_ms=created_at_ms,
                    scheduled_for_ms=scheduled_for_ms,
                    reason=str(raw.get("reason") or "learned_rhythm"),
                )
        raw_activity = data.get("user_activity_ms")
        if isinstance(raw_activity, dict):
            for cid, value in raw_activity.items():
                if not isinstance(cid, str):
                    continue
                try:
                    ts = int(value)
                except (TypeError, ValueError):
                    continue
                if ts > 0:
                    self._user_activity_ms[cid] = ts

    def _append_audit_line(self, record: dict[str, Any]) -> None:
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _save_poll_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "candidates": {
                cid: asdict(candidate) for cid, candidate in self._candidates.items()
            },
            "user_activity_ms": self._user_activity_ms,
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _weighted_score(features: dict[str, float]) -> float:
    weights = {
        "time_match": 0.24,
        "weekday_match": 0.08,
        "idle_gap_match": 0.2,
        "previous_speaker_match": 0.12,
        "opening_confidence": 0.12,
        "availability": 0.12,
        "evidence": 0.12,
    }
    total = 0.0
    for key, weight in weights.items():
        total += max(0.0, min(1.0, features.get(key, 0.0))) * weight
    return total


def _next_profile_time_ms(
    profile: ProactiveProfile,
    *,
    now: datetime,
    earliest_ms: int,
) -> int:
    earliest = datetime.fromtimestamp(earliest_ms / 1000, tz=now.tzinfo)
    peak = max(profile.hour_weights or [0.0])
    if peak <= 0:
        return earliest_ms
    threshold = max(0.45, peak * 0.75)
    step = timedelta(minutes=15)
    # 从最早允许时间开始，最多向后看 36 小时；找到第一个“TA 历史上常主动”
    # 的小时。返回的是请求时间，不是发送时间，真正发送仍要二次门控。
    candidate = earliest
    for _ in range(36 * 4):
        hour_weight = _list_weight(profile.hour_weights, candidate.hour, fallback=0.0)
        weekday_weight = _list_weight(
            profile.weekday_weights,
            candidate.weekday(),
            fallback=0.0,
        )
        if hour_weight >= threshold and weekday_weight > 0:
            return int(candidate.timestamp() * 1000)
        candidate += step
    return earliest_ms


def _private_context(
    profile: ProactiveProfile,
    *,
    now: datetime,
    idle_minutes: float | None,
    last_role: str,
    life: LifeSnapshot | None,
    reason: str,
    score: float,
    features: dict[str, float],
) -> str:
    idle_text = "未知" if idle_minutes is None else f"{int(idle_minutes)} 分钟"
    life_text = ""
    if life is not None:
        life_text = (
            f" 当前 life 状态：{life.current_activity}；可用性：{life.availability}；"
            f"可聊话题：{life.topic_seed}。"
        )
    sample_text = ""
    if profile.samples:
        samples = " / ".join(sample.opening_text for sample in profile.samples[:3] if sample.opening_text)
        if samples:
            sample_text = f" 历史主动开场样本：{samples}。"
    return (
        f"主动聊天候选原因：{reason}。当前时间 {now:%Y-%m-%d %H:%M}，"
        f"距离最近会话空闲 {idle_text}，上一条运行时消息角色：{last_role}。"
        f"画像摘要：{profile.summary or '样本不足，保守处理'}。"
        f"评分 {score:.2f}，特征：{_feature_summary(features)}。"
        f"{life_text}{sample_text}"
    )


def _topic_hint(profile: ProactiveProfile, *, life: LifeSnapshot | None) -> str:
    opening = _top_key(profile.opening_type_weights)
    topic = life.topic_seed if life is not None and life.topic_seed else ""
    opening_text = {
        "greeting": "轻短问候",
        "life_check": "问问在干嘛或忙完没",
        "care": "接住近况做轻微关心",
        "continue_topic": "接上上一轮话题",
        "self_share": "像随手分享自己状态那样开场",
        "playful": "轻松调侃式开场",
        "affection": "按历史贴近习惯轻短开场，不主动升级关系",
        "wake_ping": "像叫醒或早间戳一下那样短开场",
        "short_ping": "像随手 ping 一下那样轻短开场，但要补一个可回复的小钩子",
        "question_probe": "用一个自然小问题开场",
        "night_ping": "深夜短短问一句",
        "other": "自然短开场",
    }.get(opening, "自然短开场")
    parts = [
        f"按历史主动开聊习惯，用“{opening_text}”方式主动找用户。",
        "必须短、像私聊，不要解释调度或系统判断。",
        "即使历史开场很短，也不能只发 ping；要有一个用户容易接住的点。",
        "不要推断用户当前在线、没睡、正在看消息；没有 presence 信号时，只能表达自己的状态或用条件句。",
    ]
    if topic:
        parts.append(f"可以自然带到：{topic}。")
    return " ".join(parts)


def _profile_to_dict(profile: ProactiveProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    data = asdict(profile)
    data["samples"] = data.get("samples", [])[:8]
    return data


def _list_weight(values: list[float], index: int, *, fallback: float) -> float:
    if 0 <= index < len(values):
        value = values[index]
        if value > 0:
            return value
    return fallback


def _availability_weight(life: LifeSnapshot | None) -> float:
    if life is None:
        return 0.75
    return {
        "available": 1.0,
        "idle": 0.9,
        "busy": 0.35,
        "sleeping": 0.15,
        "away": 0.25,
        "unavailable": 0.2,
    }.get(life.availability, 0.65)


def _idle_minutes(last_live: dict[str, Any] | None, now: datetime) -> float | None:
    if last_live is None:
        return None
    ts = int(last_live.get("created_at_ms") or 0)
    if ts <= 0:
        return None
    now_ms_value = int(now.timestamp() * 1000)
    return max(0.0, (now_ms_value - ts) / 60_000)


def _idle_minutes_from_decision(_decision: ProactiveDecision) -> float | None:
    # Decision intentionally exposes only normalized features. Poll scheduling can still
    # use the configured interval; precise idle gating is rechecked when the candidate is due.
    return None


def _user_activity_after_candidate(
    last_user_message_at_ms: int | None,
    candidate: _PollCandidate,
) -> bool:
    if last_user_message_at_ms is None:
        return False
    try:
        value = int(last_user_message_at_ms)
    except (TypeError, ValueError):
        return False
    return value > candidate.created_at_ms


def _in_quiet_hours(now: datetime, spec: str) -> bool:
    text = spec.strip()
    if not text or "-" not in text:
        return False
    start_text, end_text = [part.strip() for part in text.split("-", 1)]
    start = _parse_hhmm(start_text)
    end = _parse_hhmm(end_text)
    if start is None or end is None or start == end:
        return False
    current = now.hour * 60 + now.minute
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _quiet_hours_end(now: datetime, spec: str) -> int | None:
    text = spec.strip()
    if not text or "-" not in text or not _in_quiet_hours(now, text):
        return None
    _start_text, end_text = [part.strip() for part in text.split("-", 1)]
    end = _parse_hhmm(end_text)
    if end is None:
        return None
    end_hour, end_minute = divmod(end, 60)
    candidate = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return int(candidate.timestamp() * 1000)


def _parse_hhmm(value: str) -> int | None:
    parts = value.split(":", 1)
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _decision_reason(skip_reasons: list[str], score: float, threshold: float) -> str:
    if skip_reasons:
        return ",".join(skip_reasons)
    return f"score_below_threshold:{score:.2f}<{threshold:.2f}"


def _feature_summary(features: dict[str, float]) -> str:
    return ", ".join(f"{key}={value:.2f}" for key, value in sorted(features.items()))


def _top_key(values: dict[str, float]) -> str:
    if not values:
        return "other"
    return max(values.items(), key=lambda item: item[1])[0]


def _short(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
