"""主动消息分析报告的数据质量评估。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from xuwen.analysis.models import ProactiveAnalysisReport, ProactiveOpeningRecord
from xuwen.content_i18n import content_text

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
        issues.append(
            content_text(
                "Only {p0} valid proactive openings after long gaps; insufficient evidence for a scheduling profile",
                p0=total,
            )
        )
        recommendations.append(
            content_text("Import a longer date range and rerun proactive analysis")
        )
    elif total < 30:
        issues.append(
            content_text(
                "Only {p0} valid proactive openings after long gaps; profile stability is limited",
                p0=total,
            )
        )

    if meaningful_ratio < 0.45:
        critical = True
        issues.append(
            content_text("Only {p0:.0%} of openings contain meaningful text", p0=meaningful_ratio)
        )
    elif meaningful_ratio < 0.70:
        issues.append(
            content_text(
                "{p0:.0%} of openings contain meaningful text; low-information samples are common",
                p0=meaningful_ratio,
            )
        )

    if placeholder_ratio > 0.35:
        issues.append(
            content_text(
                "{p0:.0%} of samples contain image/sticker placeholders", p0=placeholder_ratio
            )
        )
    if specific_ratio < 0.30:
        issues.append(
            content_text(
                "Only {p0:.0%} of openings offer a specific topic; generic fallbacks may be frequent",
                p0=specific_ratio,
            )
        )
        recommendations.append(
            content_text(
                "Keep automatic proactive messages disabled until the share of specific-topic samples improves"
            )
        )
    if other_ratio > 0.60:
        issues.append(content_text("{p0:.0%} of openings are unclassified (other)", p0=other_ratio))
        recommendations.append(
            content_text("Review opening classification and placeholder cleaning rules")
        )
    if report.ai_analysis_status != "not_requested" and attribution_ratio < 0.70:
        issues.append(
            content_text(
                "AI motivation attribution covers only {p0:.0%} of samples", p0=attribution_ratio
            )
        )
        recommendations.append(
            content_text("Rerun proactive analysis to fill in failed AI attribution batches")
        )

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
        "ready": content_text("Data quality passed. Proactive messages can be enabled."),
        "limited": content_text(
            "The data can inform scheduling preferences, but automatic proactive messages are not recommended."
        ),
        "not_ready": content_text("Insufficient data quality. Keep proactive messages disabled."),
    }[status]
    if not recommendations and not recommended:
        recommendations.append(content_text("Fix the listed issues, then rerun the quality check"))

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
