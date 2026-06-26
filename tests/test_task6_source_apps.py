"""Tests for Task 6: Source apps data inserted in the DAG."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_source_apps_inserted_in_dag():
    dag = (ROOT / "dags/agentic_bi_pipeline.py").read_text()
    assert "INSERT INTO vsf_mart.source_apps" in dag
    for app in ("hoang_ai_chat", "an_ai_chat", "tuan_ai_chat", "bach_ai_chat"):
        assert app in dag


def test_ensure_source_apps_called():
    dag = (ROOT / "dags/agentic_bi_pipeline.py").read_text()
    assert "ensure_source_apps(ch_client)" in dag
