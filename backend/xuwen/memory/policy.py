"""记忆来源（source）策略。

集中定义不同 source 的语义、权重、可参与的检索类别。
所有 retriever / writer / persona 都从这里读，不在业务代码里散落 if-else。

来源类型：
- human_original：导入的真人原始聊天（最高信任，唯一允许参与 persona / 风格蒸馏的来源）。
- user_new：新会话里用户输入（用于"用户最近发生了什么"事实记忆，不参与风格）。
- ai_generated：AI 分身生成的回复（仅用于连续性检索；默认不跨会话长期累积）。
- history：旧版兼容标记，等同 human_original（旧库不需要重导）。
- live：旧版兼容标记，运行时回写但未细分（旧库可能存在）。
"""

from __future__ import annotations

from typing import Literal

from xuwen.config import Settings

MemorySource = Literal[
    "human_original",
    "user_new",
    "ai_generated",
    "history",
    "live",
]

# 允许作为 persona / 风格证据 —— 只有真人原始聊天
PERSONA_SOURCES: frozenset[str] = frozenset({"human_original", "history"})

# 允许参与"用户近况"事实记忆
USER_FACT_SOURCES: frozenset[str] = frozenset({"user_new"})

# 允许参与运行时连续性检索（live 语义召回）
CONTINUITY_SOURCES: frozenset[str] = frozenset({"user_new", "ai_generated", "live"})


def is_persona_eligible(source: str) -> bool:
    """该 source 是否允许参与 persona / 风格蒸馏。"""
    return source in PERSONA_SOURCES


def is_continuity_eligible(source: str) -> bool:
    """该 source 是否允许参与运行时连续性检索。"""
    return source in CONTINUITY_SOURCES


def source_weight(source: str, settings: Settings) -> float:
    """该 source 在融合时的默认权重。

    - human_original / history：最高，作为真人语气基准
    - user_new：略低于历史（事实价值高、风格价值 0）
    - ai_generated：受 AI_GENERATED_SOURCE_WEIGHT 控制（默认 0.25）
    - live：兼容旧库
    """
    if source in {"human_original", "history"}:
        return settings.history_source_weight
    if source == "live":
        return settings.live_source_weight
    if source == "user_new":
        # user_new 视为高权重事实信号，但不超过 history
        return min(settings.live_source_weight, 1.0)
    if source == "ai_generated":
        return settings.ai_generated_source_weight
    return settings.history_source_weight


def label_for_ui(source: str) -> str:
    """前端记忆溯源的可读标签。"""
    return {
        "human_original": "真人历史片段",
        "history": "真人历史片段",
        "user_new": "你最近说过",
        "ai_generated": "此前 AI 回复",
        "live": "运行时记忆",
    }.get(source, "记忆片段")


def ai_generated_long_term_enabled(settings: Settings) -> bool:
    return bool(settings.ai_generated_long_term_enabled)
