from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ingestion_full_refreshes_raw_tables_before_loading():
    dag = read("dags/agentic_bi_pipeline.py")

    assert "RAW_TABLES = (" in dag
    assert 'ch_client.command(f"TRUNCATE TABLE {table}")' in dag
    for table in (
        "raw.signoz_logs",
        "raw.signoz_traces",
        "raw.signoz_metrics",
        "raw.langfuse_generations",
        "raw.user_interactions",
    ):
        assert f'"{table}"' in dag


def test_quality_gate_fails_when_gold_table_is_empty():
    dag = read("dags/agentic_bi_pipeline.py")

    assert "if total_count <= 0:" in dag
    assert "No rows found in agentic_bi.fct_llm_interactions" in dag


def test_smoke_dag_langfuse_check_is_strict():
    smoke = read("dags/smoke_dag.py")
    langfuse_block = smoke.split("def check_langfuse():", 1)[1].split("    @task()", 1)[0]

    assert "except Exception" not in langfuse_block
    assert 'return "ok"' not in langfuse_block


def test_verify_script_extracts_json_before_convertfrom_json():
    script = read("scripts/verify-system.ps1")

    assert "Convert-AirflowCliJson" in script
    assert "Convert-AirflowCliJson $runsOutput" in script
    assert "$runsJson | ConvertFrom-Json" not in script


def test_airflow_ui_url_is_discovered_from_published_container_port():
    verify_script = read("scripts/verify-system.ps1")
    start_script = read("scripts/start-system.ps1")

    for script in (verify_script, start_script):
        assert "Get-AgenticAirflowWebUrl" in script
        assert "docker port $apiServer 8080/tcp" in script
        assert "http://localhost:8080" not in script


def test_dbt_config_does_not_reference_missing_bronze_models():
    dbt_project = read("include/dbt_project/dbt_project.yml")

    assert "bronze:" not in dbt_project


def test_generated_artifacts_are_ignored():
    gitignore = read(".gitignore")

    for pattern in (".astro/", "include/dbt_project/target/", "include/dbt_project/logs/"):
        assert pattern in gitignore


def test_seed_uses_worker_compatible_watermark_names():
    seed = read("scripts/seed_dwh.py")

    for watermark in (
        "('signoz', 'logs'",
        "('signoz', 'traces'",
        "('signoz', 'metrics'",
        "('langfuse', 'generations'",
        "('langfuse', 'user_interactions'",
    ):
        assert watermark in seed

    for legacy_watermark in (
        "('signoz', 'signoz_logs'",
        "('signoz', 'signoz_traces'",
        "('signoz', 'signoz_metrics'",
        "('langfuse', 'langfuse_generations'",
    ):
        assert legacy_watermark not in seed
