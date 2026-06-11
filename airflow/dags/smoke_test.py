"""
Airflow DAG: Smoke Test
Basic connectivity check: MinIO S3 + PostgreSQL DWH.
"""

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from datetime import datetime
import psycopg2


def check_s3():
    hook = S3Hook(aws_conn_id="minio_s3")
    exists = hook.check_for_bucket(bucket_name="telemetry-landing")
    print(f"[OK] MinIO bucket 'telemetry-landing' exists: {exists}")
    assert exists, "telemetry-landing bucket not found!"
    keys = hook.list_prefixes(bucket_name="telemetry-landing", delimiter="/")
    print(f"[OK] MinIO prefixes: {keys}")


def check_dwh():
    conn = psycopg2.connect(
        host="postgres-dwh",
        port=5432,
        dbname="central_dwh",
        user="dwh_admin",
        password="dwh_admin_password",
    )
    cur = conn.cursor()
    cur.execute("SELECT 1")
    result = cur.fetchone()
    print(f"[OK] DWH connected: {result}")

    # Check schemas
    cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('raw', 'silver', 'gold')")
    schemas = [r[0] for r in cur.fetchall()]
    print(f"[OK] Schemas found: {schemas}")
    assert 'raw' in schemas, "raw schema missing!"
    assert 'silver' in schemas, "silver schema missing!"
    assert 'gold' in schemas, "gold schema missing!"

    # Check raw tables
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"[OK] Raw tables: {tables}")
    assert len(tables) >= 5, f"Expected >=5 raw tables, got {len(tables)}"

    # Check source_app_registry
    cur.execute("SELECT source_owner, is_active FROM raw.source_app_registry")
    sources = cur.fetchall()
    print(f"[OK] Registered sources: {sources}")
    assert len(sources) == 4, f"Expected 4 sources, got {len(sources)}"

    cur.close()
    conn.close()


with DAG(
    dag_id="smoke_test",
    start_date=datetime(2026, 6, 10),
    catchup=False,
    schedule=None,
    description="Health check: S3 + DWH connectivity",
    default_args={"retries": 2},
) as dag:
    task_check_s3 = PythonOperator(
        task_id="check_s3",
        python_callable=check_s3,
    )
    task_check_dwh = PythonOperator(
        task_id="check_dwh",
        python_callable=check_dwh,
    )
    task_check_s3 >> task_check_dwh
