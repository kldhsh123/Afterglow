"""关系分析流水线的离线契约测试；不调用真实模型。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xuwen.analysis.blocks import build_analysis_blocks
from xuwen.analysis.mapper import AnalysisBlockOutputError, AnalysisMapper
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
)
from xuwen.analysis.pipeline import AnalysisPipelineError, analyze_relationship
from xuwen.analysis.reducers import (
    reduce_experimental,
    reduce_personality,
    render_experimental_prompt_context,
    render_life_analysis_context,
    render_personality_prompt_context,
)
from xuwen.analysis.storage import AnalysisStorage
from xuwen.chat_api.companion_prompt import (
    load_experimental_prompt_context,
    load_personality_prompt_context,
)
from xuwen.chat_api.routes.analysis import router
from xuwen.chat_api.state import get_state
from xuwen.config import Settings
from xuwen.core.errors import LLMError
from xuwen.core.models import MessageKind, NormalizedMessage, Session


def _message(message_id: str, timestamp_ms: int, *, self_message: bool, text: str) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=message_id,
        seq=int(message_id.removeprefix("m")),
        timestamp_ms=timestamp_ms,
        sender_uid="me" if self_message else "friend",
        sender_name="我" if self_message else "TA",
        sender_role="self" if self_message else "friend",
        kind=MessageKind.TEXT,
        raw_type="text",
        text=text,
    )


def _session() -> Session:
    messages = [
        _message("m1", 1_700_000_000_000, self_message=True, text="最近还好吗"),
        _message("m2", 1_700_000_060_000, self_message=False, text="还好，你呢"),
    ]
    return Session(
        session_id="sess-1",
        messages=messages,
        start_time_ms=messages[0].timestamp_ms,
        end_time_ms=messages[-1].timestamp_ms,
    )


def _block_result(block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
    evidence = Evidence(quote="还好，你呢", session_id="sess-1", date="2023-11-14")
    return BlockAnalysis(
        block_id=block.block_id,
        start_time_ms=block.start_time_ms,
        end_time_ms=block.end_time_ms,
        experimental_requested=experimental,
        life_schema_version=1,
        events=[
            EventCandidate(
                date="2023-11-14",
                title="重新问候",
                type="emotional_shift",
                summary="双方重新开始交流",
                importance=3,
                evidence=[evidence],
                session_ids=["sess-1"],
            )
        ],
        personality_observations=[
            Observation(
                subject="friend",
                dimension="沟通方式",
                claim="会用反问延续对话",
                evidence=[evidence],
                confidence=0.6,
                alternative_explanations=["也可能只是礼貌回应"],
            )
        ],
        relationship_signals=[],
        life_habits=[
            LifeHabit(
                subject="friend",
                category="availability",
                claim="目标角色在情境A中通常可用",
                contexts=["情境A"],
                target_fields=["availability"],
                confidence=0.6,
                alternative_explanations=["样本可能未覆盖其他情境"],
            )
        ],
        experimental_signals=(
            [
                ExperimentalSignal(
                    category="attachment",
                    claim="这一次互动表现出回应意愿",
                    evidence=[evidence],
                    confidence=0.3,
                    alternative_explanations=["单次对话不足以说明稳定模式"],
                )
            ]
            if experimental
            else []
        ),
    )


def test_analysis_blocks_are_deterministic_and_keep_message_timestamps() -> None:
    first = build_analysis_blocks([_session()], self_name="我", friend_name="TA")
    second = build_analysis_blocks([_session()], self_name="我", friend_name="TA")

    assert [block.block_id for block in first] == [block.block_id for block in second]
    assert "[2023-11-" in first[0].text
    assert "我: 最近还好吗" in first[0].text
    assert "TA: 还好，你呢" in first[0].text


def test_storage_keeps_experimental_block_data_in_separate_directory(tmp_path: Path) -> None:
    storage = AnalysisStorage(tmp_path / "analysis")
    storage.prepare(experimental=True)
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]
    result = _block_result(block, experimental=True)

    storage.save_block(result)

    normal_text = storage.block_path(block.block_id).read_text(encoding="utf-8")
    assert "attachment" not in normal_text
    assert storage.experimental_blocks_dir.joinpath(f"{block.block_id}.json").exists()
    restored = storage.load_block(block.block_id, require_experimental=True)
    assert restored is not None
    assert restored.experimental_signals[0].category == "attachment"


def test_storage_invalidates_legacy_normal_analysis_cache(tmp_path: Path) -> None:
    storage = AnalysisStorage(tmp_path / "analysis")
    storage.prepare(experimental=False)
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]
    result = _block_result(block, experimental=False)
    storage.save_block(result)
    path = storage.block_path(block.block_id)
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 4
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    assert storage.load_block(block.block_id) is None


class _FakeMapper:
    def __init__(self) -> None:
        self.calls = 0
        self.experimental_calls = 0

    async def map_block(self, block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
        self.calls += 1
        return _block_result(block, experimental=experimental)

    async def map_experimental_block(self, block: AnalysisBlock) -> BlockAnalysis:
        self.experimental_calls += 1
        return _block_result(block, experimental=True)

    async def propose_phases(self, _events: object) -> list[object]:
        return []


class _FailingMapper(_FakeMapper):
    async def map_block(self, block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
        self.calls += 1
        raise LLMError("LLM 响应缺少 choices[0].message.content")


class _OutputFailingMapper(_FakeMapper):
    async def map_block(self, block: AnalysisBlock, *, experimental: bool) -> BlockAnalysis:
        self.calls += 1
        raise AnalysisBlockOutputError(
            block.block_id,
            stage="map",
            errors=["ValueError: 模型输出中没有 JSON 对象"],
            raw_outputs=["模型拒绝文本"],
        )


class _SummaryLlm:
    """不联网地记录 Reduce 模型调用并返回预设 JSON。"""

    def __init__(self) -> None:
        self.stages: list[str] = []
        self.messages: list[object] = []

    async def complete_chat(self, _messages: object, _params: object, **kwargs: object) -> str:
        stage = str(kwargs["stage"])
        self.stages.append(stage)
        self.messages.append(_messages)
        if stage == "analysis.reduce.personality_context":
            return json.dumps(
                {
                    "profile": "## 沟通特征\n在情境A中可能更常使用表达方式A。"
                },
                ensure_ascii=False,
            )
        if stage == "analysis.reduce.personality_report":
            return json.dumps(
                {
                    "summary": "目标角色可能具有特征A。",
                    "sections": [
                        {
                            "key": "core_traits",
                            "title": "核心特征",
                            "observations": [
                                {
                                    "subject": "friend",
                                    "dimension": "特征A",
                                    "claim": "在情境A中表现出特征A",
                                    "evidence": [{"quote": "原始证据A"}],
                                    "confidence": 0.68,
                                    "alternative_explanations": ["也可能由情境A临时引起"],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if stage == "analysis.reduce.life_profile":
            return json.dumps(
                {
                    "summary": "目标角色可能存在规律A。",
                    "habits": [
                        {
                            "source_ids": [0],
                            "category": "availability",
                            "claim": "在条件A下通常可用",
                            "time_patterns": ["时段A"],
                            "contexts": ["条件A"],
                            "target_fields": ["availability"],
                            "confidence": 0.7,
                            "counterexamples": [],
                            "alternative_explanations": ["也可能由条件A临时引起"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if stage == "analysis.reduce.experimental_context":
            return json.dumps(
                {
                    "profile": (
                        "## 依恋类型假设\n"
                        "在条件A下可能表现出互动模式A。"
                    )
                },
                ensure_ascii=False,
            )
        if stage == "analysis.reduce.experimental":
            return json.dumps(
                {
                    "experimental_signals": [
                        {
                            "subject": "friend",
                            "category": "attachment",
                            "claim": "多个区段都出现互动模式A",
                            "inference_basis": "不同区段出现相似观察",
                            "conditions": ["条件A"],
                            "evidence": [{"quote": "原始证据B"}],
                            "confidence": 0.68,
                            "counterexamples": ["区段B未出现该模式"],
                            "alternative_explanations": ["也可能由条件A临时引起"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if stage == "analysis.experimental.map":
            return json.dumps(
                {
                    "experimental_signals": [
                        {
                            "category": "internal_contradiction",
                            "claim": "观察A与观察B存在尚未解释的差异",
                            "evidence": [{"quote": "原始证据C"}],
                            "confidence": 0.55,
                            "alternative_explanations": ["条件可能在期间发生变化"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected stage: {stage}")


class _GenericContextLlm(_SummaryLlm):
    async def complete_chat(self, _messages: object, _params: object, **kwargs: object) -> str:
        if kwargs.get("stage") == "analysis.reduce.experimental_context":
            return '{"context":"保持温和、耐心倾听并尊重边界。"}'
        return await super().complete_chat(_messages, _params, **kwargs)


class _RefusingMapLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_chat(self, _messages: object, _params: object, **_kwargs: object) -> str:
        self.calls += 1
        return "I cannot assist with that request because it goes against my guidelines."


async def test_mapper_stops_retrying_after_map_refusal() -> None:
    refusing = _RefusingMapLlm()
    mapper = AnalysisMapper(
        Settings(),
        llm=refusing,  # type: ignore[arg-type]
    )
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]

    with pytest.raises(ValueError, match="多次返回无效 JSON"):
        await mapper.map_block(block, experimental=False)

    assert refusing.calls == 1


async def test_mapper_uses_final_model_for_complete_personality_report() -> None:
    llm = _SummaryLlm()
    mapper = AnalysisMapper(Settings(), llm=llm)  # type: ignore[arg-type]
    report = PersonalityReport(
        sections=[
            ReportSection(
                key="candidates",
                title="候选观察",
                observations=[
                    Observation(
                        subject="friend",
                        dimension="候选特征A",
                        claim="在情境A中出现特征A",
                        evidence=[Evidence(quote="原始证据A")],
                        confidence=0.6,
                    )
                ],
            )
        ]
    )

    reduced = await mapper.reduce_personality_report(report)

    assert reduced.summary == "目标角色可能具有特征A。"
    assert reduced.sections[0].key == "core_traits"
    assert reduced.sections[0].observations[0].subject == "friend"
    assert llm.stages == ["analysis.reduce.personality_report"]


async def test_mapper_reduces_life_profile_by_source_ids_and_restores_evidence() -> None:
    llm = _SummaryLlm()
    mapper = AnalysisMapper(Settings(), llm=llm)  # type: ignore[arg-type]
    source = LifeHabit(
        subject="friend",
        category="availability",
        claim="候选规律A",
        time_patterns=["时段A"],
        contexts=["条件A"],
        target_fields=["availability"],
        evidence=[Evidence(quote="原始证据A")],
        confidence=0.6,
    )

    profile = await mapper.reduce_life_profile([source])

    assert profile.summary == "目标角色可能存在规律A。"
    assert profile.habits[0].claim == "在条件A下通常可用"
    assert profile.habits[0].evidence[0].quote == "原始证据A"
    assert llm.stages == ["analysis.reduce.life_profile"]


async def test_mapper_optimizes_experimental_context_without_evidence_quotes() -> None:
    llm = _SummaryLlm()
    mapper = AnalysisMapper(Settings(), llm=llm)  # type: ignore[arg-type]
    report = ExperimentalReport(
        signals=[
            ExperimentalSignal(
                category="attachment",
                claim="在条件A下可能出现互动模式A",
                confidence=0.7,
                evidence=[Evidence(quote="这段原文不能发送给优化器")],
                alternative_explanations=["也可能由条件A临时引起"],
            )
        ]
    )

    context = await mapper.optimize_experimental_context(report)

    assert "依恋类型假设" in context
    assert "互动模式A" in context
    assert "回应策略" not in context
    assert "禁止" not in context
    assert llm.stages == ["analysis.reduce.experimental_context"]
    assert "这段原文不能发送给优化器" not in json.dumps(llm.messages, ensure_ascii=False)
    assert "这段原文不能发送给优化器" not in context


async def test_mapper_optimizes_personality_context_without_evidence_quotes() -> None:
    llm = _SummaryLlm()
    mapper = AnalysisMapper(Settings(), llm=llm)  # type: ignore[arg-type]
    report = PersonalityReport(
        sections=[
            ReportSection(
                key="communication",
                title="沟通与情绪模式",
                observations=[
                    Observation(
                        subject="friend",
                        dimension="沟通特征A",
                        claim="在情境A中常使用表达方式A",
                        confidence=0.72,
                        evidence=[Evidence(quote="这段人格证据不能发送给优化器")],
                        alternative_explanations=["也可能只在熟悉话题中如此"],
                    )
                ],
            )
        ]
    )

    context = await mapper.optimize_personality_context(report)

    assert "沟通特征" in context
    assert "表达方式A" in context
    assert "回应策略" not in context
    assert "禁止" not in context
    assert llm.stages == ["analysis.reduce.personality_context"]
    payload = json.dumps(llm.messages, ensure_ascii=False)
    assert "这段人格证据不能发送给优化器" not in payload
    assert "这段人格证据不能发送给优化器" not in context


async def test_mapper_rejects_legacy_generic_response_strategy_context() -> None:
    mapper = AnalysisMapper(Settings(), llm=_GenericContextLlm())  # type: ignore[arg-type]
    report = ExperimentalReport(
        signals=[
            ExperimentalSignal(
                category="internal_contradiction",
                claim="观察A与观察B存在尚未解释的差异",
                confidence=0.65,
                alternative_explanations=["条件可能在期间发生变化"],
            )
        ]
    )

    context = await mapper.optimize_experimental_context(report)

    assert context == ""


async def test_mapper_reduces_block_candidates_into_global_experimental_report() -> None:
    llm = _SummaryLlm()
    mapper = AnalysisMapper(Settings(), llm=llm)  # type: ignore[arg-type]
    report = ExperimentalReport(
        signals=[
            ExperimentalSignal(
                category="attachment",
                claim="一次互动模式A的候选线索",
                evidence=[Evidence(quote="原始证据B")],
                confidence=0.4,
                alternative_explanations=["也可能由条件A临时引起"],
            )
        ]
    )

    reduced = await mapper.reduce_experimental_report(report)

    assert reduced.signals[0].confidence == 0.68
    assert "多个区段" in reduced.signals[0].claim
    assert llm.stages == ["analysis.reduce.experimental"]


async def test_mapper_extracts_experimental_signals_in_a_separate_call() -> None:
    llm = _SummaryLlm()
    mapper = AnalysisMapper(Settings(), llm=llm)  # type: ignore[arg-type]
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]

    result = await mapper.map_experimental_block(block)

    assert result.events == []
    assert result.personality_observations == []
    assert result.experimental_signals[0].category == "internal_contradiction"
    assert llm.stages == ["analysis.experimental.map"]


def test_map_prompt_omits_experimental_instructions_when_generation_is_disabled() -> None:
    mapper = AnalysisMapper(Settings(), llm=_SummaryLlm())  # type: ignore[arg-type]
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]

    disabled_prompt = mapper._map_prompt(block, experimental=False)
    enabled_prompt = mapper._map_prompt(block, experimental=True)

    assert "relationship_signals、experimental_signals" not in disabled_prompt
    assert "experimental_signals 仅允许以下类别" not in disabled_prompt
    assert "personality_hypothesis、interpersonal_style" not in disabled_prompt
    assert "experimental_signals 仅允许以下类别" in enabled_prompt
    assert "personality_hypothesis、interpersonal_style" in enabled_prompt
    assert "life_habits" not in disabled_prompt
    life_prompt = mapper._life_prompt(block)
    assert "不要求单个块已经证明是长期规律" in life_prompt
    assert "共同活动和主体不明内容不要输出" in life_prompt


def test_final_analysis_connection_defaults_to_main_chat_connection() -> None:
    inherited = Settings(
        openai_base_url="https://main.invalid/v1",
        openai_api_key="main-key",
        chat_model="main-model",
    )

    assert inherited.resolved_analysis_final_api_url == "https://main.invalid/v1"
    assert inherited.resolved_analysis_final_api_key.get_secret_value() == "main-key"
    assert inherited.resolved_analysis_final_model == "main-model"

    independent = Settings(
        openai_base_url="https://main.invalid/v1",
        openai_api_key="main-key",
        chat_model="main-model",
        analysis_final_api_url="https://analysis.invalid/v1",
        analysis_final_api_key="analysis-key",
        analysis_final_model="analysis-model",
    )
    assert independent.resolved_analysis_final_api_url == "https://analysis.invalid/v1"
    assert independent.resolved_analysis_final_api_key.get_secret_value() == "analysis-key"
    assert independent.resolved_analysis_final_model == "analysis-model"


def _write_source(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "afterglow": {"format": "afterglow-chat", "version": "1.0"},
                "conversation": {"type": "private"},
                "participants": [
                    {"uid": "me", "name": "我", "role": "self"},
                    {"uid": "friend", "name": "TA", "role": "friend"},
                ],
                "messages": [
                    {
                        "id": "m1",
                        "seq": 1,
                        "timestamp_ms": 1_700_000_000_000,
                        "sender_uid": "me",
                        "sender_name": "我",
                        "kind": "text",
                        "text": "最近还好吗",
                    },
                    {
                        "id": "m2",
                        "seq": 2,
                        "timestamp_ms": 1_700_000_060_000,
                        "sender_uid": "friend",
                        "sender_name": "TA",
                        "kind": "text",
                        "text": "还好，你呢",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def test_pipeline_writes_reports_and_resumes_completed_blocks(tmp_path: Path) -> None:
    source = tmp_path / "chat.json"
    _write_source(source)
    settings = Settings(
        self_name="我",
        self_uid="me",
        friend_name="TA",
        friend_uid="friend",
        analysis_data_dir=tmp_path / "analysis",
        analysis_experimental_enabled=True,
    )
    first_mapper = _FakeMapper()
    first = await analyze_relationship([source], settings, mapper=first_mapper)  # type: ignore[arg-type]

    assert first_mapper.calls == 1
    assert first_mapper.experimental_calls == 1
    assert first.failed == 0
    assert Path(first.timeline_path or "").exists()
    assert Path(first.personality_path or "").exists()
    assert Path(first.personality_prompt_path or "").exists()
    assert Path(first.life_profile_path or "").exists()
    assert Path(first.life_context_path or "").exists()
    assert Path(first.experimental_path or "").exists()

    second_mapper = _FakeMapper()
    second = await analyze_relationship([source], settings, mapper=second_mapper)  # type: ignore[arg-type]
    assert second_mapper.calls == 0
    assert second_mapper.experimental_calls == 0
    assert second.resumed == 1

    experimental_cache = next(
        (tmp_path / "analysis" / "experimental" / "blocks").glob("*.json")
    )
    legacy = json.loads(experimental_cache.read_text(encoding="utf-8"))
    legacy.pop("experimental_schema_version", None)
    experimental_cache.write_text(
        json.dumps(legacy, ensure_ascii=False),
        encoding="utf-8",
    )

    migrated_mapper = _FakeMapper()
    migrated = await analyze_relationship(
        [source], settings, mapper=migrated_mapper  # type: ignore[arg-type]
    )
    assert migrated_mapper.calls == 0
    assert migrated_mapper.experimental_calls == 1
    assert migrated.resumed == 0


async def test_pipeline_does_not_publish_reports_when_any_block_fails(tmp_path: Path) -> None:
    source = tmp_path / "chat.json"
    _write_source(source)
    analysis_dir = tmp_path / "analysis"
    settings = Settings(
        self_name="我",
        self_uid="me",
        friend_name="TA",
        friend_uid="friend",
        analysis_data_dir=analysis_dir,
    )

    with pytest.raises(AnalysisPipelineError, match="未生成最终报告") as caught:
        await analyze_relationship(
            [source],
            settings,
            mapper=_FailingMapper(),  # type: ignore[arg-type]
        )

    assert "LLM 响应缺少 choices" in str(caught.value)
    assert not (analysis_dir / "timeline.json").exists()
    assert not (analysis_dir / "personality_report.json").exists()
    assert not (analysis_dir / "personality_report.md").exists()
    assert not (analysis_dir / "personality_prompt_context.md").exists()
    assert not (analysis_dir / "life_profile.json").exists()
    assert not (analysis_dir / "life_context.md").exists()
    assert not (analysis_dir / "manifest.json").exists()


async def test_pipeline_logs_and_skips_invalid_model_output_block(tmp_path: Path) -> None:
    source = tmp_path / "chat.json"
    _write_source(source)
    analysis_dir = tmp_path / "analysis"
    settings = Settings(
        self_name="我",
        self_uid="me",
        friend_name="TA",
        friend_uid="friend",
        analysis_data_dir=analysis_dir,
    )

    report = await analyze_relationship(
        [source],
        settings,
        mapper=_OutputFailingMapper(),  # type: ignore[arg-type]
    )

    assert report.skipped == 1
    assert report.failed == 0
    assert Path(report.timeline_path or "").exists()
    failure_files = list((analysis_dir / "failures").glob("*.json"))
    assert len(failure_files) == 1
    failure = json.loads(failure_files[0].read_text(encoding="utf-8"))
    assert failure["attempts"][0]["raw_output"] == "模型拒绝文本"


def test_experimental_endpoint_is_independently_gated(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis"
    experimental_path = analysis_dir / "experimental" / "insights.json"
    experimental_path.parent.mkdir(parents=True)
    experimental_path.write_text('{"signals": []}', encoding="utf-8")

    app = FastAPI()
    app.include_router(router)
    disabled = Settings(analysis_data_dir=analysis_dir, analysis_experimental_enabled=False)
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(settings=disabled)
    with TestClient(app) as client:
        assert client.get("/analysis/experimental").status_code == 404

    enabled = disabled.model_copy(update={"analysis_experimental_enabled": True})
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(settings=enabled)
    with TestClient(app) as client:
        response = client.get("/analysis/experimental")
    assert response.status_code == 200
    assert response.json() == {"signals": []}


def test_experimental_prompt_requires_second_switch_and_uses_optimized_file(
    tmp_path: Path,
) -> None:
    analysis_dir = tmp_path / "analysis"
    prompt_path = analysis_dir / "experimental" / "prompt_context.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("优化后的内部参考", encoding="utf-8")
    disabled = Settings(
        analysis_data_dir=analysis_dir,
        analysis_experimental_enabled=True,
        analysis_experimental_prompt_enabled=False,
    )
    assert load_experimental_prompt_context(disabled) == ""

    enabled = disabled.model_copy(update={"analysis_experimental_prompt_enabled": True})
    assert load_experimental_prompt_context(enabled) == "优化后的内部参考"


def test_personality_prompt_uses_independent_switch_and_context_file(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis"
    prompt_path = analysis_dir / "personality_prompt_context.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("普通人格画像", encoding="utf-8")

    disabled = Settings(
        analysis_data_dir=analysis_dir,
        analysis_personality_prompt_enabled=False,
    )
    assert load_personality_prompt_context(disabled) == ""

    enabled = disabled.model_copy(update={"analysis_personality_prompt_enabled": True})
    assert load_personality_prompt_context(enabled) == "普通人格画像"


def test_personality_prompt_context_drops_quotes_and_keeps_uncertainty() -> None:
    report = PersonalityReport(
        sections=[
            ReportSection(
                key="personality",
                title="性格轮廓",
                observations=[
                    Observation(
                        subject="friend",
                        dimension="特征A",
                        claim="在情境A中表现出倾向A",
                        confidence=0.8,
                        evidence=[Evidence(quote="原始证据A")],
                        alternative_explanations=["也可能由情境A临时引起"],
                    )
                ],
            )
        ]
    )

    context = render_personality_prompt_context(report)

    assert "在情境A中表现出倾向A" in context
    assert "也可能由情境A临时引起" in context
    assert "原始证据A" not in context
    assert "AI 可如何体现" not in context
    assert "生活作息与当前活动由生活时间线负责" in context


def test_life_analysis_context_only_keeps_relevant_non_evidence_observations() -> None:
    profile = LifeProfile(
        habits=[
            LifeHabit(
                subject="friend",
                category="sleep",
                claim="目标角色在时段A更常活跃",
                time_patterns=["时段A"],
                contexts=["常规日"],
                target_fields=["daily_plan", "availability"],
                confidence=0.76,
                evidence=[Evidence(quote="原始证据A")],
                alternative_explanations=["也可能是阶段性安排"],
            )
        ]
    )

    context = render_life_analysis_context(profile, friend_name="TA")

    assert "目标角色在时段A更常活跃" in context
    assert "也可能是阶段性安排" in context
    assert "原始证据A" not in context
    assert "主体：TA" in context


def test_life_analysis_context_excludes_non_target_sensitive_and_unmapped_items() -> None:
    profile = LifeProfile(
        habits=[
            LifeHabit(
                subject="self",
                category="sleep",
                claim="用户习惯不应进入目标角色画像",
                target_fields=["daily_plan"],
                confidence=0.95,
            ),
            LifeHabit(
                subject="both",
                category="activity",
                claim="共同活动不应进入目标角色画像",
                target_fields=["current_activity"],
                confidence=0.95,
            ),
            LifeHabit(
                subject="friend",
                category="activity",
                claim="目标角色空闲时常进行活动A",
                target_fields=["current_activity", "topic_seed"],
                confidence=0.9,
            ),
            LifeHabit(
                subject="friend",
                category="activity",
                claim="敏感关系活动不应进入生活画像",
                target_fields=["current_activity"],
                confidence=0.9,
                sensitive_relationship_context=True,
            ),
            LifeHabit(
                subject="friend",
                category="availability",
                claim="没有目标字段的条目不可注入",
                confidence=0.9,
            ),
        ]
    )

    context = render_life_analysis_context(profile)

    assert "用户习惯不应进入目标角色画像" not in context
    assert "共同活动不应进入目标角色画像" not in context
    assert "目标角色空闲时常进行活动A" in context
    assert "敏感关系活动不应进入生活画像" not in context
    assert "没有目标字段的条目不可注入" not in context
    assert "可影响字段：current_activity、topic_seed" in context


def test_personality_reduce_never_merges_self_observation_into_friend() -> None:
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]
    result = _block_result(block, experimental=False)
    result.personality_observations.extend(
        [
            Observation(
                subject="self",
                dimension="特征A",
                claim="用户表现出特征A",
                confidence=0.95,
            ),
            Observation(
                subject="friend",
                dimension="特征A",
                claim="目标角色表现出特征A",
                confidence=0.7,
            ),
        ]
    )

    report = reduce_personality([result])
    claims = [
        observation.claim
        for section in report.sections
        for observation in section.observations
    ]

    assert "用户表现出特征A" not in claims
    assert "目标角色表现出特征A" in claims


def test_optimized_experimental_context_drops_quotes_and_low_confidence() -> None:
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]
    result = _block_result(block, experimental=True)
    report = reduce_experimental([result])

    context = render_experimental_prompt_context(report)

    assert "还好，你呢" not in context
    assert "这一次互动表现出回应意愿" not in context
    assert "当前没有达到注入阈值" in context


def test_optimized_experimental_context_keeps_claim_but_not_quote() -> None:
    block = build_analysis_blocks([_session()], self_name="我", friend_name="TA")[0]
    result = _block_result(block, experimental=True)
    result.experimental_signals[0].confidence = 0.7

    context = render_experimental_prompt_context(reduce_experimental([result]))

    assert "这一次互动表现出回应意愿" in context
    assert "还好，你呢" not in context
    assert "不要向用户复述" in context


def test_experimental_prompt_switch_requires_generation_switch() -> None:
    with pytest.raises(ValueError, match="ANALYSIS_EXPERIMENTAL_ENABLED"):
        Settings(
            analysis_experimental_enabled=False,
            analysis_experimental_prompt_enabled=True,
        )
