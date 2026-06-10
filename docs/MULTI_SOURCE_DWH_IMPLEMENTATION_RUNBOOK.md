# Multi-Source DWH Implementation Runbook

Tài liệu này mô tả chi tiết cách implement luồng nhiều app / nhiều người cùng đẩy dữ liệu staging vào DWH server.

Mục tiêu cuối cùng:

```text
Nhiều app / nhiều team
  -> upload data.parquet + manifest.json vào MinIO/S3 landing zone
  -> Airflow validate manifest, registry, schema, row count
  -> load vào staging tables trong Central DWH
  -> dbt build silver/gold
  -> Metabase / Observation Web / Agent đọc từ gold tables
```

Nguyên tắc quan trọng: người gửi data không ghi trực tiếp vào DWH. Họ chỉ upload file vào MinIO/S3. Airflow là bên duy nhất được quyền validate và load vào DWH.

## 1. Thứ tự triển khai

Làm theo thứ tự này để tránh rối pipeline:

1. Tạo bảng `source_app_registry`.
2. Tạo bảng `raw_ingestion_batches`.
3. Thêm metadata columns vào các bảng staging.
4. Chuẩn hóa convention bucket/path trên MinIO/S3.
5. Chuẩn hóa format `manifest.json`.
6. Viết DAG `sync_external_staging_to_dwh`.
7. Cho Airflow load file vào staging.
8. Cho dbt build silver/gold từ staging.
9. Thêm dashboard quan sát batch ingest.

## 2. SQL migration cho DWH

Tạo file migration mới, ví dụ:

```text
database/add-multi-source-ingestion.sql
```

Nội dung đề xuất:

```sql
create table if not exists source_app_registry (
    source_app text primary key,
    source_owner text not null,
    source_env text not null default 'prod',
    description text,
    expected_tables text[] not null default '{}',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint source_app_registry_env_check
        check (source_env in ('dev', 'staging', 'prod'))
);

create table if not exists raw_ingestion_batches (
    batch_id text primary key,
    source_app text not null references source_app_registry(source_app),
    source_owner text not null,
    source_env text not null,
    target_table text not null,
    object_path text not null,
    manifest_path text not null,
    file_format text not null,
    declared_row_count bigint,
    loaded_row_count bigint,
    status text not null default 'PENDING',
    error_message text,
    submitted_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz not null default now(),
    constraint raw_ingestion_batches_status_check
        check (status in ('PENDING', 'VALIDATING', 'LOADING', 'LOADED', 'FAILED', 'SKIPPED')),
    constraint raw_ingestion_batches_format_check
        check (file_format in ('parquet', 'jsonl', 'csv')),
    constraint raw_ingestion_batches_env_check
        check (source_env in ('dev', 'staging', 'prod'))
);

create index if not exists idx_raw_ingestion_batches_source_app
    on raw_ingestion_batches(source_app);

create index if not exists idx_raw_ingestion_batches_status
    on raw_ingestion_batches(status);

create index if not exists idx_raw_ingestion_batches_submitted_at
    on raw_ingestion_batches(submitted_at desc);
```

Sau đó thêm metadata columns vào các bảng staging hiện có. Nếu project của bạn đang có các bảng như `staging_system_traces`, `staging_system_logs`, `staging_llm_traces`, `staging_llm_generations`, hãy thêm:

```sql
alter table staging_system_traces
    add column if not exists source_app text,
    add column if not exists batch_id text,
    add column if not exists source_owner text,
    add column if not exists source_env text;

alter table staging_system_logs
    add column if not exists source_app text,
    add column if not exists batch_id text,
    add column if not exists source_owner text,
    add column if not exists source_env text;

alter table staging_llm_traces
    add column if not exists source_app text,
    add column if not exists batch_id text,
    add column if not exists source_owner text,
    add column if not exists source_env text;

alter table staging_llm_generations
    add column if not exists source_app text,
    add column if not exists batch_id text,
    add column if not exists source_owner text,
    add column if not exists source_env text;
```

Thêm index để query/debug theo nguồn nhanh hơn:

