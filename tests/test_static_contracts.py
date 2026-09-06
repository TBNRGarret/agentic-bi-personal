from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ingestion_loads_vsf_raw_batches_from_minio():
    dag = read("dags/agentic_bi_pipeline.py")

    assert "def validate_and_load(" in dag
    assert "minio_client(" in dag
    assert 'RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")' in dag
    assert "raw_ingestion_batches" in dag
    assert "source_app_id" in dag
    assert "batch_id" in dag
    assert 'ch_client.command(f"TRUNCATE TABLE {table}")' not in dag
    for table in (
        "signoz_logs",
        "signoz_traces",
        "signoz_metrics",
        "langfuse_generations",
        "user_interactions",
    ):
        assert f'"{table}"' in dag


def test_pipeline_uses_vsf_databases_instead_of_legacy_names():
    dag = read("dags/agentic_bi_pipeline.py")
    env_template = read(".env.example")

    for schema in ("vsf_raw", "vsf_mart"):
        assert schema in dag

    assert "CLICKHOUSE_ANALYTICS_SCHEMAS=vsf_mart" in env_template
    assert "DATAHUB_MCP_DEFAULT_SCHEMA=vsf_mart" in env_template


def test_compose_defines_minio_object_store():
    compose = read("docker-compose.yml")

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


def test_smoke_dag_infra_checks_are_strict():
    smoke = read("dags/smoke_dag.py")

    assert "check_agentic_clickhouse" in smoke
    assert "check_minio" in smoke


def test_verify_script_delegates_to_verify_system_py():
    script_ps1 = read("scripts/verify-system.ps1")
    script_sh = read("scripts/verify-system.sh")

    assert "verify_system.py" in script_ps1
    assert "verify_system.py" in script_sh


def test_airflow_ui_url_is_discovered_from_published_container_port():
    start_script = read("scripts/start-system.ps1")

    assert "Get-AgenticAirflowWebUrl" in start_script
    assert "docker port $apiServer 8080/tcp" in start_script
    assert "http://localhost:8080" not in start_script


def test_dbt_config_does_not_reference_missing_bronze_models():
    guide = read("docs/PROJECT_GUIDE.md")

    assert "bronze" not in guide
    assert "vsf_raw" in guide
    assert "vsf_stg" in guide
    assert "vsf_mart" in guide


def test_generated_artifacts_are_ignored():
    gitignore = read(".gitignore")

    for pattern in (".astro/", "include/dbt_project/target/", "include/dbt_project/logs/"):
        assert pattern in gitignore


def test_datahub_vps_compose_defines_pinned_resource_limited_stack():
    compose = read("docker-compose-datahub.yml")

    for service in (
        "  datahub-mysql:",
        "  datahub-opensearch:",
        "  datahub-kafka-broker:",
        "  datahub-system-update:",
        "  datahub-gms:",
        "  datahub-frontend:",
        "  datahub-actions:",
    ):
        assert service in compose

    assert "acryldata/datahub-gms:${DATAHUB_VERSION:-v1.4.0}" in compose
    assert "acryldata/datahub-frontend-react:${DATAHUB_VERSION:-v1.4.0}" in compose
    assert "opensearchproject/opensearch:2.19.3" in compose
    assert "confluentinc/cp-kafka:8.0.0" in compose
    assert "datahub-zookeeper" not in compose
    assert "docker.elastic.co/elasticsearch" not in compose

    for memory_limit in ("mem_limit: 768m", "mem_limit: 1536m", "mem_limit: 1024m"):
        assert memory_limit in compose

    assert '"${DATAHUB_MAPPED_GMS_PORT:-28080}:8080"' in compose
    assert '"${DATAHUB_MAPPED_FRONTEND_PORT:-9002}:9002"' in compose
    assert "DATAHUB_TOKEN_SERVICE_SIGNING_KEY" in compose
    assert "DATAHUB_TOKEN_SERVICE_SALT" in compose


def test_datahub_vps_env_template_documents_required_runtime_values():
    env_template = read(".env.example")

    for key in (
        "DATAHUB_VERSION=v1.4.0",
        "DATAHUB_TOKEN_SERVICE_SIGNING_KEY=",
        "DATAHUB_TOKEN_SERVICE_SALT=",
        "DATAHUB_MAPPED_GMS_PORT=28080",
        "DATAHUB_MAPPED_FRONTEND_PORT=9002",
    ):
        assert key in env_template

    assert "openssl rand -base64 32" in env_template


def test_datahub_vps_monitoring_scripts_capture_memory_and_crash_evidence():
    monitor = read("scripts/vps-datahub-monitor.sh")
    capacity_check = read("scripts/vps-datahub-capacity-check.sh")

    for script in (monitor, capacity_check):
        assert "docker-compose-datahub.yml" in script
        assert "datahub-vps" in script

    assert "docker stats --no-stream" in monitor
    assert "free -m" in monitor
    assert "OOMKilled" in monitor
    assert "RestartCount" in monitor
    assert "ExitCode" in monitor
    assert "datahub-system-update" in monitor
    assert "logs/datahub-vps" in monitor
    assert "htop" in monitor

    assert "vps-datahub-monitor.sh" in capacity_check
    assert "http://localhost:${DATAHUB_MAPPED_GMS_PORT:-28080}/health" in capacity_check
    assert "source \"$ENV_FILE\"" not in monitor
    assert "source \"$ENV_FILE\"" not in capacity_check
    assert "env_value()" in capacity_check
    assert "vm.max_map_count" in capacity_check
    assert "docker compose" in capacity_check


def test_datahub_test_plan_points_to_runnable_vps_artifacts():
    compose = read("docker-compose-datahub.yml")
    capacity_check = read("scripts/vps-datahub-capacity-check.sh")
    monitor = read("scripts/vps-datahub-monitor.sh")
    guide = read("docs/PROJECT_GUIDE.md")

    assert "--env-file .env" in compose
    assert "ENV_FILE=\"${ENV_FILE:-.env}\"" in capacity_check
    assert "ENV_FILE=\"${ENV_FILE:-.env}\"" in monitor
    assert "docker stats --no-stream" in monitor
    assert "free -m" in monitor
    assert "vm.max_map_count" in capacity_check
    assert "DataHub-only stack" in guide


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
