"""Tests for Task 5: Mart views live only in vsf_mart, with legacy gold schema removed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_fct_llm_interactions_is_view():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "CREATE OR REPLACE VIEW vsf_mart.fct_llm_interactions" in sql


def test_mart_daily_cost_is_view_not_table():
    init_vsf = (ROOT / "include/clickhouse/init_vsf.sql").read_text()
    init_agentic_ui = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS vsf_mart.gold_app_daily_cost" not in init_vsf
    assert "CREATE OR REPLACE VIEW vsf_mart.gold_app_daily_cost" in init_agentic_ui


def test_legacy_gold_schema_is_dropped_and_not_recreated():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "DROP DATABASE IF EXISTS gold" in sql
    assert "CREATE DATABASE IF NOT EXISTS gold" not in sql
    assert "CREATE OR REPLACE VIEW gold." not in sql


def test_mart_views_reference_silver_not_dbt():
    sql = (ROOT / "include/clickhouse/init_agentic_ui.sql").read_text()
    assert "vsf_stg." not in sql
