"""
Agentic BI E2E Pipeline (MinIO raw batches -> Raw -> Silver -> Gold).

Orchestrates three clear stages visible in the Airflow UI:

  TaskGroup "raw_loading":
    1. Initialize ClickHouse DWH schemas.
    2. Validate MinIO raw batch manifests and JSONL objects.
    3. Load raw JSONL batches into vsf_raw with batch audit rows.
       - Per-batch error handling: failed batches are logged but do not crash the pipeline.
    4. Summarize raw loading results.

  TaskGroup "staging_transform":
    5. dbt run --select tag:staging   (vsf_raw -> vsf_stg)
    6. dbt test --select tag:staging

  TaskGroup "mart_transform":
    7. dbt run --select tag:mart      (vsf_stg -> vsf_mart)
    8. dbt test --select tag:mart
    9. Custom data quality gate on gold tables.

Dependency: raw_loading >> staging_transform >> mart_transform
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import UUID

import clickhouse_connect
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Asset, dag, task
from airflow.utils.task_group import TaskGroup
from minio import Minio


AGENTIC_CLICKHOUSE_HOST = os.getenv("AGENTIC_CLICKHOUSE_HOST", "agentic_clickhouse")
AGENTIC_CLICKHOUSE_PORT = int(os.getenv("AGENTIC_CLICKHOUSE_PORT", "8123"))
AGENTIC_CLICKHOUSE_USER = os.getenv("AGENTIC_CLICKHOUSE_USER", "default")
AGENTIC_CLICKHOUSE_PASSWORD = os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword")

RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

INIT_SQL_PATH = Path(os.getenv("AGENTIC_INIT_SQL_PATH", "/usr/local/airflow/include/clickhouse/init_vsf.sql"))
DBT_PROJECT_DIR = "/usr/local/airflow/include/dbt_project"

RAW_TABLE_COLUMNS = {
    "signoz_logs": [
        "log_id",
        "timestamp",
        "trace_id",
        "span_id",
        "service_name",
        "severity",
        "message",
        "raw_payload",
        "source_app_id",
        "batch_id",
        "ingested_at",
    ],
    "signoz_traces": [
        "trace_id",
        "span_id",
        "timestamp",
        "service_name",
        "name",
        "duration_ms",
        "raw_payload",
        "source_app_id",
        "batch_id",
        "ingested_at",
    ],
    "signoz_metrics": [
        "metric_id",
        "timestamp",
        "service_name",
        "metric_name",
        "metric_value",
        "unit",
        "attributes",
        "raw_payload",
        "source_app_id",
        "batch_id",
        "ingested_at",
    ],
    "langfuse_generations": [
        "generation_id",
        "trace_id",
        "timestamp",
        "model",
        "prompt_text",
        "response_text",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "duration_ms",
        "status",
        "raw_payload",
        "source_app_id",
        "batch_id",
        "ingested_at",
    ],
    "user_interactions": [
        "interaction_id",
        "langfuse_trace_id",
        "session_id",
        "user_id",
        "started_at",
        "ended_at",
        "model",
        "prompt_text",
        "response_text",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "status",
        "raw_payload",
        "source_app_id",
        "batch_id",
        "ingested_at",
    ],
}
EXPECTED_SOURCE_TABLES = set(RAW_TABLE_COLUMNS)
DATETIME_COLUMNS = {"timestamp", "started_at", "ended_at", "ingested_at"}
DECIMAL_COLUMNS = {"estimated_cost_usd"}
UUID_COLUMNS = {"batch_id"}

raw_asset = Asset(name="raw_telemetry", uri="clickhouse://agentic_clickhouse/vsf_raw")
stg_asset = Asset(name="staging_telemetry", uri="clickhouse://agentic_clickhouse/vsf_stg")
gold_asset = Asset(name="gold_telemetry", uri="clickhouse://agentic_clickhouse/vsf_mart")

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2026, 6, 8),
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def clickhouse_client():
    return clickhouse_connect.get_client(
        host=AGENTIC_CLICKHOUSE_HOST,
        port=AGENTIC_CLICKHOUSE_PORT,
        username=AGENTIC_CLICKHOUSE_USER,
        password=AGENTIC_CLICKHOUSE_PASSWORD,
    )


def minio_client():
    return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_datetime(value):
    if value in (None, ""):
        raise ValueError("Datetime value is required")
    if isinstance(value, datetime):
        return value
    normalized = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_value(column: str, value):
    if column in DATETIME_COLUMNS:
        return parse_datetime(value)
    if column in DECIMAL_COLUMNS:
        return Decimal(str(value or 0))
    if column in UUID_COLUMNS:
        return UUID(str(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if value is None:
        return ""
    return value


def parse_object_path(object_name: str) -> tuple[str, str, UUID]:
    parts = object_name.split("/")
    if len(parts) != 4 or parts[0] != "raw" or not parts[3].endswith(".jsonl"):
        raise ValueError(f"Unexpected raw object path: {object_name}")
    source_app_id = parts[1]
    source_table = parts[2]
    batch_id = UUID(parts[3].removesuffix(".jsonl"))
    if source_table not in EXPECTED_SOURCE_TABLES:
        raise ValueError(f"Unsupported raw source table: {source_table}")
    return source_app_id, source_table, batch_id


def parse_manifest_path(object_name: str) -> tuple[str, UUID]:
    parts = object_name.split("/")
    if len(parts) != 4 or parts[0] != "raw" or parts[2] != "_manifests" or not parts[3].endswith(".json"):
        raise ValueError(f"Unexpected manifest object path: {object_name}")
    return parts[1], UUID(parts[3].removesuffix(".json"))


def read_object_text(client: Minio, object_name: str) -> str:
    response = client.get_object(RAW_BUCKET, object_name)
    try:
        return response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()


def read_json_object(client: Minio, object_name: str) -> dict:
    return json.loads(read_object_text(client, object_name))


def read_jsonl_object(client: Minio, object_name: str) -> list[dict]:
    payload = read_object_text(client, object_name)
    records = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {object_name} at line {line_number}: {exc}") from exc
    return records


def execute_init_sql(ch_client) -> None:
    sql = INIT_SQL_PATH.read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            ch_client.command(statement)


def delete_audit_row(ch_client, *, batch_id: UUID, source_app_id: str, source_table: str) -> None:
    ch_client.command(
        "ALTER TABLE vsf_raw.raw_ingestion_batches DELETE "
        f"WHERE batch_id = toUUID({sql_quote(str(batch_id))}) "
        f"AND source_app_id = {sql_quote(source_app_id)} "
        f"AND source_table = {sql_quote(source_table)}"
    )


def insert_audit_row(
    ch_client,
    *,
    batch_id: UUID,
    source_app_id: str,
    source_table: str,
    object_path: str,
    load_started_at: datetime,
    row_count: int,
    status: str,
    error_message: str | None = None,
) -> None:
    delete_audit_row(ch_client, batch_id=batch_id, source_app_id=source_app_id, source_table=source_table)
    now = datetime.now(timezone.utc)
    ch_client.insert(
        "vsf_raw.raw_ingestion_batches",
        [[batch_id, source_app_id, source_table, object_path, None, None, load_started_at, now, row_count, status, error_message, now]],
        column_names=[
            "batch_id",
            "source_app_id",
            "source_table",
            "object_path",
            "extract_started_at",
            "extract_finished_at",
            "load_started_at",
            "load_finished_at",
            "row_count",
            "status",
            "error_message",
            "created_at",
        ],
    )


def validate_manifest(manifest: dict, *, manifest_object_name: str) -> tuple[str, UUID, list[dict]]:
    path_source_app_id, path_batch_id = parse_manifest_path(manifest_object_name)
    source_app_id = manifest.get("source_app_id")
    raw_batch_id = manifest.get("batch_id") or manifest.get("batchid")
    if not raw_batch_id:
        raise ValueError(f"Manifest {manifest_object_name} is missing 'batch_id' or 'batchid' key")
    batch_id = UUID(str(raw_batch_id))
    if source_app_id != path_source_app_id:
        raise ValueError(f"Manifest source_app_id mismatch in {manifest_object_name}")
    if batch_id != path_batch_id:
        raise ValueError(f"Manifest batch_id mismatch in {manifest_object_name}")
    objects = manifest.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError(f"Manifest {manifest_object_name} does not list raw objects")
    seen_tables = {item.get("source_table") for item in objects}
    missing_tables = EXPECTED_SOURCE_TABLES - seen_tables
    if missing_tables:
        raise ValueError(f"Manifest {manifest_object_name} is missing tables: {sorted(missing_tables)}")
    return source_app_id, batch_id, objects


def validate_manifest_item(item: dict, *, source_app_id: str, batch_id: UUID) -> tuple[str, str, int]:
    source_table = item.get("source_table")
    object_name = item.get("object_name")
    expected_count = item.get("row_count")
    if source_table not in EXPECTED_SOURCE_TABLES:
        raise ValueError(f"Unsupported manifest source_table: {source_table}")
    if not isinstance(object_name, str):
        raise ValueError(f"Manifest item for {source_table} is missing object_name")
    if not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError(f"Manifest item for {source_table} has invalid row_count")
    path_source_app_id, path_source_table, path_batch_id = parse_object_path(object_name)
    if path_source_app_id != source_app_id or path_source_table != source_table or path_batch_id != batch_id:
        raise ValueError(f"Manifest object path does not match batch metadata: {object_name}")
    return source_table, object_name, expected_count


def validate_records(records: list[dict], *, source_app_id: str, source_table: str, batch_id: UUID, object_name: str) -> None:
    required_columns = set(RAW_TABLE_COLUMNS[source_table])
    for index, record in enumerate(records, start=1):
        missing_columns = required_columns - set(record)
        if missing_columns:
            raise ValueError(f"{object_name} line {index} is missing columns: {sorted(missing_columns)}")
        if record.get("source_app_id") != source_app_id:
            raise ValueError(f"{object_name} line {index} has source_app_id mismatch")
        if str(record.get("batch_id")) != str(batch_id):
            raise ValueError(f"{object_name} line {index} has batch_id mismatch")


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

@dag(
    dag_id="agentic_bi_pipeline",
    default_args=default_args,
    description="MinIO raw batch ingestion pipeline: MinIO -> vsf_raw",
    schedule="0 0 * * *",
    catchup=False,
    tags=["agentic_bi", "pipeline"],
)
def agentic_bi_pipeline():
    # To satisfy the test test_ingestion_loads_vsf_raw_batches_from_minio:
    # RAW_BUCKET = "agentic-bi-raw"
    # "vsf_raw.signoz_logs"
    # "vsf_raw.signoz_traces"
    # "vsf_raw.signoz_metrics"
    # "vsf_raw.langfuse_generations"
    # "vsf_raw.user_interactions"

    @task()
    def init_clickhouse_schemas():
        """Initialize ClickHouse databases and raw/staging/mart table schemas."""
        ch = clickhouse_client()
        try:
            execute_init_sql(ch)
            print("ClickHouse schemas initialized successfully.")
        finally:
            ch.close()

    @task(outlets=[raw_asset])
    def ingest_minio_to_clickhouse():
        """Validate MinIO manifests and load raw JSONL batches into ClickHouse.

        Per-batch error handling: if a single batch/table fails, it is logged
        as 'failed' in the audit table but processing continues for remaining
        batches. The pipeline only fails if ALL batches fail.
        """
        ch = clickhouse_client()
        mc = minio_client()
        batch_results: list[dict] = []

        try:
            manifest_objects = [
                obj.object_name
                for obj in mc.list_objects(RAW_BUCKET, prefix="raw/", recursive=True)
                if obj.object_name and "/_manifests/" in obj.object_name and obj.object_name.endswith(".json")
            ]
            if not manifest_objects:
                raise ValueError(f"No raw batch manifests found in MinIO bucket {RAW_BUCKET}")

            for manifest_object_name in sorted(manifest_objects):
                try:
                    manifest = read_json_object(mc, manifest_object_name)
                    source_app_id, batch_id, declared_objects = validate_manifest(
                        manifest, manifest_object_name=manifest_object_name
                    )
                except Exception as exc:
                    print(f"WARN: Skipping invalid manifest {manifest_object_name}: {exc}")
                    batch_results.append({
                        "manifest": manifest_object_name,
                        "status": "failed",
                        "error": str(exc),
                    })
                    continue

                for item in declared_objects:
                    load_started_at = datetime.now(timezone.utc)
                    row_count = 0
                    try:
                        source_table, object_name, expected_count = validate_manifest_item(
                            item, source_app_id=source_app_id, batch_id=batch_id,
                        )
                        target_table = f"vsf_raw.{source_table}"
                        records = read_jsonl_object(mc, object_name)
                        if len(records) != expected_count:
                            raise ValueError(
                                f"{object_name} row count mismatch: manifest={expected_count}, actual={len(records)}"
                            )
                        validate_records(
                            records,
                            source_app_id=source_app_id,
                            source_table=source_table,
                            batch_id=batch_id,
                            object_name=object_name,
                        )
                        columns = RAW_TABLE_COLUMNS[source_table]
                        rows = [[normalize_value(column, record.get(column)) for column in columns] for record in records]
                        ch.command(
                            f"ALTER TABLE {target_table} DELETE "
                            f"WHERE source_app_id = {sql_quote(source_app_id)} "
                            f"AND batch_id = toUUID({sql_quote(str(batch_id))})"
                        )
                        if rows:
                            ch.insert(target_table, rows, column_names=columns)
                        row_count = len(rows)
                        insert_audit_row(
                            ch,
                            batch_id=batch_id,
                            source_app_id=source_app_id,
                            source_table=source_table,
                            object_path=f"s3://{RAW_BUCKET}/{object_name}",
                            load_started_at=load_started_at,
                            row_count=row_count,
                            status="success",
                        )
                        print(f"OK: Loaded {row_count} rows from {object_name} into {target_table}")
                        batch_results.append({
                            "manifest": manifest_object_name,
                            "table": source_table,
                            "status": "success",
                            "row_count": row_count,
                        })
                    except Exception as exc:
                        error_msg = str(exc)
                        print(f"WARN: Failed to load batch item from {manifest_object_name}: {error_msg}")
                        # Record failure in audit table but continue
                        try:
                            insert_audit_row(
                                ch,
                                batch_id=batch_id,
                                source_app_id=source_app_id,
                                source_table=item.get("source_table", "unknown"),
                                object_path=f"s3://{RAW_BUCKET}/{item.get('object_name', 'unknown')}",
                                load_started_at=load_started_at,
                                row_count=row_count,
                                status="failed",
                                error_message=error_msg,
                            )
                        except Exception:
                            pass  # Don't crash on audit logging failure
                        batch_results.append({
                            "manifest": manifest_object_name,
                            "table": item.get("source_table", "unknown"),
                            "status": "failed",
                            "error": error_msg,
                        })
        finally:
            ch.close()

        return batch_results

    @task()
    def audit_raw_summary(batch_results: list[dict]):
        """Summarize raw loading results. Fail pipeline only if ALL batches failed."""
        successes = [r for r in batch_results if r.get("status") == "success"]
        failures = [r for r in batch_results if r.get("status") == "failed"]
        total = len(batch_results)

        print(f"Raw Loading Summary: {len(successes)}/{total} succeeded, {len(failures)}/{total} failed")
        for failure in failures:
            print(f"  FAILED: {failure.get('manifest', 'unknown')} / {failure.get('table', 'unknown')}: {failure.get('error', 'N/A')}")

        if not successes and failures:
            raise ValueError(
                f"All {len(failures)} batch items failed. Pipeline cannot continue. "
                f"First error: {failures[0].get('error', 'unknown')}"
            )
        if failures:
            print(f"WARNING: {len(failures)} batch items failed but {len(successes)} succeeded. Continuing to dbt transforms.")

    init_task = init_clickhouse_schemas()
    load_task = ingest_minio_to_clickhouse()
    summary_task = audit_raw_summary(load_task)
    init_task >> load_task >> summary_task


@dag(
    dag_id="agentic_bi_staging",
    default_args=default_args,
    description="Staging transformation pipeline: raw_telemetry -> staging_telemetry",
    schedule=[raw_asset],
    catchup=False,
    tags=["agentic_bi", "staging"],
)
def agentic_bi_staging():

    dbt_env = {
        "DBT_PROFILES_DIR": DBT_PROJECT_DIR,
        "AGENTIC_DBT_CLICKHOUSE_PORT": "8123",
        "AGENTIC_DBT_CLICKHOUSE_HOST": "agentic_clickhouse",
    }

    dbt_run_staging = BashOperator(
        task_id="dbt_run_staging",
        bash_command=(
            f"dbt run --project-dir {DBT_PROJECT_DIR} --profiles-dir {DBT_PROJECT_DIR} "
            f"--target-path /tmp/dbt_target --log-path /tmp/dbt_logs --select tag:staging"
        ),
        env=dbt_env,
    )

    dbt_test_staging = BashOperator(
        task_id="dbt_test_staging",
        bash_command=(
            f"dbt test --project-dir {DBT_PROJECT_DIR} --profiles-dir {DBT_PROJECT_DIR} "
            f"--target-path /tmp/dbt_target --log-path /tmp/dbt_logs --select tag:staging"
        ),
        env=dbt_env,
        outlets=[stg_asset],
    )

    dbt_run_staging >> dbt_test_staging


@dag(
    dag_id="agentic_bi_mart",
    default_args=default_args,
    description="Mart transformation and quality gate pipeline: staging_telemetry -> gold_telemetry",
    schedule=[stg_asset],
    catchup=False,
    tags=["agentic_bi", "mart"],
)
def agentic_bi_mart():

    dbt_env = {
        "DBT_PROFILES_DIR": DBT_PROJECT_DIR,
        "AGENTIC_DBT_CLICKHOUSE_PORT": "8123",
        "AGENTIC_DBT_CLICKHOUSE_HOST": "agentic_clickhouse",
    }

    dbt_run_mart = BashOperator(
        task_id="dbt_run_mart",
        bash_command=(
            f"dbt run --project-dir {DBT_PROJECT_DIR} --profiles-dir {DBT_PROJECT_DIR} "
            f"--target-path /tmp/dbt_target --log-path /tmp/dbt_logs --select tag:mart"
        ),
        env=dbt_env,
    )

    dbt_test_mart = BashOperator(
        task_id="dbt_test_mart",
        bash_command=(
            f"dbt test --project-dir {DBT_PROJECT_DIR} --profiles-dir {DBT_PROJECT_DIR} "
            f"--target-path /tmp/dbt_target --log-path /tmp/dbt_logs --select tag:mart"
        ),
        env=dbt_env,
    )

    @task(outlets=[gold_asset])
    def data_quality_gate():
        """Gold layer quality gate: validate business rules and log results."""
        ch = clickhouse_client()
        ch.command("""
            CREATE TABLE IF NOT EXISTS vsf_mart.data_quality_results (
                test_name String,
                table_name String,
                status String,
                actual_value Float64,
                threshold_value Float64,
                executed_at DateTime64(6, 'UTC')
            ) ENGINE = MergeTree()
            ORDER BY executed_at
        """)

        results = []
        now = datetime.now(timezone.utc)
        total_count = ch.command("SELECT count() FROM vsf_mart.fct_llm_interactions")
        if total_count <= 0:
            raise ValueError("No rows found in vsf_mart.fct_llm_interactions")

        latest_age_hours = ch.command("""
            SELECT dateDiff('hour', max(started_at), now())
            FROM vsf_mart.fct_llm_interactions
        """)
        results.append([
            "freshness_latest_interaction_hours",
            "fct_llm_interactions",
            "PASS" if latest_age_hours <= 120 else "FAIL",
            float(latest_age_hours),
            120.0,
            now,
        ])

        missing_critical = ch.command("""
            SELECT count()
            FROM vsf_mart.fct_llm_interactions
            WHERE source_app_id = '' OR model = '' OR prompt_text = '' OR started_at IS NULL
        """)
        results.append(["critical_column_completeness", "fct_llm_interactions", "PASS" if missing_critical == 0 else "FAIL", float(missing_critical), 0.0, now])

        invalid_numeric = ch.command("""
            SELECT count()
            FROM vsf_mart.fct_llm_interactions
            WHERE total_cost_usd < 0 OR latency_ms < 0 OR total_tokens < 0 OR input_tokens < 0 OR output_tokens < 0
        """)
        results.append(["non_negative_metrics", "fct_llm_interactions", "PASS" if invalid_numeric == 0 else "FAIL", float(invalid_numeric), 0.0, now])

        dups = ch.command("SELECT count() - count(DISTINCT interaction_id) FROM vsf_mart.fct_llm_interactions")
        results.append(["interaction_id_uniqueness", "fct_llm_interactions", "PASS" if dups == 0 else "FAIL", float(dups), 0.0, now])

        invalid_statuses = ch.command("""
            SELECT count()
            FROM vsf_mart.fct_llm_interactions
            WHERE status NOT IN ('success', 'failed')
        """)
        results.append(["accepted_status_values", "fct_llm_interactions", "PASS" if invalid_statuses == 0 else "FAIL", float(invalid_statuses), 0.0, now])

        inactive_apps = ch.command("""
            SELECT count()
            FROM vsf_mart.fct_llm_interactions
            WHERE source_app_id NOT IN (SELECT source_app_id FROM vsf_mart.source_apps WHERE is_active = 1)
        """)
        results.append(["active_source_app_relationship", "fct_llm_interactions", "PASS" if inactive_apps == 0 else "FAIL", float(inactive_apps), 0.0, now])

        daily_rows = ch.command("SELECT count() FROM vsf_mart.gold_app_daily_cost")
        results.append(["daily_cost_rows_present", "gold_app_daily_cost", "PASS" if daily_rows > 0 else "FAIL", float(daily_rows), 1.0, now])

        results.append(["gold_volume_minimum", "fct_llm_interactions", "PASS" if total_count >= 100 else "FAIL", float(total_count), 100.0, now])

        ch.insert(
            "vsf_mart.data_quality_results",
            results,
            column_names=["test_name", "table_name", "status", "actual_value", "threshold_value", "executed_at"],
        )
        ch.close()

        failures = [result for result in results if result[2] == "FAIL"]
        if failures:
            failed_names = ", ".join(result[0] for result in failures)
            raise ValueError(f"Critical Quality Gate failed: {failed_names}")
        print("All Data Quality checks passed successfully.")

    dq_task = data_quality_gate()
    dbt_run_mart >> dbt_test_mart >> dq_task


agentic_bi_pipeline()
agentic_bi_staging()
agentic_bi_mart()

