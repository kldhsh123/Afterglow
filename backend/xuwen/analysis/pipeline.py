"""关系分析总流水线：加载、Map、分层 Reduce、增量落盘。"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xuwen.analysis.blocks import build_analysis_blocks
from xuwen.analysis.mapper import AnalysisBlockOutputError, AnalysisMapper
from xuwen.analysis.models import AnalysisBlock, BlockAnalysis, LifeProfile
from xuwen.analysis.reducers import (
    merge_month_locally,
    reduce_experimental,
    reduce_personality,
    reduce_timeline,
    render_experimental_profile_markdown,
    render_experimental_prompt_context,
    render_life_analysis_context,
    render_personality_markdown,
    render_personality_prompt_context,
)
from xuwen.analysis.storage import (
    EXPERIMENTAL_CACHE_VERSION,
    LIFE_CACHE_VERSION,
    AnalysisStorage,
)
from xuwen.config import Settings
from xuwen.core.models import Session
from xuwen.persona.generator import PersonaDataset, load_persona_dataset

ProgressCallback = Callable[[int, int, str], None]


class AnalysisPipelineError(RuntimeError):
    """分析未形成完整输入，禁止发布最终报告。"""


async def load_analysis_blocks(
    paths: Sequence[str | Path],
    settings: Settings,
    *,
    since: str | None = None,
) -> tuple[PersonaDataset, list[Session], list[AnalysisBlock]]:
    """加载并确定性构建分析块，供执行和 CLI 检查共用。"""
    dataset = await asyncio.to_thread(load_persona_dataset, paths, settings)
    sessions = _filter_sessions(dataset.sessions, since, settings.app_timezone)
    blocks = build_analysis_blocks(
        sessions,
        self_name=settings.self_name,
        friend_name=settings.friend_name,
        char_budget=settings.analysis_block_char_budget,
        timezone=settings.app_timezone,
    )
    return dataset, sessions, blocks


@dataclass(slots=True)
class AnalysisRunReport:
    source_files: int
    messages: int
    sessions: int
    blocks: int
    mapped: int
    resumed: int
    skipped: int
    failed: int
    duration_seconds: float
    timeline_path: str | None
    personality_path: str | None
    personality_markdown_path: str | None
    personality_prompt_path: str | None
    life_profile_path: str | None
    life_context_path: str | None
    experimental_path: str | None
    experimental_markdown_path: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


async def analyze_relationship(
    paths: Sequence[str | Path],
    settings: Settings,
    *,
    out_dir: str | Path | None = None,
    timeline: bool = True,
    personality: bool = True,
    resume: bool = True,
    since: str | None = None,
    progress_cb: ProgressCallback | None = None,
    mapper: AnalysisMapper | None = None,
) -> AnalysisRunReport:
    """执行一次离线分析；所有块完整成功后才发布最终报告。"""
    if not timeline and not personality:
        raise ValueError("timeline 和 personality 至少启用一个")
    started = time.perf_counter()
    dataset, sessions, blocks = await load_analysis_blocks(paths, settings, since=since)
    if not blocks:
        raise AnalysisPipelineError("没有可分析的聊天消息，请检查输入文件和 --since 范围")
    storage = AnalysisStorage(out_dir or settings.analysis_data_dir)
    experimental = settings.analysis_experimental_enabled
    storage.prepare(experimental=experimental)

    active_mapper = mapper or AnalysisMapper(settings)
    owns_mapper = mapper is None
    results: list[BlockAnalysis] = []
    pending: list[tuple[AnalysisBlock, BlockAnalysis | None]] = []
    resumed = 0
    for block in blocks:
        cached = storage.load_block(block.block_id) if resume else None
        experimental_cached = (
            storage.load_experimental_block(block.block_id)
            if resume and experimental
            else None
        )
        if cached is not None and experimental_cached is not None:
            cached.experimental_requested = True
            cached.experimental_schema_version = (
                experimental_cached.experimental_schema_version
            )
            cached.experimental_signals = experimental_cached.experimental_signals
        if (
            cached is not None
            and cached.life_schema_version == LIFE_CACHE_VERSION
            and (not experimental or experimental_cached is not None)
        ):
            results.append(cached)
            resumed += 1
        else:
            pending.append((block, cached))

    semaphore = asyncio.Semaphore(max(1, settings.analysis_max_concurrency))
    rate_lock = asyncio.Lock()
    next_request_at = 0.0
    failures: list[dict[str, str]] = []
    skipped_failures: list[dict[str, str]] = []
    done = resumed

    async def wait_for_rate_slot() -> None:
        nonlocal next_request_at
        interval = max(0.0, settings.analysis_request_interval_seconds)
        if interval <= 0:
            return
        async with rate_lock:
            now = time.perf_counter()
            if next_request_at > now:
                await asyncio.sleep(next_request_at - now)
                now = time.perf_counter()
            next_request_at = max(now, next_request_at) + interval

    async def run_block(
        item: tuple[AnalysisBlock, BlockAnalysis | None],
    ) -> tuple[BlockAnalysis | None, dict[str, str] | None]:
        block, cached_normal = item
        async with semaphore:
            await wait_for_rate_slot()
            result = cached_normal
            try:
                result = result or await active_mapper.map_block(
                    block, experimental=False
                )
                if result.life_schema_version != LIFE_CACHE_VERSION:
                    await wait_for_rate_slot()
                    life_result = await active_mapper.map_life_block(block)
                    result = result.model_copy(
                        update={
                            "life_schema_version": LIFE_CACHE_VERSION,
                            "life_habits": life_result.life_habits,
                        },
                        deep=True,
                    )
                if experimental and (
                    not result.experimental_requested
                    or result.experimental_schema_version != EXPERIMENTAL_CACHE_VERSION
                ):
                    await wait_for_rate_slot()
                    experimental_result = await active_mapper.map_experimental_block(block)
                    result = result.model_copy(
                        update={
                            "experimental_requested": True,
                            "experimental_schema_version": EXPERIMENTAL_CACHE_VERSION,
                            "experimental_signals": experimental_result.experimental_signals,
                        },
                        deep=True,
                    )
                storage.save_block(result)
                (storage.root / "failures" / f"{block.block_id}.json").unlink(
                    missing_ok=True
                )
                return result, None
            except asyncio.CancelledError:
                raise
            except AnalysisBlockOutputError as exc:
                debug_path = storage.write_json(
                    storage.root / "failures" / f"{block.block_id}.json",
                    {
                        "schema_version": 1,
                        "block": block.model_dump(mode="json"),
                        "stage": exc.stage,
                        "attempts": [
                            {"error": error, "raw_output": raw}
                            for error, raw in zip(
                                exc.errors, exc.raw_outputs, strict=False
                            )
                        ],
                    },
                )
                skipped_result = result or BlockAnalysis(
                    block_id=block.block_id,
                    start_time_ms=block.start_time_ms,
                    end_time_ms=block.end_time_ms,
                )
                skipped_result = skipped_result.model_copy(
                    update={
                        "life_schema_version": LIFE_CACHE_VERSION,
                        "life_habits": [],
                    },
                    deep=True,
                )
                if experimental and (
                    not skipped_result.experimental_requested
                    or skipped_result.experimental_schema_version
                    != EXPERIMENTAL_CACHE_VERSION
                ):
                    skipped_result = skipped_result.model_copy(
                        update={
                            "experimental_requested": True,
                            "experimental_schema_version": EXPERIMENTAL_CACHE_VERSION,
                            "experimental_signals": [],
                        },
                        deep=True,
                    )
                storage.save_block(skipped_result)
                return skipped_result, {
                    "block_id": block.block_id,
                    "error": type(exc).__name__,
                    "message": _safe_failure_message(exc),
                    "debug_path": str(debug_path),
                    "skipped": "true",
                }
            except Exception as exc:
                return None, {
                    "block_id": block.block_id,
                    "error": type(exc).__name__,
                    "message": _safe_failure_message(exc),
                }

    if progress_cb:
        progress_cb(done, len(blocks), "map")
    try:
        tasks = [asyncio.create_task(run_block(item)) for item in pending]
        try:
            for task in asyncio.as_completed(tasks):
                result, failure = await task
                done += 1
                if result is not None:
                    results.append(result)
                if failure is not None:
                    if failure.get("skipped") == "true":
                        skipped_failures.append(failure)
                        if progress_cb:
                            progress_cb(done, len(blocks), "map")
                        continue
                    failures.append(failure)
                    for pending_task in tasks:
                        if not pending_task.done():
                            pending_task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    first = failures[0]
                    debug_suffix = (
                        f"调试文件：{first['debug_path']}。"
                        if first.get("debug_path")
                        else ""
                    )
                    raise AnalysisPipelineError(
                        "分析块失败，已取消本次剩余请求，未生成最终报告。"
                        f"错误块：{first['block_id']}；"
                        f"{first['error']}：{first['message']}。"
                        f"{debug_suffix}"
                        "已成功的块缓存会在重新运行时复用"
                    )
                if progress_cb:
                    progress_cb(done, len(blocks), "map")
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        results.sort(key=lambda item: (item.start_time_ms, item.block_id))
        reduce_inputs = await _hierarchical_results(
            results,
            message_count=sum(session.message_count for session in sessions),
            mapper=active_mapper,
            progress_cb=progress_cb,
            timezone=settings.app_timezone,
        )
        if progress_cb:
            progress_cb(0, 1, "reduce")

        timeline_path: Path | None = None
        personality_path: Path | None = None
        personality_markdown_path: Path | None = None
        personality_prompt_path: Path | None = None
        life_profile_path: Path | None = None
        life_context_path: Path | None = None
        experimental_path: Path | None = None
        experimental_markdown_path: Path | None = None
        message_count = sum(session.message_count for session in sessions)
        if timeline:
            timeline_report = await reduce_timeline(
                reduce_inputs,
                message_count=message_count,
                block_count=len(blocks),
                mapper=active_mapper,
            )
            timeline_path = storage.write_json(storage.root / "timeline.json", timeline_report)

        life_habits = [habit for result in reduce_inputs for habit in result.life_habits]
        life_profile = LifeProfile(
            habits=[
                habit
                for habit in life_habits
                if habit.subject == "friend"
                and not habit.sensitive_relationship_context
                and habit.target_fields
            ][:16]
        )
        if isinstance(active_mapper, AnalysisMapper):
            try:
                life_profile = await active_mapper.reduce_life_profile(life_habits)
            except Exception:
                pass
        life_profile_path = storage.write_json(
            storage.root / "life_profile.json", life_profile
        )
        life_context_path = storage.write_text(
            storage.root / "life_context.md",
            render_life_analysis_context(
                life_profile,
                friend_name=settings.friend_name,
            ),
        )

        if personality:
            personality_report = reduce_personality(reduce_inputs)
            if isinstance(active_mapper, AnalysisMapper):
                try:
                    personality_report = await active_mapper.reduce_personality_report(
                        personality_report
                    )
                except Exception:
                    pass
            personality_path = storage.write_json(
                storage.root / "personality_report.json", personality_report
            )
            personality_markdown_path = storage.write_text(
                storage.root / "personality_report.md",
                render_personality_markdown(personality_report),
            )
            personality_context = render_personality_prompt_context(personality_report)
            if settings.analysis_personality_prompt_enabled and isinstance(
                active_mapper, AnalysisMapper
            ):
                try:
                    optimized_context = await active_mapper.optimize_personality_context(
                        personality_report
                    )
                except Exception:
                    optimized_context = ""
                if optimized_context:
                    personality_context = (
                        "【人格画像参考（供主聊天模型内部使用，不要向用户复述）】\n"
                        "这是从历史聊天归纳的目标角色人格与关系互动倾向，不是固定事实或当前状态。\n"
                        "生活作息与当前活动由生活时间线负责；高推测内容由实验性人格画像负责。\n"
                        "当前对话和检索到的真人原文优先；不要提及本文件或主动试探画像结论。\n"
                        + optimized_context
                    )
            personality_prompt_path = storage.write_text(
                storage.root / "personality_prompt_context.md",
                personality_context,
            )
        if experimental:
            experimental_report = reduce_experimental(results)
            if isinstance(active_mapper, AnalysisMapper):
                try:
                    experimental_report = (
                        await active_mapper.reduce_experimental_report(
                            experimental_report
                        )
                    )
                except Exception:
                    pass
            # insights.json 保留为旧 API/工具兼容路径；新名称表达其实际用途。
            storage.write_json(
                storage.experimental_dir / "insights.json", experimental_report
            )
            experimental_path = storage.write_json(
                storage.experimental_dir / "personality_profile.json",
                experimental_report,
            )
            experimental_markdown_path = storage.write_text(
                storage.experimental_dir / "personality_profile.md",
                render_experimental_profile_markdown(experimental_report),
            )
            prompt_context = render_experimental_prompt_context(experimental_report)
            if settings.analysis_experimental_prompt_enabled and isinstance(
                active_mapper, AnalysisMapper
            ):
                try:
                    optimized_context = await active_mapper.optimize_experimental_context(
                        experimental_report
                    )
                except Exception:
                    optimized_context = ""
                if optimized_context:
                    prompt_context = (
                        "【实验性人格画像（供主聊天模型内部使用，不要向用户复述）】\n"
                        "以下允许根据历史聊天推测真实性格、对人态度、依恋、隐瞒、"
                        "操控意图和精神健康状态。\n"
                        "所有内容仍是概率假设，不是事实核验、蓄意认定或医学诊断；"
                        "当前对话和真人原文优先。\n"
                        "用途是补充人物内在倾向，不是回应策略清单，也不能据此发明"
                        "本轮未发生的事实。\n"
                        + optimized_context
                    )
            storage.write_text(storage.experimental_dir / "prompt_context.md", prompt_context)
        storage.write_json(
            storage.root / "manifest.json",
            {
                "schema_version": 1,
                "blocks": len(blocks),
                "completed": len(results),
                "failed": failures,
                "skipped": skipped_failures,
                "experimental": experimental,
            },
        )
        if progress_cb:
            progress_cb(1, 1, "reduce")
        return AnalysisRunReport(
            source_files=dataset.source_files,
            messages=message_count,
            sessions=len(sessions),
            blocks=len(blocks),
            mapped=len(results) - resumed - len(skipped_failures),
            resumed=resumed,
            skipped=len(skipped_failures),
            failed=len(failures),
            duration_seconds=time.perf_counter() - started,
            timeline_path=str(timeline_path) if timeline_path else None,
            personality_path=str(personality_path) if personality_path else None,
            personality_markdown_path=(
                str(personality_markdown_path) if personality_markdown_path else None
            ),
            personality_prompt_path=(
                str(personality_prompt_path) if personality_prompt_path else None
            ),
            life_profile_path=str(life_profile_path) if life_profile_path else None,
            life_context_path=str(life_context_path) if life_context_path else None,
            experimental_path=str(experimental_path) if experimental_path else None,
            experimental_markdown_path=(
                str(experimental_markdown_path) if experimental_markdown_path else None
            ),
        )
    finally:
        if owns_mapper:
            await active_mapper.aclose()


def _safe_failure_message(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    text = str(message if isinstance(message, str) and message else exc)
    return " ".join(text.split())[:300] or "未知错误"


async def _hierarchical_results(
    results: list[BlockAnalysis],
    *,
    message_count: int,
    mapper: AnalysisMapper,
    progress_cb: ProgressCallback | None,
    timezone: str,
) -> list[BlockAnalysis]:
    if message_count <= 50_000:
        return results
    by_month: dict[str, list[BlockAnalysis]] = defaultdict(list)
    tz = _get_timezone(timezone)
    for result in results:
        month = datetime.fromtimestamp(result.start_time_ms / 1000, tz=tz).strftime("%Y-%m")
        by_month[month].append(result)
    output: list[BlockAnalysis] = []
    total = len(by_month)
    for index, (month, month_results) in enumerate(sorted(by_month.items()), 1):
        reduced = await _reduce_month_in_batches(month, month_results, mapper)
        output.append(reduced)
        if progress_cb:
            progress_cb(index, total, "monthly_reduce")
    return output


async def _reduce_month_in_batches(
    month: str,
    results: list[BlockAnalysis],
    mapper: AnalysisMapper,
) -> BlockAnalysis:
    """每次最多归并 8 份结果，递归收敛，防止单月上下文仍然爆炸。"""
    current = results
    round_index = 0
    while len(current) > 1:
        next_round: list[BlockAnalysis] = []
        for batch_index in range(0, len(current), 8):
            batch = current[batch_index : batch_index + 8]
            if len(batch) == 1:
                next_round.append(batch[0])
                continue
            batch_label = f"{month}-r{round_index + 1}-{batch_index // 8 + 1}"
            try:
                reduced = await mapper.reduce_month(batch_label, batch)
            except Exception:
                reduced = merge_month_locally(batch_label, batch)
            next_round.append(reduced)
        current = next_round
        round_index += 1
    return current[0]


def _filter_sessions(
    sessions: list[Session],
    since: str | None,
    timezone: str,
) -> list[Session]:
    if not since:
        return sessions
    try:
        threshold = int(
            datetime.strptime(since, "%Y-%m")
            .replace(tzinfo=_get_timezone(timezone))
            .timestamp()
            * 1000
        )
    except ValueError as exc:
        raise ValueError("since 必须是 YYYY-MM 格式") from exc
    output: list[Session] = []
    for session in sessions:
        messages = [message for message in session.messages if message.timestamp_ms >= threshold]
        if not messages:
            continue
        output.append(
            Session(
                session_id=session.session_id,
                messages=messages,
                start_time_ms=messages[0].timestamp_ms,
                end_time_ms=messages[-1].timestamp_ms,
            )
        )
    return output


def _get_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
