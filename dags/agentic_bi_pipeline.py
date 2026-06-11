"""
Agentic BI E2E Pipeline (Central object store -> Raw -> Silver -> Gold).

Orchestrates:
  1. Initialize the proposal-aligned ClickHouse DWH schemas.
  2. Load raw JSONL batches from MinIO into vsf_raw with batch audit rows.
  3. Run dbt build to transform vsf_raw -> vsf_stg -> vsf_mart.
  4. Validate business quality rules and save results to vsf_mart.
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
from minio import Minio


AGENTIC_CLICKHOUSE_HOST = os.getenv("AGENTIC_CLICKHOUSE_HOST", "agentic_clickhouse")
AGENTIC_CLICKHOUSE_PORT = int(os.getenv("AGENTIC_CLICKHOUSE_PORT", "8123"))
AGENTIC_CLICKHOUSE_USER = os.getenv("AGENTIC_CLICKHOUSE_USER", "default")
AGENTIC_CLICKHOUSE_PASSWORD = os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword")

RAW_BUCKET = "agentic-bi-raw"
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

INIT_SQL_PATH = Path(os.getenv("AGENTIC_INIT_SQL_PATH", "/usr/local/airflow/include/clickhouse/init_vsf.sql"))

RAW_TABLES = (
    "vsf_raw.signoz_logs",
    "vsf_raw.signoz_traces",
    "vsf_raw.signoz_metrics",
    "vsf_raw.langfuse_generations",
    "vsf_raw.user_interactions",
)

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

DATETIME_COLUMNS = {"timestamp", "started_at", "ended_at", "ingested_at"}
DECIMAL_COLUMNS = {"estimated_cost_usd"}
UUID_COLUMNS = {"batch_id"}

raw_asset = Asset(
    name="raw_telemetry",
    uri="clickhouse://agentic_clickhouse/vsf_raw",
)
gold_asset = Asset(
    name="gold_telemetry",
    uri="clickhouse://agentic_clickhouse/vsf_mart",
)

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2026, 6, 8),
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


def clickhouse_client():
    return clickhouse_connect.get_client(
        host=AGENTIC_CLICKHOUSE_HOST,
        port=AGENTIC_CLICKHOUSE_PORT,
        username=AGENTIC_CLICKHOUSE_USER,
        password=AGENTIC_CLICKHOUSE_PASSWORD,
    )


def minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_datetime(value):
    if value in (None, ""):
        return datetime.now(timezone.utc)
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
    if source_table not in RAW_TABLE_COLUMNS:
        raise ValueError(f"Unsupported raw source table: {source_table}")
    return source_app_id, source_table, batch_id


def read_jsonl_object(client: Minio, object_name: str) -> list[dict]:
    response = client.get_object(RAW_BUCKET, object_name)
    try:
        payload = response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def execute_init_sql(ch_client) -> None:
    sql = INIT_SQL_PATH.read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            ch_client.command(statement)


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
    now = datetime.now(timezone.utc)
    ch_client.insert(
        "vsf_raw.raw_ingestion_batches",
        [[
            batch_id,
            source_app_id,
            source_table,
            object_path,
            None,
            None,
            load_started_at,
            now,
            row_count,
            status,
            error_message,
            now,
        ]],
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


@dag(
    dag_id="agentic_bi_pipeline",
    default_args=default_args,
    description="End-to-end central DWH pipeline from MinIO raw batches to vsf_mart with quality gate",
    schedule="0 0 * * *",
    catchup=False,
    tags=["agentic_bi", "pipeline"],
)
def agentic_bi_pipeline():

    @task(outlets=[raw_asset])
    def ingest_minio_to_clickhouse():
        """Load raw JSONL batch objects from MinIO into proposal-aligned ClickHouse schemas."""
        ch_client = clickhouse_client()
        object_client = minio_client()
        execute_init_sql(ch_client)

        objects = list(object_client.list_objects(RAW_BUCKET, prefix="raw/", recursive=True))
        if not objects:
            raise ValueError(f"No raw objects found in MinIO bucket {RAW_BUCKET}")

        for obj in objects:
            object_name = obj.object_name
            source_app_id, source_table, batch_id = parse_object_path(object_name)
            target_table = f"vsf_raw.{source_table}"
            load_started_at = datetime.now(timezone.utc)
            row_count = 0

            try:
                records = read_jsonl_object(object_client, object_name)
                rows = []
                columns = RAW_TABLE_COLUMNS[source_table]
                for record in records:
                    record["source_app_id"] = record.get("source_app_id") or source_app_id
                    record["batch_id"] = record.get("batch_id") or str(batch_id)
                    rows.append([normalize_value(column, record.get(column)) for column in columns])

                ch_client.command(
                    f"ALTER TABLE {target_table} DELETE "
                    f"WHERE source_app_id = {sql_quote(source_app_id)} "
                    f"AND batch_id = toUUID({sql_quote(str(batch_id))})"
                )
                if rows:
                    ch_client.insert(target_table, rows, column_names=columns)
                row_count = len(rows)
                insert_audit_row(
                    ch_client,
                    batch_id=batch_id,
                    source_app_id=source_app_id,
                    source_table=source_table,
                    object_path=f"s3://{RAW_BUCKET}/{object_name}",
                    load_started_at=load_started_at,
                    row_count=row_count,
                    status="success",
                )
                print(f"Loaded {row_count} rows from {object_name} into {target_table}")
            except Exception as exc:
                insert_audit_row(
                    ch_client,
                    batch_id=batch_id,
                    source_app_id=source_app_id,
                    source_table=source_table,
                    object_path=f"s3://{RAW_BUCKET}/{object_name}",
                    load_started_at=load_started_at,
                    row_count=row_count,
                    status="failed",
                    error_message=str(exc),
                )
                raise

        ch_client.close()

    run_dbt = BashOperator(
        task_id="run_dbt_transformations",
        bash_command="dbt build --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project",
        env={"DBT_PROFILES_DIR": "/usr/local/airflow/include/dbt_project"},
    )

    @task(outlets=[gold_asset])
    def run_custom_data_quality_checks():
        """Gold layer quality gate: validate business rules and log results."""
        ch_client = clickhouse_client()

        ch_client.command("""
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

        total_count = ch_client.command("SELECT count() FROM vsf_mart.fct_llm_interactions")
        if total_count <= 0:
            raise ValueError("No rows found in vsf_mart.fct_llm_interactions")

        missing_source_apps = ch_client.command(
            "SELECT count() FROM vsf_mart.fct_llm_interactions WHERE source_app_id = ''"
        )
        status = "PASS" if missing_source_apps == 0 else "FAIL"
        results.append(["source_app_id_completeness", "fct_llm_interactions", status, float(missing_source_apps), 0.0, now])

        invalid_cost_latency = ch_client.command("""
            SELECT count()
            FROM vsf_mart.fct_llm_interactions
            WHERE total_cost_usd < 0 OR latency_ms < 0
        """)
        status = "PASS" if invalid_cost_latency == 0 else "FAIL"
        results.append(["non_negative_cost_latency", "fct_llm_interactions", status, float(invalid_cost_latency), 0.0, now])

        daily_rows = ch_client.command("SELECT count() FROM vsf_mart.gold_app_daily_cost")
        status = "PASS" if daily_rows > 0 else "FAIL"
        results.append(["daily_cost_freshness", "gold_app_daily_cost", status, float(daily_rows), 1.0, now])

        empty_prompts = ch_client.command("SELECT count() FROM vsf_mart.fct_llm_interactions WHERE prompt_text = ''")
        missing_rate = (empty_prompts / total_count) * 100
        status = "PASS" if missing_rate < 5.0 else "FAIL"
        results.append(["prompt_completeness_rate", "fct_llm_interactions", status, 100.0 - missing_rate, 95.0, now])

        dups = ch_client.command("SELECT count() - count(DISTINCT interaction_id) FROM vsf_mart.fct_llm_interactions")
        status = "PASS" if dups == 0 else "FAIL"
        results.append(["interaction_id_uniqueness", "fct_llm_interactions", status, float(dups), 0.0, now])

        ch_client.insert(
            "vsf_mart.data_quality_results",
            results,
            column_names=["test_name", "table_name", "status", "actual_value", "threshold_value", "executed_at"],
        )
        ch_client.close()

        for result in results:
            if result[2] == "FAIL":
                raise ValueError(f"Critical Quality Gate failed: {result[0]} on {result[1]}")

        print("All Data Quality checks passed successfully.")

    ingest_task = ingest_minio_to_clickhouse()
    dq_task = run_custom_data_quality_checks()

    ingest_task >> run_dbt >> dq_task


agentic_bi_pipeline()
