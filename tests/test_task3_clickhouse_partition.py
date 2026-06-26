"""Tests for Task 3: ClickHouse partition changed from monthly (toYYYYMM) to daily (toYYYYMMDD)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_vsf_tables_use_daily_partition():
    sql = (ROOT / "include/clickhouse/init_vsf.sql").read_text()
    assert "toYYYYMMDD" in sql
    assert "toYYYYMM(" not in sql  # Không còn monthly partition
    assert "gold_app_daily_cost" not in sql  # Đã chuyển sang View ở Task 5


def test_raw_tables_have_daily_partition():
    sql = (ROOT / "include/clickhouse/init_vsf.sql").read_text()
    for table in (
        "signoz_logs",
        "signoz_traces",
        "signoz_metrics",
        "langfuse_generations",
        "user_interactions",
        "raw_ingestion_batches",
    ):
        assert f"PARTITION BY toYYYYMMDD(" in sql, f"{table} missing daily partition"