```sql
create index if not exists idx_staging_system_traces_source_batch
    on staging_system_traces(source_app, batch_id);

create index if not exists idx_staging_system_logs_source_batch
    on staging_system_logs(source_app, batch_id);

create index if not exists idx_staging_llm_traces_source_batch
    on staging_llm_traces(source_app, batch_id);

create index if not exists idx_staging_llm_generations_source_batch
    on staging_llm_generations(source_app, batch_id);
```

Nếu staging table có unique key riêng, ví dụ `trace_id`, `generation_id`, nên đổi unique key thành dạng có `source_app` để tránh đụng ID giữa nhiều app:

```sql
-- Ví dụ, chỉ áp dụng nếu bảng có các cột này.
create unique index if not exists uq_staging_llm_traces_source_trace
    on staging_llm_traces(source_app, trace_id);
```

## 3. Đăng ký source app

Mỗi app/team muốn gửi data phải có một dòng trong `source_app_registry`.

Ví dụ:

```sql
insert into source_app_registry (
    source_app,
    source_owner,
    source_env,
    description,
    expected_tables
)
values
    (
        'medical_chatbot',
        'team_health_ai',
        'prod',
        'App chat y te production',
        array['staging_llm_traces', 'staging_llm_generations']
    ),
    (
        'education_chatbot',
        'team_edu_ai',
        'prod',
        'App chat giao duc production',
        array['staging_llm_traces', 'staging_llm_generations']
    )
on conflict (source_app) do update set
    source_owner = excluded.source_owner,
    source_env = excluded.source_env,
    description = excluded.description,
    expected_tables = excluded.expected_tables,
    updated_at = now();
```

Ý nghĩa:

- `source_app`: mã nguồn dữ liệu, phải ổn định, không đổi lung tung.
- `source_owner`: team hoặc người chịu trách nhiệm.
- `source_env`: môi trường nguồn, thường là `prod`.
- `expected_tables`: danh sách staging table mà app này được phép gửi.
- `is_active`: nếu false thì Airflow phải từ chối batch mới.

## 4. MinIO/S3 landing zone

Tạo bucket riêng cho data landing, ví dụ:

```text
external-staging
```

Convention path:

```text
s3://external-staging/{source_app}/{source_env}/{target_table}/{batch_id}/manifest.json
s3://external-staging/{source_app}/{source_env}/{target_table}/{batch_id}/data.parquet
```

Ví dụ:

```text
s3://external-staging/medical_chatbot/prod/staging_llm_traces/2026-06-10T020000Z/manifest.json
s3://external-staging/medical_chatbot/prod/staging_llm_traces/2026-06-10T020000Z/data.parquet
```

Không nên để người gửi tự đặt path tùy ý. Path phải map được ra:

- source app nào gửi
- env nào
- target staging table nào
- batch nào

## 5. Chuẩn manifest.json

Mỗi batch bắt buộc có `manifest.json`.

Ví dụ:

```json
{
  "batch_id": "2026-06-10T020000Z",
  "source_app": "medical_chatbot",
  "source_owner": "team_health_ai",
  "source_env": "prod",
  "target_table": "staging_llm_traces",
  "file_name": "data.parquet",
  "file_format": "parquet",
  "row_count": 12500,
  "schema_version": "v1",
  "exported_at": "2026-06-10T02:00:00Z",
  "checksum_sha256": "optional-but-recommended"
}
```

Các field bắt buộc:

```text
batch_id
source_app
source_owner
source_env
target_table
file_name
file_format
row_count
schema_version
exported_at
```

Airflow phải validate:

- `source_app` tồn tại trong `source_app_registry`.
- `is_active = true`.
- `source_owner` khớp registry.
- `source_env` hợp lệ.
- `target_table` nằm trong `expected_tables`.
- `file_format` được hỗ trợ.
- `row_count > 0`.
- `data.parquet` tồn tại cùng folder với manifest.
- `batch_id` chưa load thành công trước đó.

## 6. Upload data lên MinIO/S3

Người gửi data cần upload đúng 2 file:

```text
manifest.json
data.parquet
```

Ví dụ dùng MinIO client:

