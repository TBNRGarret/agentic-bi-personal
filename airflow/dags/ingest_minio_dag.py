"""
Airflow DAG: Ingest Parquet files from remote MinIO S3 into PostgreSQL DWH.
Handles all 5 tables x 4 owners with schema differences.
"""

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from datetime import datetime, timedelta
import pyarrow.parquet as pq
import pyarrow as pa
import io
import json
import psycopg2
import os

S3_BUCKET = "telemetry-landing"

SOURCE_PREFIXES = [
    "AN_staging/local_dwh/prod/",
    "BACH_staging/bach_local_dwh/prod/",
    "HOANG_staging/hoang_local_dwh/prod/",
    "TUAN_staging/tuan_local_dwh/prod/",
]

TABLE_MAP = {
    "staging_llm_generations": "llm_generations",
    "staging_llm_traces": "llm_traces",
    "staging_system_logs": "system_logs",
    "staging_system_traces": "system_traces",
    "staging_system_metrics": "system_metrics",
}


# Check connect to DWH
def get_dwh_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres-dwh"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "central_dwh"),
        user=os.getenv("POSTGRES_USER", "dwh_admin"),
        password=os.getenv("POSTGRES_PASSWORD", "dwh_admin_password"),
    )

# Ingest new Parquet files from S3, with idempotency and manifest validation
def ingest_new_files(**context):
    hook = S3Hook(aws_conn_id="minio_s3")
    conn = get_dwh_connection()
    cur = conn.cursor()
    total_loaded = 0

    for prefix in SOURCE_PREFIXES:
        owner = prefix.split("_")[0]
        source_app = prefix.split("/")[1]

        # Validate source in registry
        cur.execute(
            "SELECT is_active FROM raw.source_app_registry WHERE source_app = %s AND source_owner = %s",
            (source_app, owner),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            print(f"Skip {owner}/{source_app}: not registered or inactive")
            continue

        keys = hook.list_keys(bucket_name=S3_BUCKET, prefix=prefix)
        parquet_files = [k for k in keys if k.endswith("data.parquet")]

        for s3_key in parquet_files:
            parts = s3_key.split("/")
            folder_name = parts[3]
            raw_table = TABLE_MAP.get(folder_name)
            if not raw_table:
                continue

            # Read manifest.json
            manifest_key = s3_key.replace("data.parquet", "manifest.json")
            try:
                obj = hook.get_key(manifest_key, S3_BUCKET)
                manifest = json.loads(obj.get()["Body"].read())
                batch_id = manifest["batch_id"]
                declared_rows = manifest.get("row_count", 0)
            except Exception as e:
                print(f"No manifest for {s3_key}: {e}")
                continue

            # Idempotency check
            cur.execute(
                "SELECT status FROM raw.ingestion_batches WHERE batch_id = %s",
                (batch_id,),
            )
            existing = cur.fetchone()
            if existing:
                if existing[0] == "LOADED":
                    print(f"Skip batch {batch_id}: already loaded")
                    continue
                if existing[0] == "LOADING":
                    print(f"Skip batch {batch_id}: still loading")
                    continue

            # Read Parquet
            obj = hook.get_key(s3_key, S3_BUCKET)
            data = obj.get()["Body"].read()
            table = pq.read_table(io.BytesIO(data))
            actual_rows = table.num_rows

            if declared_rows and actual_rows != declared_rows:
                print(f"Row count mismatch: manifest={declared_rows}, actual={actual_rows}")

            # Register batch
            cur.execute(
                """
                INSERT INTO raw.ingestion_batches
                    (batch_id, source_owner, target_table, object_path, manifest_path,
                     declared_rows, status, started_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'LOADING', NOW())
                ON CONFLICT (batch_id) DO UPDATE SET status = 'LOADING', started_at = NOW()
                """,
                (batch_id, owner, raw_table, s3_key, manifest_key, declared_rows),
            )
            conn.commit()

            # Add metadata columns
            owner_col = pa.array([owner] * actual_rows, type=pa.string())
            batch_col = pa.array([batch_id] * actual_rows, type=pa.string())
            table = table.append_column("source_owner", owner_col)
            table = table.append_column("_batch_id", batch_col)

            # Convert all columns to TEXT
            new_columns = []
            for name in table.column_names:
                col = table.column(name)
                if pa.types.is_integer(col.type) or pa.types.is_floating(col.type) or pa.types.is_timestamp(col.type):
                    str_arr = pa.array(
                        [str(v) if v is not None else None for v in col],
                        type=pa.string(),
                    )
                    new_columns.append(str_arr)
                else:
                    new_columns.append(col)
            table = pa.table(dict(zip(table.column_names, new_columns)))

            # Batch insert
            columns = table.column_names
            placeholders = ", ".join(["%s"] * len(columns))
            cols_sql = ", ".join(columns)
            sql = f"INSERT INTO raw.{raw_table} ({cols_sql}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"

            loaded = 0
            for i in range(actual_rows):
                row_data = []
                for j in range(len(columns)):
                    val = table.column(j)[i].as_py()
                    if val is None or (isinstance(val, float) and str(val) == "nan"):
                        row_data.append(None)
                    else:
                        row_data.append(val)
                try:
                    cur.execute(sql, row_data)
                    loaded += 1
                except Exception as e:
                    print(f"Error row {i} in {s3_key}: {e}")
                    continue

            # Update batch status
            cur.execute(
                """
                UPDATE raw.ingestion_batches
                SET status = 'LOADED', loaded_rows = %s, finished_at = NOW()
                WHERE batch_id = %s
                """,
                (loaded, batch_id),
            )
            conn.commit()
            total_loaded += loaded
            print(f"Loaded {loaded}/{actual_rows} rows from {s3_key} -> raw.{raw_table}")

    cur.close()
    conn.close()
    print(f"Total rows loaded: {total_loaded}")
    return total_loaded


with DAG(
    dag_id="ingest_minio_to_dwh",
    schedule="@hourly",
    start_date=datetime(2026, 6, 10),
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    description="Ingest Parquet from MinIO S3 into PostgreSQL raw tables",
) as dag:
    ingest = PythonOperator(
        task_id="ingest_all_parquet_files",
        python_callable=ingest_new_files,
    )
