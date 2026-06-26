"""Tests for Task 5: Gold views created in init_agentic_ui.sql replacing dbt gold models."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_fct_llm_interactions_is_view():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "CREATE OR REPLACE VIEW vsf_mart.fct_llm_interactions" in sql


def test_gold_app_daily_cost_is_view_not_table():
    init_vsf = (ROOT / "include/clickhouse/init_vsf.sql").read_text()
    init_agentic_ui = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS vsf_mart.gold_app_daily_cost" not in init_vsf
    assert "CREATE OR REPLACE VIEW vsf_mart.gold_app_daily_cost" in init_agentic_ui


def test_gold_views_reference_silver_not_dbt():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "vsf_stg." not in sql
