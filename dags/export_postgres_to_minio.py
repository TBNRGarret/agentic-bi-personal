"""
Automated Export DAG: Postgres DWH → MinIO Raw Landing.

Replaces manual `python scripts/export_dwh_to_minio.py` with a scheduled
Airflow DAG that runs every 3 hours.

Flow:
  1. export_tables  — Connect to Postgres, export each table as JSONL
  2. upload_to_minio — Upload JSONL files + manifest to MinIO
  3. trigger_pipeline — Trigger agentic_bi_pipeline to process the new data
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
import os
from typing import Any
from uuid import UUID, uuid4

from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import dag, task
from minio import Minio


POSTGRES_DWH_HOST = os.getenv("POSTGRES_DWH_HOST", "postgres_dwh")
POSTGRES_DWH_PORT = int(os.getenv("POSTGRES_DWH_PORT", "5432"))
POSTGRES_DWH_USER = os.getenv("POSTGRES_DWH_USER", "postgres")
POSTGRES_DWH_PASSWORD = os.getenv("POSTGRES_DWH_PASSWORD", "securepassword")
POSTGRES_DWH_DB = os.getenv("POSTGRES_DWH_DB", "dwh_db")
POSTGRES_DWH_FALLBACK_HOST = os.getenv("POSTGRES_DWH_FALLBACK_HOST", "localhost")

RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

DEFAULT_SOURCE_APP_ID = os.getenv("SOURCE_APP_ID", "hoangvl_ai_chat")

TABLE_EXPORTS = (
    "signoz_logs",
    "signoz_traces",
    "signoz_metrics",
    "langfuse_generations",
    "user_interactions",
)

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2026, 6, 8),
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def json_default(value: Any) -> str | int | float:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _connect_postgres():
    import psycopg2
    try:
        return psycopg2.connect(
            host=POSTGRES_DWH_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB,
        )
    except Exception:
        return psycopg2.connect(
            host=POSTGRES_DWH_FALLBACK_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB,
        )


def _minio_client() -> Minio:
    return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)


@dag(
    dag_id="export_postgres_to_minio",
    default_args=default_args,
    description="Automated export from Postgres DWH staging tables to MinIO raw landing zone",
    schedule="0 */3 * * *",
    catchup=False,
    tags=["agentic_bi", "export", "minio"],
)
def export_postgres_to_minio():

    @task()
    def export_and_upload() -> dict[str, Any]:
        """Export all staging tables from Postgres and upload as JSONL + manifest to MinIO."""
        batch_id = uuid4()
        source_app_id = DEFAULT_SOURCE_APP_ID
        now = datetime.now(timezone.utc)
        mc = _minio_client()

        # Ensure bucket exists
        if not mc.bucket_exists(RAW_BUCKET):
            mc.make_bucket(RAW_BUCKET)

        conn = _connect_postgres()
        cursor = conn.cursor()
        manifest_objects: list[dict[str, Any]] = []
        summary: dict[str, Any] = {"batch_id": str(batch_id), "source_app_id": source_app_id, "tables": {}}

        try:
            for source_table in TABLE_EXPORTS:
                cursor.execute(f"SELECT * FROM staging.{source_table}")
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()

                # Build JSONL payload
                payload_lines = []
                for row in rows:
                    record = dict(zip(columns, row))
                    record["source_app_id"] = source_app_id
                    record["batch_id"] = str(batch_id)
                    payload_lines.append(json.dumps(record, default=json_default, ensure_ascii=False))

                payload = ("\n".join(payload_lines) + ("\n" if payload_lines else "")).encode("utf-8")

                # Upload JSONL to MinIO
                object_name = f"raw/{source_app_id}/{source_table}/{batch_id}.jsonl"
                mc.put_object(
                    RAW_BUCKET,
                    object_name,
                    BytesIO(payload),
                    len(payload),
                    content_type="application/jsonl",
                )

                manifest_objects.append({
                    "source_table": source_table,
                    "object_name": object_name,
                    "row_count": len(rows),
                    "content_type": "application/jsonl",
                })
                summary["tables"][source_table] = {
                    "object_name": object_name,
                    "row_count": len(rows),
                }
                print(f"Exported {len(rows)} rows from staging.{source_table} → {object_name}")
        finally:
            cursor.close()
            conn.close()

        # Upload manifest
        manifest = {
            "schema_version": "1.0",
            "batch_id": str(batch_id),
            "source_app_id": source_app_id,
            "generated_at": now.isoformat(),
            "objects": manifest_objects,
            "row_counts": {obj["source_table"]: obj["row_count"] for obj in manifest_objects},
        }
        manifest_name = f"raw/{source_app_id}/_manifests/{batch_id}.json"
        manifest_payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        mc.put_object(
            RAW_BUCKET,
            manifest_name,
            BytesIO(manifest_payload),
            len(manifest_payload),
            content_type="application/json",
        )
        summary["manifest"] = manifest_name
        print(f"Uploaded manifest: {manifest_name}")

        total_rows = sum(item["row_count"] for item in manifest_objects)
        print(f"Export complete: {total_rows} total rows across {len(TABLE_EXPORTS)} tables in batch {batch_id}")
        return summary

    trigger_pipeline = TriggerDagRunOperator(
        task_id="trigger_agentic_bi_pipeline",
        trigger_dag_id="sync_external_staging_to_dwh",
        wait_for_completion=False,
        reset_dag_run=True,
    )

    export_task = export_and_upload()
    export_task >> trigger_pipeline


export_postgres_to_minio()