```powershell
mc alias set vin-minio http://YOUR_SERVER:9000 MINIO_ACCESS_KEY MINIO_SECRET_KEY
mc cp .\manifest.json vin-minio/external-staging/medical_chatbot/prod/staging_llm_traces/2026-06-10T020000Z/manifest.json
mc cp .\data.parquet vin-minio/external-staging/medical_chatbot/prod/staging_llm_traces/2026-06-10T020000Z/data.parquet
```

Nếu upload từ máy local lên VPS, endpoint phải là MinIO/S3 của VPS, không phải MinIO local trên laptop.

Nếu bạn muốn kiểm soát chặt hơn, mỗi source app nên có access key riêng và chỉ được ghi vào prefix của chính nó:

```text
external-staging/medical_chatbot/*
external-staging/education_chatbot/*
```

## 7. Airflow Connections cần có

Trong Airflow UI, tạo các connection:

```text
aws_default
dwh_postgres
```

`aws_default` trỏ tới MinIO/S3:

```text
Conn Type: Amazon Web Services
AWS Access Key ID: ...
AWS Secret Access Key: ...
Extra:
{
  "endpoint_url": "http://minio:9000",
  "region_name": "us-east-1"
}
```

`dwh_postgres` trỏ tới Central DWH:

```text
Conn Type: Postgres
Host: dwh
Schema: agentic_bi
Login: ...
Password: ...
Port: 5432
```

## 8. DAG sync_external_staging_to_dwh

Tạo file:

```text
airflow/dags/sync_external_staging_to_dwh.py
```

Flow đề xuất:

```text
scan_manifests
  -> validate_manifest
  -> register_batch
  -> load_to_staging
  -> verify_loaded_count
  -> trigger_dbt_build
```

Skeleton:

