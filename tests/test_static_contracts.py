from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ingestion_loads_vsf_raw_batches_from_minio():
    dag = read("dags/agentic_bi_pipeline.py")

    assert "def ingest_minio_to_clickhouse():" in dag
    assert "Minio(" in dag
    assert 'RAW_BUCKET = "agentic-bi-raw"' in dag
    assert "raw_ingestion_batches" in dag
    assert "source_app_id" in dag
    assert "batch_id" in dag
    assert 'ch_client.command(f"TRUNCATE TABLE {table}")' not in dag
    for table in (
        "vsf_raw.signoz_logs",
        "vsf_raw.signoz_traces",
        "vsf_raw.signoz_metrics",
        "vsf_raw.langfuse_generations",
        "vsf_raw.user_interactions",
    ):
        assert f'"{table}"' in dag


def test_pipeline_uses_vsf_databases_instead_of_legacy_names():
    dag = read("dags/agentic_bi_pipeline.py")
    dbt_project = read("include/dbt_project/dbt_project.yml")
    profiles = read("include/dbt_project/profiles.yml")
    sources = read("include/dbt_project/models/sources.yml")

    for schema in ("vsf_raw", "vsf_stg", "vsf_mart"):
        assert schema in dag

    assert "schema: \"{{ env_var('AGENTIC_DBT_SCHEMA', 'vsf_mart') }}\"" in profiles
    assert "+schema: vsf_stg" in dbt_project
    assert "+schema: vsf_mart" in dbt_project
    assert "database: vsf_raw" in sources
    assert "database: raw" not in sources


def test_compose_defines_minio_object_store():
    compose = read("docker-compose.override.yml")

    assert "  minio:" in compose
    assert "minio/minio:" in compose
    assert '"19001:9000"' in compose
    assert '"19002:9001"' in compose
    assert "minio_data:" in compose


def test_datahub_recipes_include_vsf_schemas():
    clickhouse_recipe = read("metadata/recipes/clickhouse_recipe.yml")
    client = read("metadata/client/datahub_client.py")

    for schema in ("vsf_raw", "vsf_stg", "vsf_mart"):
        assert f'- "{schema}"' in clickhouse_recipe

    assert 'schema: str = "vsf_mart"' in client


def test_airflow_runtime_requirements_do_not_install_datahub_plugin():
    requirements = read("requirements.txt")

    assert "acryl-datahub-airflow-plugin" not in requirements


def test_dockerfile_stabilizes_uv_dependency_downloads():
    dockerfile = read("Dockerfile")

    assert "UV_HTTP_TIMEOUT=180" in dockerfile
    assert "UV_CONCURRENT_DOWNLOADS=1" in dockerfile


def test_quality_gate_fails_when_gold_table_is_empty():
    dag = read("dags/agentic_bi_pipeline.py")

    assert "if total_count <= 0:" in dag
    assert "No rows found in vsf_mart.fct_llm_interactions" in dag


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
