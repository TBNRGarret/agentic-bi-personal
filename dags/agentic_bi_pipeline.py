"""
Agentic BI E2E Pipeline (Raw -> Silver -> Gold).

Orchestrates:
  1. Raw ingestion: full-refresh staging data from postgres_dwh into ClickHouse raw tables.
  2. dbt build: transform raw -> silver -> gold and run dbt tests.
  3. Custom data quality: validate business rules and save history to ClickHouse.
"""

from datetime import datetime, timedelta
import json
import os

import clickhouse_connect
import psycopg2
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Asset, dag, task


AGENTIC_CLICKHOUSE_HOST = os.getenv("AGENTIC_CLICKHOUSE_HOST", "agentic_clickhouse")
AGENTIC_CLICKHOUSE_PORT = int(os.getenv("AGENTIC_CLICKHOUSE_PORT", "8123"))
AGENTIC_CLICKHOUSE_USER = os.getenv("AGENTIC_CLICKHOUSE_USER", "default")
AGENTIC_CLICKHOUSE_PASSWORD = os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword")

POSTGRES_DWH_HOST = os.getenv("POSTGRES_DWH_HOST", "postgres_dwh")
POSTGRES_DWH_PORT = int(os.getenv("POSTGRES_DWH_PORT", "5432"))
POSTGRES_DWH_USER = os.getenv("POSTGRES_DWH_USER", "postgres")
POSTGRES_DWH_PASSWORD = os.getenv("POSTGRES_DWH_PASSWORD", "securepassword")
POSTGRES_DWH_DB = os.getenv("POSTGRES_DWH_DB", "dwh_db")

RAW_TABLES = (
    "raw.signoz_logs",
    "raw.signoz_traces",
    "raw.signoz_metrics",
    "raw.langfuse_generations",
    "raw.user_interactions",
)

