"""Tests for Task 4: Silver views created in init_agentic_ui.sql replacing dbt silver models."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_silver_views_defined_in_init_agentic_ui():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    expected_views = [
        "silver_user_interactions",
        "silver_langfuse_generations",
        "silver_signoz_logs",
        "silver_signoz_traces",
        "silver_signoz_metrics",
        "silver_staging_llm_generations",
    ]
    for view in expected_views:
        assert f"silver.{view}" in sql, f"Missing view silver.{view}"


def test_silver_views_query_raw_not_dbt():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "vsf_stg." not in sql  # Không còn reference đến dbt staging


def test_silver_views_no_longer_reference_vsf_stg():
    """Existing silver views that previously used vsf_stg now use silver.* instead."""
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    # silver_llm_generations should now use silver.silver_langfuse_generations
    assert "FROM silver.silver_langfuse_generations" in sql
    # silver_system_logs should now use silver.silver_signoz_logs
    assert "FROM silver.silver_signoz_logs" in sql
