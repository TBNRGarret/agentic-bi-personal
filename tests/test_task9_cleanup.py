"""Tests for Task 9: Cleanup dbt-related files after migration to ClickHouse views."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dbt_directory_removed():
    assert not (ROOT / "dbt").exists(), "dbt/ directory should be removed"
    assert not (ROOT / "include/dbt_project").exists(), "include/dbt_project/ should be removed"


def test_dbt_pipeline_dag_removed():
    assert not (ROOT / "airflow/dags/dbt_pipeline_dag.py").exists(), "dbt_pipeline_dag.py should be removed"


def test_no_dbt_references_in_dag():
    dag = (ROOT / "dags/agentic_bi_pipeline.py").read_text()
    # Only allow "dbt" in comments/docstrings, not in actual code
    assert "dbt build" not in dag.lower()
    assert "DBT_PROJECT_DIR" not in dag


def test_requirements_txt_no_dbt():
    req = (ROOT / "requirements.txt").read_text()
    assert "dbt" not in req.lower(), "requirements.txt should not reference dbt"
