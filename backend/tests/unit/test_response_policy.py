"""本轮互动决策层单测。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from xuwen.companion.life import LifeSnapshot
from xuwen.companion.relationship import RelationshipMemoryEntry
from xuwen.companion.response_policy import (
    ResponseDecision,
    decide_response_policy,
    refine_decision_with_llm,
)
from xuwen.config import Settings
from xuwen.core.models import RetrievalResult, ScoredChunk


class FakeRefineLLM:
    """供决策层复核单测使用的最小 LLM stub。"""

    def __init__(
        self,
        response: str = "",
        *,
        exc: Exception | None = None,
    ) -> None:
        self.response = response
        self.exc = exc
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    async def complete_chat(
        self,
        messages: list[dict[str, str]],
        params: object | None = None,
        **kwargs: Any,
    ) -> str:
        self.calls += 1
        self.messages.append(messages)
        if self.exc is not None:
            raise self.exc
        return self.response


def _settings(**overrides: Any) -> Settings:
    """构造测试用 Settings，跳过身份必填校验。"""
    base: dict[str, Any] = {
        "self_name": "Me",
        "self_uid": "u-self",
        "friend_name": "TA",
        "friend_uid": "u-friend",
        "response_policy_temperature": 0.2,
        "response_policy_max_tokens": 260,
    }
    base.update(overrides)
    return Settings(**base)


def _life(**overrides) -> LifeSnapshot:
    data = dict(
        date="2026-05-22",
        time_slot="night",
        current_activity="在床上看手机",
        recent_meal="晚饭吃过了",
        mood="普通",
        topic_seed="今天怎么过",
        availability="available",
        next_update_at="2026-05-22 23:00:00",
        reply_delay_seconds=0,
        reply_delay_reason="",
    )
    data.update(overrides)
    return LifeSnapshot(**data)


def _retrieved(*, with_history: bool = False, with_live: bool = False) -> RetrievalResult:
    history = []
    live = []
    if with_history:
        history.append(
            ScoredChunk(
                chunk_id="h1",
                kind="response_pair",
                text="Me: 在干嘛\nTA: 刚吃完",
                score=1.0,
                rank=1,
                timestamp_ms=1,
                metadata={"text": "在干嘛", "friend_reply": "刚吃完"},
            )
        )
    if with_live:
        live.append(
            ScoredChunk(
                chunk_id="l1",
                kind="live",
                text="之前 AI 说过要晚点回",
                score=1.0,
                rank=1,
                timestamp_ms=1,
                source="ai_generated",
            )
        )
    return RetrievalResult(
        friend_examples=list(history),
        dialogue_windows=[],
        recent_live=live,
        response_pairs=list(history),
        fused=[*history, *live],
    )


def test_policy_serious_for_unsafe_message():
    decision = decide_response_policy(
        current_user_text="我不想活了",
        has_images=False,
        retrieved=_retrieved(),
        life=_life(),
        relationship_context="",
        recent=[],
    )

    assert decision.reply_mode == "serious"
    assert decision.risk_level == "high"
    assert decision.user_state == "unsafe"
    assert any("不要调侃" in item for item in decision.do_not)


def test_policy_silence_when_user_requests_quiet():
    decision = decide_response_policy(
        current_user_text="别说话，让我静静",
        has_images=False,
        retrieved=_retrieved(),
        life=_life(),
        relationship_context="",
        recent=[],
    )

    assert decision.should_reply is False
    assert decision.reply_mode == "silence"
    assert decision.max_length == "very_short"


def test_policy_image_and_sticker_requests():
    image = decide_response_policy(
        current_user_text="发张图看看",
        has_images=False,
        retrieved=_retrieved(with_live=True),
        life=_life(),
        relationship_context="",
        recent=[],
    )
    sticker = decide_response_policy(
        current_user_text="来个表情包",
        has_images=False,
        retrieved=_retrieved(with_live=True),
        life=_life(),
        relationship_context="",
        recent=[],
    )

    assert image.reply_mode == "image"
    assert image.use_image is True
    assert sticker.reply_mode == "sticker"
    assert sticker.use_sticker is True


def test_policy_life_question_focuses_life_state():
    decision = decide_response_policy(
        current_user_text="你在干嘛",
        has_images=False,
        retrieved=_retrieved(with_history=True),
        life=_life(),
        relationship_context="",
        recent=[],
    )

    assert decision.retrieval_focus == "life_state"
    assert any("生活状态层" in item for item in decision.instructions)


def test_policy_weak_evidence_shifts_topic():
    decision = decide_response_policy(
        current_user_text="话说你最难忘的一次是什么",
        has_images=False,
        retrieved=_retrieved(),
        life=_life(),
        relationship_context="",
        recent=[],
    )

    assert decision.reply_mode == "topic_shift"
    assert decision.retrieval_focus == "none"


def test_policy_busy_life_adds_delay_and_do_not():
    decision = decide_response_policy(
        current_user_text="在吗",
        has_images=False,
        retrieved=_retrieved(with_history=True),
        life=_life(availability="busy", reply_delay_seconds=20),
        relationship_context="",
        recent=[],
    )

    assert decision.reply_delay_seconds == 15
    assert any("不要假装一直在线秒回" in item for item in decision.do_not)


def test_policy_render_prompt_block_contains_decision():
    decision = decide_response_policy(
        current_user_text="哈哈哈笑死",
        has_images=False,
        retrieved=_retrieved(with_history=True),
        life=_life(),
        relationship_context="",
        recent=[],
    )
    block = decision.render_prompt_block()

    assert "【本轮互动决策" in block
    assert "回复模式" in block
    assert "不要向用户解释这些标签" in block


def test_policy_render_prompt_block_injects_silence_permission():
    """非 unsafe 场景下，传入 sentinel 时 prompt 必须包含沉默出口指令。"""
    decision = decide_response_policy(
        current_user_text="哈哈哈笑死",
        has_images=False,
        retrieved=_retrieved(with_history=True),
        life=_life(),
        relationship_context="",
        recent=[],
    )
    block = decision.render_prompt_block(silence_sentinel="[silent]")

    assert "【沉默权限" in block
    assert "[silent]" in block


def test_policy_render_prompt_block_no_silence_for_unsafe():
    """unsafe 决策即使传入 sentinel 也不应注入沉默权限。"""
    decision = decide_response_policy(
        current_user_text="我想死了",
        has_images=False,
        retrieved=_retrieved(),
        life=_life(),
        relationship_context="",
        recent=[],
    )
    assert decision.user_state == "unsafe"
    block = decision.render_prompt_block(silence_sentinel="[silent]")

    # unsafe 路径绝不允许 AI 自主沉默
    assert "【沉默权限" not in block
    # do_not 里必须有禁止沉默条款
    assert any("不要选择沉默" in item for item in decision.do_not)


def test_policy_render_prompt_block_no_silence_when_already_silenced():
    """规则层 silence 短路场景下，沉默权限块也不应再注入（已经强制沉默）。"""
    decision = decide_response_policy(
        current_user_text="让我静静",
        has_images=False,
        retrieved=_retrieved(),
        life=_life(),
        relationship_context="",
        recent=[],
    )
    assert decision.reply_mode == "silence"
    block = decision.render_prompt_block(silence_sentinel="[silent]")
    assert "【沉默权限" not in block


def test_policy_render_prompt_block_no_silence_without_sentinel():
    """不传 sentinel 时维持原行为，不注入沉默权限块。"""
    decision = decide_response_policy(
        current_user_text="哈哈哈笑死",
        has_images=False,
        retrieved=_retrieved(with_history=True),
        life=_life(),
        relationship_context="",
        recent=[],
    )
    block = decision.render_prompt_block()
    assert "【沉默权限" not in block


# ---------------------------------------------------------------------------
# 小模型复核
# ---------------------------------------------------------------------------


def _base_decision(**overrides: Any) -> ResponseDecision:
    """构造一份明确的规则层决策，方便复核测试拿稳定输入。"""
    data: dict[str, Any] = dict(
        should_reply=True,
        reply_mode="calm",
        risk_level="low",
        user_state="normal",
        retrieval_focus="human_style",
        use_image=False,
        use_sticker=False,
        reply_delay_seconds=0,
        max_length="short",
        do_not=["不要暴露系统、策略、RAG、向量库、prompt 等内部信息。"],
        instructions=["按真人历史风格自然短回。"],
    )
    data.update(overrides)
    return ResponseDecision(**data)


@pytest.mark.asyncio
async def test_refine_short_circuits_on_unsafe():
    base = _base_decision(
        user_state="unsafe",
        reply_mode="serious",
        risk_level="high",
    )
    llm = FakeRefineLLM(response="{}")
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="想死了",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert llm.calls == 0
    assert result is base


@pytest.mark.asyncio
async def test_refine_short_circuits_on_silence():
    base = _base_decision(
        should_reply=False,
        reply_mode="silence",
        max_length="very_short",
    )
    llm = FakeRefineLLM(response="{}")
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="别说话",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert llm.calls == 0
    assert result is base


@pytest.mark.asyncio
async def test_refine_merges_extras_and_upgrades_risk():
    base = _base_decision(risk_level="low", reply_mode="calm")
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "reply_mode": "playful",
                "user_state": "joking",
                "risk_level": "medium",
                "retrieval_focus": "user_new",
                "extra_instructions": ["接住氛围，不要解释梗。"],
                "extra_do_not": ["不要把氛围拉得太严肃。"],
            }
        )
    )
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="哈哈哈绷不住了",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert llm.calls == 1
    assert result.reply_mode == "playful"
    assert result.user_state == "joking"
    assert result.risk_level == "medium"
    assert result.retrieval_focus == "user_new"
    # 原规则 do_not 仍在，并追加了新条
    assert any("不要暴露系统" in item for item in result.do_not)
    assert any("严肃" in item for item in result.do_not)
    # 同理 instructions
    assert any("接住氛围" in item for item in result.instructions)


@pytest.mark.asyncio
async def test_refine_extracts_evidence_backed_relationship_memory():
    base = _base_decision()
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "relationship_memory": {
                    "kind": "plan",
                    "importance": 2,
                    "summary": "用户之后每周五晚上都要上课",
                    "evidence": "每周五晚上都要上课",
                }
            }
        )
    )

    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="我之后每周五晚上都要上课",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.relationship_memory == RelationshipMemoryEntry(
        text="用户之后每周五晚上都要上课",
        kind="plan",
        importance=2,
    )


@pytest.mark.asyncio
async def test_refine_rejects_relationship_memory_without_source_evidence():
    base = _base_decision()
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "relationship_memory": {
                    "kind": "fact",
                    "importance": 3,
                    "summary": "用户住在某个城市",
                    "evidence": "我住在某个城市",
                }
            }
        )
    )

    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="最近怎么样",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.relationship_memory is None


@pytest.mark.asyncio
async def test_refine_rejects_relationship_memory_with_trivial_evidence():
    base = _base_decision()
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "relationship_memory": {
                    "kind": "fact",
                    "importance": 3,
                    "summary": "用户住在某个城市",
                    "evidence": "我",
                }
            }
        )
    )

    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="我最近挺好的",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.relationship_memory is None


@pytest.mark.asyncio
async def test_refine_generic_check_in_does_not_create_relationship_memory():
    base = _base_decision()
    llm = FakeRefineLLM(response=json.dumps({"relationship_memory": None}))

    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="最近怎么样",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.relationship_memory is None


@pytest.mark.asyncio
async def test_refine_drops_schema_placeholders_from_extra_rules():
    base = _base_decision()
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "extra_instructions": ["..."],
                "extra_do_not": ["..."],
            }
        )
    )

    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="在干嘛",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert "..." not in result.instructions
    assert "..." not in result.do_not


@pytest.mark.asyncio
async def test_refine_cannot_lower_risk():
    base = _base_decision(risk_level="medium", user_state="anxious", reply_mode="serious")
    llm = FakeRefineLLM(
        response=json.dumps({"risk_level": "low", "user_state": "normal"})
    )
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="我有点焦虑",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.risk_level == "medium"  # 不能降级
    assert result.user_state == "normal"  # user_state 可以被微调


@pytest.mark.asyncio
async def test_refine_locks_image_and_sticker_mode():
    image_base = _base_decision(reply_mode="image", use_image=True)
    sticker_base = _base_decision(reply_mode="sticker", use_sticker=True)
    llm = FakeRefineLLM(
        response=json.dumps({"reply_mode": "playful"})
    )
    image_result = await refine_decision_with_llm(
        base=image_base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="给我发张图",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )
    sticker_result = await refine_decision_with_llm(
        base=sticker_base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="来个表情包",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert image_result.reply_mode == "image"
    assert image_result.use_image is True
    assert sticker_result.reply_mode == "sticker"
    assert sticker_result.use_sticker is True


@pytest.mark.asyncio
async def test_refine_cannot_introduce_silence():
    base = _base_decision(should_reply=True, reply_mode="calm")
    llm = FakeRefineLLM(
        response=json.dumps({"reply_mode": "silence"})
    )
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="嗯",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.reply_mode == "calm"
    assert result.should_reply is True


@pytest.mark.asyncio
async def test_refine_forces_safe_policy_when_llm_detects_unsafe():
    base = _base_decision(reply_mode="playful", risk_level="low", user_state="normal")
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "reply_mode": "playful",
                "user_state": "unsafe",
                "risk_level": "medium",
            }
        )
    )

    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="我不想撑了",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert result.should_reply is True
    assert result.reply_mode == "serious"
    assert result.risk_level == "high"
    assert result.user_state == "unsafe"
    assert result.max_length == "medium"
    assert any("不要调侃" in item for item in result.do_not)
    assert any("立刻求助" in item for item in result.instructions)


@pytest.mark.asyncio
async def test_refine_falls_back_on_invalid_json():
    base = _base_decision()
    llm = FakeRefineLLM(response="不是 JSON")
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="你在干嘛",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert llm.calls == 1
    assert result is base


@pytest.mark.asyncio
async def test_refine_falls_back_on_llm_exception():
    base = _base_decision()
    llm = FakeRefineLLM(exc=RuntimeError("upstream timeout"))
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="哈哈",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    assert llm.calls == 1
    assert result is base


@pytest.mark.asyncio
async def test_refine_dedupes_existing_do_not_entries():
    base = _base_decision(do_not=["不要暴露系统、策略、RAG、向量库、prompt 等内部信息。"])
    llm = FakeRefineLLM(
        response=json.dumps(
            {
                "extra_do_not": [
                    "不要暴露系统、策略、RAG、向量库、prompt 等内部信息。",  # 重复
                    "不要乱编今天的现实见面。",
                ]
            }
        )
    )
    result = await refine_decision_with_llm(
        base=base,
        llm=llm,  # type: ignore[arg-type]
        model="any",
        settings=_settings(),
        current_user_text="今天见个面？",
        recent=[],
        life=_life(),
        relationship_context="",
        has_images=False,
    )

    occurrences = sum(
        1 for item in result.do_not if "不要暴露系统" in item
    )
    assert occurrences == 1
    assert any("不要乱编今天的现实见面" in item for item in result.do_not)