raw_asset = Asset(
    name="raw_telemetry",
    uri="clickhouse://agentic_clickhouse/raw",
)
gold_asset = Asset(
    name="gold_telemetry",
    uri="clickhouse://agentic_clickhouse/agentic_bi",
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


def postgres_connection():
    return psycopg2.connect(
        host=POSTGRES_DWH_HOST,
        port=POSTGRES_DWH_PORT,
        user=POSTGRES_DWH_USER,
        password=POSTGRES_DWH_PASSWORD,
        dbname=POSTGRES_DWH_DB,
    )


def serialize(val):
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return val


@dag(
    dag_id="agentic_bi_pipeline",
    default_args=default_args,
    description="End-to-end data pipeline from raw ingestion to gold metrics with data quality gate",
    schedule="0 0 * * *",
    catchup=False,
    tags=["agentic_bi", "pipeline"],
)
def agentic_bi_pipeline():

    @task(outlets=[raw_asset])
    def ingest_postgres_to_clickhouse():
        """Full-refresh Postgres DWH staging into ClickHouse raw tables."""
        ch_client = clickhouse_client()
        ch_client.command("CREATE DATABASE IF NOT EXISTS raw")
        ch_client.command("CREATE DATABASE IF NOT EXISTS agentic_bi")

        ch_client.command("""
            CREATE TABLE IF NOT EXISTS raw.signoz_logs (
                log_id String,
                timestamp DateTime64(6, 'UTC'),
                trace_id String,
                span_id String,
                service_name String,
                severity String,
                message String,
                raw_payload String,
                ingested_at DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (timestamp, log_id)
        """)

        ch_client.command("""
            CREATE TABLE IF NOT EXISTS raw.signoz_traces (
                trace_id String,
                span_id String,
                timestamp DateTime64(6, 'UTC'),
                service_name String,
                name String,
                duration_ms Int64,
                raw_payload String,
                ingested_at DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (timestamp, trace_id, span_id)
        """)

        ch_client.command("""
            CREATE TABLE IF NOT EXISTS raw.signoz_metrics (
                metric_id String,
                timestamp DateTime64(6, 'UTC'),
                service_name String,
                metric_name String,
                metric_value Float64,
                unit String,
                attributes String,
                raw_payload String,
                ingested_at DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (timestamp, metric_name, metric_id)
        """)

        ch_client.command("""
            CREATE TABLE IF NOT EXISTS raw.langfuse_generations (
                generation_id String,
                trace_id String,
                timestamp DateTime64(6, 'UTC'),
                model String,
                prompt_text String,
                response_text String,
                input_tokens Int32,
                output_tokens Int32,
                total_tokens Int32,
                estimated_cost_usd Decimal(12, 6),
                duration_ms Int64,
                status String,
                raw_payload String,
                ingested_at DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (timestamp, generation_id)
        """)

        ch_client.command("""
            CREATE TABLE IF NOT EXISTS raw.user_interactions (
                interaction_id String,
                langfuse_trace_id String,
                session_id String,
                user_id String,
                started_at DateTime64(6, 'UTC'),
                ended_at DateTime64(6, 'UTC'),
                model String,
                prompt_text String,
                response_text String,
                input_tokens Int32,
                output_tokens Int32,
                total_tokens Int32,
                estimated_cost_usd Decimal(12, 6),
                status String,
                raw_payload String,
                ingested_at DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree()
            ORDER BY (started_at, interaction_id)
        """)
        for table in RAW_TABLES:
            ch_client.command(f"TRUNCATE TABLE {table}")

        pg_conn = postgres_connection()
        pg_cur = pg_conn.cursor()

        def clean_str(val):
            return "" if val is None else str(val)

        def clean_int(val):
            return 0 if val is None else int(val)

        def clean_float(val):
            return 0.0 if val is None else float(val)

        pg_cur.execute(
            "SELECT log_id, timestamp, trace_id, span_id, service_name, severity, message, raw_payload, ingested_at "
            "FROM staging.signoz_logs"
        )
        rows = pg_cur.fetchall()
        if rows:
            data = [[
                clean_str(r[0]),
                r[1],
                clean_str(r[2]),
                clean_str(r[3]),
                clean_str(r[4]),
                clean_str(r[5]),
                clean_str(r[6]),
                serialize(r[7]),
                r[8]
            ] for r in rows]
            ch_client.insert(
                "raw.signoz_logs",
                data,
                column_names=["log_id", "timestamp", "trace_id", "span_id", "service_name", "severity", "message", "raw_payload", "ingested_at"],
            )
            print(f"Ingested {len(data)} rows to raw.signoz_logs")

        pg_cur.execute(
            "SELECT trace_id, span_id, timestamp, service_name, name, duration_ms, raw_payload, ingested_at "
            "FROM staging.signoz_traces"
        )
        rows = pg_cur.fetchall()
        if rows:
            data = [[
                clean_str(r[0]),
                clean_str(r[1]),
                r[2],
                clean_str(r[3]),
                clean_str(r[4]),
                clean_int(r[5]),
                serialize(r[6]),
                r[7]
            ] for r in rows]
            ch_client.insert(
                "raw.signoz_traces",
                data,
                column_names=["trace_id", "span_id", "timestamp", "service_name", "name", "duration_ms", "raw_payload", "ingested_at"],
            )
            print(f"Ingested {len(data)} rows to raw.signoz_traces")

        pg_cur.execute(
            "SELECT metric_id, timestamp, service_name, metric_name, metric_value, unit, attributes, raw_payload, ingested_at "
            "FROM staging.signoz_metrics"
        )
        rows = pg_cur.fetchall()
        if rows:
            data = [[
                clean_str(r[0]),
                r[1],
                clean_str(r[2]),
                clean_str(r[3]),
                clean_float(r[4]),
                clean_str(r[5]),
                serialize(r[6]),
                serialize(r[7]),
                r[8]
            ] for r in rows]
            ch_client.insert(
                "raw.signoz_metrics",
                data,
                column_names=["metric_id", "timestamp", "service_name", "metric_name", "metric_value", "unit", "attributes", "raw_payload", "ingested_at"],
            )
            print(f"Ingested {len(data)} rows to raw.signoz_metrics")

        pg_cur.execute(
            "SELECT generation_id, trace_id, timestamp, model, prompt_text, response_text, input_tokens, output_tokens, "
            "total_tokens, estimated_cost_usd, duration_ms, status, raw_payload, ingested_at "
            "FROM staging.langfuse_generations"
        )
        rows = pg_cur.fetchall()
        if rows:
            data = [[
                clean_str(r[0]),
                clean_str(r[1]),
                r[2],
                clean_str(r[3]),
                clean_str(r[4]),
                clean_str(r[5]),
                clean_int(r[6]),
                clean_int(r[7]),
                clean_int(r[8]),
                clean_float(r[9]),
                clean_int(r[10]),
                clean_str(r[11]),
                serialize(r[12]),
                r[13]
            ] for r in rows]
            ch_client.insert(
                "raw.langfuse_generations",
                data,
                column_names=["generation_id", "trace_id", "timestamp", "model", "prompt_text", "response_text", "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd", "duration_ms", "status", "raw_payload", "ingested_at"],
            )
            print(f"Ingested {len(data)} rows to raw.langfuse_generations")

        pg_cur.execute(
            "SELECT interaction_id, langfuse_trace_id, session_id, user_id, started_at, ended_at, model, prompt_text, "
            "response_text, input_tokens, output_tokens, total_tokens, estimated_cost_usd, status, raw_payload, ingested_at "
            "FROM staging.user_interactions"
        )
        rows = pg_cur.fetchall()
        if rows:
            data = [[
                clean_str(r[0]),
                clean_str(r[1]),
                clean_str(r[2]),
                clean_str(r[3]),
                r[4],
                r[5] if r[5] is not None else r[4],
                clean_str(r[6]),
                clean_str(r[7]),
                clean_str(r[8]),
                clean_int(r[9]),
                clean_int(r[10]),
                clean_int(r[11]),
                clean_float(r[12]),
                clean_str(r[13]),
                serialize(r[14]),
                r[15]
            ] for r in rows]
            ch_client.insert(
                "raw.user_interactions",
                data,
                column_names=["interaction_id", "langfuse_trace_id", "session_id", "user_id", "started_at", "ended_at", "model", "prompt_text", "response_text", "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd", "status", "raw_payload", "ingested_at"],
            )
            print(f"Ingested {len(data)} rows to raw.user_interactions")

        pg_cur.close()
        pg_conn.close()
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
            CREATE TABLE IF NOT EXISTS agentic_bi.data_quality_results (
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
        now = datetime.now()

        total_count = ch_client.command("SELECT count() FROM agentic_bi.fct_llm_interactions")
        if total_count <= 0:
            raise ValueError("No rows found in agentic_bi.fct_llm_interactions")

        empty_prompts = ch_client.command("SELECT count() FROM agentic_bi.fct_llm_interactions WHERE prompt_text = ''")
        missing_rate = (empty_prompts / total_count) * 100
        status = "PASS" if missing_rate < 5.0 else "FAIL"
        results.append(["prompt_completeness_rate", "fct_llm_interactions", status, 100.0 - missing_rate, 95.0, now])
        print(f"Quality Check 1: Prompt Completeness rate is {100.0 - missing_rate}% (status: {status})")

        dups = ch_client.command("SELECT count() - count(DISTINCT interaction_id) FROM agentic_bi.fct_llm_interactions")
        status = "PASS" if dups == 0 else "FAIL"
        results.append(["interaction_id_uniqueness", "fct_llm_interactions", status, float(dups), 0.0, now])
        print(f"Quality Check 2: Duplicated interaction_ids: {dups} (status: {status})")

        invalid_latency = ch_client.command("SELECT count() FROM agentic_bi.fct_llm_interactions WHERE ended_at < started_at")
        status = "PASS" if invalid_latency == 0 else "FAIL"
        results.append(["latency_validity", "fct_llm_interactions", status, float(invalid_latency), 0.0, now])
        print(f"Quality Check 3: Invalid latency records: {invalid_latency} (status: {status})")

        ch_client.insert(
            "agentic_bi.data_quality_results",
            results,
            column_names=["test_name", "table_name", "status", "actual_value", "threshold_value", "executed_at"],
        )
        ch_client.close()

        for result in results:
            if result[2] == "FAIL":
                raise ValueError(f"Critical Quality Gate failed: {result[0]} on {result[1]}")

        print("All Data Quality checks passed successfully.")

    ingest_task = ingest_postgres_to_clickhouse()
    dq_task = run_custom_data_quality_checks()

    ingest_task >> run_dbt >> dq_task


agentic_bi_pipeline()
