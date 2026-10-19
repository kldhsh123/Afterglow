"""共享 Map 阶段：一次模型调用提取关系、人格与生活结构化候选。"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from xuwen.analysis.models import (
    AnalysisBlock,
    BlockAnalysis,
    EventCandidate,
    Evidence,
    ExperimentalReport,
    ExperimentalSignal,
    LifeHabit,
    LifeProfile,
    Observation,
    PersonalityReport,
    ReportSection,
    TimelineEvent,
    TimelinePhase,
)
from xuwen.analysis.storage import LIFE_CACHE_VERSION
from xuwen.chat_api.llm_client import GenerationParams, LLMClient
from xuwen.config import Settings
from xuwen.content_i18n import content_text
from xuwen.core.errors import LLMError
from xuwen.error_i18n import error_text
from xuwen.prompt_language import output_language, structured_output_instruction

logger = logging.getLogger(__name__)


class AnalysisBlockOutputError(ValueError):
    """模型返回不可解析的块级输出，并保留本地诊断信息。"""

    def __init__(
        self,
        block_id: str,
        *,
        stage: str,
        errors: list[str],
        raw_outputs: list[str],
    ) -> None:
        self.block_id = block_id
        self.stage = stage
        self.errors = errors
        self.raw_outputs = raw_outputs
        detail = errors[-1] if errors else error_text("Unknown parsing error")
        super().__init__(
            error_text(
                "Analysis block {block} repeatedly returned invalid JSON; last error: {reason}",
                block=block_id,
                reason=detail,
            )
        )


class AnalysisMapper:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        final_llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings
        self._owned_llm = llm is None
        self.llm = llm or LLMClient(
            settings,
            api_url=settings.resolved_analysis_api_url,
            api_key=settings.resolved_analysis_api_key.get_secret_value(),
            timeout_seconds=settings.analysis_timeout_seconds,
            max_retries=3,
        )
        self._owned_final_llm = final_llm is None and llm is None
        if final_llm is not None:
            self.final_llm = final_llm
        elif llm is not None:
            # 单元测试或调用方显式注入同一个 client 时保持原有行为。
            self.final_llm = llm
        else:
            self.final_llm = LLMClient(
                settings,
                api_url=settings.resolved_analysis_final_api_url,
                api_key=settings.resolved_analysis_final_api_key.get_secret_value(),
                timeout_seconds=settings.analysis_timeout_seconds,
                max_retries=3,
            )

    async def aclose(self) -> None:
        if self._owned_llm:
            await self.llm.aclose()
        if self._owned_final_llm and self.final_llm is not self.llm:
            await self.final_llm.aclose()

    async def map_block(self, block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
        messages = [
            {
                "role": "system",
                "content": (
                    "Analyze chat records using only the supplied text. Return strict JSON; never invent facts."
                    + "\n"
                    + structured_output_instruction()
                ),
            },
            {"role": "user", "content": self._map_prompt(block, experimental=experimental)},
        ]
        last_error: Exception | None = None
        errors: list[str] = []
        raw_outputs: list[str] = []
        for attempt in range(2):
            try:
                raw = await self.llm.complete_chat(
                    messages,
                    GenerationParams(
                        temperature=self.settings.analysis_temperature,
                        max_tokens=self.settings.analysis_max_tokens,
                    ),
                    model=self.settings.resolved_analysis_model,
                    stage="analysis.map" if attempt == 0 else "analysis.map.repair",
                )
            except LLMError as exc:
                if _is_model_refusal_error(exc):
                    raise AnalysisBlockOutputError(
                        block.block_id,
                        stage="map",
                        errors=[f"LLMError: {exc.message}"],
                        raw_outputs=[],
                    ) from exc
                raise
            try:
                return _coerce_block_result(raw, block, experimental=experimental)
            except (ValueError, ValidationError) as exc:
                last_error = exc
                raw_outputs.append(raw)
                errors.append(f"{type(exc).__name__}: {exc}")
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"The previous output could not be parsed: {type(exc).__name__}: {exc}. "
                            "Return complete, more concise JSON only. Shorten claim, summary and quote; "
                            "respect array limits and retain all required root and item fields."
                        ),
                    }
                )
                if _looks_like_model_refusal(raw):
                    break
        raise AnalysisBlockOutputError(
            block.block_id,
            stage="map",
            errors=errors,
            raw_outputs=raw_outputs,
        ) from last_error

    async def map_life_block(self, block: AnalysisBlock) -> BlockAnalysis:
        """独立提取生活候选；长期稳定性留给最终大模型跨块判断。"""
        messages = [
            {
                "role": "system",
                "content": (
                    (
                        "Extract candidate daily states of the target character from private chat records. Use only "
                        "the input and return strict JSON. Never recast the user's routines or joint interactions as "
                        "the character's own habits."
                    )
                    + "\n"
                    + structured_output_instruction()
                ),
            },
            {"role": "user", "content": self._life_prompt(block)},
        ]
        last_error: Exception | None = None
        errors: list[str] = []
        raw_outputs: list[str] = []
        for attempt in range(2):
            try:
                raw = await self.llm.complete_chat(
                    messages,
                    GenerationParams(
                        temperature=self.settings.analysis_temperature,
                        max_tokens=self.settings.analysis_max_tokens,
                    ),
                    model=self.settings.resolved_analysis_model,
                    stage=("analysis.life.map" if attempt == 0 else "analysis.life.map.repair"),
                )
            except LLMError as exc:
                if _is_model_refusal_error(exc):
                    raise _refusal_output_error(
                        block,
                        stage="life_map",
                        exc=exc,
                    ) from exc
                raise
            try:
                obj = _parse_json_object(raw)
                raw_habits = obj.get("life_habits")
                habits = _validated_items(raw_habits, LifeHabit)
                if isinstance(raw_habits, list) and raw_habits and not habits:
                    raise ValueError(
                        error_text(
                            "life_habits is populated, but none of its entries match the schema"
                        )
                    )
                return BlockAnalysis(
                    output_language=output_language(),
                    block_id=block.block_id,
                    start_time_ms=block.start_time_ms,
                    end_time_ms=block.end_time_ms,
                    life_schema_version=LIFE_CACHE_VERSION,
                    life_habits=habits,
                )
            except (ValueError, ValidationError) as exc:
                last_error = exc
                raw_outputs.append(raw)
                errors.append(f"{type(exc).__name__}: {exc}")
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            'The previous output could not be parsed. Return only complete JSON: {"life_habits": [...]}. Shorten text and include at most 10 entries.'
                        ),
                    }
                )
                if _looks_like_model_refusal(raw):
                    break
        raise AnalysisBlockOutputError(
            block.block_id,
            stage="life_map",
            errors=errors,
            raw_outputs=raw_outputs,
        ) from last_error

    async def map_experimental_block(self, block: AnalysisBlock) -> BlockAnalysis:
        """独立提取实验信号，避免和普通 Map 争用输出预算。"""
        messages = [
            {
                "role": "system",
                "content": (
                    (
                        "Collect candidate evidence for experimental personality hypotheses for a final model. Return"
                        " strict JSON. Personality, attachment, concealment, manipulation intent and mental-health "
                        "hypotheses are permitted, but a single block supports only candidates; retain uncertainty "
                        "and alternative explanations."
                    )
                    + "\n"
                    + structured_output_instruction()
                ),
            },
            {"role": "user", "content": self._experimental_prompt(block)},
        ]
        last_error: Exception | None = None
        errors: list[str] = []
        raw_outputs: list[str] = []
        for attempt in range(2):
            try:
                raw = await self.llm.complete_chat(
                    messages,
                    GenerationParams(
                        temperature=self.settings.analysis_temperature,
                        max_tokens=self.settings.analysis_max_tokens,
                    ),
                    model=self.settings.resolved_analysis_model,
                    stage=(
                        "analysis.experimental.map"
                        if attempt == 0
                        else "analysis.experimental.map.repair"
                    ),
                )
            except LLMError as exc:
                if _is_model_refusal_error(exc):
                    raise _refusal_output_error(
                        block,
                        stage="experimental_map",
                        exc=exc,
                    ) from exc
                raise
            try:
                return _coerce_block_result(raw, block, experimental=True)
            except (ValueError, ValidationError) as exc:
                last_error = exc
                raw_outputs.append(raw)
                errors.append(f"{type(exc).__name__}: {exc}")
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": "The previous output could not be parsed. Return only a JSON object containing an experimental_signals array.",
                    }
                )
                if _looks_like_model_refusal(raw):
                    break
        raise AnalysisBlockOutputError(
            block.block_id,
            stage="experimental_map",
            errors=errors,
            raw_outputs=raw_outputs,
        ) from last_error

    async def reduce_month(self, month: str, results: list[BlockAnalysis]) -> BlockAnalysis:
        """超大库的第一级归并；失败由上层回退为直接拼接。"""
        payload = [_compact_result(result) for result in results]
        prompt = (
            f"Merge block analyses for {month}. Deduplicate events and observations while preserving "
            "evidence, counterexamples and alternative explanations. Merge observations only when "
            "their subjects match and their meanings overlap; never change self/both/relationship "
            "into friend. Return the Map schema with events, personality_observations, "
            "relationship_signals, life_habits and experimental_signals.\nInput:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        raw = await self.llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Merge structured analysis, returning strict JSON."
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            GenerationParams(temperature=0.1, max_tokens=self.settings.analysis_max_tokens),
            model=self.settings.resolved_analysis_model,
            stage="analysis.reduce.month",
        )
        block = AnalysisBlock(
            block_id=f"month-{month}",
            start_time_ms=min(result.start_time_ms for result in results),
            end_time_ms=max(result.end_time_ms for result in results),
            session_ids=[],
            message_count=0,
            text="",
        )
        return _coerce_block_result(
            raw,
            block,
            experimental=any(result.experimental_requested for result in results),
        )

    async def propose_phases(self, events: list[TimelineEvent]) -> list[TimelinePhase]:
        compact = [
            {
                "event_id": event.event_id,
                "date": event.date,
                "title": event.title,
                "type": event.type,
                "summary": event.summary,
            }
            for event in events
        ]
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Divide the relationship into phases based only on the supplied event timeline. Return strict JSON."
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (
                            "Group events into consecutive, non-overlapping relationship phases. Name each phase for its "
                            "change, such as meeting, growing closer, stability or distance. Do not force extra phases. "
                            'Return {"phases":[{"title":"phase name","start_date":"YYYY-MM-DD","end_date":"YYYY-MM-'
                            'DD","summary":"overview","event_ids":["evt-id"]}]}.\n'
                        )
                        + json.dumps(compact, ensure_ascii=False)
                    ),
                },
            ],
            GenerationParams(temperature=0.2, max_tokens=self.settings.analysis_max_tokens),
            model=self.settings.resolved_analysis_final_model,
            stage="analysis.reduce.timeline",
        )
        obj = _parse_json_object(raw)
        phases = obj.get("phases", [])
        if not isinstance(phases, list):
            raise ValueError(error_text("The phase output is missing the phases array"))
        valid_ids = {event.event_id for event in events}
        output: list[TimelinePhase] = []
        for item in phases:
            try:
                phase = TimelinePhase.model_validate(item)
            except ValidationError:
                continue
            phase.event_ids = [event_id for event_id in phase.event_ids if event_id in valid_ids]
            if phase.event_ids:
                output.append(phase)
        return output

    async def reduce_personality_report(self, report: PersonalityReport) -> PersonalityReport:
        """由最终大模型重组完整普通人格报告，保留结构化证据。"""
        observations = [
            {
                "source_section": section.title,
                **observation.model_dump(mode="json"),
            }
            for section in report.sections
            for observation in section.observations
        ][:60]
        if not observations:
            return report
        source_observations = [
            observation for section in report.sections for observation in section.observations
        ]
        allowed_evidence = {
            evidence.quote: evidence
            for observation in source_observations
            for evidence in observation.evidence
        }
        allowed_claims = {observation.claim for observation in source_observations}
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        (
                            "Create a descriptive personality report about the target character from structured chat "
                            "observations. Return strict JSON. Do not analyze the user, invent evidence, diagnose mental "
                            "disorders or assert deliberate manipulation."
                        )
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (
                            "Merge observations across periods and organize meaningful sections, such as core "
                            "personality, communication/emotion, interpersonal style, attitude toward the user, "
                            "values/boundaries. Omit unsupported empty sections. Preserve subject, dimension, claim, "
                            "evidence, confidence, counterexamples and alternative_explanations. Personality observations"
                            " must have subject=friend; relationship observations may use friend/both/relationship. At "
                            "most 8 sections and 12 observations per section. Return "
                            '{"summary":"...","sections":[{"key":"...","title":"...","observations":[...]}]}.\n'
                        )
                        + json.dumps(observations, ensure_ascii=False)
                    ),
                },
            ],
            GenerationParams(
                temperature=0.15,
                max_tokens=self.settings.analysis_final_max_tokens,
            ),
            model=self.settings.resolved_analysis_final_model,
            stage="analysis.reduce.personality_report",
        )
        obj = _parse_json_object(raw)
        sections = _validated_items(obj.get("sections"), ReportSection)
        valid_sections: list[ReportSection] = []
        for section in sections[:8]:
            grounded: list[Observation] = []
            for observation in section.observations:
                evidence = [
                    allowed_evidence[item.quote]
                    for item in observation.evidence
                    if item.quote in allowed_evidence
                ][:6]
                if not evidence and observation.claim not in allowed_claims:
                    continue
                if observation.subject not in {"friend", "both", "relationship"}:
                    continue
                grounded.append(observation.model_copy(update={"evidence": evidence}, deep=True))
            if grounded:
                valid_sections.append(
                    section.model_copy(update={"observations": grounded[:12]}, deep=True)
                )
        if not valid_sections:
            return report
        return PersonalityReport(
            summary=_clean_generated_text(obj.get("summary"), 1200),
            sections=valid_sections,
        )

    async def optimize_personality_context(self, report: PersonalityReport) -> str:
        """让最终大模型生成去证据的普通人格画像。"""
        observations: list[dict[str, Any]] = []
        item_id = 0
        for section in report.sections:
            section_observations = sorted(
                (
                    observation
                    for observation in section.observations
                    if observation.confidence >= 0.3
                ),
                key=lambda observation: (observation.confidence, len(observation.evidence)),
                reverse=True,
            )[:6]
            for observation in section_observations:
                observations.append(
                    {
                        "item_id": item_id,
                        "section_key": section.key,
                        "section_title": section.title,
                        "subject": observation.subject,
                        "dimension": observation.dimension,
                        "claim": observation.claim,
                        "confidence": observation.confidence,
                        "counterexamples": observation.counterexamples[:2],
                        "alternative_explanations": observation.alternative_explanations[:2],
                    }
                )
                item_id += 1
        if not observations:
            return ""
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        (
                            "Write an internal personality profile for the main chat model from structured descriptive "
                            "observations. Return strict JSON. Do not include original quotations, diagnoses or response "
                            "strategies."
                        )
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (
                            "Use the existing report sections to organize supported core traits, communication/emotion, "
                            "interpersonal style, attitude toward the user and values/boundaries. Preserve probabilities,"
                            " conditions, counterexamples and alternative explanations. Do not include chat quotations, "
                            "session_id, response suggestions, trigger strategies or prohibitions. Return "
                            '{"profile":"..."}.\n'
                        )
                        + json.dumps(observations, ensure_ascii=False)
                    ),
                },
            ],
            GenerationParams(
                temperature=0.15,
                max_tokens=min(2500, self.settings.analysis_final_max_tokens),
            ),
            model=self.settings.resolved_analysis_final_model,
            stage="analysis.reduce.personality_context",
        )
        profile = str(_parse_json_object(raw).get("profile") or "").strip()
        return profile[:10000]

    async def optimize_experimental_context(self, report: ExperimentalReport) -> str:
        """让最终大模型把全局信号组织为去证据的实验人格画像。"""
        signals = [
            {
                "signal_id": index,
                "subject": signal.subject,
                "category": signal.category,
                "claim": signal.claim,
                "inference_basis": signal.inference_basis,
                "conditions": signal.conditions,
                "confidence": signal.confidence,
                "counterexamples": signal.counterexamples[:2],
                "alternative_explanations": signal.alternative_explanations[:2],
            }
            for index, signal in enumerate(report.signals)
            if signal.subject == "friend" and signal.confidence > 0.3
        ][:20]
        if not signals:
            return ""
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        (
                            "Write an experimental personality profile from globally merged chat signals. Return strict "
                            "JSON. Personality, attachment, concealment, manipulation intent and mental-health hypotheses"
                            " are permitted, but preserve probabilistic wording, conditions, counterexamples and "
                            "alternatives. Never fabricate quotations."
                        )
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (
                            "Create an internal personality profile for the main chat model. Organize supported "
                            "hypotheses about core traits, interpersonal style, attachment, concealment/distortion, "
                            "manipulation intent and mental health. Omit unsupported sections. Include no response "
                            "suggestions, trigger strategies, prohibitions, quotations or session_id. State that these "
                            'are historical hypotheses, not current facts. Return {"profile":"..."}, at most 5000 '
                            "characters.\n"
                        )
                        + json.dumps(signals, ensure_ascii=False)
                    ),
                },
            ],
            GenerationParams(
                temperature=0.25,
                max_tokens=min(5000, self.settings.analysis_final_max_tokens),
            ),
            model=self.settings.resolved_analysis_final_model,
            stage="analysis.reduce.experimental_context",
        )
        profile = str(_parse_json_object(raw).get("profile") or "").strip()
        return profile[:10000]

    async def reduce_experimental_report(self, report: ExperimentalReport) -> ExperimentalReport:
        """跨块归并候选线索，形成带证据的最终实验报告。"""
        if not report.signals:
            return report
        allowed_evidence = {
            evidence.quote: evidence for signal in report.signals for evidence in signal.evidence
        }
        allowed_claims = {signal.claim for signal in report.signals}
        candidates = [signal.model_dump(mode="json") for signal in report.signals[:40]]
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        (
                            "Merge experimental personality hypotheses using evidence across periods. Return strict JSON."
                            " Possible underlying traits, interpersonal attitudes, attachment, concealment, manipulation "
                            "intent and mental-health explanations must remain hypotheses with counterevidence."
                        )
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (
                            "Merge block-level candidates into a final experimental profile. Repeated evidence from "
                            "distinct periods may increase confidence; isolated signals warrant low confidence. Analyze "
                            "only friend. Preserve representative evidence, inference_basis, conditions, counterexamples "
                            "and alternative_explanations; at most 24 signals. Allowed categories: personality_hypothesis"
                            "/interpersonal_style/attachment/deception_pattern/manipulation_intent/mental_health_hypothes"
                            "is/internal_contradiction. Mental-health labels are only possible explanations, never "
                            "diagnoses. For concealment or manipulation hypotheses, include a non-malicious alternative. "
                            'Return {"summary":"global overview","experimental_signals":[{"subject":"friend","category":"'
                            '...","claim":"...","inference_basis":"...","conditions":["..."],"evidence":[...],"confidence'
                            '":0.0,"counterexamples":["..."],"alternative_explanations":["..."]}]}.\n'
                        )
                        + json.dumps(candidates, ensure_ascii=False)
                    ),
                },
            ],
            GenerationParams(
                temperature=0.15,
                max_tokens=self.settings.analysis_final_max_tokens,
            ),
            model=self.settings.resolved_analysis_final_model,
            stage="analysis.reduce.experimental",
        )
        obj = _parse_json_object(raw)
        signals = _validated_items(obj.get("experimental_signals"), ExperimentalSignal)
        friend_signals: list[ExperimentalSignal] = []
        for signal in signals:
            evidence = [
                allowed_evidence[item.quote]
                for item in signal.evidence
                if item.quote in allowed_evidence
            ][:6]
            if signal.subject != "friend":
                continue
            if not evidence and signal.claim not in allowed_claims:
                continue
            friend_signals.append(signal.model_copy(update={"evidence": evidence}, deep=True))
            if len(friend_signals) >= 24:
                break
        summary = _clean_generated_text(obj.get("summary"), 1200)
        return (
            ExperimentalReport(summary=summary, signals=friend_signals)
            if friend_signals
            else report
        )

    async def reduce_life_profile(self, habits: list[LifeHabit]) -> LifeProfile:
        """由最终大模型跨时段归并结构化生活规律。"""
        source_habits = [
            habit
            for habit in habits
            if habit.subject == "friend"
            and not habit.sensitive_relationship_context
            and habit.target_fields
        ]
        if not source_habits:
            return LifeProfile()
        payload = [
            {"source_id": source_id, **habit.model_dump(mode="json")}
            for source_id, habit in enumerate(source_habits[:200])
        ]
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Merge the target character's long-term daily patterns. Return strict JSON. Keep only their own non-sensitive routines supported across periods."
                        + "\n"
                        + structured_output_instruction()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        (
                            "Merge candidates into sleep/meal/activity/availability patterns. Deduplicate while retaining"
                            " time conditions, contexts, target_fields, counterexamples and alternatives. Never turn user"
                            " habits, joint interactions or sensitive relationship activities into personal daily "
                            "routines. target_fields must be daily_plan/current_activity/recent_meal/availability/next_up"
                            "date_at/reply_delay_seconds/topic_seed. At most 16 habits. Each final habit must cite at "
                            "least one existing source_id in source_ids; prefer multiple sources from different "
                            "days/blocks for stable patterns. Do not return evidence: code restores exact evidence from "
                            'source_ids. Return {"summary":"...","habits":[{"source_ids":[0,1],"category":"sleep","claim"'
                            ':"...","time_patterns":["..."],"contexts":["..."],"target_fields":["..."],"confidence":0.0,"'
                            'counterexamples":["..."],"alternative_explanations":["..."]}]}.\n'
                        )
                        + json.dumps(payload, ensure_ascii=False)
                    ),
                },
            ],
            GenerationParams(
                temperature=0.15,
                max_tokens=self.settings.analysis_final_max_tokens,
            ),
            model=self.settings.resolved_analysis_final_model,
            stage="analysis.reduce.life_profile",
        )
        obj = _parse_json_object(raw)
        valid: list[LifeHabit] = []
        raw_habits = obj.get("habits")
        output_habits = raw_habits if isinstance(raw_habits, list) else []
        for item in output_habits:
            if not isinstance(item, dict):
                continue
            source_ids = _valid_source_ids(item.get("source_ids"), len(payload))
            if not source_ids:
                continue
            sources = [source_habits[source_id] for source_id in source_ids]
            source_evidence = _unique_evidence(
                [evidence for source in sources for evidence in source.evidence]
            )[:6]
            target_fields = (
                item.get("target_fields")
                or list(
                    dict.fromkeys(field for source in sources for field in source.target_fields)
                )[:5]
            )
            try:
                habit = LifeHabit.model_validate(
                    {
                        **item,
                        "subject": "friend",
                        "target_fields": target_fields,
                        "evidence": [
                            evidence.model_dump(mode="json") for evidence in source_evidence
                        ],
                        "sensitive_relationship_context": False,
                    }
                )
            except ValidationError:
                continue
            valid.append(habit)
            if len(valid) >= 16:
                break
        return LifeProfile(
            summary=_clean_generated_text(obj.get("summary"), 800),
            habits=valid,
        )

    def _map_prompt(self, block: AnalysisBlock, *, experimental: bool) -> str:
        output_fields = "events, personality_observations, relationship_signals"
        experimental_instruction = ""
        if experimental:
            output_fields += ", experimental_signals"
            experimental_instruction = (
                "\n        experimental_signals categories: personality_hypothesis, interpersonal_style, "
                "attachment,\n        deception_pattern, manipulation_intent, mental_health_hypothesis, "
                "internal_contradiction.\n        Each requires subject=friend, claim, inference_basis, "
                "conditions, evidence, confidence(0..1),\n        counterexamples and at least one "
                "alternative_explanations entry. Inference is not fact,\n        proof of intent or a "
                "diagnosis.\n        "
            )
        return f"""Analyze this two-person chat. Return a JSON object with root fields:
        {output_fields}.
        events entries: date(YYYY-MM-DD), title, type, summary, importance(1..5),
        evidence([{{quote,session_id,date}}]), session_ids.
        type: milestone/conflict/reconciliation/intimacy/shared_activity/emotional_shift/separation/daily/other.
        Extract everyday events only when representative of the relationship. At most 12 events.
        personality_observations describe only {self.settings.friend_name or "Friend"}: descriptive Big Five
        tendencies, communication/emotion, values/boundaries. Exclude sleep, meals and daily activities.
        relationship_signals describe initiative, response quality, care, conflict and change over time.
        Be specific about contact, replies, investment and shared activities, rather than generic traits.
        Give alternative explanations for period-specific habits.
        Both observation arrays require subject, dimension, claim, evidence, confidence(0..1),
        counterexamples and alternative_explanations. Quote source text exactly. Include known
        counterexamples. Omit unsupported observations. At most 8 entries per array.
        Allowed subjects: friend/self/both/relationship/unknown. personality_observations must be friend.
        Do not analyze the user's own habits. Put joint interaction patterns in relationship_signals,
        whose subject must be friend/both/relationship, never self/unknown. Shared activities do not
        prove the character has a personal daily routine.
        {experimental_instruction}
        Keep empty arrays for fields without evidence. No Markdown or explanation.
        Block ID: {block.block_id}
        Session IDs: {", ".join(block.session_ids)}
        Chat records:
        {block.text}
        """

    def _life_prompt(self, block: AnalysisBlock) -> str:
        return f"""Extract daily-life candidates only for {self.settings.friend_name or "Friend"}. Return:
        {{"life_habits":[{{"subject":"friend","category":"sleep",
        "claim":"...","time_patterns":["..."],"contexts":["..."],
        "target_fields":["daily_plan","availability"],
        "evidence":[{{"quote":"...","session_id":"...","date":"..."}}],"confidence":0.0,
        "counterexamples":["..."],"alternative_explanations":["..."],
        "sensitive_relationship_context":false}}]}}
        Rules:
        - These are block-level candidates; the final model judges stability across periods.
        - category must be sleep (sleep/waking/naps/active hours), meal (meal timing/plans),
          activity (work/study/travel/leisure), or availability (busy/free/online/reply timing).
        - subject must be friend. Exclude the user's states, joint activities and ambiguous subjects.
        - Sensitive or relationship-specific activities may be audit candidates only when marked
          sensitive_relationship_context=true; they will not enter the daily-life timeline.
        - A single explicit state warrants low confidence; repetition may increase it. Include exact
          short quotations with dates. At most 10 entries; each quote at most 120 characters.
        - Return an empty array when unsupported. No explanation or Markdown.
        Block ID: {block.block_id}
        Session IDs: {", ".join(block.session_ids)}
        Chat records:
        {block.text}
        """

    def _experimental_prompt(self, block: AnalysisBlock) -> str:
        return f"""Extract experimental personality candidates from this two-person chat. Return JSON only:
        {{"experimental_signals":[{{"subject":"friend","category":"personality_hypothesis","claim":"...","inference_basis":"...","conditions":["..."],"evidence":[{{"quote":"...","session_id":"...","date":"..."}}],"confidence":0.0,"counterexamples":["..."],"alternative_explanations":["..."]}}]}}
        Rules:
        - Analyze only {self.settings.friend_name or "Friend"}, subject=friend. Do not analyze the user
          or turn shared patterns into individual traits.
        - These are conservative block-level candidates, not final conclusions; the final model judges
          stability across periods. At most 6 candidates. Everyday content requires personality relevance.
        - category must be one of:
          personality_hypothesis: possible core traits, emotion regulation, self-presentation contrasts;
          interpersonal_style: attitudes to others, closeness differences, respect, care or distance;
          attachment: possible attachment tendencies, approach/withdrawal and relationship triggers;
          deception_pattern: possible concealment/exaggeration/minimization/changed accounts. One
          contradiction does not establish habitual lying;
          manipulation_intent: hypotheses about pressure, guilt, privacy, money or control to influence others;
          mental_health_hypothesis: possible compatibility with mental-health states/symptoms, never diagnoses;
          internal_contradiction: unexplained differences in the record, not proof of deliberate deception.
        - Each entry requires inference_basis, conditions, exact short quotations, counterexamples and
          at least one alternative_explanations entry. If no counterexample exists, state that this block
          contains none. Keep all uncertainty explicit.
        Block ID: {block.block_id}
        Session IDs: {", ".join(block.session_ids)}
        Chat records:
        {block.text}
        """


def _coerce_block_result(raw: str, block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
    obj = _parse_json_object(raw)
    return BlockAnalysis(
        output_language=output_language(),
        block_id=block.block_id,
        start_time_ms=block.start_time_ms,
        end_time_ms=block.end_time_ms,
        experimental_requested=experimental,
        events=_validated_items(obj.get("events"), EventCandidate),
        personality_observations=_validated_items(obj.get("personality_observations"), Observation),
        relationship_signals=_validated_items(obj.get("relationship_signals"), Observation),
        life_habits=_validated_items(obj.get("life_habits"), LifeHabit),
        experimental_signals=(
            _validated_items(obj.get("experimental_signals"), ExperimentalSignal)
            if experimental
            else []
        ),
    )


def _validated_items[ModelT: BaseModel](
    value: object,
    model: type[ModelT],
) -> list[ModelT]:
    if not isinstance(value, list):
        return []
    output: list[ModelT] = []
    for item in value:
        try:
            output.append(model.model_validate(item))
        except ValidationError:
            logger.debug(content_text("Ignoring invalid analysis entry: %s"), model.__name__)
    return output


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(error_text("No JSON object found in the model output")) from None
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError(error_text("The model output root is not a JSON object"))
    return value


def _compact_result(result: BlockAnalysis) -> dict[str, Any]:
    value = result.model_dump(mode="json")
    value.pop("schema_version", None)
    return value


def _clean_generated_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").replace("<", "").replace(">", "").split())
    return text[:limit]


def _valid_source_ids(value: object, source_count: int) -> list[int]:
    if not isinstance(value, list):
        return []
    output: list[int] = []
    for item in value:
        try:
            source_id = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= source_id < source_count and source_id not in output:
            output.append(source_id)
    return output[:12]


def _unique_evidence(items: list[Evidence]) -> list[Evidence]:
    seen: set[tuple[str, str, str]] = set()
    output: list[Evidence] = []
    for item in items:
        key = (item.quote, item.session_id, item.date)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _looks_like_model_refusal(raw: str) -> bool:
    if "{" in raw:
        return False
    normalized = " ".join(raw.lower().split())
    markers = (
        "against my guidelines",
        "go against my guidelines",
        "can't help with",
        "cannot help with",
        "can't assist with",
        "cannot assist with",
        "无法协助",
        "不能协助",
        "无法帮助",
    )
    return any(marker in normalized for marker in markers)


def _is_model_refusal_error(exc: LLMError) -> bool:
    if not isinstance(exc.detail, dict):
        return False
    return exc.detail.get("reason") in {
        "content_filter",
        "message_refusal",
        "responses_refusal",
    }


def _refusal_output_error(
    block: AnalysisBlock,
    *,
    stage: str,
    exc: LLMError,
) -> AnalysisBlockOutputError:
    return AnalysisBlockOutputError(
        block.block_id,
        stage=stage,
        errors=[f"LLMError: {exc.message}"],
        raw_outputs=[],
    )
