"""AI 生活时间线。

这不是现实定位或真实生活，而是给角色维护一条可持续的虚拟日常。
默认时段计划只做兜底；每次聊天前，模型会根据当前时间、旧状态和对话内容
决定"现在在做什么"，并把决定写回 life_state.json。
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
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
_LIFE_INTERRUPT_PATTERNS = (
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
_USER_NIGHT_PATTERNS = ("睡不着", "没睡", "还没睡", "熬夜", "睡不下", "失眠")
# "被叫醒/半醒"识别：availability=sleeping 但 activity 文本里有这些词，
# 说明角色是被消息戳醒/半梦半醒，next_update 应该压短，等没消息时回到睡眠。
_WOKEN_ACTIVITY_PATTERNS = (
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
        delay_line = ""
        if self.reply_delay_seconds > 0:
            delay_line = (
                f"- 回复延迟建议：{self.reply_delay_seconds} 秒"
                f"（{self.reply_delay_reason or '当前不方便立刻回复'}）\n"
            )
        plan_line = (
            f"- 今天计划：{self.day_plan_summary}\n"
            if self.day_plan_summary
            else ""
        )
        timeline_line = (
            f"- 今天已经发生的状态变化：{self.recent_timeline_summary}\n"
            if self.recent_timeline_summary
            else ""
        )
        return (
            "【你自己的当前生活状态（优先级高于历史记忆）】\n"
            f"- 日期：{self.date}\n"
            f"- 现在大概：{self.time_slot}\n"
            f"- 你在做：{self.current_activity}\n"
            f"- 最近吃/喝：{self.recent_meal}\n"
            f"- 心情：{self.mood}\n"
            f"- 可用状态：{self.availability}\n"
            f"- 当前日程节点：{self.current_event_id or '未绑定'}\n"
            f"- 下一次自然状态更新时间：{self.next_update_at}\n"
            f"{plan_line}"
            f"{timeline_line}"
            f"{delay_line}"
            f"- 可以自然提起的话题：{self.topic_seed}\n"
            "使用规则：如果用户问你今天/现在在做什么、吃了什么、睡没睡，"
            "只能依据本块回答；历史记忆只用于语气和偏好，不代表今天事实。"
            "不要从历史片段推断“想你”“偷偷打游戏”“不理我”等当前事件；"
            "不要每次主动汇报，也不要编造成真实承诺或现实见面安排。"
        )


class LifeStateManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.persona_data_dir / "life_state.json"

    def _load_circadian_profile(self) -> CircadianProfile | None:
        """加载作息画像；文件不存在则返回 None，调用方走默认作息。"""
        from xuwen.persona.circadian import CIRCADIAN_PROFILE_FILENAME

        return load_circadian_profile(
            self.settings.persona_data_dir / CIRCADIAN_PROFILE_FILENAME
        )

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
                current_activity=str(current.get("activity") or "在处理自己的事"),
                recent_meal=str(current.get("meal") or "还没特别吃什么"),
                mood=str(current.get("mood") or state.get("mood") or "普通"),
                topic_seed=str(current.get("topic") or "今天怎么过"),
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
                current_event_id=str(
                    current.get("event_id")
                    or (plan_event or {}).get("id")
                    or ""
                ),
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
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是角色的生活时间线控制器。你的任务不是回复用户，"
                    "而是决定角色此刻的虚拟日常状态。只输出 JSON 对象。"
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
            logger.warning("生活时间线决策失败，沿用旧状态", exc_info=True)
            return before
        if not patch:
            return before
        return self._apply_decision(state, patch, now=now, trigger=trigger)

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

    def apply_marker_patch(
        self,
        patch_text: str,
        *,
        now: datetime | None = None,
        trigger: str = "marker",
    ) -> LifeSnapshot | None:
        """主模型在回复里输出的 life-update 标记块写入 life。

        patch_text 是 <life-update>...</life-update> 中间的 JSON 字符串。
        解析失败、无可用字段时返回 None（不影响主回复链路）。
        """
        if not patch_text or not patch_text.strip():
            return None
        try:
            data = _extract_json_object(patch_text)
        except Exception:
            logger.warning("life marker JSON 解析失败", exc_info=True)
            return None
        if not isinstance(data, dict):
            return None
        patch: dict[str, object] = {}
        for src, dst in (
            ("current_activity", "current_activity"),
            ("current_event_id", "current_event_id"),
            ("recent_meal", "recent_meal"),
            ("mood", "mood"),
            ("availability", "availability"),
            ("topic_seed", "topic_seed"),
            ("next_update_at", "next_update_at"),
            ("reply_delay_reason", "reply_delay_reason"),
            ("reason", "reason"),
        ):
            value = data.get(src)
            if isinstance(value, str):
                cleaned = " ".join(value.split()).strip()
                if cleaned:
                    patch[dst] = cleaned[:_MAX_FIELD_CHARS]
        delay = data.get("reply_delay_seconds")
        if isinstance(delay, int | float | str):
            patch["reply_delay_seconds"] = delay
        if not patch:
            return None
        now = now or datetime.now()
        state = self._load_or_create(now)
        return self._apply_decision(state, patch, now=now, trigger=trigger)

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
    moods = ["有点放松", "普通但还算轻快", "有点犯困", "安静", "精神还可以"]
    lunch = ["吃了点面", "随便吃了盖饭", "喝了咖啡又吃了点东西", "吃得比较简单", "点了外卖"]
    dinner = ["晚饭吃得挺普通", "吃了点热的", "还没认真吃", "吃完在歇着", "随便垫了点"]
    daily_plan = _generate_daily_plan(date, rng)
    return {
        "date": date,
        "mood": rng.choice(moods),
        "daily_plan": daily_plan,
        "plan_decided_by_model": False,
        "slots": {
            "上午": {
                "activity": rng.choice(["刚醒一会儿", "在慢慢进入状态", "边收拾边看消息"]),
                "meal": rng.choice(["喝了水", "吃了点早饭", "还没怎么吃"]),
                "topic": rng.choice(["今天打算怎么过", "昨晚睡得怎么样", "上午有没有安排"]),
            },
            "中午": {
                "activity": rng.choice(["刚吃完饭在歇着", "准备吃饭", "午休前看一眼消息"]),
                "meal": rng.choice(lunch),
                "topic": rng.choice(["中午吃了什么", "下午忙不忙", "要不要休息一会儿"]),
            },
            "下午": {
                "activity": rng.choice(["在处理手头的事", "有点犯困但还醒着", "刚从午后的困意里缓过来"]),
                "meal": rng.choice(lunch),
                "topic": rng.choice(["下午有没有累", "今天进展怎么样", "要不要喝点东西"]),
            },
            "晚上": {
                "activity": rng.choice(["吃完饭在放空", "边休息边看消息", "准备慢慢收尾今天"]),
                "meal": rng.choice(dinner),
                "topic": rng.choice(["晚上准备做什么", "今天过得怎么样", "要不要早点休息"]),
            },
            "深夜": {
                "activity": rng.choice(["还没睡，在发呆", "准备躺下了", "夜里有点安静"]),
                "meal": rng.choice(dinner),
                "topic": rng.choice(["怎么还没睡", "是不是又熬夜了", "要不要聊一会儿再睡"]),
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
            "activity": "在睡觉",
            "availability": "sleeping",
            "topic": "醒来后再慢慢回消息",
        },
        {
            "id": "morning",
            "from": f"{date} 08:{wake_minute:02d}",
            "to": f"{date} 11:30",
            "activity": rng.choice(["刚醒一会儿", "在慢慢进入状态", "边收拾边看消息"]),
            "availability": "available",
            "topic": rng.choice(["上午有没有安排", "昨晚睡得怎么样", "今天打算怎么过"]),
        },
        {
            "id": "lunch",
            "from": f"{date} 11:30",
            "to": f"{date} 13:30",
            "activity": rng.choice(["准备吃饭", "刚吃完饭在歇着", "午休前看一眼消息"]),
            "availability": "available",
            "topic": rng.choice(["中午吃了什么", "下午忙不忙", "要不要休息一会儿"]),
        },
        {
            "id": "afternoon",
            "from": f"{date} 13:30",
            "to": f"{date} 18:30",
            "activity": rng.choice(["在处理手头的事", "有点犯困但还醒着", "刚从午后的困意里缓过来"]),
            "availability": rng.choice(["available", "busy"]),
            "topic": rng.choice(["下午有没有累", "今天进展怎么样", "要不要喝点东西"]),
        },
        {
            "id": "evening",
            "from": f"{date} 18:30",
            "to": f"{date} 23:{sleep_minute:02d}",
            "activity": rng.choice(["吃完饭在放空", "边休息边看消息", "准备慢慢收尾今天"]),
            "availability": "available",
            "topic": rng.choice(["晚上准备做什么", "今天过得怎么样", "要不要早点休息"]),
        },
        {
            "id": "late_sleep",
            "from": f"{date} 23:{sleep_minute:02d}",
            "to": f"{date} 23:59",
            "activity": "准备睡了",
            "availability": "sleeping",
            "topic": "怎么还没睡",
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
) -> str:
    recent_text = "\n".join(
        f"{_speaker(settings, m.role)}: {m.content}" for m in recent[-8:]
    )
    timeline = state.get("timeline")
    timeline_text = ""
    if isinstance(timeline, list) and timeline:
        timeline_text = json.dumps(timeline[-8:], ensure_ascii=False)
    plan = state.get("daily_plan")
    plan_text = json.dumps(plan, ensure_ascii=False) if isinstance(plan, list) else "（暂无）"
    plan_decided = bool(state.get("plan_decided_by_model"))
    circadian_text = _format_circadian_for_prompt(circadian, now)
    relationship_text = relationship_context[:1200]
    memory_text = memory_context[:1600]
    next_update_at = before.next_update_at or "（未设置）"
    delay_text = (
        f"{before.reply_delay_seconds} 秒，原因：{before.reply_delay_reason}"
        if before.reply_delay_seconds > 0
        else "无"
    )
    return f"""请更新 {settings.friend_name or "TA"} 的当前生活状态。

