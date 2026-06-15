"""
Smoke DAG - local Sprint 1 infrastructure verification.

Verifies repo-owned services only:
  1. Agentic BI ClickHouse
  2. MinIO raw object store
"""

from datetime import datetime
import os

from airflow.sdk import dag, task


AGENTIC_CLICKHOUSE_HOST = os.getenv("AGENTIC_CLICKHOUSE_HOST", "agentic_clickhouse")
AGENTIC_CLICKHOUSE_PORT = int(os.getenv("AGENTIC_CLICKHOUSE_PORT", "8123"))
AGENTIC_CLICKHOUSE_USER = os.getenv("AGENTIC_CLICKHOUSE_USER", "default")
AGENTIC_CLICKHOUSE_PASSWORD = os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")


@dag(
    dag_id="smoke_dag",
    description="Verify local Agentic BI infrastructure services",
    schedule=None,
    start_date=datetime(2026, 6, 8),
    catchup=False,
    tags=["infra", "smoke"],
    default_args={"retries": 2},
)
def smoke_dag():
    @task()
    def check_agentic_clickhouse():
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=AGENTIC_CLICKHOUSE_HOST,
            port=AGENTIC_CLICKHOUSE_PORT,
            username=AGENTIC_CLICKHOUSE_USER,
            password=AGENTIC_CLICKHOUSE_PASSWORD,
        )
        version = client.command("SELECT version()")
        client.command("CREATE DATABASE IF NOT EXISTS vsf_raw")
        client.command("CREATE DATABASE IF NOT EXISTS vsf_stg")
        client.command("CREATE DATABASE IF NOT EXISTS vsf_mart")
        print(f"[OK] Agentic ClickHouse connected - version {version}")
        client.close()
        return version

    @task()
    def check_minio():
        from minio import Minio

        client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        if not client.bucket_exists(MINIO_BUCKET):
            print(f"[WARN] MinIO bucket {MINIO_BUCKET} does not exist yet; seed_minio.py will create it.")
            return "missing_bucket"
        objects = list(client.list_objects(MINIO_BUCKET, prefix="raw/", recursive=True))
        print(f"[OK] MinIO reachable - {len(objects)} raw objects currently present")
        return "ok"

    check_agentic_clickhouse()
    check_minio()


smoke_dag()
