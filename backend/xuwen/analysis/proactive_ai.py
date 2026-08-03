"""用分析模型解释双方每次重新开聊的动机与时间原因。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter

from pydantic import BaseModel, Field, ValidationError

from xuwen.analysis.models import (
    Evidence,
    ProactiveAnalysisReport,
    ProactiveOpeningRecord,
    ProactiveReasonCategory,
)
from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings

logger = logging.getLogger(__name__)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class _OpeningAttribution(BaseModel):
    opening_id: str
    reason_category: ProactiveReasonCategory = "unknown"
    reason_summary: str = Field(default="", max_length=500)
    time_explanation: str = Field(default="", max_length=500)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=4)
    confidence: float = Field(default=0.2, ge=0, le=1)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=4)


async def analyze_proactive_reasons(
    report: ProactiveAnalysisReport,
    *,
    llm: LLMClient,
    settings: Settings,
    batch_size: int | None = None,
) -> ProactiveAnalysisReport:
    """补全尚未分析的开场归因；单批失败时保留其它批次结果。"""
    pending = [opening for opening in report.openings if opening.reason_category is None]
    if not pending:
        return _finalize_status(report)

    configured_size = settings.analysis_proactive_batch_size if batch_size is None else batch_size
    size = max(1, min(12, configured_size))
    batches = [pending[offset : offset + size] for offset in range(0, len(pending), size)]
    semaphore = asyncio.Semaphore(settings.analysis_proactive_max_concurrency)

    async def analyze_batch(
        batch: list[ProactiveOpeningRecord],
    ) -> list[_OpeningAttribution]:
        try:
            async with semaphore:
                return await _analyze_batch(
                    batch,
                    all_openings=report.openings,
                    llm=llm,
                    settings=settings,
                )
        except Exception:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                logger.info(
                    "主动开聊原因分析批次解析失败，将 %d 条拆为 %d + %d 条重试",
                    len(batch),
                    midpoint,
                    len(batch) - midpoint,
                )
                left, right = await asyncio.gather(
                    analyze_batch(batch[:midpoint]),
                    analyze_batch(batch[midpoint:]),
                )
                return [*left, *right]
            logger.warning("主动开聊原因分析批次失败，保留未分析状态", exc_info=True)
            return []

    batch_results = await asyncio.gather(*(analyze_batch(batch) for batch in batches))
    analyzed = {
        item.opening_id: item
        for results in batch_results
        for item in results
    }

    output: list[ProactiveOpeningRecord] = []
    for opening in report.openings:
        attribution = analyzed.get(opening.opening_id)
        if attribution is None:
            output.append(opening)
            continue
        evidence = _ground_evidence(opening, attribution.evidence_quotes)
        output.append(
            opening.model_copy(
                update={
                    "reason_category": attribution.reason_category,
                    "reason_summary": attribution.reason_summary.strip(),
                    "time_explanation": attribution.time_explanation.strip(),
                    "reason_evidence": evidence,
                    "reason_confidence": attribution.confidence,
                    "reason_alternative_explanations": [
                        item.strip()
                        for item in attribution.alternative_explanations
                        if item.strip()
                    ][:4],
                },
                deep=True,
            )
        )
    return _finalize_status(report.model_copy(update={"openings": output}, deep=True))


def merge_cached_proactive_reasons(
    current: ProactiveAnalysisReport,
    cached: ProactiveAnalysisReport | None,
) -> ProactiveAnalysisReport:
    """按稳定 opening_id 复用已有 AI 归因，内容变化时不复用。"""
    if cached is None:
        return current
    cached_by_id = {opening.opening_id: opening for opening in cached.openings}
    output: list[ProactiveOpeningRecord] = []
    for opening in current.openings:
        old = cached_by_id.get(opening.opening_id)
        if (
            old is None
            or old.content != opening.content
            or old.initiator != opening.initiator
            or old.reason_category is None
        ):
            output.append(opening)
            continue
        output.append(
            opening.model_copy(
                update={
                    "reason_category": old.reason_category,
                    "reason_summary": old.reason_summary,
                    "time_explanation": old.time_explanation,
                    "reason_evidence": old.reason_evidence,
                    "reason_confidence": old.reason_confidence,
                    "reason_alternative_explanations": old.reason_alternative_explanations,
                },
                deep=True,
            )
        )
    return _finalize_status(current.model_copy(update={"openings": output}, deep=True))


async def _analyze_batch(
    openings: list[ProactiveOpeningRecord],
    *,
    all_openings: list[ProactiveOpeningRecord],
    llm: LLMClient,
    settings: Settings,
) -> list[_OpeningAttribution]:
    payload = [_opening_payload(opening, all_openings) for opening in openings]
    messages = [
        {
            "role": "system",
            "content": (
                "你分析私人聊天中双方为什么重新开聊，以及为什么可能选择这个时间。"
                "只能依据输入，严格输出 JSON；不得把相关性写成确定因果。"
            ),
        },
        {
            "role": "user",
            "content": (
                "逐条分析 records。reason_category 只能是 continue_topic、event_trigger、"
                "care、self_share、question、emotional_need、routine、greeting、playful、"
                "affection、other、unknown。reason_summary 解释发起者为什么开聊；"
                "time_explanation 解释为什么可能在该时点开聊，必须区分消息中的直接时间线索、"
                "历史时段规律和无法判断。仅凭几点钟不能推断作息或心理动机；证据不足就写 unknown。"
                "evidence_quotes 只能逐字引用 previous_tail、opening_content 或 first_response 中存在的短句。"
                "alternative_explanations 至少给出一种其它可能，confidence 按证据强弱给 0~1。"
                "返回 {\"analyses\":[{\"opening_id\":\"...\","
                "\"reason_category\":\"unknown\",\"reason_summary\":\"...\","
                "\"time_explanation\":\"...\",\"evidence_quotes\":[\"...\"],"
                "\"confidence\":0.0,\"alternative_explanations\":[\"...\"]}]}。\n"
                + json.dumps({"records": payload}, ensure_ascii=False)
            ),
        },
    ]
    last_error: ValueError | None = None
    for attempt in range(2):
        raw = await llm.complete_chat(
            messages,
            GenerationParams(
                temperature=0.0 if attempt else min(0.2, settings.analysis_temperature),
                max_tokens=settings.analysis_final_max_tokens,
            ),
            model=settings.resolved_analysis_final_model,
            stage=(
                "analysis.proactive.reasons"
                if attempt == 0
                else "analysis.proactive.reasons.repair"
            ),
        )
        try:
            return _parse_batch_attributions(raw, openings)
        except ValueError as exc:
            last_error = exc
            if attempt == 0:
                messages.extend(
                    [
                        {"role": "assistant", "content": raw[:6_000]},
                        {
                            "role": "user",
                            "content": (
                                "上一个回答不是要求的 JSON。不要解释，不要使用 Markdown；"
                                "只返回符合既定结构的一个 JSON 对象，并覆盖输入中的每个 opening_id。"
                            ),
                        },
                    ]
                )
    raise ValueError("主动开聊原因分析修复后仍无有效 JSON") from last_error


def _parse_batch_attributions(
    raw: str,
    openings: list[ProactiveOpeningRecord],
) -> list[_OpeningAttribution]:
    obj = _parse_json_object(raw)
    items = obj.get("analyses")
    if not isinstance(items, list):
        raise ValueError("主动开聊原因分析缺少 analyses 数组")
    allowed_ids = {opening.opening_id for opening in openings}
    output: list[_OpeningAttribution] = []
    seen: set[str] = set()
    for item in items:
        try:
            parsed = _OpeningAttribution.model_validate(item)
        except ValidationError:
            continue
        if parsed.opening_id not in allowed_ids or parsed.opening_id in seen:
            continue
        output.append(parsed)
        seen.add(parsed.opening_id)
    if len(output) != len(allowed_ids):
        raise ValueError(
            f"主动开聊原因分析返回条目不完整：期望 {len(allowed_ids)}，实际 {len(output)}"
        )
    return output


def _opening_payload(
    opening: ProactiveOpeningRecord,
    all_openings: list[ProactiveOpeningRecord],
) -> dict[str, object]:
    same_initiator = [item for item in all_openings if item.initiator == opening.initiator]
    hour_count = sum(1 for item in same_initiator if item.hour == opening.hour)
    weekday_count = sum(1 for item in same_initiator if item.weekday == opening.weekday)
    return {
        "opening_id": opening.opening_id,
        "initiator": opening.initiator,
        "occurred_at": opening.occurred_at,
        "weekday": opening.weekday,
        "idle_gap_minutes": opening.idle_gap_minutes,
        "previous_tail": opening.previous_tail[:300],
        "opening_content": opening.content[:800],
        "first_response": opening.response_excerpt[:300],
        "same_initiator_total": len(same_initiator),
        "same_hour_count": hour_count,
        "same_weekday_count": weekday_count,
    }


def _ground_evidence(
    opening: ProactiveOpeningRecord,
    quotes: list[str],
) -> list[Evidence]:
    source = "\n".join(
        [opening.previous_tail, opening.content, opening.response_excerpt]
    )
    evidence: list[Evidence] = []
    for raw in quotes:
        quote = " ".join(raw.split()).strip()[:300]
        if not quote or quote not in source or any(item.quote == quote for item in evidence):
            continue
        evidence.append(
            Evidence(
                quote=quote,
                session_id=opening.session_id,
                date=opening.occurred_at[:10],
            )
        )
        if len(evidence) >= 4:
            break
    return evidence


def _finalize_status(report: ProactiveAnalysisReport) -> ProactiveAnalysisReport:
    analyzed = [opening for opening in report.openings if opening.reason_category is not None]
    if not report.openings:
        status = "completed"
    elif len(analyzed) == len(report.openings):
        status = "completed"
    elif analyzed:
        status = "partial"
    else:
        status = "failed"
    reason_counts = Counter(opening.reason_category for opening in analyzed)
    return report.model_copy(
        update={
            "ai_analysis_status": status,
            "ai_analyzed_count": len(analyzed),
            "reason_counts": dict(reason_counts),
        },
        deep=True,
    )


def _parse_json_object(raw: str) -> dict[str, object]:
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if match is None:
            raise ValueError("模型未返回 JSON") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError("模型返回的 JSON 无法解析") from exc
    if not isinstance(parsed, dict):
        raise ValueError("模型输出根节点必须是对象")
    return parsed
