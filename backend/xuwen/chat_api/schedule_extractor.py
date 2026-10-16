"""定时任务提取小模型。

主聊天模型在回复里输出 `<schedule-hint>明天早上7点叫我起床</schedule-hint>`
表达定时任务意图；本模块负责调用小模型把自然语言转为结构化 `ScheduleTask`：

- `trigger_at` 永远是 ISO 8601 含时区的【绝对】时间（首次触发时间）
- `recurrence` 是 iCalendar RRULE 子集；一次性任务为 None
- 失败、超时、解析错误均降级为空列表，永不影响主回复链路

设计要点：
- 复用项目"小模型 + reuse 链"模式：`SCHEDULE → LABEL → RESPONSE_POLICY → LIFE → 主 LLM`
- 失败 fail-open：任何异常都吞掉，返回已成功的部分（或空列表）
- 严格 JSON 解析 + 字段级校验，避免脏数据进入响应
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.chat_api.schemas import ScheduleTask
from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.core.metrics import MetricsRecorder

logger = logging.getLogger(__name__)

# ISO 8601 含时区：允许 +HH:MM / -HH:MM / Z；秒可选
_ISO_8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

# RRULE 关键字（iCalendar RFC 5545 子集）；用于轻量校验
_RRULE_PART_RE = re.compile(
    r"^(FREQ|INTERVAL|COUNT|UNTIL|BYSECOND|BYMINUTE|BYHOUR|BYDAY|BYMONTHDAY|BYMONTH|BYYEARDAY|BYWEEKNO|BYSETPOS|WKST)=[A-Z0-9,;:\-+]+$"
)
_RRULE_FREQ_VALUES = frozenset(
    {"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
)

_SYSTEM_PROMPT = (
    "Extract a scheduling request in ANY language into a single JSON object. Output JSON only, "
    "with no explanation, Markdown or code fences.\nUse the supplied now (with timezone) as the "
    "sole time reference, never your training date.\nFields:\n- trigger_at: absolute ISO 8601 "
    "timestamp WITH timezone, e.g. 2026-06-01T07:00:00+08:00. For recurring tasks this is the "
    "FIRST future occurrence.\n- recurrence: an iCalendar RRULE string, or null for a one-off "
    "task. Daily: FREQ=DAILY; Monday: FREQ=WEEKLY;BYDAY=MO; weekdays: "
    "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR; first of each month: FREQ=MONTHLY;BYMONTHDAY=1.\n- message:"
    " short (at most 80 characters), warm, natural text to send AT trigger_at. Use the request's "
    "language. At delivery the requested time has arrived: do not repeat relative or absolute "
    "timing such as tomorrow, next week, at 7 or in 5 minutes. Example: 'Wake up, time to get "
    "going!' rather than 'Tomorrow at 7, wake up'. For a water reminder: 'Time for some water!' "
    "rather than 'It is 9 now'.\n- title: optional short title, at most 12 characters, in the "
    "request's language.\nRecurring words (every day/week/month, weekdays/weekends) require "
    "recurrence. One-off expressions (tomorrow, next Monday, in 5 minutes, 17:00) require "
    'recurrence=null.\nIf the time cannot be resolved, return {"unparseable": true}. Preserve the '
    "user's task meaning without inventing details."
)

_USER_TEMPLATE = (
    'now: {now_iso}\nhint: {hint}\n\nReturn only JSON matching this schema:\n{{"trigger_at":"ISO 8601'
    ' with timezone","recurrence":null,"message":"short text in the request\'s '
    'language","title":"optional short title"}}\nUse an RRULE string instead of null for recurring'
    ' tasks. If unparseable: {{"unparseable": true}}'
)


def _stable_id(trigger_at: str, recurrence: str | None, message: str) -> str:
    """Feature #9：稳定哈希 ID，便于第三方做幂等去重。

    同一请求被重试（同 trigger_at + recurrence + message）时返回相同 ID，
    避免第三方为同一逻辑任务创建多个定时器。
    不同时刻产生不同 trigger_at 时 ID 也会变（正确区分不同任务）。
    """
    payload = f"{trigger_at}|{recurrence or ''}|{message}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return "t_" + digest[:8]


def _strip_codefence(text: str) -> str:
    """模型偶尔会用 ```json ... ``` 包裹；这里抹掉。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def _parse_extractor_output(raw: str) -> dict[str, Any] | None:
    """从模型输出抽 JSON 对象；失败返回 None。"""
    text = _strip_codefence(raw)
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate_iso_8601_with_tz(value: object) -> str | None:
    """非空字符串 + 正则 + datetime.fromisoformat 三重校验。"""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if not _ISO_8601_RE.match(v):
        return None
    # 进一步用 fromisoformat 校验（Python 3.11+ 支持 Z 后缀）
    try:
        # 兼容 3.10：把 Z 换成 +00:00
        normalized = v.replace("Z", "+00:00") if v.endswith("Z") else v
        datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return v


