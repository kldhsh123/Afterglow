"""主动消息数据质量检查测试。"""

from __future__ import annotations

from xuwen.analysis.models import ProactiveAnalysisReport, ProactiveOpeningRecord
from xuwen.analysis.proactive_quality import evaluate_proactive_quality


def _opening(index: int, content: str, opening_type: str = "self_share") -> ProactiveOpeningRecord:
    return ProactiveOpeningRecord(
        opening_id=f"opening-{index}",
        session_id=f"session-{index}",
        timestamp_ms=1_700_000_000_000 + index,
        occurred_at="2026-01-01T12:00:00+08:00",
        hour=12,
        weekday=3,
        idle_gap_minutes=180,
        opening_type=opening_type,  # type: ignore[arg-type]
        content=content,
    )


def test_quality_recommends_clean_specific_dataset() -> None:
    report = ProactiveAnalysisReport(
        openings=[
            _opening(index, f"我刚看到第{index}个有意思的事情")
            for index in range(40)
        ]
    )

    quality = evaluate_proactive_quality(report)

    assert quality.status == "ready"
    assert quality.recommended_enabled is True
    assert quality.metrics["fallback_risk"] == "low"


def test_quality_rejects_placeholder_heavy_dataset() -> None:
    report = ProactiveAnalysisReport(
        openings=[
            _opening(index, "[图片] [图片]", "other")
            for index in range(40)
        ]
    )

    quality = evaluate_proactive_quality(report)

    assert quality.status == "not_ready"
    assert quality.recommended_enabled is False
    assert quality.metrics["fallback_risk"] == "high"
    assert any("占位符" in issue for issue in quality.issues)
    assert any("兜底" in issue for issue in quality.issues)
