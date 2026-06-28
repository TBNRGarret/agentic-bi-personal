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
    llm.settings.llm_configured = True
    llm.assess_complexity = AsyncMock(return_value={"status": "success", "level": "simple", "score": 2})
    llm.solve_sub_question = AsyncMock(return_value={"status": "success", "sql": "SELECT 1 FROM vsf_raw.users"})
    llm.refine_decomposed_sql = AsyncMock(return_value={"status": "success", "sql": "SELECT 1 FROM vsf_raw.users", "candidates": [{"sql": "SELECT 1 FROM vsf_raw.users"}]})

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
    assert plan.sql == "SELECT 1 FROM vsf_raw.users"
    assert plan.decomposition_strategy in {"single_shot", "decomposed", "rule_based", "simple_mac_sql_solver"}
    assert plan.selected_tables == ["vsf_raw.users"]


@pytest.mark.asyncio
async def test_decomposer_agent_plan_decomposed() -> None:
    llm = MagicMock()
    llm.settings.llm_configured = True
    llm.assess_complexity = AsyncMock(return_value={"status": "success", "level": "complex", "score": 8, "recommendedSteps": 3})
    llm.decompose_sql_question = AsyncMock(return_value={
        "status": "success",
        "subQuestions": [{"id": "sq1", "question": "Get active users", "expectedOutput": "users table"}],
    })
    llm.solve_sub_question = AsyncMock(return_value={"status": "success", "sql": "SELECT id FROM users WHERE active=1"})
    llm.refine_decomposed_sql = AsyncMock(return_value={
        "status": "success",
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
    llm.settings.llm_configured = True
    llm.assess_complexity = AsyncMock(return_value={"status": "success", "level": "simple", "score": 2})
    llm.solve_sub_question = AsyncMock(return_value={"status": "failed"})
    llm.refine_decomposed_sql = AsyncMock(return_value={"status": "failed"})

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



def test_decomposer_join_aware_fallback_uses_join_plan_when_llm_disabled() -> None:
    import asyncio

    async def run() -> DecompositionPlan:
        llm = MagicMock()
        llm.settings.llm_configured = False

        sales = {
            "tableName": "vsf_mart.fct_sales",
            "columns": [
                {"name": "date", "type": "Date"},
                {"name": "publisher_id", "type": "String"},
                {"name": "total_revenue", "type": "Float64"},
            ],
        }
        publishers = {
            "tableName": "vsf_mart.dim_publishers",
            "columns": [
                {"name": "publisher_id", "type": "String"},
                {"name": "publisher_name", "type": "String"},
            ],
        }
        selector = SelectorResult(
            selected_tables=[sales, publishers],
            selected_table_names=["vsf_mart.fct_sales", "vsf_mart.dim_publishers"],
            schema_prompt="",
            decisions=[],
            confidence=0.9,
            strategy="unit",
            graph_edges=[],
        )
        join_plan = {
            "status": "ready",
            "selectedPaths": [
                {
                    "tables": ["vsf_mart.fct_sales", "vsf_mart.dim_publishers"],
                    "joinSteps": [
                        {
                            "leftTable": "vsf_mart.fct_sales",
                            "rightTable": "vsf_mart.dim_publishers",
                            "edgeType": "SHARED_COLUMN",
                            "columns": ["publisher_id"],
                            "condition": "vsf_mart.fct_sales.publisher_id = vsf_mart.dim_publishers.publisher_id",
                        }
                    ],
                    "bridgeTables": [],
                    "score": 1.2,
                }
            ],
            "hintBlock": "/* MODEL2 JOIN HINTS */",
            "validation": {"requiredJoinConditions": ["publisher_id"]},
        }

        return await DecomposerAgent().plan(
            question="doanh thu theo publisher tháng này",
            selector=selector,
            datasource="clickhouse",
            limit=25,
            llm=llm,
            conversation=[],
            memory={},
            request_plan={},
            join_paths=join_plan["selectedPaths"],
            join_plan=join_plan,
        )

    plan = asyncio.run(run())

    assert plan.status == "fallback"
    assert "JOIN vsf_mart.dim_publishers AS" in plan.sql
    assert ".publisher_id =" in plan.sql
    assert "total_revenue" in plan.sql
    assert "publisher_name AS publisher" in plan.sql
    assert "GROUP BY" in plan.sql



def test_decomposer_dwh_audit_short_circuit_returns_query_details_without_llm() -> None:
    import asyncio

    async def run() -> DecompositionPlan:
        llm = MagicMock()
        llm.settings.llm_configured = True
        llm.assess_complexity = AsyncMock(side_effect=AssertionError("audit query should not assess via LLM"))
        llm.solve_sub_question = AsyncMock(side_effect=AssertionError("audit query should not solve via LLM"))
        llm.refine_decomposed_sql = AsyncMock(side_effect=AssertionError("audit query should not refine via LLM"))
        audit = {
            "tableName": "ops.agent_query_audit",
            "columns": [
                {"name": "query_id", "type": "text"},
                {"name": "endpoint", "type": "text"},
                {"name": "question", "type": "text"},
                {"name": "generated_sql", "type": "text"},
                {"name": "status", "type": "text"},
                {"name": "execution_time_ms", "type": "bigint"},
                {"name": "token_usage", "type": "jsonb"},
                {"name": "metadata", "type": "jsonb"},
                {"name": "username", "type": "text"},
                {"name": "created_at", "type": "timestamp"},
            ],
        }
        selector = SelectorResult(
            selected_tables=[audit],
            selected_table_names=["ops.agent_query_audit"],
            schema_prompt="",
            decisions=[],
            confidence=0.9,
            strategy="unit",
            graph_edges=[],
        )
        return await DecomposerAgent().plan(
            question="Show the most expensive agent queries",
            selector=selector,
            datasource="dwh",
            limit=20,
            llm=llm,
            conversation=[],
            memory={},
            request_plan={},
            join_paths=[],
        )

    plan = asyncio.run(run())

    assert plan.status == "success"
    assert plan.source == "deterministic_model2_dwh_audit"
    assert plan.decomposition_strategy == "dwh_agent_query_audit_detail"
    assert "FROM ops.agent_query_audit" in plan.sql
    assert "query_id" in plan.sql
    assert "trace_id" in plan.sql
    assert "estimated_cost_usd" in plan.sql
    assert "total_tokens" in plan.sql
    assert "ORDER BY estimated_cost_usd DESC, total_tokens DESC" in plan.sql
    assert "GROUP BY" not in plan.sql
    assert "toDate(" not in plan.sql
