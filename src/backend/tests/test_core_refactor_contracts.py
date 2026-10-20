"""Tests for Core Refactor (Task 3 / PR-00 - PR-02).

Validates:
- F01: Shared metadata packaging / imports
- F02: SQL AST safety, outer limit enforcement, literal preservation, comment handling
- F03: Row-level security for mixed tables, CTEs, and outer join semantics
- F07: Cross-model decoupling (no private imports from Model 1 in Model 2, no circular imports in PartitionAgent)
- F08: Unified semantic scoring and vocabulary across agents
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import pytest

from app.agents.row_security import RowSecurityError, apply_row_security, visible_tables
from app.agents.semantics import (
    asks_agent_cost,
    asks_latency_root_cause,
    asks_log_error_detail,
    infer_intent,
    normalize_text,
    semantic_domain,
    semantic_domain_column_score,
)
from app.agents.sql_safety import (
    SqlSafetyError,
    prepare_sql_candidate,
    validate_known_tables,
    validate_readonly_sql,
)
from app.security import Principal


# ─────────────────────────────────────────────────────────────
# F01: Docker Packaging & Shared Metadata Import
# ─────────────────────────────────────────────────────────────

def test_f01_metadata_package_importable():
    """Verify metadata.client.parsing is cleanly importable without external deps."""
    from metadata.client.parsing import custom_properties, global_tags, partition_info

    props = {"customProperties": [{"key": "partition_key", "value": "toYYYYMM(event_time)"}]}
    assert custom_properties(props) == {"partition_key": "toYYYYMM(event_time)"}
    assert global_tags({"tags": [{"tag": {"name": "tier1"}}]}) == [{"tagFQN": "tier1"}]


def test_f01_dockerfile_contains_metadata():
    """Verify backend Dockerfile copies metadata and configures PYTHONPATH."""
    dockerfile_path = Path(__file__).resolve().parents[1] / "Dockerfile"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "COPY metadata /app/metadata" in content
    assert "PYTHONPATH=/app" in content


# ─────────────────────────────────────────────────────────────
# F02: SQL Safety & AST Policy
# ─────────────────────────────────────────────────────────────

def test_f02_enforce_limit_caps_large_limit():
    """SELECT 1 LIMIT 999999 should cap to the specified limit (10)."""
    sql = "SELECT 1 LIMIT 999999"
    result = validate_readonly_sql(sql, limit=10)
    assert "LIMIT 10" in result
    assert "999999" not in result


def test_f02_enforce_outer_limit_on_subquery():
    """SELECT * FROM (SELECT 1 LIMIT 1) AS t should add an outer LIMIT."""
    sql = "SELECT * FROM (SELECT 1 LIMIT 1) AS t"
    result = validate_readonly_sql(sql, limit=10)
    # The outer query must have LIMIT 10
    assert result.endswith("LIMIT 10") or "AS t LIMIT 10" in result


def test_f02_forbidden_keyword_in_string_literal():
    """SELECT 'DROP' AS message should not be blocked because it is inside a string literal."""
    sql = "SELECT 'DROP' AS message"
    result = validate_readonly_sql(sql, limit=10)
    assert "LIMIT 10" in result
    assert "'DROP'" in result or "DROP" in result


def test_f02_string_literal_preserves_url_encoding():
    """SELECT 'a%20b' AS message should not be URL-decoded to 'a b'."""
    sql = "SELECT 'a%20b' AS message"
    result = validate_readonly_sql(sql, limit=10)
    assert "a%20b" in result
    assert "a b" not in result


def test_f02_rejects_forbidden_statements():
    """DDL/DML operations must be rejected."""
    with pytest.raises(SqlSafetyError):
        validate_readonly_sql("DROP TABLE vsf_mart.gold_app_daily_cost", limit=10)

    with pytest.raises(SqlSafetyError):
        validate_readonly_sql("INSERT INTO vsf_mart.gold_app_daily_cost VALUES (1)", limit=10)

    with pytest.raises(SqlSafetyError):
        validate_readonly_sql("UPDATE vsf_mart.gold_app_daily_cost SET cost = 0", limit=10)

    with pytest.raises(SqlSafetyError):
        validate_readonly_sql("DELETE FROM vsf_mart.gold_app_daily_cost", limit=10)


def test_f02_rejects_multi_statement():
    """Multiple statements separated by semicolon must be rejected."""
    with pytest.raises(SqlSafetyError):
        validate_readonly_sql("SELECT 1; SELECT 2", limit=10)


# ─────────────────────────────────────────────────────────────
# F03: Row-Level Security, CTEs, and Outer Joins
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_catalog_tables():
    return [
        {
            "tableName": "vsf_mart.gold_app_daily_cost",
            "database": "vsf_mart",
            "columns": [
                {"name": "source_app_id"},
                {"name": "total_cost_usd"},
                {"name": "metric_date"},
            ],
        },
        {
            "tableName": "vsf_mart.users",
            "database": "vsf_mart",
            "columns": [
                {"name": "id"},
                {"name": "source_app"},
                {"name": "username"},
            ],
        },
    ]


@pytest.fixture
def analyst_principal():
    return Principal(
        user_id="user-1",
        username="analyst",
        role="analyst",
        team="payments",
        allowed_source_apps=("PAYMENTS",),
    )


def test_f03_rejects_mixed_known_and_unknown_tables(sample_catalog_tables, analyst_principal):
    """Query mixing a known table with an unknown table must be rejected."""
    sql = (
        "SELECT * FROM vsf_mart.gold_app_daily_cost "
        "JOIN secret_unauthorized_table ON 1=1"
    )
    with pytest.raises(RowSecurityError) as exc_info:
        apply_row_security(
            sql,
            dialect="clickhouse",
            tables=sample_catalog_tables,
            principal=analyst_principal,
        )
    assert "outside the visible schema" in str(exc_info.value)


def test_f03_cte_recognized_and_secured(sample_catalog_tables, analyst_principal):
    """CTE references should not be flagged as unknown tables, and physical tables inside secured."""
    sql = (
        "WITH cost_summary AS ("
        "  SELECT source_app_id, total_cost_usd FROM vsf_mart.gold_app_daily_cost"
        ") "
        "SELECT * FROM cost_summary"
    )
    secured = apply_row_security(
        sql,
        dialect="clickhouse",
        tables=sample_catalog_tables,
        principal=analyst_principal,
    )
    assert "source_app_id IN ('PAYMENTS')" in secured
    assert "cost_summary" in secured


def test_f03_outer_join_attaches_predicate_to_on(sample_catalog_tables, analyst_principal):
    """Row security for right table of LEFT JOIN must attach to ON clause to preserve outer join."""
    sql = (
        "SELECT g.total_cost_usd, u.username "
        "FROM vsf_mart.gold_app_daily_cost g "
        "LEFT JOIN vsf_mart.users u ON g.source_app_id = u.id"
    )
    secured = apply_row_security(
        sql,
        dialect="clickhouse",
        tables=sample_catalog_tables,
        principal=analyst_principal,
    )
    # The users predicate should be in the ON clause
    assert "LEFT JOIN vsf_mart.users AS u ON g.source_app_id = u.id AND u.source_app IN ('PAYMENTS')" in secured
    # The gold_app_daily_cost predicate should be in WHERE
    assert "WHERE g.source_app_id IN ('PAYMENTS')" in secured


# ─────────────────────────────────────────────────────────────
# F07: Cross-Model Decoupling & Circular Dependency Removal
# ─────────────────────────────────────────────────────────────

def test_f07_model2_does_not_import_monitoring_agent():
    """Verify model2 does not import private helpers from monitoring_agent (Model 1)."""
    model2_dir = Path(__file__).resolve().parents[1] / "app" / "agents" / "model2"
    for py_file in model2_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "monitoring_agent" not in module, (
                    f"{py_file.name} imports from monitoring_agent: {module}"
                )


def test_f07_partition_agent_does_not_import_multi_agent():
    """Verify partition_agent does not import multi_agent."""
    pa_file = Path(__file__).resolve().parents[1] / "app" / "agents" / "partition_agent.py"
    tree = ast.parse(pa_file.read_text(encoding="utf-8"), filename=str(pa_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "multi_agent" not in module, (
                f"partition_agent imports from multi_agent: {module}"
            )


# ─────────────────────────────────────────────────────────────
# F08: Unified Semantic Rules
# ─────────────────────────────────────────────────────────────

def test_f08_shared_semantics_helpers():
    """Verify unified semantic helper calculations."""
    assert semantic_domain("check error logs for payments") == "log"
    assert semantic_domain("total cost of llm token generation") == "llm"
    assert semantic_domain("agent query audit") == "agent"

    assert asks_latency_root_cause("why is latency slow in service") is True
    assert asks_latency_root_cause("show latency over time") is False

    assert asks_log_error_detail("show error log details and messages") is True
    assert asks_agent_cost("what is the total agent cost") is True

    assert infer_intent("overview dashboard") == "dashboard"
    assert infer_intent("daily trend of cost") == "time_series"
    assert infer_intent("top 5 services by latency") == "aggregation"
