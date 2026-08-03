"""主动消息倾向分析的独立运行入口。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xuwen.analysis.proactive import analyze_proactive_openings, load_proactive_analysis
from xuwen.analysis.proactive_ai import (
    analyze_proactive_reasons,
    merge_cached_proactive_reasons,
)
from xuwen.analysis.storage import AnalysisStorage
from xuwen.chat_api.llm_client import LLMClient
from xuwen.config import Settings
from xuwen.core.models import Session
from xuwen.persona.generator import load_persona_dataset


@dataclass(slots=True)
class ProactiveAnalysisRunReport:
    source_files: int
    messages: int
    sessions: int
    openings: int
    friend_openings: int
    ai_analysis_status: str
    duration_seconds: float
    output_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


async def analyze_proactive_tendency(
    paths: Sequence[str | Path],
    settings: Settings,
    *,
    out_dir: str | Path | None = None,
    resume: bool = True,
    since: str | None = None,
    llm: LLMClient | None = None,
) -> ProactiveAnalysisRunReport:
    """单独生成主动消息倾向报告，不运行关系分析 Map/Reduce。"""
    started = time.perf_counter()
    dataset = await asyncio.to_thread(load_persona_dataset, paths, settings)
    sessions = _filter_sessions(dataset.sessions, since, settings.app_timezone)
    if not sessions:
        raise ValueError("没有可分析的聊天消息，请检查输入文件和 --since 范围")

    report = analyze_proactive_openings(
        sessions,
        session_gap_minutes=settings.session_gap_minutes,
        timezone=settings.app_timezone,
    )
    storage = AnalysisStorage(out_dir or settings.analysis_data_dir)
    output_path = storage.root / "proactive_analysis.json"
    if resume:
        report = merge_cached_proactive_reasons(
            report,
            load_proactive_analysis(output_path),
        )

    active_llm = llm
    owns_llm = False
    if settings.analysis_proactive_enabled and active_llm is None:
        active_llm = LLMClient(
            settings,
            api_url=settings.resolved_analysis_final_api_url,
            api_key=settings.resolved_analysis_final_api_key.get_secret_value(),
            timeout_seconds=settings.analysis_timeout_seconds,
            max_retries=3,
        )
        owns_llm = True
    try:
        if settings.analysis_proactive_enabled and active_llm is not None:
            report = await analyze_proactive_reasons(
                report,
                llm=active_llm,
                settings=settings,
            )
    finally:
        if owns_llm and active_llm is not None:
            await active_llm.aclose()

    storage.write_json(output_path, report)
    return ProactiveAnalysisRunReport(
        source_files=dataset.source_files,
        messages=sum(session.message_count for session in sessions),
        sessions=len(sessions),
        openings=report.opening_count,
        friend_openings=report.friend_initiative_count,
        ai_analysis_status=report.ai_analysis_status,
        duration_seconds=time.perf_counter() - started,
        output_path=str(output_path),
    )


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
            .replace(tzinfo=_timezone(timezone))
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


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
