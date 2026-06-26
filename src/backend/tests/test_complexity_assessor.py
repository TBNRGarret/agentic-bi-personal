"""Tests for ComplexityAssessor and ComplexityAssessment."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.agents.model2.complexity_assessor import ComplexityAssessor, ComplexityAssessment
from app.agents.model2.contracts import SelectorResult


def test_complexity_assessor_rule_based() -> None:
    assessor = ComplexityAssessor()
    selector = SelectorResult(
        selected_tables=[{"tableName": "vsf_raw.users"}],
        selected_table_names=["vsf_raw.users"],
        schema_prompt="",
        decisions=[],
        confidence=0.9,
        strategy="rule_based",
        graph_edges=[],
    )

    simple = assessor.assess(
        question="Count users",
        selector=selector,
        join_paths=[],
        request_plan={"slots": {"metrics": ["count"]}},
    )
    assert isinstance(simple, ComplexityAssessment)
    assert simple.level == "low"
    assert simple.score < 0.4
    assert simple.strategy == "rule_based"

    complex_plan = assessor.assess(
        question="Show active users and average order amount with temporal comparison over last 30 days and breakdown by region and product catalog full report",
        selector=selector,
        join_paths=[{"tables": ["vsf_raw.users", "vsf_raw.orders"], "edges": []}],
        request_plan={"slots": {"metrics": ["count", "avg"], "dimensions": ["region", "product"], "time_range": "last 30 days"}},
    )
    assert complex_plan.level in {"medium", "high"}
    assert complex_plan.score >= 0.4
    assert len(complex_plan.complexity_drivers) > 0


@pytest.mark.asyncio
async def test_complexity_assessor_assess_hybrid() -> None:
    llm = MagicMock()
    llm.assess_complexity = AsyncMock(return_value={
        "status": "success",
        "level": "high",
        "score": 0.85,
        "rationale": "Requires temporal comparison and multi-table join",
        "complexityDrivers": ["multi_table_join", "temporal_comparison"],
    })

    assessor = ComplexityAssessor()
    selector = SelectorResult(
        selected_tables=[{"tableName": "vsf_raw.users"}, {"tableName": "vsf_raw.orders"}],
        selected_table_names=["vsf_raw.users", "vsf_raw.orders"],
        schema_prompt="",
        decisions=[],
        confidence=0.85,
        strategy="hybrid",
        graph_edges=[],
    )

    hybrid = await assessor.assess_hybrid(
        question="Compare orders across regions over past 6 months",
        selector=selector,
        join_paths=[{"tables": ["vsf_raw.users", "vsf_raw.orders"], "edges": []}],
        request_plan={"slots": {"metrics": ["sum"], "dimensions": ["region"]}},
        llm=llm,
    )

    assert hybrid.level == "high"
    assert hybrid.score == 0.85
    assert hybrid.strategy == "hybrid"
    assert "multi_table_join" in hybrid.complexity_drivers
    assert hybrid.rationale == "Requires temporal comparison and multi-table join"


@pytest.mark.asyncio
async def test_complexity_assessor_assess_hybrid_fallback() -> None:
    llm = MagicMock()
    llm.assess_complexity = AsyncMock(side_effect=Exception("LLM Timeout"))

    assessor = ComplexityAssessor()
    selector = SelectorResult(
        selected_tables=[{"tableName": "vsf_raw.users"}],
        selected_table_names=["vsf_raw.users"],
        schema_prompt="",
        decisions=[],
        confidence=0.9,
        strategy="rule_based",
        graph_edges=[],
    )

    fallback = await assessor.assess_hybrid(
        question="Count users",
        selector=selector,
        join_paths=[],
        request_plan={"slots": {"metrics": ["count"]}},
        llm=llm,
    )

    assert fallback.level == "low"
    assert fallback.strategy == "rule_based"
