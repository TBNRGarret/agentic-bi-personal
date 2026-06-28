"""Tests for Model 2 RefinerAgent and ErrorClassifier."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.agents.model2.error_classifier import ErrorClassifier, ErrorClassification
from app.agents.model2.refiner_agent import RefinerAgent


TABLES = [
    {
        "tableName": "vsf_mart.gold_app_daily_cost",
        "columns": [
            {"name": "date", "type": "Date"},
            {"name": "model", "type": "String"},
            {"name": "total_tokens", "type": "UInt64"},
            {"name": "total_cost_usd", "type": "Float64"},
        ],
    }
]


def test_error_classifier_categories() -> None:
    classifier = ErrorClassifier()

    syntax_err = classifier.classify("Syntax error: unexpected identifier SELECTT at line 1", TABLES)
    assert isinstance(syntax_err, ErrorClassification)
    assert syntax_err.category == "syntax_error"

    col_err = classifier.classify("Missing column: total_token not found in table vsf_mart.gold_app_daily_cost", TABLES)
    assert col_err.category == "unknown_column"
    assert "total_tokens" in col_err.suggested_fix

    type_err = classifier.classify("Type mismatch: cannot compare Date with String", TABLES)
    assert type_err.category == "type_mismatch"

    agg_err = classifier.classify("Column model must appear in the GROUP BY clause", TABLES)
    assert agg_err.category == "aggregation_error"

    general_err = classifier.classify("Unknown ClickHouse exception 500", TABLES)
    assert general_err.category == "general_database_error"


def test_refiner_deterministic_unknown_column_repair_skips_llm() -> None:
    async def run() -> None:
        llm = MagicMock()
        llm.smart_repair_sql = AsyncMock()
        refiner = RefinerAgent()

        res = await refiner.refine(
            question="show tokens by model",
            failed_sql="SELECT model, sum(total_token) FROM gold_app_daily_cost GROUP BY model",
            database_error="Missing column: total_token not found in table vsf_mart.gold_app_daily_cost",
            tables=TABLES,
            datasource="clickhouse",
            limit=20,
            llm=llm,
            request_plan={"slots": {}},
            error_history=[],
        )

        assert res["status"] == "success"
        assert res["repairStrategy"] == "deterministic_first"
        assert "total_tokens" in res["sql"]
        assert "FROM vsf_mart.gold_app_daily_cost" in res["sql"]
        assert res["sql"].endswith("LIMIT 20")
        llm.smart_repair_sql.assert_not_called()

    asyncio.run(run())


def test_refiner_repairs_group_by_before_llm() -> None:
    async def run() -> None:
        llm = MagicMock()
        llm.smart_repair_sql = AsyncMock()
        refiner = RefinerAgent()

        res = await refiner.refine(
            question="show cost by model",
            failed_sql="SELECT model, sum(total_cost_usd) AS cost FROM vsf_mart.gold_app_daily_cost",
            database_error="Column model must appear in the GROUP BY clause",
            tables=TABLES,
            datasource="clickhouse",
            limit=50,
            llm=llm,
            request_plan={"slots": {}},
        )

        assert res["status"] == "success"
        assert "GROUP BY model" in res["sql"]
        assert any(candidate["strategy"] == "repair_group_by" for candidate in res["candidates"])
        llm.smart_repair_sql.assert_not_called()

    asyncio.run(run())


def test_refiner_calls_smart_repair_for_low_confidence_general_error() -> None:
    async def run() -> None:
        llm = MagicMock()
        llm.smart_repair_sql = AsyncMock(return_value={
            "status": "success",
            "sql": "SELECT model, sum(total_tokens) FROM vsf_mart.gold_app_daily_cost GROUP BY model LIMIT 20",
            "candidates": [
                {
                    "sql": "SELECT model, sum(total_tokens) FROM vsf_mart.gold_app_daily_cost GROUP BY model LIMIT 20",
                    "strategy": "llm_repair",
                }
            ],
        })
        refiner = RefinerAgent()

        res = await refiner.refine(
            question="show tokens by model",
            failed_sql="SELECT model, sum(tokens) FROM gold_app_daily_cost GROUP BY model",
            database_error="Unknown ClickHouse exception 500",
            tables=TABLES,
            datasource="clickhouse",
            limit=20,
            llm=llm,
            request_plan={"slots": {}},
        )

        assert res["status"] == "success"
        assert res["repairStrategy"] == "llm_after_deterministic_candidates"
        assert len(res["candidates"]) >= 2
        llm.smart_repair_sql.assert_awaited_once()

    asyncio.run(run())


def test_refiner_passes_join_validation_context_to_smart_repair() -> None:
    async def run() -> None:
        llm = MagicMock()
        llm.smart_repair_sql = AsyncMock(return_value={
            "status": "success",
            "sql": (
                "SELECT t2.publisher_name, sum(t1.total_revenue) "
                "FROM vsf_mart.fct_sales t1 "
                "JOIN vsf_mart.dim_publishers t2 ON t1.publisher_id = t2.publisher_id "
                "GROUP BY t2.publisher_name LIMIT 20"
            ),
            "candidates": [],
        })
        refiner = RefinerAgent()
        tables = [
            {
                "tableName": "vsf_mart.fct_sales",
                "columns": [
                    {"name": "publisher_id", "type": "String"},
                    {"name": "total_revenue", "type": "Float64"},
                ],
            },
            {
                "tableName": "vsf_mart.dim_publishers",
                "columns": [
                    {"name": "publisher_id", "type": "String"},
                    {"name": "publisher_name", "type": "String"},
                ],
            },
        ]
        request_plan = {
            "model2JoinValidation": {
                "requiredJoinConditions": ["vsf_mart.fct_sales.publisher_id = vsf_mart.dim_publishers.publisher_id"],
                "noCrossJoin": True,
                "validateGrainBeforeAggregation": True,
            }
        }

        res = await refiner.refine(
            question="doanh thu theo publisher",
            failed_sql="SELECT publisher_name, sum(total_revenue) FROM vsf_mart.fct_sales GROUP BY publisher_name",
            database_error="Unknown ClickHouse exception 500",
            tables=tables,
            datasource="clickhouse",
            limit=20,
            llm=llm,
            request_plan=request_plan,
        )

        assert res["status"] == "success"
        kwargs = llm.smart_repair_sql.await_args.kwargs
        assert "Re-check graph join path" in kwargs["strategy_hint"]
        assert "Do not use CROSS JOIN" in kwargs["strategy_hint"]
        assert kwargs["request_plan"]["refinerJoinValidation"] == request_plan["model2JoinValidation"]

    asyncio.run(run())

