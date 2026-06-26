"""Tests for Task 7: dbt step removed from transform DAG."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_transform_dag_no_longer_runs_dbt():
    dag = (ROOT / "dags/agentic_bi_pipeline.py").read_text()
    assert "run_dbt_transformations" not in dag
    assert "dbt build" not in dag
    assert "DBT_PROJECT_DIR" not in dag


def test_quality_checks_still_present():
    dag = (ROOT / "dags/agentic_bi_pipeline.py").read_text()
    assert "run_custom_data_quality_checks" in dag
    assert "fct_llm_interactions" in dag


def test_pipeline_still_triggers_transform():
    dag = (ROOT / "dags/agentic_bi_pipeline.py").read_text()
    assert "trigger_transform_raw_to_gold" in dag
