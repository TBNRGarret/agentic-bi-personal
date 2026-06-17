from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import json
import os
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from dotenv import load_dotenv

load_dotenv()


RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
DEFAULT_SOURCE_APP_ID = "hoangvl_ai_chat"

POSTGRES_DWH_HOST = os.getenv("POSTGRES_DWH_HOST", "postgres_dwh")
POSTGRES_DWH_PORT = int(os.getenv("POSTGRES_DWH_PORT", "5432"))
POSTGRES_DWH_USER = os.getenv("POSTGRES_DWH_USER", "postgres")
POSTGRES_DWH_PASSWORD = os.getenv("POSTGRES_DWH_PASSWORD", "securepassword")
POSTGRES_DWH_DB = os.getenv("POSTGRES_DWH_DB", "dwh_db")
POSTGRES_DWH_FALLBACK_HOST = os.getenv("POSTGRES_DWH_FALLBACK_HOST", "localhost")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:19001")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

TABLE_EXPORTS = (
    "signoz_logs",
    "signoz_traces",
    "signoz_metrics",
    "langfuse_generations",
    "user_interactions",
)

TABLE_TARGET_MAP = {
    "signoz_logs": "staging_system_logs",
    "signoz_traces": "staging_system_traces",
    "signoz_metrics": "staging_system_metrics",
    "langfuse_generations": "staging_llm_generations",
    "user_interactions": "staging_llm_traces",
}


def json_default(value: Any) -> str | int | float:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def connect_postgres():
    try:
        return psycopg2.connect(
            host=POSTGRES_DWH_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB,
        )
    except psycopg2.OperationalError:
        return psycopg2.connect(
            host=POSTGRES_DWH_FALLBACK_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB,
        )


def create_minio_client():
    from minio import Minio

    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def ensure_bucket(minio_client, bucket_name: str) -> None:
    if not minio_client.bucket_exists(bucket_name):
        minio_client.make_bucket(bucket_name)


def export_all_tables(
    pg_connection=None,
    minio_client=None,
    source_app_id: str | None = None,
    batch_id: UUID | None = None,
    bucket_name: str = RAW_BUCKET,
) -> dict[str, Any]:
    """Export Sprint 1 Postgres staging tables as JSONL batch objects."""
    owns_pg_connection = pg_connection is None
    pg_connection = pg_connection or connect_postgres()
    minio_client = minio_client or create_minio_client()
    source_app_id = source_app_id or os.getenv("SOURCE_APP_ID", DEFAULT_SOURCE_APP_ID)
    batch_id = batch_id or uuid4()

    ensure_bucket(minio_client, bucket_name)

    cursor = pg_connection.cursor()
    summary: dict[str, Any] = {"batch_id": str(batch_id), "tables": {}}
    try:
        for source_table in TABLE_EXPORTS:
            cursor.execute(f"SELECT * FROM staging.{source_table}")
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()

            payload_lines = []
            for row in rows:
                record = dict(zip(columns, row))
                record["source_app_id"] = source_app_id
                record["batch_id"] = str(batch_id)
                payload_lines.append(json.dumps(record, default=json_default, ensure_ascii=False))

            payload = ("\n".join(payload_lines) + ("\n" if payload_lines else "")).encode("utf-8")
            target_table_dir = TABLE_TARGET_MAP.get(source_table, source_table)
            minio_path_prefix = os.getenv("MINIO_PATH_PREFIX", f"raw/{source_app_id}")
            object_name = f"{minio_path_prefix}/{target_table_dir}/{batch_id}.jsonl"
            minio_client.put_object(
                bucket_name,
                object_name,
                BytesIO(payload),
                len(payload),
                content_type="application/jsonl",
            )
            summary["tables"][source_table] = {
                "object_name": object_name,
                "row_count": len(rows),
            }
    finally:
        cursor.close()
        if owns_pg_connection:
            pg_connection.close()

    return summary


def main() -> None:
    summary = export_all_tables()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
