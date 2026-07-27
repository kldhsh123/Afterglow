"""schedule_extractor 单元测试（Feature #9）。

覆盖：
- hint 抽取（chat_pipeline.extract_schedule_hints）
- 输出过滤剥离 <schedule-hint>
- ISO 8601 / RRULE 校验
- 模型输出解析（含 codefence / 杂质 / 解析失败）
- enabled=false 时直接返回空
- 并发 gather 失败降级
- 流式 AssistantOutputFilter 不在 schedule-hint 标签中间切开
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from xuwen.chat_api.chat_pipeline import extract_schedule_hints
from xuwen.chat_api.output_filter import (
    AssistantOutputFilter,
    sanitize_assistant_text,
)
from xuwen.chat_api.schedule_extractor import (
    _build_task_from_payload,
    _parse_extractor_output,
    _stable_id,
    _validate_iso_8601_with_tz,
    _validate_rrule,
    extract_schedule_tasks,
)
from xuwen.config import Settings

_TZ_BJ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 5, 31, 18, 30, tzinfo=_TZ_BJ)


# ---------- hint extraction & sanitize ----------


def test_extract_schedule_hints_basic() -> None:
    text = "好呀<schedule-hint>明天早上7点叫我起床</schedule-hint>明天见～"
    assert extract_schedule_hints(text) == ["明天早上7点叫我起床"]


def test_extract_schedule_hints_multiple_and_dedup() -> None:
    text = (
        "<schedule-hint>每天9点喝水</schedule-hint>"
        "<schedule-hint>每天9点喝水</schedule-hint>"  # 重复
        "<schedule-hint>下周一开会</schedule-hint>"
    )
    assert extract_schedule_hints(text) == ["每天9点喝水", "下周一开会"]


def test_extract_schedule_hints_respects_cap() -> None:
    parts = [f"<schedule-hint>第{i}个任务</schedule-hint>" for i in range(20)]
    hints = extract_schedule_hints("".join(parts), max_hints=3)
    assert len(hints) == 3
    assert hints == ["第0个任务", "第1个任务", "第2个任务"]


def test_extract_schedule_hints_none_when_absent() -> None:
    assert extract_schedule_hints("没有任何标签的回复") == []
    assert extract_schedule_hints("") == []


def test_sanitize_strips_schedule_hint() -> None:
    """用户可见文本里必须看不到 <schedule-hint> 块。"""
    assert (
        sanitize_assistant_text("你好<schedule-hint>明天叫我</schedule-hint>再见")
        == "你好再见"
    )


def test_sanitize_strips_multiline_hint() -> None:
    text = "答复：\n<schedule-hint>\n明天早上7点\n叫我起床\n</schedule-hint>\n完成"
    out = sanitize_assistant_text(text)
    assert "schedule-hint" not in out
    assert "起床" not in out  # 整块都该剥掉
    assert "答复" in out and "完成" in out


# ---------- streaming cut protection ----------


def test_stream_filter_does_not_cut_inside_schedule_hint() -> None:
    """流式过滤器不能把 <schedule-hint> 切一半发给前端。"""
    filt = AssistantOutputFilter()
    # 第一段刚好停在 hint 中间，应该完全留在 buffer 里
    out = filt.feed("好呀<schedule-hint>明天早上7")
    assert "schedule-hint" not in out
    # 喂入剩余部分
    out2 = filt.feed("点叫我起床</schedule-hint>")
    assert "schedule-hint" not in out2
    # flush 收尾：整个 hint 块被剥掉
    rest = filt.flush()
    assert "schedule-hint" not in rest
    assert "起床" not in rest
    # 完整可见文本（feed 输出 + flush 输出）只剩自然语句
    visible = (out + out2 + rest).strip()
    assert visible == "好呀"
    # raw_text 保留原文供后端抽取
    assert "<schedule-hint>" in filt.raw_text()
    assert "明天早上7点叫我起床" in filt.raw_text()


@pytest.mark.parametrize(
    "open_tag,close_tag",
    [
        ("<SCHEDULE-HINT>", "</SCHEDULE-HINT>"),  # 全大写
        ("<Schedule-Hint>", "</Schedule-Hint>"),  # 首字母大写
        ("<sChEdUlE-hInT>", "</sChEdUlE-hInT>"),  # 大小写混合
    ],
)
def test_stream_filter_case_insensitive_schedule_hint(
    open_tag: str, close_tag: str
) -> None:
    """Finding 2：流式守卫必须与 IGNORECASE 正则对齐，否则大写变体会泄漏。"""
    filt = AssistantOutputFilter()
    out1 = filt.feed(f"好呀{open_tag}明天早上7点叫我起床")
    out2 = filt.feed(close_tag)
    rest = filt.flush()
    visible = out1 + out2 + rest
    assert "schedule" not in visible.lower(), f"标签泄漏：{visible!r}"
    assert "起床" not in visible, f"hint 内容泄漏：{visible!r}"
    assert visible.strip() == "好呀"


@pytest.mark.parametrize(
    "open_tag,close_tag",
    [
        ("<LIFE-UPDATE>", "</LIFE-UPDATE>"),
        ("<Life-Update>", "</Life-Update>"),
    ],
)
def test_stream_filter_case_insensitive_life_update(
    open_tag: str, close_tag: str
) -> None:
    """Finding 2 同样修复了 <life-update> 的大小写敏感泄漏（顺带收益）。"""
    filt = AssistantOutputFilter()
    out1 = filt.feed(f'你好{open_tag}{{"mood":"开心"}}')
    out2 = filt.feed(close_tag)
    rest = filt.flush()
    visible = out1 + out2 + rest
    assert "life-update" not in visible.lower(), f"标签泄漏：{visible!r}"
    assert "mood" not in visible, f"内部协议泄漏：{visible!r}"
    assert visible.strip() == "你好"


# ---------- ISO / RRULE validators ----------


@pytest.mark.parametrize(
    "value",
    [
        "2026-06-01T07:00:00+08:00",
        "2026-06-01T07:00+08:00",
        "2026-12-31T23:59:00Z",
        "2026-06-01T07:00:00.500+08:00",
    ],
)
def test_iso_validator_accepts(value: str) -> None:
    assert _validate_iso_8601_with_tz(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-06-01",  # 缺时间
        "2026-06-01 07:00:00+08:00",  # 缺 T
        "2026/06/01T07:00:00+08:00",  # 错分隔符
        "2026-06-01T07:00:00",  # 缺时区
        "明天早上7点",
        None,
        123,
    ],
)
def test_iso_validator_rejects(value: object) -> None:
    assert _validate_iso_8601_with_tz(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("FREQ=DAILY", "FREQ=DAILY"),
        ("FREQ=DAILY;BYHOUR=7;BYMINUTE=0", "FREQ=DAILY;BYHOUR=7;BYMINUTE=0"),
        ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("freq=daily;byhour=7", "FREQ=DAILY;BYHOUR=7"),  # 小写自动转换
    ],
)
def test_rrule_validator_accepts(value: str, expected: str) -> None:
    assert _validate_rrule(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "null",
        "every monday",
        "FREQ=BOGUS",  # 不在合法 FREQ 枚举
        "BYHOUR=7",  # 没 FREQ
        "FREQ=DAILY;NONSENSE=1",  # 含非法关键字
    ],
)
def test_rrule_validator_rejects_or_passthrough(value: object) -> None:
    """None / 空 / 非法 → None（一次性任务的合法表达）。"""
    assert _validate_rrule(value) is None


# ---------- model output parsing ----------


def test_parse_extractor_output_strips_codefence() -> None:
    raw = '```json\n{"trigger_at": "2026-06-01T07:00:00+08:00", "message": "起床"}\n```'
    result = _parse_extractor_output(raw)
    assert result is not None
    assert result["trigger_at"] == "2026-06-01T07:00:00+08:00"


def test_parse_extractor_output_returns_none_on_garbage() -> None:
    assert _parse_extractor_output("") is None
    assert _parse_extractor_output("不是 JSON") is None
    assert _parse_extractor_output("{bad json") is None


def test_build_task_oneshot() -> None:
    task = _build_task_from_payload(
        {
            "trigger_at": "2026-06-01T07:00:00+08:00",
            "recurrence": None,
            "message": "起床啦",
            "title": "晨起",
        }
    )
    assert task is not None
    assert task.trigger_at == "2026-06-01T07:00:00+08:00"
    assert task.recurrence is None
    assert task.message == "起床啦"
    assert task.title == "晨起"
    assert task.id.startswith("t_")
    assert task.source == "extractor"


def test_stable_id_is_deterministic() -> None:
    """同 (trigger_at, recurrence, message) → 同 ID（Finding 4: 幂等去重）。"""
    a = _stable_id("2026-06-01T07:00:00+08:00", None, "起床啦")
    b = _stable_id("2026-06-01T07:00:00+08:00", None, "起床啦")
    assert a == b
    assert a.startswith("t_")
    assert len(a) == 10  # "t_" + 8 hex


def test_stable_id_differs_when_content_differs() -> None:
    base = _stable_id("2026-06-01T07:00:00+08:00", None, "起床啦")
    diff_time = _stable_id("2026-06-02T07:00:00+08:00", None, "起床啦")
    diff_msg = _stable_id("2026-06-01T07:00:00+08:00", None, "喝水啦")
    diff_rec = _stable_id("2026-06-01T07:00:00+08:00", "FREQ=DAILY", "起床啦")
    assert len({base, diff_time, diff_msg, diff_rec}) == 4


def test_build_task_id_is_stable_across_calls() -> None:
    """重试同一请求时第三方拿到同一个 ID 才能正确去重。"""
    payload = {
        "trigger_at": "2026-06-01T07:00:00+08:00",
        "recurrence": None,
        "message": "起床啦",
    }
    t1 = _build_task_from_payload(payload)
    t2 = _build_task_from_payload(payload)
    assert t1 is not None and t2 is not None
    assert t1.id == t2.id


def test_build_task_recurring() -> None:
    task = _build_task_from_payload(
        {
            "trigger_at": "2026-06-01T07:00:00+08:00",
            "recurrence": "FREQ=DAILY;BYHOUR=7;BYMINUTE=0",
            "message": "起床啦",
        }
    )
    assert task is not None
    assert task.recurrence == "FREQ=DAILY;BYHOUR=7;BYMINUTE=0"


def test_build_task_unparseable_dropped() -> None:
    assert _build_task_from_payload({"unparseable": True}) is None


def test_build_task_drops_invalid_time() -> None:
    assert (
        _build_task_from_payload(
            {"trigger_at": "明天早上", "message": "起床"}
        )
        is None
    )


def test_build_task_drops_empty_message() -> None:
    assert (
        _build_task_from_payload(
            {"trigger_at": "2026-06-01T07:00:00+08:00", "message": ""}
        )
        is None
    )


def test_build_task_drops_invalid_rrule_keeps_oneshot() -> None:
    """非法 RRULE → recurrence=None，但仍返回一次性任务。"""
    task = _build_task_from_payload(
        {
            "trigger_at": "2026-06-01T07:00:00+08:00",
            "recurrence": "every monday",  # 非法
            "message": "起床",
        }
    )
    assert task is not None
    assert task.recurrence is None  # drop 非法 RRULE，但保留 task


def test_build_task_truncates_long_message_and_title() -> None:
    task = _build_task_from_payload(
        {
            "trigger_at": "2026-06-01T07:00:00+08:00",
            "message": "x" * 500,
            "title": "y" * 100,
        }
    )
    assert task is not None
    assert len(task.message) == 240
    assert len(task.title) == 24


# ---------- extract_schedule_tasks 端到端（mock LLM）----------


@pytest.fixture
def _settings() -> Settings:
    s = Settings.model_construct()
    s.schedule_extract_enabled = True
    s.schedule_temperature = 0.1
    s.schedule_max_tokens = 400
    s.schedule_max_hints_per_turn = 5
    s.schedule_extract_timeout_seconds = 10.0
    # 直接打补丁让 resolved_schedule_model 返回非空
    return s


@pytest.mark.asyncio
async def test_extract_schedule_tasks_disabled_returns_empty(_settings: Settings) -> None:
    _settings.schedule_extract_enabled = False
    llm = AsyncMock()
    out = await extract_schedule_tasks(["明天叫我"], llm=llm, settings=_settings, now=_NOW)
    assert out == []
    llm.complete_chat.assert_not_called()


@pytest.mark.asyncio
async def test_extract_schedule_tasks_no_model_returns_empty(
    monkeypatch: pytest.MonkeyPatch, _settings: Settings
) -> None:
    # resolved_schedule_model 返回空字符串
    monkeypatch.setattr(
        type(_settings),
        "resolved_schedule_model",
        property(lambda self: ""),
    )
    llm = AsyncMock()
    out = await extract_schedule_tasks(["明天叫我"], llm=llm, settings=_settings, now=_NOW)
    assert out == []


@pytest.mark.asyncio
async def test_extract_schedule_tasks_happy_path(
    monkeypatch: pytest.MonkeyPatch, _settings: Settings
) -> None:
    monkeypatch.setattr(
        type(_settings),
        "resolved_schedule_model",
        property(lambda self: "test-model"),
    )
    llm = AsyncMock()
    llm.complete_chat.return_value = (
        '{"trigger_at": "2026-06-01T07:00:00+08:00", '
        '"recurrence": null, "message": "起床啦", "title": "晨起"}'
    )
    out = await extract_schedule_tasks(
        ["明天早上7点叫我起床"], llm=llm, settings=_settings, now=_NOW
    )
    assert len(out) == 1
    assert out[0].trigger_at == "2026-06-01T07:00:00+08:00"
    assert out[0].message == "起床啦"
    assert out[0].recurrence is None
    llm.complete_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_schedule_tasks_llm_exception_silenced(
    monkeypatch: pytest.MonkeyPatch, _settings: Settings
) -> None:
    """单条 LLM 调用抛异常时，对应条 drop，其它条不受影响。"""
    monkeypatch.setattr(
        type(_settings),
        "resolved_schedule_model",
        property(lambda self: "test-model"),
    )
    llm = AsyncMock()
    # 第一条抛错，第二条返回合法 JSON
    llm.complete_chat.side_effect = [
        RuntimeError("network boom"),
        '{"trigger_at": "2026-06-02T09:00:00+08:00", "message": "喝水"}',
    ]
    out = await extract_schedule_tasks(
        ["第一条", "第二条"], llm=llm, settings=_settings, now=_NOW
    )
    assert len(out) == 1
    assert out[0].message == "喝水"


@pytest.mark.asyncio
async def test_extract_schedule_tasks_unparseable_dropped(
    monkeypatch: pytest.MonkeyPatch, _settings: Settings
) -> None:
    monkeypatch.setattr(
        type(_settings),
        "resolved_schedule_model",
        property(lambda self: "test-model"),
    )
    llm = AsyncMock()
    llm.complete_chat.return_value = '{"unparseable": true}'
    out = await extract_schedule_tasks(["这话听不懂"], llm=llm, settings=_settings, now=_NOW)
    assert out == []


@pytest.mark.asyncio
async def test_extract_schedule_tasks_caps_hints(
    monkeypatch: pytest.MonkeyPatch, _settings: Settings
) -> None:
    """超过 schedule_max_hints_per_turn 的 hint 会被截断，不调用多余 LLM。"""
    _settings.schedule_max_hints_per_turn = 2
    monkeypatch.setattr(
        type(_settings),
        "resolved_schedule_model",
        property(lambda self: "test-model"),
    )
    llm = AsyncMock()
    llm.complete_chat.return_value = (
        '{"trigger_at": "2026-06-01T07:00:00+08:00", "message": "x"}'
    )
    out = await extract_schedule_tasks(
        ["a", "b", "c", "d", "e"], llm=llm, settings=_settings, now=_NOW
    )
    # 调用数 == 截断后 hint 数
    assert llm.complete_chat.await_count == 2
    assert len(out) == 2


@pytest.mark.asyncio
async def test_extract_schedule_tasks_timeout_fails_open(
    monkeypatch: pytest.MonkeyPatch, _settings: Settings
) -> None:
    """Finding 6：小模型 endpoint 卡死时整批超时，fail-open 返回 []，不阻塞主回复。"""
    monkeypatch.setattr(
        type(_settings),
        "resolved_schedule_model",
        property(lambda self: "test-model"),
    )
    _settings.schedule_extract_timeout_seconds = 0.1  # 100ms 紧预算便于测试

    async def _hang(*_args: object, **_kwargs: object) -> str:
        await asyncio.sleep(5.0)  # 远超 100ms 预算，模拟卡死的 endpoint
        return '{"trigger_at": "2026-06-01T07:00:00+08:00", "message": "起床"}'

    llm = AsyncMock()
    llm.complete_chat.side_effect = _hang

    started = time.perf_counter()
    out = await extract_schedule_tasks(
        ["明天叫我"], llm=llm, settings=_settings, now=_NOW
    )
    elapsed = time.perf_counter() - started
    # 应在 ~timeout 内返回，远小于卡死的 5s
    assert elapsed < 1.0, f"超时未生效，实际耗时 {elapsed:.2f}s"
    assert out == [], "超时应当 fail-open 返回空列表"


def test_sanitize_strips_unterminated_schedule_hint() -> None:
    """Finding 7: max_tokens 截断时，<schedule-hint> 没有 close tag 也要剥离。"""
    # 模拟模型在协议块末尾被截断的回复
    out = sanitize_assistant_text("好呀<schedule-hint>明天早上7点叫我起床")
    assert "schedule-hint" not in out.lower()
    assert "起床" not in out  # 截断的 hint 内容也应剥掉
    assert out == "好呀"


def test_sanitize_strips_empty_open_schedule_hint() -> None:
    """只有开标签、内容尚未输出的极端截断场景。"""
    assert sanitize_assistant_text("好呀<schedule-hint>") == "好呀"


def test_sanitize_strips_orphan_close_schedule_hint() -> None:
    """孤立的关闭标签（理论上不会出现，但要防御）。"""
    assert sanitize_assistant_text("好呀</schedule-hint>") == "好呀"


def test_sanitize_strips_multiple_unterminated_schedule_hints() -> None:
    """多个开标签但没有 close（罕见的级联截断）。"""
    out = sanitize_assistant_text("a<schedule-hint>x<schedule-hint>y")
    assert "schedule-hint" not in out.lower()
    assert out == "a"


def test_sanitize_preserves_text_around_paired_block_still() -> None:
    """新正则不能破坏成对块的现有行为。"""
    out = sanitize_assistant_text("正常<schedule-hint>明天叫我</schedule-hint>结束")
    assert out == "正常结束"


def test_sanitize_strips_unterminated_life_update() -> None:
    """Finding 7 同样修复 <life-update> 的截断泄漏。"""
    out = sanitize_assistant_text('好呀<life-update>{"mood":"开心"')
    assert "life-update" not in out.lower()
    assert "mood" not in out
    assert out == "好呀"


def test_stream_flush_strips_unterminated_schedule_hint() -> None:
    """流式 flush 收尾时如果遇到截断的 hint，也要剥干净。"""
    filt = AssistantOutputFilter()
    # 第一段：含开标签 + 部分内容；feed 会回退保留开标签在 buffer 里
    filt.feed("好呀<schedule-hint>明天早上7点叫我起床")
    # 模型在此处被 max_tokens 切断，不再有 close 标签
    rest = filt.flush()
    # flush 输出不能包含任何 schedule-hint 残留
    assert "schedule-hint" not in rest.lower()
    assert "起床" not in rest
    # raw_text 应保留原文（供调试/extractor 决策，但 chat_pipeline 的 extractor 正则
    # 要求成对 close，截断的 hint 会被静默丢弃——这是期望行为）
    assert "<schedule-hint>" in filt.raw_text()