```python
from __future__ import annotations

import json
from datetime import datetime

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook


BUCKET = "external-staging"
AWS_CONN_ID = "aws_default"
DWH_CONN_ID = "dwh_postgres"

ALLOWED_TABLES = {
    "staging_system_traces",
    "staging_system_logs",
    "staging_llm_traces",
    "staging_llm_generations",
}


@dag(
    dag_id="sync_external_staging_to_dwh",
    start_date=datetime(2026, 1, 1),
    schedule="*/15 * * * *",
    catchup=False,
    max_active_runs=1,
    tags=["ingestion", "multi-source", "dwh"],
)
def sync_external_staging_to_dwh():
    @task
    def scan_manifests() -> list[str]:
        s3 = S3Hook(aws_conn_id=AWS_CONN_ID)
        keys = s3.list_keys(bucket_name=BUCKET, suffix="manifest.json") or []
        return keys

    @task
    def validate_manifest(manifest_key: str) -> dict:
        s3 = S3Hook(aws_conn_id=AWS_CONN_ID)
        pg = PostgresHook(postgres_conn_id=DWH_CONN_ID)

        raw_manifest = s3.read_key(key=manifest_key, bucket_name=BUCKET)
        manifest = json.loads(raw_manifest)

        required_fields = [
            "batch_id",
            "source_app",
            "source_owner",
            "source_env",
            "target_table",
            "file_name",
            "file_format",
            "row_count",
            "schema_version",
            "exported_at",
        ]
        missing = [field for field in required_fields if field not in manifest]
        if missing:
            raise AirflowFailException(f"Manifest missing fields: {missing}")

        if manifest["target_table"] not in ALLOWED_TABLES:
            raise AirflowFailException(f"Target table is not allowed: {manifest['target_table']}")

        if manifest["file_format"] not in {"parquet", "jsonl", "csv"}:
            raise AirflowFailException(f"Unsupported file format: {manifest['file_format']}")

        if int(manifest["row_count"]) <= 0:
            raise AirflowFailException("row_count must be greater than 0")

        registry_row = pg.get_first(
            """
            select source_owner, source_env, expected_tables, is_active
            from source_app_registry
            where source_app = %s
            """,
            parameters=(manifest["source_app"],),
        )
        if not registry_row:
            raise AirflowFailException(f"source_app is not registered: {manifest['source_app']}")

        registered_owner, registered_env, expected_tables, is_active = registry_row
        if not is_active:
            raise AirflowFailException(f"source_app is disabled: {manifest['source_app']}")

        if registered_owner != manifest["source_owner"]:
            raise AirflowFailException("source_owner does not match source_app_registry")

        if registered_env != manifest["source_env"]:
            raise AirflowFailException("source_env does not match source_app_registry")

        if manifest["target_table"] not in expected_tables:
            raise AirflowFailException("target_table is not allowed for this source_app")

        existing = pg.get_first(
            """
            select status
            from raw_ingestion_batches
            where batch_id = %s
              and source_app = %s
              and target_table = %s
            """,
            parameters=(
                manifest["batch_id"],
                manifest["source_app"],
                manifest["target_table"],
            ),
        )
        if existing and existing[0] == "LOADED":
            raise AirflowSkipException("Batch already loaded")

        data_key = manifest_key.rsplit("/", 1)[0] + "/" + manifest["file_name"]
        if not s3.check_for_key(key=data_key, bucket_name=BUCKET):
            raise AirflowFailException(f"Data file not found: {data_key}")

        manifest["manifest_key"] = manifest_key
        manifest["data_key"] = data_key
        return manifest

    @task
    def register_batch(manifest: dict) -> dict:
        pg = PostgresHook(postgres_conn_id=DWH_CONN_ID)
        pg.run(
            """
            insert into raw_ingestion_batches (
                batch_id,
                source_app,
                source_owner,
                source_env,
                target_table,
                object_path,
                manifest_path,
                file_format,
                declared_row_count,
                status,
                submitted_at,
                started_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'VALIDATING', now(), now())
            on conflict (batch_id) do update set
                status = 'VALIDATING',
                error_message = null,
                started_at = now()
            """,
            parameters=(
                manifest["batch_id"],
                manifest["source_app"],
                manifest["source_owner"],
                manifest["source_env"],
                manifest["target_table"],
                manifest["data_key"],
                manifest["manifest_key"],
                manifest["file_format"],
                manifest["row_count"],
            ),
        )
        return manifest

    @task
    def load_to_staging(manifest: dict) -> dict:
        """
        Production implementation:
        - parquet: download file, read by pandas/pyarrow, insert/copy into target table.
        - jsonl: stream lines and insert in chunks.
        - csv: copy_expert into temp table, then insert with metadata columns.

        Keep this function table-aware. Do not build raw SQL column names from user input
        except target_table after checking it against ALLOWED_TABLES.
        """
        pg = PostgresHook(postgres_conn_id=DWH_CONN_ID)
        pg.run(
            """
            update raw_ingestion_batches
            set status = 'LOADING'
            where batch_id = %s
            """,
            parameters=(manifest["batch_id"],),
        )

        # Implement the physical loader here.
        # Start with jsonl/csv if you want fewer dependencies.
        # For parquet, add pandas + pyarrow to the Airflow image.

        pg.run(
            """
            update raw_ingestion_batches
            set status = 'LOADED',
                loaded_row_count = %s,
                finished_at = now()
            where batch_id = %s
            """,
            parameters=(manifest["row_count"], manifest["batch_id"]),
        )
        return manifest

    @task
    def verify_loaded_count(manifest: dict) -> dict:
        pg = PostgresHook(postgres_conn_id=DWH_CONN_ID)
        count_row = pg.get_first(
            f"""
            select count(*)
            from {manifest["target_table"]}
            where source_app = %s
              and batch_id = %s
            """,
            parameters=(manifest["source_app"], manifest["batch_id"]),
        )
        loaded_count = int(count_row[0])
        declared_count = int(manifest["row_count"])

        if loaded_count != declared_count:
            pg.run(
                """
                update raw_ingestion_batches
                set status = 'FAILED',
                    loaded_row_count = %s,
                    error_message = %s,
                    finished_at = now()
                where batch_id = %s
                """,
                parameters=(
                    loaded_count,
                    f"Loaded row count {loaded_count} != declared row count {declared_count}",
                    manifest["batch_id"],
                ),
            )
            raise AirflowFailException("Loaded row count does not match manifest row_count")

        return manifest

    manifests = scan_manifests()
    valid = validate_manifest.expand(manifest_key=manifests)
    registered = register_batch.expand(manifest=valid)
    loaded = load_to_staging.expand(manifest=registered)
    verified = verify_loaded_count.expand(manifest=loaded)

    trigger_dbt_build = TriggerDagRunOperator(
        task_id="trigger_agentic_bi_dbt_build",
        trigger_dag_id="agentic_bi_sprint1",
        wait_for_completion=False,
        reset_dag_run=False,
    )

    verified >> trigger_dbt_build


sync_external_staging_to_dwh()
```

