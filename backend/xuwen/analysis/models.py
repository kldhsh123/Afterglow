"""关系分析的结构化数据契约。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EventType = Literal[
    "milestone",
    "conflict",
    "reconciliation",
    "intimacy",
    "shared_activity",
    "emotional_shift",
    "separation",
    "daily",
    "other",
]
ExperimentalCategory = Literal[
    "personality_hypothesis",
    "interpersonal_style",
    "attachment",
    "deception_pattern",
    "manipulation_intent",
    "mental_health_hypothesis",
    "internal_contradiction",
    # 兼容旧报告；新版提取不再主动生成下面两个类别。
    "manipulation_pattern",
    "wellbeing_signal",
]
ObservationSubject = Literal["friend", "self", "both", "relationship", "unknown"]
LifeHabitCategory = Literal["sleep", "meal", "activity", "availability"]
LifeTargetField = Literal[
    "daily_plan",
    "current_activity",
    "recent_meal",
    "availability",
    "next_update_at",
    "reply_delay_seconds",
    "topic_seed",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Evidence(BaseModel):
    quote: str = Field(min_length=1, max_length=300)
    session_id: str = ""
    date: str = ""


class EventCandidate(BaseModel):
    date: str
    title: str = Field(min_length=1, max_length=80)
    type: EventType = "other"
    summary: str = Field(default="", max_length=500)
    importance: int = Field(default=3, ge=1, le=5)
    evidence: list[Evidence] = Field(default_factory=list, max_length=5)
    session_ids: list[str] = Field(default_factory=list)


class Observation(BaseModel):
    subject: ObservationSubject = "unknown"
    dimension: str = Field(min_length=1, max_length=80)
    claim: str = Field(min_length=1, max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=6)
    confidence: float = Field(default=0.5, ge=0, le=1)
    counterexamples: list[str] = Field(default_factory=list, max_length=4)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=4)


class ExperimentalSignal(BaseModel):
    subject: ObservationSubject = "friend"
    category: ExperimentalCategory
    claim: str = Field(min_length=1, max_length=500)
    inference_basis: str = Field(default="", max_length=500)
    conditions: list[str] = Field(default_factory=list, max_length=5)
    evidence: list[Evidence] = Field(default_factory=list, max_length=6)
    confidence: float = Field(default=0.3, ge=0, le=1)
    counterexamples: list[str] = Field(default_factory=list, max_length=4)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=4)


class LifeHabit(BaseModel):
    subject: ObservationSubject
    category: LifeHabitCategory
    claim: str = Field(min_length=1, max_length=500)
    time_patterns: list[str] = Field(default_factory=list, max_length=5)
    contexts: list[str] = Field(default_factory=list, max_length=5)
    target_fields: list[LifeTargetField] = Field(default_factory=list, max_length=5)
    evidence: list[Evidence] = Field(default_factory=list, max_length=6)
    confidence: float = Field(default=0.3, ge=0, le=1)
    counterexamples: list[str] = Field(default_factory=list, max_length=4)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=4)
    sensitive_relationship_context: bool = False


class AnalysisBlock(BaseModel):
    block_id: str
    start_time_ms: int
    end_time_ms: int
    session_ids: list[str]
    message_count: int
    text: str


class BlockAnalysis(BaseModel):
    schema_version: int = 5
    block_id: str
    start_time_ms: int
    end_time_ms: int
    experimental_requested: bool = False
    experimental_schema_version: int = 0
    life_schema_version: int = 0
    events: list[EventCandidate] = Field(default_factory=list)
    personality_observations: list[Observation] = Field(default_factory=list)
    relationship_signals: list[Observation] = Field(default_factory=list)
    life_habits: list[LifeHabit] = Field(default_factory=list)
    experimental_signals: list[ExperimentalSignal] = Field(default_factory=list)


class TimelineEvent(EventCandidate):
    event_id: str


class TimelinePhase(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    start_date: str
    end_date: str
    summary: str = Field(default="", max_length=500)
    event_ids: list[str] = Field(default_factory=list)


class TimelineReport(BaseModel):
    schema_version: int = 1
    generated_at: str = Field(default_factory=utc_now_iso)
    source_message_count: int = 0
    source_block_count: int = 0
    events: list[TimelineEvent] = Field(default_factory=list)
    phases: list[TimelinePhase] = Field(default_factory=list)


class ReportSection(BaseModel):
    key: str
    title: str
    observations: list[Observation] = Field(default_factory=list)


class PersonalityReport(BaseModel):
    schema_version: int = 2
    generated_at: str = Field(default_factory=utc_now_iso)
    disclaimer: str = (
        "这是基于聊天文字痕迹的有限分析，不等同于这个人本身，也不是心理评估或诊断。"
    )
    summary: str = ""
    sections: list[ReportSection] = Field(default_factory=list)


class LifeProfile(BaseModel):
    schema_version: int = 1
    generated_at: str = Field(default_factory=utc_now_iso)
    summary: str = ""
    habits: list[LifeHabit] = Field(default_factory=list)


class ExperimentalReport(BaseModel):
    schema_version: int = 2
    generated_at: str = Field(default_factory=utc_now_iso)
    disclaimer: str = (
        "以下内容是基于聊天文字的实验性人格假设，允许推测但不等于事实核验、"
        "蓄意认定或医学诊断。"
    )
    summary: str = ""
    signals: list[ExperimentalSignal] = Field(default_factory=list)

    @field_validator("signals")
    @classmethod
    def _require_alternatives(cls, value: list[ExperimentalSignal]) -> list[ExperimentalSignal]:
        for signal in value:
            if not signal.alternative_explanations:
                signal.alternative_explanations = ["现有聊天记录不足以排除其他情境解释"]
        return value