当前真实时间：{now.strftime("%Y-%m-%d %H:%M")}
触发来源：{trigger}

上一状态：
- 时段：{before.time_slot}
- 在做：{before.current_activity}
- 最近吃/喝：{before.recent_meal}
- 心情：{before.mood}
- 可用状态：{before.availability}
- 当前日程节点：{before.current_event_id or "未绑定"}
- 下一次自然更新时间：{next_update_at}
- 回复延迟：{delay_text}
- 可聊话题：{before.topic_seed}

今日计划骨架：
{plan_text}
今日计划是否已经由模型确认：{"是" if plan_decided else "否"}

TA 的真实作息画像（来自历史聊天时间分布，请优先采纳，覆盖默认 8-23 假设）：
{circadian_text}

最近时间线事件：
{timeline_text or "（暂无）"}

关系记忆摘要：
{relationship_text or "（暂无）"}

相关历史语气参考：
{memory_text or "（暂无）"}

最近聊天：
{recent_text or "（暂无）"}

用户这次说：
{current_user_text or "（用户发了非文本内容）"}

要求：
1. 结合当前时间、今日计划骨架、上一状态、最近时间线、聊天内容和长期偏好，让角色自己决定此刻在做什么。
2. 今日计划骨架只是兜底参考，不是固定剧本；如果聊天内容或时间流逝说明状态该变了，
   可以自然更新到新的普通日常小事。状态要克制、连续、可信；不要直接复读用户的话。