Lưu ý quan trọng: đoạn `verify_loaded_count` dùng f-string cho table name nhưng chỉ sau khi `target_table` đã được validate trong `ALLOWED_TABLES`. Không nhận table name tự do từ user.

## 9. Loader parquet vào staging

Để load Parquet, Airflow image cần có:

```text
pandas
pyarrow
```

Thêm vào requirements hoặc Dockerfile Airflow tùy project đang quản lý dependency ở đâu.

Logic loader nên đi theo pattern:

```text
download data.parquet từ MinIO
read parquet thành DataFrame
add columns source_app, batch_id, source_owner, source_env
select đúng columns của target staging table
insert/copy vào DWH theo chunk
update raw_ingestion_batches.loaded_row_count
```

Pseudo-code:

```python
import pandas as pd
from psycopg2.extras import execute_values


def load_parquet_manifest(manifest: dict) -> int:
    s3 = S3Hook(aws_conn_id=AWS_CONN_ID)
    pg = PostgresHook(postgres_conn_id=DWH_CONN_ID)

    local_path = s3.download_file(
        key=manifest["data_key"],
        bucket_name=BUCKET,
        local_path="/tmp",
    )

    df = pd.read_parquet(local_path)
    df["source_app"] = manifest["source_app"]
    df["batch_id"] = manifest["batch_id"]
    df["source_owner"] = manifest["source_owner"]
    df["source_env"] = manifest["source_env"]

    target_table = manifest["target_table"]
    columns = get_allowed_columns_for_table(target_table)
    df = df[columns]

    rows = [tuple(row) for row in df.itertuples(index=False, name=None)]
    sql = f"insert into {target_table} ({', '.join(columns)}) values %s"

    conn = pg.get_conn()
    with conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=1000)

    return len(rows)
```

`get_allowed_columns_for_table` phải là mapping hard-code trong code, không lấy trực tiếp từ manifest:

```python
TABLE_COLUMNS = {
    "staging_llm_traces": [
        "trace_id",
        "session_id",
        "user_id",
        "name",
        "timestamp",
        "metadata",
        "source_app",
        "batch_id",
        "source_owner",
        "source_env",
    ],
    "staging_llm_generations": [
        "generation_id",
        "trace_id",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_usd",
        "timestamp",
        "source_app",
        "batch_id",
        "source_owner",
        "source_env",
    ],
}
```

## 10. dbt build silver/gold

Sau khi staging có thêm metadata columns, dbt phải giữ lại lineage này lên silver/gold.

Ví dụ silver model:

```sql
with source as (
    select *
    from {{ source('dwh', 'staging_llm_traces') }}
),

cleaned as (
    select
        source_app,
        batch_id,
        source_owner,
        source_env,
        trace_id,
        session_id,
        user_id,
        name,
        timestamp as trace_timestamp,
        metadata
    from source
    where source_app is not null
      and batch_id is not null
)

select *
from cleaned
```

Ví dụ gold model aggregate theo app:

```sql
select
    date_trunc('day', trace_timestamp)::date as day,
    source_app,
    source_env,
    count(*) as total_traces,
    count(distinct session_id) as total_sessions,
    count(distinct user_id) as total_users
from {{ ref('silver_llm_traces') }}
group by 1, 2, 3
```

Schema tests nên có:

```yaml
models:
  - name: silver_llm_traces
    columns:
      - name: source_app
        tests:
          - not_null
      - name: batch_id
        tests:
          - not_null
      - name: trace_id
        tests:
          - not_null
```

