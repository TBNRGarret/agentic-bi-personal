"""Tests for DecomposerAgent and DecompositionPlan contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.agents.model2.contracts import DecompositionPlan, SelectorResult
from app.agents.model2.decomposer_agent import DecomposerAgent
from app.agents.model2.complexity_assessor import ComplexityAssessor


@pytest.mark.asyncio
async def test_decomposer_agent_plan_simple() -> None:
    llm = MagicMock()
    llm.decompose_request = AsyncMock(return_value={"status": "success", "sql": "SELECT 1", "candidates": [{"sql": "SELECT 1"}]})

    selector = SelectorResult(
        selected_tables=[{"tableName": "vsf_raw.users", "columns": [{"name": "id", "type": "UUID"}]}],
        selected_table_names=["vsf_raw.users"],
        schema_prompt="CREATE TABLE vsf_raw.users (id UUID);",
        decisions=[],
        confidence=0.9,
        strategy="hybrid",
        graph_edges=[],
    )

    decomposer = DecomposerAgent()
    plan = await decomposer.plan(
        question="Count users",
        selector=selector,
        datasource="clickhouse",
        limit=10,
        llm=llm,
        conversation=[],
        memory={},
        request_plan={"slots": {"metrics": ["count"]}},
        join_paths=[],
    )

    assert isinstance(plan, DecompositionPlan)
    assert plan.status == "success"
    assert plan.sql == "SELECT 1"
    assert plan.decomposition_strategy in {"single_shot", "decomposed", "rule_based"}
    assert plan.selected_tables == ["vsf_raw.users"]


@pytest.mark.asyncio
async def test_decomposer_agent_plan_decomposed() -> None:
    llm = MagicMock()
    llm.decompose_request = AsyncMock(return_value={
        "status": "success",
        "subQuestions": [{"id": 1, "question": "Get active users", "sql": "SELECT id FROM users WHERE active=1"}],
        "sql": "SELECT count(*) FROM users WHERE active=1",
        "candidates": [{"sql": "SELECT count(*) FROM users WHERE active=1"}],
    })

    selector = SelectorResult(
        selected_tables=[
            {"tableName": "vsf_raw.users", "columns": [{"name": "id", "type": "UUID"}]},
            {"tableName": "vsf_raw.orders", "columns": [{"name": "id", "type": "UUID"}]},
        ],
        selected_table_names=["vsf_raw.users", "vsf_raw.orders"],
        schema_prompt="CREATE TABLE vsf_raw.users (id UUID); CREATE TABLE vsf_raw.orders (id UUID);",
        decisions=[],
        confidence=0.85,
        strategy="hybrid",
        graph_edges=[],
    )

    complexity = ComplexityAssessor()
    decomposer = DecomposerAgent(complexity_assessor=complexity)
    plan = await decomposer.plan(
        question="Show active users and their orders with temporal comparison over last 30 days and breakdown by region and product",
        selector=selector,
        datasource="clickhouse",
        limit=50,
        llm=llm,
        conversation=[],
        memory={},
        request_plan={"slots": {"metrics": ["count", "sum"], "dimensions": ["region", "product"], "time_range": "last 30 days"}},
        join_paths=[{"tables": ["vsf_raw.users", "vsf_raw.orders"], "edges": []}],
    )

    assert isinstance(plan, DecompositionPlan)
    assert plan.status == "success"
    assert len(plan.sub_questions) > 0
    assert plan.sub_questions[0]["question"] == "Get active users"
    assert plan.complexity is not None


@pytest.mark.asyncio
async def test_decomposer_agent_fallback() -> None:
    llm = MagicMock()
    llm.decompose_request = AsyncMock(side_effect=Exception("LLM Timeout"))

    selector = SelectorResult(
        selected_tables=[{"tableName": "vsf_raw.users", "columns": [{"name": "id", "type": "UUID"}]}],
        selected_table_names=["vsf_raw.users"],
        schema_prompt="CREATE TABLE vsf_raw.users (id UUID);",
        decisions=[],
        confidence=0.9,
        strategy="hybrid",
        graph_edges=[],
    )

    decomposer = DecomposerAgent()
    plan = await decomposer.plan(
        question="Count users",
        selector=selector,
        datasource="clickhouse",
        limit=10,
        llm=llm,
        conversation=[],
        memory={},
        request_plan={},
        join_paths=[],
    )

    assert plan.status == "fallback"
    assert "SELECT" in plan.sql.upper()
    assert plan.selected_tables == ["vsf_raw.users"]