3. 不要编造现实见面、定位、工作单位、付款、医疗等硬事实。
4. 如果用户问“在干嘛/吃了吗/睡了吗”，状态要能直接为这次回复提供依据。
5. 如果上一状态是 sleeping 或当前是深夜，且用户消息像是在叫醒你，可以保留 sleeping，
   current_activity 写成"被消息叫醒/半醒着看手机"这类状态，reply_delay_seconds 必须给 5-45（迷糊状态不可能秒回）。
   被叫醒情境下 next_update_at 必须设为 15-25 分钟后；
   这样如果一会儿没新消息，下一次状态更新可以自然把 current_activity 写回"重新睡着了"。
   如果上一状态 current_activity 已经是"被叫醒/半醒/迷糊看手机"等，且当前依然在深夜/凌晨时段，
   而用户这条新消息并不像在催你回，就把 availability 保持 sleeping、current_activity 改写成
   "又睡过去/翻个身继续睡"这类，reply_delay_seconds 仍给 ≥ 10，表示真的睡着了。
6. 如果用户表达自己熬夜/睡不着，topic_seed 应该能自然关心这件事。
7. availability=sleeping 时 reply_delay_seconds 必须 ≥ 5（睡着的人不可能 0 延迟回复）；
   availability 是 busy / away 时给适度延迟；available 默认 0，除非在忙具体的事。
   不要超过 {settings.life_max_reply_delay_seconds} 秒。