def _validate_rrule(value: object) -> str | None:
    """轻量 RRULE 校验：检查关键字 + FREQ 合法。

    None / 空串 / "null" → 返回 None（合法的一次性任务）；
    存在但格式错 → 返回 None（drop 这个字段，仍可返回一次性 task）。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or v.lower() == "null":
        return None
    # 允许大小写
    v_up = v.upper()
    parts = [p for p in v_up.split(";") if p]
    if not parts:
        return None
    has_freq = False
    for part in parts:
        if not _RRULE_PART_RE.match(part):
            return None
        if part.startswith("FREQ="):
            freq = part[5:]
            if freq not in _RRULE_FREQ_VALUES:
                return None
            has_freq = True
    if not has_freq:
        return None
    return v_up


def _build_task_from_payload(payload: dict[str, Any]) -> ScheduleTask | None:
    """把模型输出的 dict → ScheduleTask；任何字段不合法就 drop 整条。"""
    if payload.get("unparseable"):
        return None
    trigger_at = _validate_iso_8601_with_tz(payload.get("trigger_at"))
    if not trigger_at:
        return None
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    title = payload.get("title")
    title_str = title.strip() if isinstance(title, str) else ""
    recurrence = _validate_rrule(payload.get("recurrence"))
    msg_clean = message.strip()[:240]
    return ScheduleTask(
        id=_stable_id(trigger_at, recurrence, msg_clean),
        trigger_at=trigger_at,
        message=msg_clean,
        title=title_str[:24],
        recurrence=recurrence,
        source="extractor",
    )


async def _extract_one(
    hint: str,
    *,
    llm: LLMClient,
    model: str,
    settings: Settings,
    now: datetime,
    trace_id: str,
    metrics: MetricsRecorder | None,
) -> ScheduleTask | None:
    """单条 hint → 单条 ScheduleTask（或 None）。"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _USER_TEMPLATE.format(
                now_iso=now.isoformat(timespec="seconds"),
                hint=hint.strip()[:200],
            ),
        },
    ]
    params = GenerationParams(
        temperature=settings.schedule_temperature,
        max_tokens=settings.schedule_max_tokens,
    )
    try:
        raw = await llm.complete_chat(
            messages,
            params,
            model=model,
            trace_id=trace_id,
            stage="schedule.extract",
            metrics=metrics,
        )
    except Exception:
        logger.warning(content_text("schedule_extractor call failed"), exc_info=True)
        return None
    payload = _parse_extractor_output(raw)
    if not payload:
        return None
    return _build_task_from_payload(payload)


async def extract_schedule_tasks(
    hints: list[str],
    *,
    llm: LLMClient,
    settings: Settings,
    now: datetime,
    trace_id: str = "",
    metrics: MetricsRecorder | None = None,
) -> list[ScheduleTask]:
    """并发解析多条 hint → ScheduleTask 列表。

    截断到 schedule_max_hints_per_turn；解析失败的条目静默丢弃。
    任何顶层异常都返回空列表，永不向上抛——schedule_tasks 只是增益字段。
    """
    if not hints:
        return []
    if not settings.schedule_extract_enabled:
        return []
    capped = hints[: max(1, settings.schedule_max_hints_per_turn)]
    model = settings.resolved_schedule_model
    if not model:
        # 没有可用模型（用户什么都没配）→ 静默退出
        return []
    # 整批超时：避免小模型 endpoint 慢/不通时阻塞主回复（Finding 6）。
    # LLMClient 单次默认 60s timeout，N 条并发最坏也接近 60s——这里用更紧的总预算
    # 兜底，到点未完成则 fail-open 返回空列表，主回复正常发出。
    # 不做下限钳制：用户若显式配 0 或极小值，等价于"禁用 extractor"，与
    # SCHEDULE_EXTRACT_ENABLED=false 等价的快路径，是合理用法。
    timeout = settings.schedule_extract_timeout_seconds
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    _extract_one(
                        h,
                        llm=llm,
                        model=model,
                        settings=settings,
                        now=now,
                        trace_id=trace_id,
                        metrics=metrics,
                    )
                    for h in capped
                ),
                return_exceptions=True,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning(
            content_text(
                "schedule_extractor timed out after %.1fs; continuing with an empty task list"
            ),
            timeout,
        )
        return []
    except Exception:
        logger.warning(content_text("schedule_extractor gather failed"), exc_info=True)
        return []
    tasks: list[ScheduleTask] = []
    for r in results:
        if isinstance(r, ScheduleTask):
            tasks.append(r)
    return tasks
