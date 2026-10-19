"""AI 生活时间线。

这不是现实定位或真实生活，而是给角色维护一条可持续的虚拟日常。
默认时段计划只做兜底；每次聊天前，模型会根据当前时间、旧状态和对话内容
决定"现在在做什么"，并把决定写回 life_state.json。
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.core.metrics import MetricsRecorder
from xuwen.persona.circadian import (
    CircadianProfile,
    is_night_hour_for_profile,
    load_circadian_profile,
)
from xuwen.persona.prompt import ChatMessage

logger = logging.getLogger(__name__)
_TIMELINE_LIMIT = 80
_MAX_FIELD_CHARS = 80
_MIN_UPDATE_INTERVAL = timedelta(minutes=10)
_MAX_UPDATE_INTERVAL = timedelta(hours=10)
_LIFE_INTERRUPT_PATTERNS: tuple[str, ...] = (
    "在干嘛",
    "在干什么",
    "干嘛呢",
    "忙吗",
    "吃了吗",
    "吃饭",
    "睡了吗",
    "睡了没",
    "醒了吗",
    "起了吗",
    "起床",
    "怎么还没睡",
    "你呢",
)
_LIFE_INTERRUPT_PATTERNS += (
    "what are you doing",
    "are you busy",
    "have you eaten",
    "are you awake",
    "are you asleep",
)
_USER_NIGHT_PATTERNS = (
    "睡不着",
    "没睡",
    "还没睡",
    "熬夜",
    "睡不下",
    "失眠",
    "can't sleep",
    "cannot sleep",
    "still awake",
    "insomnia",
    "staying up",
)
# "被叫醒/半醒"识别：availability=sleeping 但 activity 文本里有这些词，
# 说明角色是被消息戳醒/半梦半醒，next_update 应该压短，等没消息时回到睡眠。
_WOKEN_ACTIVITY_PATTERNS = (
    "half-awake",
    "half awake",
    "woken",
    "just woke",
    "drowsy",
    "checking phone",
    "叫醒",
    "半醒",
    "迷糊",
    "看手机",
    "睁眼",
    "翻身",
    "翻了个身",
    "迷迷糊糊",
    "刚醒",
    "醒着",
)
_WOKEN_NEXT_UPDATE_LIMIT = timedelta(minutes=25)
_AVAILABILITY_VALUES = {"available", "busy", "sleeping", "away"}
_ANALYSIS_LIFE_MIN_CONFIDENCE = 0.5
_ANALYSIS_LIFE_MAX_HABITS = 6
LIFE_EVENT_TEXT_MAX_CHARS = 6400


@dataclass(slots=True, frozen=True)
class LifeSnapshot:
    date: str
    time_slot: str
    current_activity: str
    recent_meal: str
    mood: str
    topic_seed: str
    availability: str
    next_update_at: str
    reply_delay_seconds: int
    reply_delay_reason: str
    current_event_id: str = ""
    day_plan_summary: str = ""
    recent_timeline_summary: str = ""

    def render_prompt_block(self) -> str:
        return (
            "[Your current virtual daily state: takes priority over historical memories]\n"
            f"- Date: {self.date}\n- Time slot: {self.time_slot}\n"
            f"- Activity: {self.current_activity}\n- Recent food/drink: {self.recent_meal}\n"
            f"- Mood: {self.mood}\n- Availability: {self.availability}\n"
            f"- Current plan event: {self.current_event_id or 'unbound'}\n"
            f"- Next natural update: {self.next_update_at}\n- Daily plan: {self.day_plan_summary}\n"
            f"- Recent state transitions: {self.recent_timeline_summary}\n"
            f"- Suggested reply delay: {self.reply_delay_seconds} seconds; {self.reply_delay_reason}\n"
            f"- Possible topic: {self.topic_seed}\n"
            "Answer questions about today's/current activities, meals or sleep using this block only. "
            "Historical memories inform tone/preferences, not today's facts. Do not infer present events "
            "such as missing someone, secretly gaming or ignoring messages from old excerpts. "
            "Do not report your state every turn or fabricate real commitments or meetings."
        )


class LifeStateManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.persona_data_dir / "life_state.json"

    def _load_circadian_profile(self) -> CircadianProfile | None:
        """加载作息画像；文件不存在则返回 None，调用方走默认作息。"""
        from xuwen.persona.circadian import CIRCADIAN_PROFILE_FILENAME

        return load_circadian_profile(self.settings.persona_data_dir / CIRCADIAN_PROFILE_FILENAME)

    def _load_analysis_life_context(self) -> str:
        """读取精简的长期生活习惯参考；开关关闭或文件缺失时忽略。"""
        if not self.settings.analysis_life_context_enabled:
            return ""
        profile_path = self.settings.analysis_data_dir / "life_profile.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            profile = None
        rendered = _render_analysis_life_profile(profile)
        if rendered:
            return rendered

        # 兼容只生成了旧版 Markdown 的已有数据，但严格限制长度。
        context_path = self.settings.analysis_data_dir / "life_context.md"
        try:
            return context_path.read_text(encoding="utf-8").strip()[:1200]
        except OSError:
            return ""

    def snapshot(self, now: datetime | None = None) -> LifeSnapshot:
        now = now or datetime.now()
        state = self._load_or_create(now)
        slot = _slot_for_hour(now.hour)
        plan_event = _event_for_now(state, now)
        day_plan_summary = _summarize_daily_plan(state)
        timeline_summary = _summarize_recent_timeline(state)
        current = state.get("current")
        if isinstance(current, dict) and current.get("time_slot") == slot:
            return LifeSnapshot(
                date=str(state["date"]),
                time_slot=slot,
                current_activity=str(
                    current.get("activity") or content_text("Taking care of personal things")
                ),
                recent_meal=str(
                    current.get("meal") or content_text("Have not eaten anything notable")
                ),
                mood=str(current.get("mood") or state.get("mood") or content_text("Ordinary")),
                topic_seed=str(current.get("topic") or content_text("How today is going")),
                availability=_normalize_availability(current.get("availability")),
                next_update_at=str(
                    current.get("next_update_at")
                    or _format_dt(_default_next_update(now, "available"))
                ),
                reply_delay_seconds=_normalize_reply_delay(
                    current.get("reply_delay_seconds"),
                    max_seconds=self.settings.life_max_reply_delay_seconds,
                ),
                reply_delay_reason=str(current.get("reply_delay_reason") or ""),
                current_event_id=str(current.get("event_id") or (plan_event or {}).get("id") or ""),
                day_plan_summary=day_plan_summary,
                recent_timeline_summary=timeline_summary,
            )
        slot_state = state["slots"][slot]
        availability = _normalize_availability((plan_event or {}).get("availability"))
        return LifeSnapshot(
            date=state["date"],
            time_slot=slot,
            current_activity=str((plan_event or {}).get("activity") or slot_state["activity"]),
            recent_meal=str(slot_state["meal"]),
            mood=str(state["mood"]),
            topic_seed=str((plan_event or {}).get("topic") or slot_state["topic"]),
            availability=availability,
            next_update_at=str(
                (plan_event or {}).get("to") or _format_dt(_default_next_update(now, availability))
            ),
            reply_delay_seconds=0,
            reply_delay_reason="",
            current_event_id=str((plan_event or {}).get("id") or ""),
            day_plan_summary=day_plan_summary,
            recent_timeline_summary=timeline_summary,
        )

    async def decide_for_turn(
        self,
        *,
        llm: LLMClient,
        model: str,
        current_user_text: str,
        recent: list[ChatMessage],
        relationship_context: str = "",
        memory_context: str = "",
        trigger: str = "chat",
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
        now: datetime | None = None,
    ) -> LifeSnapshot:
        """让模型根据当前对话决定本轮使用的生活状态。

        模型只负责"状态更新"，真正聊天仍走主 chat prompt。失败时保留旧状态。
        """
        now = now or datetime.now()
        before = self.snapshot(now)
        state = self._load_or_create(now)
        circadian = self._load_circadian_profile()
        analysis_life_context = self._load_analysis_life_context()
        if not _should_update_state(
            settings=self.settings,
            state=state,
            before=before,
            now=now,
            current_user_text=current_user_text,
            trigger=trigger,
            circadian=circadian,
        ):
            return before
        prompt = _build_decision_prompt(
            settings=self.settings,
            now=now,
            before=before,
            state=state,
            current_user_text=current_user_text,
            recent=recent,
            relationship_context=relationship_context,
            memory_context=memory_context,
            trigger=trigger,
            circadian=circadian,
            analysis_life_context=analysis_life_context,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You control a character's virtual daily timeline, not the chat reply. "
                    "Return one JSON object parseable by json.loads(), starting with { and ending with }. "
                    "No Markdown, code fences, list bullets, introductory words or explanation."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            params = GenerationParams(
                temperature=self.settings.life_temperature,
                max_tokens=self.settings.life_max_tokens,
            )
            if metrics is None and not trace_id:
                raw = await llm.complete_chat(messages, params, model=model)
            else:
                raw = await llm.complete_chat(
                    messages,
                    params,
                    model=model,
                    trace_id=trace_id,
                    stage="life.decide",
                    metrics=metrics,
                )
            patch = _parse_decision(raw)
        except Exception:
            logger.warning(
                content_text("Life timeline decision failed; retaining the previous state"),
                exc_info=True,
            )
            # 失败兜底：写一个最小 fallback current，防止 state["current"] 永远不存在
            # 导致每轮 `_should_update_state` 都返回 True → 死循环重试。
            # 用 before snapshot + 30 分钟后的 next_update_at，给上游一个冷却窗口。
            self._write_fallback_current(state, before, now)
            return before
        if not patch:
            # JSON 解析返回空字典也走 fallback，原因同上
            self._write_fallback_current(state, before, now)
            return before
        return self._apply_decision(state, patch, now=now, trigger=trigger)

    async def apply_event(
        self,
        *,
        llm: LLMClient,
        model: str,
        event_text: str,
        assistant_text: str = "",
        current_user_text: str = "",
        trace_id: str = "",
        metrics: MetricsRecorder | None = None,
        now: datetime | None = None,
    ) -> LifeSnapshot:
        """让 Life 模型把主模型声明的自然语言事件归一化后写入时间线。"""
        now = now or datetime.now()
        before = self.snapshot(now)
        if not event_text.strip():
            return before
        state = self._load_or_create(now)
        prompt = _build_event_prompt(
            settings=self.settings,
            now=now,
            before=before,
            state=state,
            event_text=event_text,
            assistant_text=assistant_text,
            current_user_text=current_user_text,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Normalize declared life events; do not reply to the user. "
                    "Update only supported state changes; historical preferences cannot establish today's facts. "
                    "Return a JSON object parseable by json.loads(), no Markdown or explanation."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            params = GenerationParams(
                temperature=self.settings.life_temperature,
                max_tokens=min(self.settings.life_max_tokens, 900),
            )
            if metrics is None and not trace_id:
                raw = await llm.complete_chat(messages, params, model=model)
            else:
                raw = await llm.complete_chat(
                    messages,
                    params,
                    model=model,
                    trace_id=trace_id,
                    stage="life.event",
                    metrics=metrics,
                )
            patch = _parse_decision(raw)
        except Exception:
            logger.warning(
                content_text("Life-event model processing failed; preserving the current state"),
                exc_info=True,
            )
            return before
        if not patch:
            logger.info(
                content_text(
                    "Life event produced no applicable state; preserving the current state"
                )
            )
            return before
        for key, value in (
            ("current_activity", before.current_activity),
            ("current_event_id", before.current_event_id),
            ("recent_meal", before.recent_meal),
            ("mood", before.mood),
            ("availability", before.availability),
            ("topic_seed", before.topic_seed),
            ("next_update_at", before.next_update_at),
            ("reply_delay_seconds", before.reply_delay_seconds),
            ("reply_delay_reason", before.reply_delay_reason),
        ):
            patch.setdefault(key, value)
        return self._apply_decision(state, patch, now=now, trigger="model_event")

    def _write_fallback_current(
        self,
        state: dict[str, Any],
        before: LifeSnapshot,
        now: datetime,
    ) -> None:
        """LLM 调用/解析失败时的兜底：用 snapshot 数据写一个最小 current 入 state。

        作用：让 `_should_update_state` 下一轮能命中 `next_update_at` / `_is_stale_current`
        等正常检查路径，而不是因为缺 `current` 字段每轮强制触发更新。

        注意：失败本身已经记 warning 日志；这里再失败（写盘 IOError 等）就静默跳过，
        因为冷却兜底不应该淹没主调用链。
        """
        try:
            next_update = now + timedelta(minutes=30)
            current = {
                "time_slot": _slot_for_hour(now.hour),
                "event_id": before.current_event_id,
                "activity": before.current_activity,
                "meal": before.recent_meal,
                "mood": before.mood,
                "topic": before.topic_seed,
                "availability": before.availability,
                "next_update_at": _format_dt(next_update),
                "reply_delay_seconds": before.reply_delay_seconds,
                "reply_delay_reason": before.reply_delay_reason,
                "reason": "llm_fallback",
                "updated_at": now.isoformat(timespec="seconds"),
            }
            state["current"] = current
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning(content_text("Cannot persist the fallback life state"), exc_info=True)

    def _load_or_create(self, now: datetime) -> dict[str, Any]:
        today = now.strftime("%Y-%m-%d")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                state = cast(dict[str, Any], data)
                if not isinstance(state.get("daily_plan"), list):
                    rng = random.Random(f"{today}:{self.settings.friend_name or 'TA'}")
                    state["daily_plan"] = _generate_daily_plan(today, rng)
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(
                        json.dumps(state, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                return state
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        data = _generate_daily_state(today, self.settings.friend_name or "TA")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return data

    def _apply_decision(
        self,
        state: dict[str, Any],
        patch: dict[str, object],
        *,
        now: datetime,
        trigger: str,
    ) -> LifeSnapshot:
        slot = _slot_for_hour(now.hour)
        fallback = self.snapshot(now)
        plan_event = _event_for_now(state, now)
        event_id = _text_field(
            patch.get("current_event_id"),
            fallback.current_event_id or str((plan_event or {}).get("id") or ""),
        )
        availability = _normalize_availability(patch.get("availability"))
        next_update = _normalize_next_update(
            patch.get("next_update_at"),
            now=now,
            availability=availability,
        )
        reply_delay_seconds = _normalize_reply_delay(
            patch.get("reply_delay_seconds"),
            max_seconds=self.settings.life_max_reply_delay_seconds,
        )
        activity = _text_field(patch.get("current_activity"), fallback.current_activity)
        meal = _text_field(patch.get("recent_meal"), fallback.recent_meal)
        mood = _text_field(patch.get("mood"), fallback.mood)
        topic = _text_field(patch.get("topic_seed"), fallback.topic_seed)
        # sleeping + 被叫醒：把 next_update 强制压到短窗口（≤ 25 分钟），
        # 这样若用户没继续聊，下一次消息会触发 now>=next_update → 重新决策，
        # 模型看到 activity="被叫醒" + 时间已过会自然写回 sleeping/重新睡着。
        if availability == "sleeping" and _looks_woken_up(activity):
            woken_limit = now + _WOKEN_NEXT_UPDATE_LIMIT
            if next_update > woken_limit:
                next_update = woken_limit
        next_update_text = _format_dt(next_update)
        reply_delay_reason = _text_field(
            patch.get("reply_delay_reason") or patch.get("reason"),
            "",
        )
        reason = _text_field(patch.get("reason"), "")
        current = {
            "time_slot": slot,
            "event_id": event_id,
            "activity": activity,
            "meal": meal,
            "mood": mood,
            "topic": topic,
            "availability": availability,
            "next_update_at": next_update_text,
            "reply_delay_seconds": reply_delay_seconds,
            "reply_delay_reason": reply_delay_reason,
            "reason": reason,
            "updated_at": now.isoformat(timespec="seconds"),
        }
        state["current"] = current
        state["mood"] = current["mood"]
        daily_plan = _normalize_daily_plan(patch.get("daily_plan"))
        if daily_plan:
            state["daily_plan"] = daily_plan
            state["plan_decided_by_model"] = True
        slots = state.setdefault("slots", {})
        slot_state = slots.setdefault(slot, {})
        slot_state.update(
            {
                "activity": current["activity"],
                "meal": current["meal"],
                "topic": current["topic"],
            }
        )
        event = {
            "at": current["updated_at"],
            "time_slot": slot,
            "event_id": event_id,
            "activity": current["activity"],
            "meal": current["meal"],
            "mood": current["mood"],
            "topic": current["topic"],
            "availability": current["availability"],
            "next_update_at": current["next_update_at"],
            "reply_delay_seconds": current["reply_delay_seconds"],
            "reply_delay_reason": current["reply_delay_reason"],
            "trigger": trigger,
            "reason": current["reason"],
        }
        timeline = state.setdefault("timeline", [])
        if isinstance(timeline, list):
            timeline.append(event)
            del timeline[:-_TIMELINE_LIMIT]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return LifeSnapshot(
            date=str(state["date"]),
            time_slot=slot,
            current_activity=activity,
            recent_meal=meal,
            mood=mood,
            topic_seed=topic,
            availability=availability,
            next_update_at=next_update_text,
            reply_delay_seconds=reply_delay_seconds,
            reply_delay_reason=reply_delay_reason,
            current_event_id=event_id,
            day_plan_summary=_summarize_daily_plan(state),
            recent_timeline_summary=_summarize_recent_timeline(state),
        )


def _slot_for_hour(hour: int) -> str:
    if 5 <= hour < 11:
        return "上午"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def _generate_daily_state(date: str, friend_name: str) -> dict[str, Any]:
    rng = random.Random(f"{date}:{friend_name}")
    moods = [
        content_text("A little relaxed"),
        content_text("Ordinary but fairly upbeat"),
        content_text("A little sleepy"),
        content_text("Quiet"),
        content_text("Fairly alert"),
    ]
    lunch = [
        content_text("Had some noodles"),
        content_text("Had a quick rice bowl"),
        content_text("Had coffee and something to eat"),
        content_text("Had a simple meal"),
        content_text("Ordered takeout"),
    ]
    dinner = [
        content_text("Had an ordinary dinner"),
        content_text("Had something warm"),
        content_text("Have not had a proper meal yet"),
        content_text("Resting after a meal"),
        content_text("Had a quick snack"),
    ]
    daily_plan = _generate_daily_plan(date, rng)
    return {
        "date": date,
        "mood": rng.choice(moods),
        "daily_plan": daily_plan,
        "plan_decided_by_model": False,
        "slots": {
            "上午": {
                "activity": rng.choice(
                    [
                        content_text("Recently woke up"),
                        content_text("Slowly getting going"),
                        content_text("Tidying up and checking messages"),
                    ]
                ),
                "meal": rng.choice(
                    [
                        content_text("Had some water"),
                        content_text("Had some breakfast"),
                        content_text("Have not eaten much yet"),
                    ]
                ),
                "topic": rng.choice(
                    [
                        content_text("Plans for today"),
                        content_text("How last night went"),
                        content_text("Plans for the morning"),
                    ]
                ),
            },
            "中午": {
                "activity": rng.choice(
                    [
                        content_text("Resting after a meal"),
                        content_text("Getting ready to eat"),
                        content_text("Checking messages before a midday break"),
                    ]
                ),
                "meal": rng.choice(lunch),
                "topic": rng.choice(
                    [
                        content_text("Lunch"),
                        content_text("Afternoon plans"),
                        content_text("Taking a short break"),
                    ]
                ),
            },
            "下午": {
                "activity": rng.choice(
                    [
                        content_text("Taking care of things"),
                        content_text("A little drowsy but awake"),
                        content_text("Shaking off afternoon drowsiness"),
                    ]
                ),
                "meal": rng.choice(lunch),
                "topic": rng.choice(
                    [
                        content_text("How the afternoon is going"),
                        content_text("How today is going"),
                        content_text("Getting something to drink"),
                    ]
                ),
            },
            "晚上": {
                "activity": rng.choice(
                    [
                        content_text("Unwinding after dinner"),
                        content_text("Resting and checking messages"),
                        content_text("Winding down for the day"),
                    ]
                ),
                "meal": rng.choice(dinner),
                "topic": rng.choice(
                    [
                        content_text("Plans for the evening"),
                        content_text("How the day went"),
                        content_text("Getting an early rest"),
                    ]
                ),
            },
            "深夜": {
                "activity": rng.choice(
                    [
                        content_text("Still awake, daydreaming"),
                        content_text("Getting ready to lie down"),
                        content_text("Enjoying a quiet night"),
                    ]
                ),
                "meal": rng.choice(dinner),
                "topic": rng.choice(
                    [
                        content_text("Winding down for sleep"),
                        content_text("Late-night routines"),
                        content_text("A short chat before sleep"),
                    ]
                ),
            },
        },
        "timeline": [],
    }


def _generate_daily_plan(date: str, rng: random.Random) -> list[dict[str, str]]:
    wake_minute = rng.choice([0, 10, 20, 30])
    sleep_minute = rng.choice([0, 10, 20, 30])
    return [
        {
            "id": "sleep",
            "from": f"{date} 00:00",
            "to": f"{date} 08:{wake_minute:02d}",
            "activity": content_text("Sleeping"),
            "availability": "sleeping",
            "topic": content_text("Catching up on messages after waking"),
        },
        {
            "id": "morning",
            "from": f"{date} 08:{wake_minute:02d}",
            "to": f"{date} 11:30",
            "activity": rng.choice(
                [
                    content_text("Recently woke up"),
                    content_text("Slowly getting going"),
                    content_text("Tidying up and checking messages"),
                ]
            ),
            "availability": "available",
            "topic": rng.choice(
                [
                    content_text("Plans for the morning"),
                    content_text("How last night went"),
                    content_text("Plans for today"),
                ]
            ),
        },
        {
            "id": "lunch",
            "from": f"{date} 11:30",
            "to": f"{date} 13:30",
            "activity": rng.choice(
                [
                    content_text("Getting ready to eat"),
                    content_text("Resting after a meal"),
                    content_text("Checking messages before a midday break"),
                ]
            ),
            "availability": "available",
            "topic": rng.choice(
                [
                    content_text("Lunch"),
                    content_text("Afternoon plans"),
                    content_text("Taking a short break"),
                ]
            ),
        },
        {
            "id": "afternoon",
            "from": f"{date} 13:30",
            "to": f"{date} 18:30",
            "activity": rng.choice(
                [
                    content_text("Taking care of things"),
                    content_text("A little drowsy but awake"),
                    content_text("Shaking off afternoon drowsiness"),
                ]
            ),
            "availability": rng.choice(["available", "busy"]),
            "topic": rng.choice(
                [
                    content_text("How the afternoon is going"),
                    content_text("How today is going"),
                    content_text("Getting something to drink"),
                ]
            ),
        },
        {
            "id": "evening",
            "from": f"{date} 18:30",
            "to": f"{date} 23:{sleep_minute:02d}",
            "activity": rng.choice(
                [
                    content_text("Unwinding after dinner"),
                    content_text("Resting and checking messages"),
                    content_text("Winding down for the day"),
                ]
            ),
            "availability": "available",
            "topic": rng.choice(
                [
                    content_text("Plans for the evening"),
                    content_text("How the day went"),
                    content_text("Getting an early rest"),
                ]
            ),
        },
        {
            "id": "late_sleep",
            "from": f"{date} 23:{sleep_minute:02d}",
            "to": f"{date} 23:59",
            "activity": content_text("Getting ready to sleep"),
            "availability": "sleeping",
            "topic": content_text("Winding down for sleep"),
        },
    ]


def _build_decision_prompt(
    *,
    settings: Settings,
    now: datetime,
    before: LifeSnapshot,
    state: dict[str, Any],
    current_user_text: str,
    recent: list[ChatMessage],
    relationship_context: str,
    memory_context: str,
    trigger: str,
    circadian: CircadianProfile | None = None,
    analysis_life_context: str = "",
) -> str:
    timeline = state.get("timeline")
    inputs = {
        "character": settings.friend_name or "Friend",
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "trigger": trigger,
        "previous_state": asdict(before),
        "daily_plan": state.get("daily_plan", []),
        "plan_decided_by_model": bool(state.get("plan_decided_by_model")),
        "circadian_profile": _format_circadian_for_prompt(circadian, now),
        "long_term_life_candidates": analysis_life_context[:1600],
        "recent_timeline": timeline[-8:] if isinstance(timeline, list) else [],
        "relationship_memory": relationship_context[:1200],
        "historical_style_reference": memory_context[:1600],
        "recent_conversation": [{"role": m.role, "content": m.content} for m in recent[-8:]],
        "current_user_message": current_user_text,
    }
    return (
        "Update the character's current virtual daily state, not the actual chat reply.\n"
        "1. Use the real current time, measured circadian profile, long-term candidates, daily plan, "
        "previous state, timeline and conversation. Measured time patterns and current dialogue take priority "
        "over inferred long-term tendencies.\n"
        "2. The plan is a fallback, not a fixed script. Update ordinary activities naturally when time or "
        "conversation warrants it. Maintain continuity and restraint; do not simply repeat the user.\n"
        "3. Do not fabricate real meetings, location, employer, payments or medical facts.\n"
        "4. Activity, meal and sleep questions should be answerable from this state.\n"
        "5. When previously sleeping or during the night, a wake-up message may leave availability=sleeping "
        "with a half-awake activity, delay 5-45 seconds, and next_update_at 15-25 minutes later. If already "
        "half-awake, it is still night, and the user is not urging a reply, allow returning to sleep with "
        "delay at least 10 seconds.\n"
        "6. If the user mentions staying awake or insomnia, choose an appropriate caring topic.\n"
        "7. sleeping requires delay >=5 seconds. busy/away warrant moderate delay; available defaults to 0 "
        f"unless occupied. Never exceed {settings.life_max_reply_delay_seconds} seconds.\n"
        "8. Set next_update_at to a natural transition, 10 minutes to 10 hours after now, formatted YYYY-MM-DD HH:MM.\n"
        "9. If the plan is not model-confirmed, you may return daily_plan entries with "
        "id/from/to/activity/availability/topic. Preserve dates and ids.\n"
        "10. Historical tone and routines are only candidate priors, not proof of today's activities, meals, "
        "late nights, games or slow replies. Never turn past habits into established present facts.\n"
        "11. Output one JSON object only: first character {, last character }. No Markdown, lists outside "
        "JSON or explanation. All free-text state values should follow the current user's language, or "
        "recent conversation if ambiguous; preserve existing facts and identifiers.\n"
        "Schema (choose a SINGLE valid enum, never a pipe-separated list):\n"
        '{"current_activity":"short activity","current_event_id":"existing plan id or empty",'
        '"recent_meal":"known recent meal","mood":"short mood",'
        '"availability":"available","topic_seed":"natural topic",'
        '"next_update_at":"YYYY-MM-DD HH:MM","reply_delay_seconds":0,"reply_delay_reason":"",'
        '"daily_plan":[],"reason":"short internal explanation"}\n'
        "availability must be available, busy, sleeping or away.\nInput:\n"
        + json.dumps(inputs, ensure_ascii=False)
    )


def _build_event_prompt(
    *,
    settings: Settings,
    now: datetime,
    before: LifeSnapshot,
    state: dict[str, Any],
    event_text: str,
    assistant_text: str,
    current_user_text: str,
) -> str:
    timeline = state.get("timeline")
    inputs = {
        "character": settings.friend_name or "Friend",
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "previous_state": asdict(before),
        "declared_event": event_text[:LIFE_EVENT_TEXT_MAX_CHARS],
        "adopted_reply": assistant_text[:1000],
        "current_user_message": current_user_text[:600],
        "recent_timeline": timeline[-4:] if isinstance(timeline, list) else [],
    }
    return (
        "Normalize the declared life event into the character's current virtual state.\n"
        "The event is the primary evidence. Legacy JSON event text is also only a description. "
        "Update only directly supported fields; preserve unmentioned previous values. Never invent "
        'activities, meals, locations or plans. Return {"apply":false} for vague events, conflicts '
        "with the adopted reply, non-state changes or unreliable interpretations.\n"
        "next_update_at must be 10 minutes to 10 hours after now, formatted YYYY-MM-DD HH:MM. "
        "availability is one of available/busy/sleeping/away. sleeping requires delay >=5 seconds; "
        f"all delays must be <= {settings.life_max_reply_delay_seconds} seconds. "
        "Keep generated text in the event/conversation language and preserve existing facts and ids. "
        "Return only one JSON object, no Markdown or explanation. Schema:\n"
        '{"apply":true,"current_activity":"supported activity","current_event_id":"existing id",'
        '"recent_meal":"previous value unless updated","mood":"previous value unless updated",'
        '"availability":"available","topic_seed":"natural topic","next_update_at":"YYYY-MM-DD HH:MM",'
        '"reply_delay_seconds":0,"reply_delay_reason":"","reason":"event_normalized"}\n'
        "Input:\n" + json.dumps(inputs, ensure_ascii=False)
    )


def _format_circadian_for_prompt(profile: CircadianProfile | None, now: datetime) -> str:
    if profile is None or profile.sample_size < 30:
        return "No reliable profile. Default awake hours: 08:00-23:00; adjust to actual night-owl/time-zone evidence in conversation."
    is_weekend = now.weekday() >= 5
    label = "weekend" if is_weekend else "weekday"
    hourly = profile.weekend_hourly if is_weekend else profile.weekday_hourly
    if not any(hourly):
        hourly = profile.hourly_activity
    active = (
        ", ".join(f"{h:02d}:00" for h, value in enumerate(hourly) if value >= 0.3) or "sparse data"
    )
    start, end = profile.typical_awake_range
    return (
        f"- Typical awake hours: {start:02d}:00 - {end:02d}:00\n"
        f"- Late-night activity share: {profile.night_owl_score:.0%}\n"
        f"- Today is a {label}; historically active hours: {active}\n- Notes: {profile.summary}"
    )


def _render_analysis_life_profile(raw: object) -> str:
    if not isinstance(raw, dict):
        return ""
    habits = raw.get("habits")
    if not isinstance(habits, list):
        return ""

    candidates: list[tuple[float, str]] = []
    category_labels = {
        "sleep": "sleep",
        "meal": "meals",
        "activity": "activity",
        "availability": "availability",
    }
    for habit in habits:
        if not isinstance(habit, dict):
            continue
        confidence = habit.get("confidence")
        if (
            habit.get("subject") != "friend"
            or habit.get("sensitive_relationship_context") is True
            or not isinstance(confidence, int | float)
            or isinstance(confidence, bool)
            or confidence < _ANALYSIS_LIFE_MIN_CONFIDENCE
        ):
            continue
        target_fields = habit.get("target_fields")
        claim = " ".join(str(habit.get("claim") or "").split()).strip()
        if not claim or not isinstance(target_fields, list) or not target_fields:
            continue
        time_patterns = habit.get("time_patterns")
        times = (
            ", ".join(str(item) for item in time_patterns[:2] if str(item).strip())
            if isinstance(time_patterns, list)
            else ""
        )
        fields = ", ".join(str(item) for item in target_fields[:4] if str(item).strip())
        category = category_labels.get(str(habit.get("category") or ""), "daily life")
        time_part = f"; typical times: {times}" if times else ""
        candidates.append(
            (
                float(confidence),
                f"- [{category}，{round(float(confidence) * 100)}%] {claim}"
                f"{time_part}; may affect: {fields}",
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[:_ANALYSIS_LIFE_MAX_HABITS]
    if not selected:
        return ""
    return "\n".join(
        [
            "These are high-confidence historical life candidates, not facts about today. Current time, state and conversation take priority.",
            *(line for _, line in selected),
        ]
    )


def _speaker(settings: Settings, role: str) -> str:
    if role == "user":
        return settings.self_name or "用户"
    if role == "assistant":
        return settings.friend_name or "TA"
    return "系统"


def _should_update_state(
    *,
    settings: Settings,
    state: dict[str, Any],
    before: LifeSnapshot,
    now: datetime,
    current_user_text: str,
    trigger: str,
    circadian: CircadianProfile | None = None,
) -> bool:
    current = state.get("current")
    if not isinstance(current, dict):
        return True
    if trigger.startswith("proactive") and trigger != "proactive:manual":
        return True
    if _is_stale_current(current, now, settings.life_update_interval_minutes):
        return True
    next_update = _parse_next_update(str(current.get("next_update_at") or ""))
    if next_update is None or now >= next_update:
        return True
    text = current_user_text.strip()
    if not text:
        return False
    if before.availability == "sleeping":
        return True
    if _contains_any(text, _LIFE_INTERRUPT_PATTERNS):
        return True
    # 深夜熬夜判定：根据 circadian profile 而非硬编码 22-06，
    # 这样夜猫子 TA 的"对方的深夜"自然偏移到凌晨更深的时段
    if is_night_hour_for_profile(now.hour, circadian) and _contains_any(text, _USER_NIGHT_PATTERNS):
        return True
    return False


def _is_stale_current(current: dict[str, Any], now: datetime, interval_minutes: int) -> bool:
    if interval_minutes <= 0:
        return False
    raw = str(current.get("updated_at") or "")
    if not raw:
        return False
    try:
        updated_at = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if updated_at.tzinfo is not None and now.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=None)
    elif updated_at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return now - updated_at >= timedelta(minutes=interval_minutes)


def _event_for_now(state: dict[str, Any], now: datetime) -> dict[str, str] | None:
    plan = state.get("daily_plan")
    if not isinstance(plan, list):
        return None
    for item in plan:
        if not isinstance(item, dict):
            continue
        start = _parse_next_update(str(item.get("from") or ""))
        end = _parse_next_update(str(item.get("to") or ""))
        if start is None or end is None:
            continue
        if start <= now <= end:
            return {str(k): str(v) for k, v in item.items()}
    return None


def _summarize_daily_plan(state: dict[str, Any], limit: int = 6) -> str:
    plan = state.get("daily_plan")
    if not isinstance(plan, list):
        return ""
    parts: list[str] = []
    for item in plan[:limit]:
        if not isinstance(item, dict):
            continue
        start = _time_only(str(item.get("from") or ""))
        end = _time_only(str(item.get("to") or ""))
        activity = str(item.get("activity") or "").strip()
        availability = str(item.get("availability") or "").strip()
        if not activity:
            continue
        time_range = f"{start}-{end}" if start and end else ""
        label = f"{time_range} {activity}".strip()
        if availability:
            label = f"{label}（{_availability_label(availability)}）"
        parts.append(label)
    return "；".join(parts)


def _summarize_recent_timeline(state: dict[str, Any], limit: int = 4) -> str:
    timeline = state.get("timeline")
    if not isinstance(timeline, list):
        return ""
    parts: list[str] = []
    for item in timeline[-limit:]:
        if not isinstance(item, dict):
            continue
        at = _time_only(str(item.get("at") or ""))
        activity = str(item.get("activity") or "").strip()
        meal = str(item.get("meal") or "").strip()
        if not activity:
            continue
        bit = f"{at} {activity}".strip()
        if meal:
            bit = f"{bit}，{meal}"
        parts.append(bit)
    return "；".join(parts)


def _time_only(value: str) -> str:
    parsed = _parse_next_update(value)
    if parsed is None:
        return ""
    return parsed.strftime("%H:%M")


def _availability_label(value: str) -> str:
    labels = {
        "available": "可回",
        "busy": "忙",
        "sleeping": "睡觉",
        "away": "离开",
    }
    return labels.get(value, value)


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower().replace("’", "'")
    return any(p in lowered for p in patterns)


def _looks_woken_up(activity: str) -> bool:
    """activity 文本是否像"被叫醒/半醒"。

    用于 sleeping 状态下区分"在熟睡"与"被消息戳醒/半梦半醒"，
    后者下一次状态更新时间要压短，以便没新消息时自然回到熟睡。
    """
    if not activity:
        return False
    return any(p in activity.lower() for p in _WOKEN_ACTIVITY_PATTERNS)


def _fallback_availability(now: datetime) -> str:
    if 0 <= now.hour < 7:
        return "sleeping"
    return "available"


def _normalize_availability(value: object) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _AVAILABILITY_VALUES:
            return lowered
    return "available"


def _normalize_reply_delay(value: object, *, max_seconds: int) -> int:
    try:
        delay = int(float(str(value)))
    except (TypeError, ValueError):
        delay = 0
    return max(0, min(delay, max_seconds))


def _normalize_next_update(
    value: object,
    *,
    now: datetime,
    availability: str,
) -> datetime:
    parsed = _parse_next_update(str(value or ""))
    if parsed is None:
        parsed = _default_next_update(now, availability)
    min_dt = now + _MIN_UPDATE_INTERVAL
    max_dt = now + _MAX_UPDATE_INTERVAL
    if parsed < min_dt:
        return min_dt
    if parsed > max_dt:
        return max_dt
    return parsed


def _normalize_daily_plan(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    required = ("id", "from", "to", "activity", "availability", "topic")
    for item in value:
        if not isinstance(item, dict):
            continue
        row: dict[str, str] = {}
        for key in required:
            raw = item.get(key)
            if not isinstance(raw, str) or not raw.strip():
                row = {}
                break
            row[key] = raw.strip()[:_MAX_FIELD_CHARS]
        if not row:
            continue
        if _parse_next_update(row["from"]) is None or _parse_next_update(row["to"]) is None:
            continue
        row["availability"] = _normalize_availability(row["availability"])
        out.append(row)
    return out[:12]


def _parse_next_update(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _default_next_update(now: datetime, availability: str) -> datetime:
    if availability == "sleeping":
        wake_hour = 8
        target = now.replace(hour=wake_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    for hour in (11, 14, 18, 23):
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target > now:
            return target
    return (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


def _format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _text_field(value: object, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _parse_decision(raw: str) -> dict[str, object] | None:
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        return None
    if data.get("apply") is False:
        return None
    mapping = {
        "current_activity": "current_activity",
        "current_event_id": "current_event_id",
        "recent_meal": "recent_meal",
        "mood": "mood",
        "availability": "availability",
        "topic_seed": "topic_seed",
        "next_update_at": "next_update_at",
        "reply_delay_reason": "reply_delay_reason",
        "reason": "reason",
    }
    out: dict[str, object] = {}
    for src, dst in mapping.items():
        value = data.get(src)
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip()
            if cleaned:
                out[dst] = cleaned[:_MAX_FIELD_CHARS]
    delay = data.get("reply_delay_seconds")
    if isinstance(delay, int | float | str):
        out["reply_delay_seconds"] = delay
    daily_plan = data.get("daily_plan")
    if isinstance(daily_plan, list):
        out["daily_plan"] = daily_plan
    return out


def _extract_json_object(raw: str) -> object | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return cast(object, json.loads(text[start : end + 1]))
    except json.JSONDecodeError:
        return None