8. 必须决定下一次自然状态更新时间 next_update_at：
   - 格式固定为 "YYYY-MM-DD HH:MM"
   - 一般设到下一个生活节点（起床、午饭、下午、晚饭、睡前）
   - 不要早于当前时间 10 分钟，也不要晚于当前时间 10 小时。
9. 如果“今日计划是否已经由模型确认”为否，你可以输出 daily_plan 覆盖今日计划。
   daily_plan 是数组，每项必须包含 id/from/to/activity/availability/topic。
10. 相关历史语气参考只表示长期语气/偏好，不是今天事实；不要据此生成“今天一直想你”
   “猜你偷偷打游戏”“你不理我”等当前事件。
11. 只输出 JSON，不要 markdown。

JSON 格式：
{{
  "current_activity": "一句短语，说明此刻在做什么",
  "current_event_id": "当前日程节点 id，尽量从今日计划骨架中选择",
  "recent_meal": "一句短语，说明最近吃/喝了什么；不知道就自然一点",
  "mood": "一句短语，说明当前心情",
  "availability": "available|busy|sleeping|away 之一",
  "topic_seed": "一句短语，适合自然展开的话题",
  "next_update_at": "YYYY-MM-DD HH:MM",
  "reply_delay_seconds": 0,
  "reply_delay_reason": "如果有延迟，用一句短语解释；没有则空字符串",
  "daily_plan": [
    {{
      "id": "morning",
      "from": "{now.strftime('%Y-%m-%d')} 08:30",
      "to": "{now.strftime('%Y-%m-%d')} 11:30",
      "activity": "上午大概在做什么",
      "availability": "available",
      "topic": "适合自然提起的话题"
    }}
  ],
  "reason": "一句短语，说明为什么这样更新，内部用"
}}"""


def _format_circadian_for_prompt(profile: CircadianProfile | None, now: datetime) -> str:
    """把作息画像格式化成一段供 prompt 用的描述。"""
    if profile is None or profile.sample_size < 30:
        return "（无可用画像，沿用默认作息：清醒 8:00-23:00；如对方实际是夜猫子或跨时区，请根据聊天内容自行调整）"
    is_weekend = now.weekday() >= 5
    label = "周末" if is_weekend else "工作日"
    hourly = profile.weekend_hourly if is_weekend else profile.weekday_hourly
    if not any(hourly):
        hourly = profile.hourly_activity
    # 取活跃度 >= 0.3 的小时列出来
    active_hours = [str(h) for h, v in enumerate(hourly) if v >= 0.3]
    active_text = "、".join(f"{h}点" for h in active_hours) if active_hours else "（数据稀疏）"
    start, end = profile.typical_awake_range
    return (
        f"- 通常清醒时段：{start:02d}:00 - {end:02d}:00\n"
        f"- 深夜活跃占比：{profile.night_owl_score:.0%}"
        f"（{'明显夜猫子' if profile.night_owl_score >= 0.4 else '偏晚睡型' if profile.night_owl_score >= 0.2 else '常规作息'}）\n"
        f"- 今天是{label}，历史上活跃的小时：{active_text}\n"
        f"- 备注：{profile.summary}"
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
    return any(p in text for p in patterns)


def _looks_woken_up(activity: str) -> bool:
    """activity 文本是否像"被叫醒/半醒"。

    用于 sleeping 状态下区分"在熟睡"与"被消息戳醒/半梦半醒"，
    后者下一次状态更新时间要压短，以便没新消息时自然回到熟睡。
    """
    if not activity:
        return False
    return any(p in activity for p in _WOKEN_ACTIVITY_PATTERNS)


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
