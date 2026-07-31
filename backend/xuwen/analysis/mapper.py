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
        detail = errors[-1] if errors else "未知解析错误"
        super().__init__(f"分析块 {block_id} 多次返回无效 JSON；最后错误：{detail}")


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
                "content": "你分析聊天记录，只能依据给定文字，严格输出 JSON，不补写事实。",
            },
            {"role": "user", "content": self._map_prompt(block, experimental=experimental)},
        ]
        last_error: Exception | None = None
        errors: list[str] = []
        raw_outputs: list[str] = []
        for attempt in range(2):
            raw = await self.llm.complete_chat(
                messages,
                GenerationParams(
                    temperature=self.settings.analysis_temperature,
                    max_tokens=self.settings.analysis_max_tokens,
                ),
                model=self.settings.resolved_analysis_model,
                stage="analysis.map" if attempt == 0 else "analysis.map.repair",
            )
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
                            f"上一个输出无法解析：{type(exc).__name__}: {exc}。"
                            "重新生成完整且更精简的 JSON。不要复述解释；缩短 claim、summary 和 quote，"
                            "严格遵守各数组数量上限，根字段和条目字段不得省略。"
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
                    "你从私有聊天记录中提取目标角色本人的生活状态候选。"
                    "只依据输入，严格输出 JSON，不把用户或双方互动改写成目标角色习惯。"
                ),
            },
            {"role": "user", "content": self._life_prompt(block)},
        ]
        last_error: Exception | None = None
        errors: list[str] = []
        raw_outputs: list[str] = []
        for attempt in range(2):
            raw = await self.llm.complete_chat(
                messages,
                GenerationParams(
                    temperature=self.settings.analysis_temperature,
                    max_tokens=self.settings.analysis_max_tokens,
                ),
                model=self.settings.resolved_analysis_model,
                stage="analysis.life.map" if attempt == 0 else "analysis.life.map.repair",
            )
            try:
                obj = _parse_json_object(raw)
                raw_habits = obj.get("life_habits")
                habits = _validated_items(raw_habits, LifeHabit)
                if isinstance(raw_habits, list) and raw_habits and not habits:
                    raise ValueError("life_habits 有内容，但所有条目都不符合结构契约")
                return BlockAnalysis(
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
                            "上一个输出无法解析。只返回完整 JSON："
                            '{"life_habits": [...]}。缩短文字并遵守最多 10 条。'
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
                    "你为最终大模型收集实验性人格候选证据，严格输出 JSON。"
                    "允许提出性格、依恋、隐瞒、操控意图和精神健康相关假设，"
                    "但单个聊天块只能形成候选，必须保留不确定性和替代解释。"
                ),
            },
            {"role": "user", "content": self._experimental_prompt(block)},
        ]
        last_error: Exception | None = None
        errors: list[str] = []
        raw_outputs: list[str] = []
        for attempt in range(2):
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
                        "content": "上一个输出无法解析。只返回包含 experimental_signals 数组的 JSON。",
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
            f"归并 {month} 的块级分析。合并重复事件和重复观察，保留证据、反例与替代解释。"
            "观察只能在 subject 相同且语义重复时合并，不得把 self/both/relationship "
            "改写成 friend。"
            "返回与 Map 相同字段的 JSON 对象：events、personality_observations、"
            "relationship_signals、life_habits、experimental_signals。\n输入：\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        raw = await self.llm.complete_chat(
            [
                {"role": "system", "content": "你是严格输出 JSON 的归并器。"},
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
                {"role": "system", "content": "你只依据事件时间线划分关系阶段，严格输出 JSON。"},
                {
                    "role": "user",
                    "content": (
                        "将事件划分成连续、不重叠的关系阶段。阶段名应描述变化，例如相识期、升温期、"
                        "稳定期、疏远期；不要为了凑数强行分段。返回 "
                        "{\"phases\":[{\"title\":\"阶段名\",\"start_date\":\"YYYY-MM-DD\","
                        "\"end_date\":\"YYYY-MM-DD\",\"summary\":\"概括\","
                        "\"event_ids\":[\"evt-id\"]}]}。\n"
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
            raise ValueError("阶段输出缺少 phases 数组")
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

    async def reduce_personality_report(
        self, report: PersonalityReport
    ) -> PersonalityReport:
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
            observation
            for section in report.sections
            for observation in section.observations
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
                        "你根据结构化聊天观察生成目标角色的普通人格报告，严格输出 JSON。"
                        "不得分析用户本人，不得虚构证据，不做精神疾病或蓄意操控判断。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "跨时段归并并自行组织最合适的章节，例如核心性格、沟通与情绪、"
                        "对人方式、对用户的态度、价值观与边界；不要为了套模板生成空章节。"
                        "保留每条观察的 subject、dimension、claim、evidence、confidence、"
                        "counterexamples、alternative_explanations。personality 类观察只能是 friend；"
                        "关系互动可为 friend/both/relationship。最多 8 个章节、每章 12 条。"
                        "返回 {\"summary\":\"...\",\"sections\":[{\"key\":\"...\","
                        "\"title\":\"...\",\"observations\":[...]}]}。\n"
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
                grounded.append(
                    observation.model_copy(update={"evidence": evidence}, deep=True)
                )
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
                        "你把结构化普通人格观察写成供主聊天模型内部使用的人格画像。"
                        "严格输出 JSON，不输出原文引用，不诊断，不写回应策略。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "按报告已有章节组织目标角色的核心性格、沟通情绪、对人方式、"
                        "对用户态度和价值边界；没有依据的内容不要补。"
                        "保留概率、条件、反例和替代解释，不包含聊天原文、session_id、"
                        "回应建议、触发策略或禁止项。返回 {\"profile\":\"...\"}。\n"
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
                        "你根据全局归并后的聊天信号撰写实验性人格画像，严格输出 JSON。"
                        "允许提出性格、依恋、隐瞒、操控意图和精神健康相关假设，"
                        "但必须保留概率语言、条件、反例和替代解释，不得伪造原文。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "生成一份供主聊天模型内部使用的中文人格画像正文。"
                        "按核心人格、对人方式、依恋、隐瞒或失真、操控意图、"
                        "精神健康相关假设组织；没有证据的章节不要硬写。"
                        "不要写回应建议、触发策略或禁止项，不要包含聊天原文和 session_id。"
                        "明确这些是历史推测而非当前事实。"
                        "返回 {\"profile\":\"...\"}，正文不超过 5000 字。\n"
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

    async def reduce_experimental_report(
        self, report: ExperimentalReport
    ) -> ExperimentalReport:
        """跨块归并候选线索，形成带证据的最终实验报告。"""
        if not report.signals:
            return report
        allowed_evidence = {
            evidence.quote: evidence
            for signal in report.signals
            for evidence in signal.evidence
        }
        allowed_claims = {signal.claim for signal in report.signals}
        candidates = [signal.model_dump(mode="json") for signal in report.signals[:40]]
        raw = await self.final_llm.complete_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你用跨时段聊天证据归并实验性人格画像，严格输出 JSON。"
                        "允许提出可能的真实性格、人际态度、依恋类型、隐瞒情境、"
                        "操控意图和精神健康相关假设；推测必须写成假设并保留反证。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "把下列块级候选线索跨时间归并成最终实验人格画像。"
                        "重复出现且证据来自不同时段时可提高置信度；单次线索应保持低置信度。"
                        "只分析 friend；保留代表性证据、推断依据、适用条件、反例和替代解释，"
                        "最多 24 条。类别只能是 personality_hypothesis、interpersonal_style、"
                        "attachment、deception_pattern、manipulation_intent、"
                        "mental_health_hypothesis、internal_contradiction。"
                        "精神疾病名称只能写成可能相符的解释，不能写成确诊；"
                        "隐瞒和操控意图可以推测，但必须给出非恶意替代解释。"
                        "返回 {\"summary\":\"全局人格概括\","
                        "\"experimental_signals\":[{\"subject\":\"friend\","
                        "\"category\":\"...\",\"claim\":\"...\","
                        "\"inference_basis\":\"...\",\"conditions\":[\"...\"],"
                        "\"evidence\":[...],\"confidence\":0.0,"
                        "\"counterexamples\":[\"...\"],"
                        "\"alternative_explanations\":[\"...\"]}]}。\n"
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
            friend_signals.append(
                signal.model_copy(update={"evidence": evidence}, deep=True)
            )
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
                        "你归并目标角色的长期生活规律，严格输出 JSON。"
                        "只保留目标角色本人、非敏感关系活动且有跨时段支持的规律。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "把候选归并为 sleep/meal/activity/availability 四类生活规律。"
                        "合并语义重复项，保留时间条件、适用情境、允许影响字段、反例和替代解释。"
                        "不得把用户习惯、双方共同互动或敏感关系活动改写成目标角色个人日常。"
                        "target_fields 只能是 daily_plan/current_activity/recent_meal/availability/"
                        "next_update_at/reply_delay_seconds/topic_seed。最多 16 条。"
                        "每条终稿必须列出支持它的 source_ids；只能引用输入中存在的编号，"
                        "至少引用 1 条，稳定规律应优先引用不同日期或不同块的多条候选。"
                        "不要返回 evidence，代码会按 source_ids 回填原始证据。"
                        "返回 {\"summary\":\"...\",\"habits\":[{\"source_ids\":[0,1],"
                        "\"category\":\"sleep|meal|activity|availability\",\"claim\":\"...\","
                        "\"time_patterns\":[\"...\"],\"contexts\":[\"...\"],"
                        "\"target_fields\":[\"...\"],\"confidence\":0.0,"
                        "\"counterexamples\":[\"...\"],"
                        "\"alternative_explanations\":[\"...\"]}]}。\n"
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
            target_fields = item.get("target_fields") or list(
                dict.fromkeys(
                    field for source in sources for field in source.target_fields
                )
            )[:5]
            try:
                habit = LifeHabit.model_validate(
                    {
                        **item,
                        "subject": "friend",
                        "target_fields": target_fields,
                        "evidence": [
                            evidence.model_dump(mode="json")
                            for evidence in source_evidence
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
        output_fields = "events、personality_observations、relationship_signals"
        experimental_instruction = ""
        if experimental:
            output_fields += "、experimental_signals"
            experimental_instruction = """
experimental_signals 仅允许以下类别：
- personality_hypothesis、interpersonal_style、attachment、deception_pattern、
  manipulation_intent、mental_health_hypothesis、internal_contradiction。
每条必须有 subject(friend)、claim、inference_basis、conditions、evidence、confidence(0..1)、
counterexamples 和 alternative_explanations（至少一项）。推测不等于事实、动机认定或诊断。
"""
        return f"""分析下面一段双人聊天。输出 JSON 对象，根字段固定为：
{output_fields}。

events 每项字段：date(YYYY-MM-DD)、title、type、summary、importance(1..5)、
evidence([{{quote,session_id,date}}])、session_ids。
type 只能是 milestone/conflict/reconciliation/intimacy/shared_activity/emotional_shift/
separation/daily/other。日常内容只有在能代表关系状态时才提取。最多 12 条。

personality_observations 只分析 {self.settings.friend_name or "TA"} 本人的描述性大五倾向、
沟通与情绪模式、价值观与边界；不要把作息、饮食或日常活动混入这里。
relationship_signals 关注主动性、回应质量、关心方式、冲突行为和随时间的变化。
把主动联系、回应方式、关系投入和共同活动模式等写成具体观察，
不要只输出“重视关系”“比较温和”一类笼统结论。阶段性习惯应注明替代解释。
两类观察每项字段：subject、dimension、claim、evidence、confidence(0..1)、counterexamples、
alternative_explanations。必须引用原文；有反例必须列出；证据不足就不输出。
personality_observations 和 relationship_signals 各最多 8 条。
subject 只能是 friend/self/both/relationship/unknown。personality_observations 必须是 friend；
描述“我”本人的习惯不要输出，描述双方共同互动的内容放入 relationship_signals，
relationship_signals 的 subject 只能是 friend/both/relationship，不能是 self 或 unknown；
不得把“双方经常一起做某事”改写成 {self.settings.friend_name or "TA"} 的个人日常规律。

{experimental_instruction}
列出的字段即使没有内容也保留空数组。不要输出 markdown 或解释。
块 ID：{block.block_id}
会话 ID：{", ".join(block.session_ids)}
聊天记录：
{block.text}
"""

    def _life_prompt(self, block: AnalysisBlock) -> str:
        return f"""只提取 {self.settings.friend_name or "TA"} 本人的生活候选。返回：
{{"life_habits":[{{"subject":"friend","category":"sleep|meal|activity|availability",
"claim":"...","time_patterns":["..."],"contexts":["..."],
"target_fields":["daily_plan","availability"],
"evidence":[{{"quote":"...","session_id":"...","date":"..."}}],"confidence":0.0,
"counterexamples":["..."],"alternative_explanations":["..."],
"sensitive_relationship_context":false}}]}}

规则：
- 这里只收集块级候选，不要求单个块已经证明是长期规律；跨时段稳定性由最终大模型判断。
- sleep：入睡、起床、补觉、活跃时段等候选；meal：进食时段和饮食安排候选；
  activity：工作、学习、出行和日常娱乐候选；availability：忙闲、在线和回应时段候选。
- subject 必须是 friend。用户本人的状态、双方共同活动和主体不明内容不要输出。
- 敏感或只在关系互动中成立的活动可作为审计候选，但必须标记
  sensitive_relationship_context=true，后续不会进入生活时间线。
- 一次明确状态可低置信度输出；重复出现可提高置信度。必须引用带时间的原文短句。
- 最多 10 条，每段 quote 最多 120 字。无候选时返回空数组，不要返回解释或 markdown。

块 ID：{block.block_id}
会话 ID：{", ".join(block.session_ids)}
聊天记录：
{block.text}
"""

    def _experimental_prompt(self, block: AnalysisBlock) -> str:
        return f"""只分析下面这段双人聊天中的实验性人格候选。只返回 JSON 对象：
{{"experimental_signals":[{{"subject":"friend","category":"personality_hypothesis|interpersonal_style|attachment|deception_pattern|manipulation_intent|mental_health_hypothesis|internal_contradiction","claim":"...","inference_basis":"...","conditions":["..."],"evidence":[{{"quote":"...","session_id":"...","date":"..."}}],"confidence":0.0,"counterexamples":["..."],"alternative_explanations":["..."]}}]}}

规则：
- 只分析 {self.settings.friend_name or "TA"}，subject 必须是 friend；不要分析“我”或把双方共同模式写成对方个人特征。
- 这里只收集块级候选，不要求单块形成最终结论；confidence 应保守，跨时段稳定性由最终大模型判断。
- personality_hypothesis：可能的核心性格、情绪调节、自我呈现和真实性格反差。
- interpersonal_style：如何对人、亲疏差异、尊重/功利/照顾/冷淡等态度。
- attachment：可能的依恋类型、亲近/撤离方式和关系触发点。
- deception_pattern：可能隐瞒、夸大、淡化或改变说法的情境；不得把单次矛盾直接写成撒谎习惯。
- manipulation_intent：可能通过施压、愧疚、隐私、金钱或控制权影响他人的模式与意图假设。
- mental_health_hypothesis：可提出与某类精神健康状态或症状相符的假设，但只能写“可能相符”，不能写成确诊。
- internal_contradiction：记录中尚未解释的前后差异，为 deception_pattern 提供候选证据但本身不证明故意。
- 每条必须包含推断依据、可能出现的条件、原文短引、反例和至少一个替代解释；没有反例时说明当前块未见反例。
- 普通日常只有在能支持人格推断时才提取。最多返回 6 条候选。

块 ID：{block.block_id}
会话 ID：{", ".join(block.session_ids)}
聊天记录：
{block.text}
"""


def _coerce_block_result(raw: str, block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
    obj = _parse_json_object(raw)
    return BlockAnalysis(
        block_id=block.block_id,
        start_time_ms=block.start_time_ms,
        end_time_ms=block.end_time_ms,
        experimental_requested=experimental,
        events=_validated_items(obj.get("events"), EventCandidate),
        personality_observations=_validated_items(
            obj.get("personality_observations"), Observation
        ),
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
            logger.debug("忽略无效分析条目：%s", model.__name__)
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
            raise ValueError("模型输出中没有 JSON 对象") from None
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型输出根节点不是 JSON 对象")
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