Nếu bảng có business key, thêm uniqueness theo source:

```yaml
models:
  - name: silver_llm_traces
    tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns:
            - source_app
            - trace_id
```

## 11. Trigger dbt sau khi load

Có 2 cách:

### Cách A: DAG sync trigger DAG chính

Sau khi batch load xong:

```text
sync_external_staging_to_dwh -> trigger agentic_bi_sprint1
```

Phù hợp khi DAG chính đang làm:

```text
source freshness -> dbt build -> DQ publish -> dashboard
```

### Cách B: DAG sync tự chạy dbt build

Nếu muốn sync DAG tự xử lý luôn:

```text
load_to_staging -> dbt source freshness -> dbt build --select silver+ gold+ -> publish DQ
```

Production nên dùng cách A nếu bạn đã có DAG dbt chuẩn riêng. Như vậy ingestion và transform tách trách nhiệm rõ hơn.

## 12. Dashboard theo dõi batch

Nên expose endpoint:

```text
GET /api/dashboard/ingestion-batches
```

Query chính:

```sql
select
    batch_id,
    source_app,
    source_owner,
    source_env,
    target_table,
    file_format,
    declared_row_count,
    loaded_row_count,
    status,
    error_message,
    submitted_at,
    started_at,
    finished_at
from raw_ingestion_batches
order by submitted_at desc
limit 100;
```

UI nên hiển thị:

- batch mới nhất
- batch failed
- loaded rows theo ngày
- loaded rows theo source_app
- source_app nào gửi data nhiều nhất
- source_app nào đang lỗi nhiều nhất

## 13. Checklist end-to-end

Sau khi implement, test theo thứ tự:

1. Tạo source app:

```sql
select * from source_app_registry;
```

2. Upload `manifest.json` và `data.parquet` vào MinIO.

3. Chạy DAG:

```text
sync_external_staging_to_dwh
```

4. Kiểm tra batch:

```sql
select *
from raw_ingestion_batches
order by submitted_at desc
limit 10;
```

5. Kiểm tra staging:

```sql
select
    source_app,
    batch_id,
    source_owner,
    source_env,
    count(*) as rows
from staging_llm_traces
group by 1, 2, 3, 4
order by rows desc;
```

6. Chạy dbt:

```bash
dbt build --select silver+ gold+
```

7. Kiểm tra gold:

```sql
select *
from gold_llm_usage_daily
order by day desc
limit 20;
```

8. Kiểm tra dashboard:

```text
Observation Web -> Overview / Data Quality / Ingestion Batches
Metabase -> Gold dashboards
```

## 14. Rollout khuyến nghị

Làm theo 3 phase:

### Phase 1: chạy được

- Tạo 2 registry tables.
- Thêm 4 metadata columns vào staging.
- Upload `jsonl` hoặc `csv` trước nếu muốn dễ debug.
- Airflow load vào staging.
- dbt build silver/gold.

### Phase 2: chuẩn hóa production

- Chuyển sang `parquet`.
- Validate checksum.
- Add schema version.
- Add source-app-specific IAM/access key.
- Add dashboard batch status.
- Add alert khi batch failed hoặc stale.

### Phase 3: scale nhiều team

- Mỗi team có prefix riêng.
- Mỗi source app có contract riêng.
- Schema evolution qua `schema_version`.
- Auto quarantine file lỗi.
- SLA per source app.
- Cost/usage tracking theo source app.

## 15. Quy tắc vận hành

Các team/app chỉ cần biết:

```text
1. source_app của mình là gì
2. được gửi vào target_table nào
3. schema file data.parquet gồm những cột nào
4. upload vào path nào
5. manifest.json viết ra sao
```

Platform owner chịu trách nhiệm:

```text
1. registry
2. Airflow validation
3. DWH load
4. dbt build
5. dashboard/alert
6. quyền truy cập MinIO/S3
```

Không cho bất kỳ app nào ghi trực tiếp vào Central DWH. Đây là ranh giới quan trọng để pipeline không bị bẩn, không bị phá schema, và có thể audit được ai gửi batch nào.
