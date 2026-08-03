"""主动消息分析报告的数据质量评估。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from xuwen.analysis.models import ProactiveAnalysisReport, ProactiveOpeningRecord

QualityStatus = Literal["ready", "limited", "not_ready"]

_PLACEHOLDER_RE = re.compile(r"\[(?:图片|表情|动画表情|语音|视频|文件|撤回)\]")
_REPLY_RE = re.compile(r"\[回复[^\]]*\]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_SPECIFIC_TYPES = {
    "care",
    "continue_topic",
    "self_share",
    "question_probe",
}


@dataclass(slots=True)
class ProactiveQualityReport:
    status: QualityStatus
    recommended_enabled: bool
    summary: str
    metrics: dict[str, int | float | str]
    issues: list[str]
    recommendations: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_proactive_quality(
    report: ProactiveAnalysisReport,
    *,
    min_gap_minutes: int = 120,
) -> ProactiveQualityReport:
    eligible = [
        opening
        for opening in report.openings
        if opening.initiator == "friend"
        and opening.idle_gap_minutes is not None
        and opening.idle_gap_minutes >= min_gap_minutes
    ]
    meaningful = [opening for opening in eligible if _is_meaningful(opening)]
    specific = [opening for opening in meaningful if _is_specific(opening)]
    placeholder = [opening for opening in eligible if _PLACEHOLDER_RE.search(opening.content)]
    placeholder_only = [opening for opening in eligible if not _clean_content(opening.content)]
    other = [opening for opening in eligible if opening.opening_type == "other"]
    attributed = [opening for opening in eligible if opening.reason_category is not None]

    total = len(eligible)
    meaningful_ratio = _ratio(len(meaningful), total)
    specific_ratio = _ratio(len(specific), total)
    placeholder_ratio = _ratio(len(placeholder), total)
    other_ratio = _ratio(len(other), total)
    attribution_ratio = _ratio(len(attributed), total)
    issues: list[str] = []
    recommendations: list[str] = []
    critical = False

    if total < 12:
        critical = True
        issues.append(f"有效长间隔主动开场只有 {total} 条，调度画像证据不足")
        recommendations.append("补充更长时间范围的聊天记录后重新运行主动倾向分析")
    elif total < 30:
        issues.append(f"有效长间隔主动开场只有 {total} 条，画像稳定性有限")

    if meaningful_ratio < 0.45:
        critical = True
        issues.append(f"有实际文本信息的开场仅占 {meaningful_ratio:.0%}")
    elif meaningful_ratio < 0.70:
        issues.append(f"有实际文本信息的开场占 {meaningful_ratio:.0%}，低信息样本偏多")

    if placeholder_ratio > 0.35:
        issues.append(f"含图片/表情等占位符的样本占 {placeholder_ratio:.0%}")
    if specific_ratio < 0.30:
        issues.append(f"可形成具体话题的开场仅占 {specific_ratio:.0%}，容易触发通用兜底")
        recommendations.append("不要启用自动主动消息，直到具体话题样本占比提升")
    if other_ratio > 0.60:
        issues.append(f"无法分类的 other 开场占 {other_ratio:.0%}")
        recommendations.append("检查开场分类和占位符清洗规则")
    if report.ai_analysis_status != "not_requested" and attribution_ratio < 0.70:
        issues.append(f"AI 动机归因覆盖率只有 {attribution_ratio:.0%}")
        recommendations.append("重新运行主动倾向分析，补齐失败的 AI 归因批次")

    fallback_risk = "low"
    if specific_ratio < 0.30 or meaningful_ratio < 0.70:
        fallback_risk = "high"
    elif placeholder_ratio > 0.20 or other_ratio > 0.50:
        fallback_risk = "medium"

    if critical:
        status: QualityStatus = "not_ready"
    elif issues:
        status = "limited"
    else:
        status = "ready"
    recommended = status == "ready"
    summary = {
        "ready": "数据质量通过，建议启用主动消息。",
        "limited": "数据可用于观察调度倾向，但不建议自动发送主动消息。",
        "not_ready": "数据质量不足，不建议启用主动消息。",
    }[status]
    if not recommendations and not recommended:
        recommendations.append("先修复列出的问题，再重新运行质量检查")

    return ProactiveQualityReport(
        status=status,
        recommended_enabled=recommended,
        summary=summary,
        metrics={
            "total_openings": len(report.openings),
            "eligible_openings": total,
            "meaningful_openings": len(meaningful),
            "meaningful_ratio": meaningful_ratio,
            "specific_openings": len(specific),
            "specific_ratio": specific_ratio,
            "placeholder_openings": len(placeholder),
            "placeholder_only_openings": len(placeholder_only),
            "placeholder_ratio": placeholder_ratio,
            "other_ratio": other_ratio,
            "attribution_ratio": attribution_ratio,
            "fallback_risk": fallback_risk,
        },
        issues=issues,
        recommendations=list(dict.fromkeys(recommendations)),
    )


def _clean_content(text: str) -> str:
    cleaned = _PLACEHOLDER_RE.sub(" ", text)
    cleaned = _REPLY_RE.sub(" ", cleaned)
    return " ".join(cleaned.split()).strip("。！？!?~～,，:： ")


def _is_meaningful(opening: ProactiveOpeningRecord) -> bool:
    cleaned = _clean_content(opening.content)
    tokens = _TOKEN_RE.findall(cleaned)
    return bool(tokens)


def _is_specific(opening: ProactiveOpeningRecord) -> bool:
    cleaned = _clean_content(opening.content)
    tokens = _TOKEN_RE.findall(cleaned)
    return opening.opening_type in _SPECIFIC_TYPES or len(tokens) >= 8


def _ratio(value: int, total: int) -> float:
    return round(value / total, 4) if total else 0.0
