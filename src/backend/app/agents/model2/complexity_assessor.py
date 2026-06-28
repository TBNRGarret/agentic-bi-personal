from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...services.llm import LlmService

from .contracts import SelectorResult
from .utils import normalize_text


@dataclass(frozen=True)
class ComplexityAssessment:
    level: str
    score: float
    signals: list[str]
    recommended_steps: int
    strategy: str = "rule_based"
    llm_rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": round(self.score, 4),
            "signals": self.signals,
            "recommendedSteps": self.recommended_steps,
            "strategy": self.strategy,
            "llmRationale": self.llm_rationale,
        }


class ComplexityAssessor:
    """Hybrid rule-based + LLM gate deciding when Model 2 should ask the LLM to decompose."""

    """
    Overview
    1. Natural language question + SelectorResult + join_paths + request_plan
    2. Rule-based assess()
        a. Count signals: multi-table, join paths, temporal comparison,
           multi-dim grouping, subquery need, multiple metrics, full report
        b. Score 0.0-1.0, classify: simple (<0.25), moderate (0.25-0.5), complex (>=0.5)
        c. recommendedSteps: simple=1, moderate=2, complex=3-4
    3. LLM hybrid gate (optional)
        a. If LLM configured, call llm.assess_complexity()
        b. LLM can override rule-based level/score/signals
        c. Strategy: "hybrid_rule_llm" vs "rule_based"
    4. Return ComplexityAssessment {level, score, signals, recommendedSteps}

    Used by DecomposerAgent to decide: simple -> direct SQL, complex -> decompose.
    """

    async def assess_hybrid(
        self,
        question: str,
        *,
        selector: SelectorResult,
        join_paths: list[dict[str, Any]],
        request_plan: dict[str, Any] | None = None,
        llm: LlmService | None = None,
    ) -> ComplexityAssessment:
        base = self.assess(question, selector=selector, join_paths=join_paths, request_plan=request_plan)
        if not llm or not llm.settings.llm_configured:
            return base

        llm_result = await llm.assess_complexity(
            question=question,
            tables=selector.selected_tables,
            join_paths=join_paths,
            rule_based_assessment=base.to_dict(),
        )
        if llm_result.get("status") == "success":
            signals = list(base.signals)
            for sig in llm_result.get("signals") or []:
                if sig not in signals:
                    signals.append(sig)
            try:
                score = float(llm_result.get("score") or base.score)
            except (ValueError, TypeError):
                score = base.score
            try:
                steps = int(llm_result.get("recommendedSteps") or base.recommended_steps)
            except (ValueError, TypeError):
                steps = base.recommended_steps
            return ComplexityAssessment(
                level=str(llm_result.get("level") or base.level),
                score=max(base.score, score),
                signals=signals,
                recommended_steps=steps,
                strategy="hybrid_rule_llm",
                llm_rationale=str(llm_result.get("rationale") or ""),
            )
        return base

    def assess(
        self,
        question: str,
        *,
        selector: SelectorResult,
        join_paths: list[dict[str, Any]],
        request_plan: dict[str, Any] | None = None,
    ) -> ComplexityAssessment:
        text = normalize_text(question)
        slots = (request_plan or {}).get("slots") if isinstance(request_plan, dict) else {}
        score = 0.0
        signals: list[str] = []

        if len(selector.selected_tables) > 1:
            score += 0.2
            signals.append(f"{len(selector.selected_tables)} tables selected")

        if join_paths:
            score += 0.15
            signals.append(f"{len(join_paths)} graph join path(s)")

        if _has_temporal_comparison(text):
            score += 0.2
            signals.append("temporal comparison detected")

        dimensions = _slot_values(slots, "dimensions")
        if len(dimensions) >= 2 or _has_multi_dimensional_grouping(text):
            score += 0.15
            signals.append("multi-dimensional grouping")

        if _needs_subquery(text):
            score += 0.2
            signals.append("subquery, rank, or CTE likely needed")

        metrics = _slot_values(slots, "metric")
        if len(metrics) >= 2 or _has_multiple_metrics(text):
            score += 0.1
            signals.append("multiple metrics requested")

        if _asks_for_full_report(text):
            score += 0.15
            signals.append("broad analytical report requested")

        score = min(score, 1.0)
        if score >= 0.5:
            level = "complex"
            steps = 3 if score < 0.75 else 4
        elif score >= 0.25:
            level = "moderate"
            steps = 2
        else:
            level = "simple"
            steps = 1

        if not signals:
            signals.append("single-table direct query")
        return ComplexityAssessment(
            level=level,
            score=score,
            signals=signals,
            recommended_steps=steps,
        )


def _slot_values(slots: Any, name: str) -> list[str]:
    if not isinstance(slots, dict):
        return []
    slot = slots.get(name) or {}
    value = slot.get("value") if isinstance(slot, dict) else None
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value not in (None, "", []):
        return [str(value)]
    return []


def _has_temporal_comparison(text: str) -> bool:
    return any(
        pattern in text
        for pattern in (
            "so sanh",
            "compare",
            "vs",
            "versus",
            "tuan truoc",
            "thang truoc",
            "hom qua",
            "last week",
            "last month",
            "previous",
            "week over week",
            "month over month",
            "year over year",
            "wow",
            "mom",
            "yoy",
        )
    )


def _has_multi_dimensional_grouping(text: str) -> bool:
    grouping_markers = (
        "theo tung",
        "tung",
        "moi",
        "each",
        "by each",
        "per",
        "breakdown by",
    )
    dimension_terms = (
        "model",
        "provider",
        "source app",
        "source_app",
        "service",
        "ngay",
        "date",
        "status",
        "environment",
    )
    marker_count = sum(1 for marker in grouping_markers if marker in text)
    dimension_count = sum(1 for term in dimension_terms if term in text)
    return marker_count >= 1 and dimension_count >= 2


def _needs_subquery(text: str) -> bool:
    return any(
        pattern in text
        for pattern in (
            "top",
            "highest",
            "lowest",
            "rank",
            "xep hang",
            "cao nhat",
            "thap nhat",
            "lon nhat",
            "nho nhat",
            "percentile",
            "above average",
            "below average",
            "outlier",
            "bat thuong",
        )
    )


def _has_multiple_metrics(text: str) -> bool:
    metric_terms = (
        "cost",
        "chi phi",
        "latency",
        "do tre",
        "token",
        "request",
        "error",
        "loi",
        "count",
        "rate",
        "p95",
    )
    hits = sum(1 for term in metric_terms if term in text)
    return hits >= 2


def _asks_for_full_report(text: str) -> bool:
    return any(
        pattern in text
        for pattern in (
            "phan tich",
            "analysis",
            "root cause",
            "nguyen nhan",
            "bao cao",
            "report",
            "tong quan",
            "overview",
        )
    )
