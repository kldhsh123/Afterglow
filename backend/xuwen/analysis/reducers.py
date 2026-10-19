"""关系分析的三路归并与人类可读报告渲染。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date

from xuwen.analysis.mapper import AnalysisMapper
from xuwen.analysis.models import (
    BlockAnalysis,
    EventCandidate,
    ExperimentalReport,
    ExperimentalSignal,
    LifeHabit,
    LifeProfile,
    Observation,
    PersonalityReport,
    ReportSection,
    TimelineEvent,
    TimelinePhase,
    TimelineReport,
)
from xuwen.content_i18n import content_text
from xuwen.prompt_language import output_language

_LIFE_CATEGORY_LABELS = {
    "sleep": "Sleep patterns",
    "meal": "Meals",
    "activity": "Activities",
    "availability": "Availability and replies",
}


async def reduce_timeline(
    results: list[BlockAnalysis],
    *,
    message_count: int,
    block_count: int,
    mapper: AnalysisMapper | None = None,
    max_events: int = 100,
) -> TimelineReport:
    candidates = [event for result in results for event in result.events]
    merged = _merge_events(candidates)
    selected = _select_events(merged, max_events=max_events)
    events = [_to_timeline_event(event) for event in selected]
    phases: list[TimelinePhase] = []
    if mapper is not None and events:
        try:
            phases = await mapper.propose_phases(events)
        except Exception:
            phases = []
    if events and not phases:
        phases = [_fallback_phase(events)]
    return TimelineReport(
        source_message_count=message_count,
        source_block_count=block_count,
        events=events,
        phases=phases,
    )


def reduce_personality(results: list[BlockAnalysis]) -> PersonalityReport:
    personality = _merge_observations(
        [
            observation
            for result in results
            for observation in result.personality_observations
            if observation.subject == "friend"
        ]
    )[:36]
    relationship = _merge_observations(
        [
            observation
            for result in results
            for observation in result.relationship_signals
            if observation.subject in {"friend", "both", "relationship"}
        ]
    )[:24]
    sections = [
        ReportSection(
            key="personality_observations",
            title=content_text("Character observations"),
            observations=personality,
        ),
        ReportSection(
            key="relationship_observations",
            title=content_text("Relationship observations"),
            observations=relationship,
        ),
    ]
    strongest = sorted(
        (item for section in sections for item in section.observations),
        key=lambda item: (item.confidence, len(item.evidence)),
        reverse=True,
    )[:3]
    summary = "；".join(item.claim.rstrip("。") for item in strongest)
    if summary:
        summary += "。"
    return PersonalityReport(summary=summary, sections=sections)


def reduce_experimental(results: list[BlockAnalysis]) -> ExperimentalReport:
    signals = [signal for result in results for signal in result.experimental_signals]
    merged: list[ExperimentalSignal] = []
    for signal in sorted(signals, key=lambda item: item.confidence, reverse=True):
        duplicate = next(
            (
                current
                for current in merged
                if current.category == signal.category
                and _text_similarity(current.claim, signal.claim) >= 0.58
            ),
            None,
        )
        if duplicate is None:
            merged.append(signal.model_copy(deep=True))
        else:
            _merge_signal(duplicate, signal)
    return ExperimentalReport(signals=merged[:24])


def render_personality_markdown(report: PersonalityReport) -> str:
    lines = [
        content_text("# Relationship and personality analysis"),
        "",
        f"> {report.disclaimer}",
        "",
    ]
    if report.summary:
        lines.extend([content_text("## Overview"), "", report.summary, ""])
    for section in report.sections:
        lines.extend([f"## {section.title}", ""])
        if not section.observations:
            lines.extend(
                [
                    content_text(
                        "The current records do not contain sufficiently stable evidence."
                    ),
                    "",
                ]
            )
            continue
        for observation in section.observations:
            lines.append(f"### {observation.claim}")
            lines.append("")
            lines.append(f"{content_text('Confidence: ')}{round(observation.confidence * 100)}%")
            lines.append("")
            for evidence in observation.evidence:
                suffix = f"（{evidence.date}）" if evidence.date else ""
                lines.append(f"> {evidence.quote}{suffix}")
            if observation.counterexamples:
                lines.append("")
                lines.append(
                    content_text("Counterexamples: ") + "；".join(observation.counterexamples)
                )
            if observation.alternative_explanations:
                lines.append("")
                lines.append(
                    content_text("Alternative explanations: ")
                    + "；".join(observation.alternative_explanations)
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_personality_prompt_context(report: PersonalityReport) -> str:
    """把普通人格报告转为不携带原文证据的主模型内部画像。"""
    candidates: list[tuple[ReportSection, Observation]] = []
    for section in report.sections:
        section_candidates = sorted(
            (observation for observation in section.observations if observation.confidence >= 0.3),
            key=lambda item: (item.confidence, len(item.evidence)),
            reverse=True,
        )[:6]
        candidates.extend((section, observation) for observation in section_candidates)
    lines = [
        "[Personality reference: internal use only; never recite to the user]",
        "These are historical personality and relationship tendencies, not fixed facts or current states.",
        "Use the life timeline for current activities/routines, and the experimental profile for speculative hypotheses.",
        "Current conversation and genuine retrieved text take priority. Never mention this file or probe the user to validate its conclusions.",
    ]
    for section, observation in candidates:
        alternative = _observation_uncertainty(observation)
        lines.extend(
            [
                f"[{section.title}] {observation.dimension}：{observation.claim}",
                f"- Confidence: about {round(observation.confidence * 100)}%; exceptions or alternatives: {alternative}",
            ]
        )
    if not candidates:
        lines.append("- No sufficiently supported stable personality/habit observations.")
    return "\n".join(lines)[:12000]


def render_life_analysis_context(profile: LifeProfile, *, friend_name: str = "TA") -> str:
    """把结构化生活画像渲染为生活时间线上下文，不读取原文证据。"""
    candidates = [
        habit
        for habit in profile.habits
        if habit.subject == "friend"
        and habit.confidence >= 0.3
        and (not habit.sensitive_relationship_context)
        and habit.target_fields
    ][:16]
    lines = [
        "[Historical daily patterns: internal life-timeline reference]",
        "These describe only the character's own long-term tendencies, excluding user habits, joint interactions and sensitive relationship content.",
        "Use only as priors for plausible daily-state candidates, never as facts about today.",
        "Current time, measured rhythms, today's timeline and explicit dialogue take priority. Do not infer present activity from these priors.",
    ]
    subject_name = friend_name.strip() or "TA"
    for habit in candidates:
        time_patterns = "、".join(habit.time_patterns) or "No stable time pattern"
        contexts = "、".join(habit.contexts) or "General context"
        target_fields = "、".join(habit.target_fields)
        lines.append(
            f"- [{_LIFE_CATEGORY_LABELS[habit.category]}] Subject: {subject_name}; pattern: {habit.claim}; typical times: {time_patterns}; contexts: {contexts}; allowed fields: {target_fields}; confidence about {round(habit.confidence * 100)}%; exceptions or alternatives: {_life_habit_uncertainty(habit)}; only adjusts candidate likelihood, never establishes a current fact."
        )
    if not candidates:
        lines.append("- No sufficiently supported sleep/daily habit observations.")
    return "\n".join(lines)[:5000]


def render_experimental_prompt_context(report: ExperimentalReport) -> str:
    """把实验报告渲染为可注入主模型的去证据人格画像。"""
    labels = {
        "personality_hypothesis": "Possible core traits",
        "interpersonal_style": "Interpersonal style and attitudes",
        "attachment": "Attachment hypotheses",
        "deception_pattern": "Possible concealment or distortion",
        "manipulation_intent": "Manipulation-intent hypotheses",
        "mental_health_hypothesis": "Mental-health hypotheses",
        "manipulation_pattern": "Interaction influence patterns (legacy)",
        "internal_contradiction": "Contradictions in the records",
        "wellbeing_signal": "Wellbeing signals (legacy)",
    }
    candidates = sorted(
        (
            signal
            for signal in report.signals
            if signal.subject == "friend" and signal.confidence > 0.3
        ),
        key=lambda signal: signal.confidence,
        reverse=True,
    )[:20]
    lines = [
        "[Experimental personality profile: internal use only; never recite to the user]",
        "Historical chats may support hypotheses about traits, interpersonal attitudes, attachment, concealment, manipulation intent and mental health.",
        "All are probabilistic hypotheses, not verified facts, determinations of intent or medical diagnoses. Current conversation and genuine text take priority.",
        "Use as background tendencies, not a list of response strategies or grounds for inventing events in this turn.",
    ]
    if report.summary:
        lines.extend(["", "Overall profile: " + report.summary])
    for signal in candidates:
        lines.extend(
            [
                "",
                f"[{labels[signal.category]}] {signal.claim}",
                f"- Confidence: about {round(signal.confidence * 100)}%",
                f"- Inference basis: {signal.inference_basis or 'Inferred from text patterns across periods'}",
                "- Possible conditions: "
                + (
                    "；".join(signal.conditions)
                    if signal.conditions
                    else "No clear conditions established"
                ),
                "- Counterexamples: "
                + (
                    "；".join(signal.counterexamples)
                    if signal.counterexamples
                    else "No clear counterexamples in current records"
                ),
                "- Alternatives: "
                + (
                    "；".join(signal.alternative_explanations)
                    if signal.alternative_explanations
                    else "Other contextual explanations remain possible"
                ),
            ]
        )
    if not candidates:
        lines.append("- No experimental hypotheses meet the inclusion threshold.")
    return "\n".join(lines)[:12000]


def render_experimental_profile_markdown(report: ExperimentalReport) -> str:
    """渲染包含证据的完整实验人格报告，供用户查看而非注入主模型。"""
    labels = {
        "personality_hypothesis": content_text("Possible core traits"),
        "interpersonal_style": content_text("Interpersonal style and attitudes"),
        "attachment": content_text("Attachment hypotheses"),
        "deception_pattern": content_text("Possible concealment or distortion"),
        "manipulation_intent": content_text("Manipulation-intent hypotheses"),
        "mental_health_hypothesis": content_text("Mental-health hypotheses"),
        "manipulation_pattern": content_text("Interaction influence patterns (legacy)"),
        "internal_contradiction": content_text("Contradictions in the records"),
        "wellbeing_signal": content_text("Wellbeing signals (legacy)"),
    }
    lines = [content_text("# Experimental personality profile"), "", f"> {report.disclaimer}", ""]
    if report.summary:
        lines.extend([content_text("## Overall profile"), "", report.summary, ""])
    for signal in report.signals:
        lines.extend(
            [
                f"## {labels[signal.category]}",
                "",
                signal.claim,
                "",
                f"{content_text('Confidence: ')}{round(signal.confidence * 100)}%",
                "",
            ]
        )
        if signal.inference_basis:
            lines.extend([content_text("Inference basis: ") + signal.inference_basis, ""])
        if signal.conditions:
            lines.extend([content_text("Possible conditions: ") + "；".join(signal.conditions), ""])
        for evidence in signal.evidence:
            suffix = f"（{evidence.date}）" if evidence.date else ""
            lines.append(f"> {evidence.quote}{suffix}")
        if signal.counterexamples:
            lines.extend(
                ["", content_text("Counterexamples: ") + "；".join(signal.counterexamples)]
            )
        if signal.alternative_explanations:
            lines.extend(
                [
                    "",
                    content_text("Alternative explanations: ")
                    + "；".join(signal.alternative_explanations),
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _observation_uncertainty(observation: Observation) -> str:
    if observation.counterexamples:
        return observation.counterexamples[0]
    if observation.alternative_explanations:
        return observation.alternative_explanations[0]
    return "The records may not cover behavior in other contexts"


def _life_habit_uncertainty(habit: LifeHabit) -> str:
    if habit.counterexamples:
        return habit.counterexamples[0]
    if habit.alternative_explanations:
        return habit.alternative_explanations[0]
    return "The records may not cover other periods or contexts"


def merge_month_locally(month: str, results: list[BlockAnalysis]) -> BlockAnalysis:
    """月度模型归并失败时保留高质量条目的确定性兜底。"""
    events = _select_events(_merge_events([e for r in results for e in r.events]), max_events=30)
    personality = _merge_observations(
        [item for result in results for item in result.personality_observations]
    )[:30]
    relationship = _merge_observations(
        [item for result in results for item in result.relationship_signals]
    )[:30]
    life_habits = _merge_life_habits([item for result in results for item in result.life_habits])[
        :40
    ]
    experimental = reduce_experimental(results).signals
    return BlockAnalysis(
        output_language=output_language(),
        block_id=f"month-{month}",
        start_time_ms=min(result.start_time_ms for result in results),
        end_time_ms=max(result.end_time_ms for result in results),
        experimental_requested=any(result.experimental_requested for result in results),
        events=events,
        personality_observations=personality,
        relationship_signals=relationship,
        life_habits=life_habits,
        experimental_signals=experimental,
    )


def _merge_events(candidates: list[EventCandidate]) -> list[EventCandidate]:
    merged: list[EventCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.date, -item.importance)):
        duplicate = next((item for item in merged if _same_event(item, candidate)), None)
        if duplicate is None:
            merged.append(candidate.model_copy(deep=True))
            continue
        if candidate.importance > duplicate.importance:
            duplicate.title = candidate.title
            duplicate.summary = candidate.summary
            duplicate.importance = candidate.importance
            duplicate.type = candidate.type
        duplicate.evidence = _unique_models(duplicate.evidence + candidate.evidence, "quote")[:5]
        duplicate.session_ids = list(dict.fromkeys(duplicate.session_ids + candidate.session_ids))
    return merged


def _select_events(events: list[EventCandidate], *, max_events: int) -> list[EventCandidate]:
    if len(events) <= max_events:
        return sorted(events, key=lambda item: item.date)
    by_month: dict[str, list[EventCandidate]] = defaultdict(list)
    for event in events:
        by_month[event.date[:7]].append(event)
    selected = [max(items, key=lambda item: item.importance) for items in by_month.values()]
    selected_ids = {id(item) for item in selected}
    remaining = sorted(
        (item for item in events if id(item) not in selected_ids),
        key=lambda item: item.importance,
        reverse=True,
    )
    selected.extend(remaining[: max(0, max_events - len(selected))])
    return sorted(selected[:max_events], key=lambda item: item.date)


def _same_event(left: EventCandidate, right: EventCandidate) -> bool:
    try:
        days = abs((date.fromisoformat(left.date) - date.fromisoformat(right.date)).days)
    except ValueError:
        days = 99
    if days > 3:
        return False
    left_text = f"{left.title}{left.summary}"
    right_text = f"{right.title}{right.summary}"
    return left.title == right.title or _text_similarity(left_text, right_text) >= 0.48


def _to_timeline_event(event: EventCandidate) -> TimelineEvent:
    digest = hashlib.sha256(f"{event.date}\n{event.type}\n{event.title}".encode()).hexdigest()[:16]
    return TimelineEvent(event_id=f"evt-{digest}", **event.model_dump())


def _fallback_phase(events: list[TimelineEvent]) -> TimelinePhase:
    return TimelinePhase(
        title=content_text("Relationship history"),
        start_date=events[0].date,
        end_date=events[-1].date,
        summary=content_text("Major relationship events in chronological order."),
        event_ids=[event.event_id for event in events],
    )


def _merge_observations(observations: list[Observation]) -> list[Observation]:
    merged: list[Observation] = []
    for observation in sorted(
        observations,
        key=lambda item: (item.confidence, len(item.evidence)),
        reverse=True,
    ):
        duplicate = next(
            (
                current
                for current in merged
                if current.subject == observation.subject
                and _text_similarity(current.claim, observation.claim) >= 0.58
            ),
            None,
        )
        if duplicate is None:
            merged.append(observation.model_copy(deep=True))
            continue
        duplicate.confidence = max(duplicate.confidence, observation.confidence)
        duplicate.evidence = _unique_models(duplicate.evidence + observation.evidence, "quote")[:6]
        duplicate.counterexamples = list(
            dict.fromkeys(duplicate.counterexamples + observation.counterexamples)
        )[:4]
        duplicate.alternative_explanations = list(
            dict.fromkeys(duplicate.alternative_explanations + observation.alternative_explanations)
        )[:4]
    return merged


def _merge_signal(target: ExperimentalSignal, incoming: ExperimentalSignal) -> None:
    target.confidence = max(target.confidence, incoming.confidence)
    target.evidence = _unique_models(target.evidence + incoming.evidence, "quote")[:6]
    target.conditions = list(dict.fromkeys(target.conditions + incoming.conditions))[:5]
    target.counterexamples = list(dict.fromkeys(target.counterexamples + incoming.counterexamples))[
        :4
    ]
    if not target.inference_basis and incoming.inference_basis:
        target.inference_basis = incoming.inference_basis
    target.alternative_explanations = list(
        dict.fromkeys(target.alternative_explanations + incoming.alternative_explanations)
    )[:4]


def _merge_life_habits(habits: list[LifeHabit]) -> list[LifeHabit]:
    merged: list[LifeHabit] = []
    for habit in sorted(
        habits,
        key=lambda item: (item.confidence, len(item.evidence)),
        reverse=True,
    ):
        duplicate = next(
            (
                current
                for current in merged
                if current.subject == habit.subject
                and current.category == habit.category
                and current.sensitive_relationship_context == habit.sensitive_relationship_context
                and _text_similarity(current.claim, habit.claim) >= 0.58
            ),
            None,
        )
        if duplicate is None:
            merged.append(habit.model_copy(deep=True))
            continue
        duplicate.confidence = max(duplicate.confidence, habit.confidence)
        duplicate.time_patterns = list(
            dict.fromkeys(duplicate.time_patterns + habit.time_patterns)
        )[:5]
        duplicate.contexts = list(dict.fromkeys(duplicate.contexts + habit.contexts))[:5]
        duplicate.target_fields = list(
            dict.fromkeys(duplicate.target_fields + habit.target_fields)
        )[:5]
        duplicate.evidence = _unique_models(duplicate.evidence + habit.evidence, "quote")[:6]
        duplicate.counterexamples = list(
            dict.fromkeys(duplicate.counterexamples + habit.counterexamples)
        )[:4]
        duplicate.alternative_explanations = list(
            dict.fromkeys(duplicate.alternative_explanations + habit.alternative_explanations)
        )[:4]
    return merged


def _unique_models[ModelT](items: list[ModelT], attribute: str) -> list[ModelT]:
    seen: set[str] = set()
    output: list[ModelT] = []
    for item in items:
        key = str(getattr(item, attribute, ""))
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _text_similarity(left: str, right: str) -> float:
    def grams(value: str) -> set[str]:
        normalized = re.sub(r"\s+|[，。！？、；：,.!?;:]", "", value.lower())
        if len(normalized) < 2:
            return {normalized} if normalized else set()
        return {normalized[index : index + 2] for index in range(len(normalized) - 1)}

    left_grams = grams(left)
    right_grams = grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)
