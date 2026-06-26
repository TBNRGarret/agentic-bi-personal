"""Tests for RefinerAgent and ErrorClassifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.agents.model2.error_classifier import ErrorClassifier, ErrorClassification
from app.agents.model2.refiner_agent import RefinerAgent


def test_error_classifier_categories() -> None:
    classifier = ErrorClassifier()
    tables = [
        {
            "tableName": "vsf_raw.users",
            "columns": [{"name": "id", "type": "UUID"}, {"name": "email", "type": "String"}],
        }
    ]

    syntax_err = classifier.classify("Syntax error: unexpected identifier SELECTT at line 1", tables)
    assert isinstance(syntax_err, ErrorClassification)
    assert syntax_err.category == "syntax_error"

    col_err = classifier.classify("Missing column: user_name not found in table vsf_raw.users", tables)
    assert col_err.category == "non_existent_column"
    assert col_err.correction_hints is not None

    type_err = classifier.classify("Type mismatch: cannot compare UUID with Int64", tables)
    assert type_err.category == "type_mismatch"

    div_err = classifier.classify("Division by zero error in expression sum(val)/0", tables)
    assert div_err.category == "division_by_zero"

    general_err = classifier.classify("Unknown ClickHouse exception 500", tables)
    assert general_err.category == "general_sql_error"


@pytest.mark.asyncio
async def test_refiner_agent_refine_success() -> None:
    llm = MagicMock()
    llm.refine_sql = AsyncMock(return_value={
        "status": "success",
        "sql": "SELECT id, email FROM vsf_raw.users WHERE email != ''",
        "rationale": "Corrected column name user_name to email",
    })

    classifier = ErrorClassifier()
    refiner = RefinerAgent(error_classifier=classifier)

    tables = [
        {
            "tableName": "vsf_raw.users",
            "columns": [{"name": "id", "type": "UUID"}, {"name": "email", "type": "String"}],
        }
    ]

    res = await refiner.refine(
        question="Find active users",
        failed_sql="SELECT id, user_name FROM vsf_raw.users WHERE user_name != ''",
        database_error="Missing column: user_name not found in table vsf_raw.users",
        tables=tables,
        datasource="clickhouse",
        limit=20,
        llm=llm,
        request_plan={"slots": {}},
        error_history=[{"error": "Missing column: user_name not found in table vsf_raw.users", "sql": "SELECT id, user_name FROM vsf_raw.users WHERE user_name != ''"}],
    )

    assert res["status"] == "success"
    assert res["sql"] == "SELECT id, email FROM vsf_raw.users WHERE email != ''"
    assert res["errorClassification"]["category"] == "non_existent_column"
    assert res["rationale"] == "Corrected column name user_name to email"


@pytest.mark.asyncio
async def test_refiner_agent_refine_fallback() -> None:
    llm = MagicMock()
    llm.refine_sql = AsyncMock(side_effect=Exception("LLM refinement failed"))

    refiner = RefinerAgent()
    tables = [{"tableName": "vsf_raw.users", "columns": [{"name": "id", "type": "UUID"}]}]

    res = await refiner.refine(
        question="Find active users",
        failed_sql="SELECT id, user_name FROM vsf_raw.users",
        database_error="Missing column",
        tables=tables,
        datasource="clickhouse",
        limit=20,
        llm=llm,
        request_plan={},
    )

    assert res["status"] == "failed"
    assert res["sql"] == "SELECT id, user_name FROM vsf_raw.users"
    assert "LLM refinement failed" in res.get("reason", "") or res.get("errorClassification") is not None
