# Multi-Source Data Standardization Plan

## 1. Muc tieu

Chuan hoa du lieu cua nhieu nguon nhu `AN`, `BACH`, `HOANG`, `TUAN` ve cung mot data contract truoc khi Airflow load vao Central DWH.

Luong muc tieu:

```text
Nhieu nguon data
  -> MinIO landing zone theo cung convention
  -> Airflow validate tung manifest
  -> load vao Central DWH staging
  -> dbt build silver/gold
  -> dashboard va agent doc gold
```

Nguyen tac:

- Mot batch chi chua du lieu cua mot table.
- Mot batch gom `data.parquet` va `manifest.json`.
- Nguoi gui khong ghi truc tiep vao Central DWH.
- Airflow la thanh phan duy nhat validate va load data.
- Batch cua tung nguon duoc xu ly doc lap.

## 2. Hien trang MinIO

Bucket:

```text
telemetry-landing
```

Hien co cac prefix:

```text
AN_staging
BACH_staging
HOANG_staging
TUAN_stagging
```

Van de:

| Nguon | Format hien tai | Manifest | Cau truc |
|---|---|---|---|
| AN | Parquet | Co | Gan dung chuan |
| BACH | JSONL | Khong | Chua dung chuan |
| HOANG | JSONL | Khong | Chua dung chuan |
| TUAN | JSON | Khong | Sai convention va sai chinh ta `stagging` |

AN dang co hai bo batch cung du lieu:

- Batch cu khong co `ingested_at`.
- Batch moi co `ingested_at`.

Khong duoc load ca hai bo vao DWH vi se nhan doi du lieu.

## 3. Chuan hoa MinIO path

Tat ca nguon phai dung convention:

```text
telemetry-landing/
  {owner}_staging/
    {source_app}/
      {source_env}/
        {target_table}/
          {batch_id}/
            data.parquet
            manifest.json
```

Vi du:

```text
AN_staging/local_dwh/prod/staging_llm_traces/{batch_id}/
BACH_staging/bach_local_dwh/prod/staging_llm_traces/{batch_id}/
HOANG_staging/hoang_local_dwh/prod/staging_llm_traces/{batch_id}/
TUAN_staging/tuan_local_dwh/prod/staging_llm_traces/{batch_id}/
```

Can sua:

```text
TUAN_stagging -> TUAN_staging
```

Khong de file data truc tiep duoi folder table. Moi file phai nam trong mot folder `batch_id`.

## 4. Chuan hoa dinh dang file

Tat ca nguon phai gui:

```text
data.parquet
manifest.json
```

Ke hoach chuyen doi:

| Nguon | Hien tai | Chuan moi |
|---|---|---|
| AN | Parquet + manifest | Giu nguyen batch moi |
| BACH | JSONL | Parquet + manifest |
| HOANG | JSONL | Parquet + manifest |
| TUAN | JSON | Parquet + manifest |

Airflow production chi scan:

```text
**/manifest.json
```

Airflow khong scan truc tiep cac file `.json`, `.jsonl` hoac `.parquet` khong co manifest.

## 5. Chuan hoa batch ID

Format:

```text
{source_app}_{target_table}_{yyyyMMddTHHmmssZ}_{uuid8}
```

Vi du:

```text
bach_local_dwh_staging_system_logs_20260610T084436Z_a91cbe20
```

Quy tac:

- `batch_id` phai unique.
- Khong tai su dung batch ID da load thanh cong.
- Khi retry cung mot batch, giu nguyen `batch_id`.
- Khi export du lieu moi, tao `batch_id` moi.

## 6. Chuan hoa manifest.json

Moi batch bat buoc co manifest:

```json
{
  "batch_id": "bach_local_dwh_staging_llm_traces_20260610T084436Z_a91cbe20",
  "source_app": "bach_local_dwh",
  "source_owner": "BACH",
  "source_env": "prod",
  "target_table": "staging_llm_traces",
  "file_name": "data.parquet",
  "file_format": "parquet",
  "row_count": 1000,
  "schema_version": "v1",
  "exported_at": "2026-06-10T08:44:36Z"
}
```

Field bat buoc:

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

Y nghia timestamp:

```text
exported_at = thoi diem he thong nguon export file
ingested_at = thoi diem Airflow load file vao Central DWH
```

`ingested_at` nen do Airflow server ghi. Khong dung thoi gian do nguoi gui tu khai bao lam thoi gian load DWH.

## 7. Chuan hoa source registry

Dang ky cac nguon:

```text
local_dwh        -> AN
bach_local_dwh   -> BACH
hoang_local_dwh  -> HOANG
tuan_local_dwh   -> TUAN
```

Bang `source_app_registry` can quan ly:

```text
source_app
source_owner
source_env
expected_tables
schema_version
is_active
created_at
updated_at
```

Vi du:

```sql
insert into source_app_registry (
    source_app,
    source_owner,
    source_env,
    expected_tables,
    is_active
)
values (
    'local_dwh',
    'AN',
    'prod',
    array[
        'staging_llm_generations',
        'staging_llm_traces',
        'staging_system_logs',
        'staging_system_traces'
    ],
    true
);
```

Airflow phai tu choi:

- Source app chua dang ky.
- Source bi disable.
- Owner khong khop registry.
- Target table khong nam trong `expected_tables`.
- Source environment khong hop le.

## 8. Chuan hoa schema tung table

Tao data contract rieng:

```text
contracts/
  staging_llm_traces_v1.json
  staging_llm_generations_v1.json
  staging_system_logs_v1.json
  staging_system_traces_v1.json
  staging_system_metrics_v1.json
```

Moi contract quy dinh:

```text
column name
data type
nullable
required/optional
business key
schema version
```

Vi du:

```json
{
  "table": "staging_llm_traces",
  "schema_version": "v1",
  "business_key": ["source_app", "trace_id"],
  "columns": [
    {
      "name": "trace_id",
      "type": "string",
      "nullable": false
    },
    {
      "name": "timestamp",
      "type": "timestamp",
      "nullable": false
    }
  ]
}
```

`staging_system_metrics` cua HOANG chua duoc load den khi:

1. Da tao contract.
2. Da tao staging table tren Central DWH.
3. Da them vao `expected_tables`.
4. Da co dbt silver/gold model neu can.

## 9. Metadata do Airflow bo sung

Moi staging table tren Central DWH phai co:

```text
source_app
source_owner
source_env
batch_id
ingested_at
```

Vi du migration:

```sql
alter table staging_llm_traces
    add column if not exists source_app text,
    add column if not exists source_owner text,
    add column if not exists source_env text,
    add column if not exists batch_id text,
    add column if not exists ingested_at timestamptz;
```

Airflow gan metadata cho tung row:

```text
source_app   = doc tu manifest
source_owner = doc tu manifest va validate voi registry
source_env   = doc tu manifest
batch_id     = doc tu manifest
ingested_at  = thoi diem load thuc te tren server
```

## 10. Chuan hoa du lieu hien co

Viet migration job chay mot lan:

```text
BACH JSONL -> Parquet + manifest
HOANG JSONL -> Parquet + manifest
TUAN JSON  -> Parquet + manifest
```

Migration khong ghi de file goc. Output duoc ghi vao batch folder moi.

Vi du:

```text
BACH_staging/bach_local_dwh/prod/staging_llm_traces/{new_batch_id}/
  data.parquet
  manifest.json
```

File legacy duoc chuyen sang:

```text
telemetry-landing/archive/legacy/
```

Hoac giu nguyen tai vi tri hien tai, nhung DAG production khong duoc scan chung.

Voi AN:

- Chi load bon batch moi co timestamp `20260610T055756Z`.
- Khong load bon batch cu co timestamp `20260610T053843Z`.
- Sau khi xac nhan batch moi hop le, chuyen batch cu vao archive.

## 11. DAG ingestion chuan

DAG:

```text
sync_external_staging_to_dwh
```

Flow:

```text
scan_manifests
  -> validate_path
  -> validate_registry
  -> validate_manifest
  -> validate_parquet_schema
  -> check_duplicate_batch
  -> register_batch
  -> load_staging
  -> verify_row_count
  -> mark_loaded
  -> trigger_dbt
```

### scan_manifests

Scan:

```text
AN_staging/**/manifest.json
BACH_staging/**/manifest.json
HOANG_staging/**/manifest.json
TUAN_staging/**/manifest.json
```

Khong scan:

```text
archive/**
quarantine/**
**/*.jsonl
**/*.json
```

### validate_path

Kiem tra path khop voi manifest:

```text
owner prefix
source_app
source_env
target_table
batch_id
```

### validate_registry

Kiem tra:

```text
source_app ton tai
is_active = true
source_owner khop
source_env khop
target_table duoc phep
```

### validate_manifest

Kiem tra:

```text
du field
file_format = parquet
row_count > 0
schema_version duoc ho tro
data.parquet ton tai
```

### validate_parquet_schema

So sanh:

```text
column names
data types
required columns
nullable rules
schema version
```

### check_duplicate_batch

Kiem tra `raw_ingestion_batches`:

```text
LOADED  -> skip
FAILED  -> cho phep retry
LOADING -> khong chay song song
PENDING -> tiep tuc
```

### load_staging

Load Parquet vao staging table va bo sung:

```text
source_app
source_owner
source_env
batch_id
ingested_at
```

### verify_row_count

So sanh:

```text
manifest.row_count
loaded_row_count
```

Khong khop thi batch `FAILED`.

### trigger_dbt

Chi trigger dbt khi batch staging da load thanh cong.

## 12. Quan ly raw_ingestion_batches

Moi batch co mot dong audit:

```text
batch_id
source_app
source_owner
source_env
target_table
object_path
manifest_path
file_format
declared_row_count
loaded_row_count
status
error_message
submitted_at
started_at
finished_at
```

Trang thai:

```text
PENDING
VALIDATING
LOADING
LOADED
FAILED
SKIPPED
QUARANTINED
```

## 13. Chong trung du lieu

Ba cap kiem soat:

### Batch level

```text
raw_ingestion_batches.batch_id unique
```

### Staging level

Unique theo:

```text
source_app + business_key
```

Vi du:

```text
source_app + trace_id
source_app + generation_id
```

Khong unique chi bang `trace_id`, vi hai app co the sinh cung mot ID.

### Silver level

dbt deduplicate theo:

```text
source_app + business_key
```

Neu co nhieu version:

```text
giu record moi nhat theo event timestamp hoac ingested_at
```

## 14. Quarantine

Batch sai chuan duoc dua vao:

```text
telemetry-landing/quarantine/{source_app}/{batch_id}/
```

Ma loi:

```text
UNKNOWN_SOURCE_APP
INACTIVE_SOURCE_APP
INVALID_PATH
INVALID_MANIFEST
SCHEMA_MISMATCH
ROW_COUNT_MISMATCH
DUPLICATE_BATCH
UNSUPPORTED_TABLE
UNSUPPORTED_SCHEMA_VERSION
```

Mot batch loi khong duoc lam cac batch khac that bai.

## 15. dbt silver/gold

Silver:

- Chuan hoa type.
- Rename columns.
- Loai record khong hop le.
- Deduplicate.
- Giu `source_app`, `batch_id`, `source_owner`, `source_env`, `ingested_at`.

Vi du:

```sql
select
    source_app,
    source_owner,
    source_env,
    batch_id,
    ingested_at,
    trace_id,
    session_id,
    user_id,
    event_timestamp
from {{ source('dwh', 'staging_llm_traces') }}
where source_app is not null
  and batch_id is not null
```

Gold:

- Aggregate theo ngay.
- Aggregate theo source app.
- Tinh token, cost, latency, error rate.
- Tao table phuc vu dashboard va agent.

Vi du:

```sql
select
    date_trunc('day', event_timestamp)::date as day,
    source_app,
    count(*) as total_traces
from {{ ref('silver_llm_traces') }}
group by 1, 2
```

## 16. Dashboard ingestion

Dashboard can hien thi:

```text
batch moi nhat
batch loaded
batch failed
batch quarantined
row count theo source_app
row count theo ngay
schema mismatch
source bi stale
```

Nguon du lieu dashboard:

```text
raw_ingestion_batches
source_app_registry
gold_ingestion_health_daily
```

## 17. Lo trinh trien khai

### Phase 1: Chot contract

- Chot `source_app`.
- Chot owner prefix.
- Chot target tables.
- Chot path convention.
- Chot manifest schema.
- Chot schema version `v1`.

### Phase 2: Migration data hien tai

- Convert BACH JSONL thanh Parquet.
- Convert HOANG JSONL thanh Parquet.
- Convert TUAN JSON thanh Parquet.
- Sinh manifest cho tung table.
- Sua `TUAN_stagging` thanh `TUAN_staging`.
- Archive file legacy.
- Archive batch AN cu.

### Phase 3: Central DWH

- Tao `source_app_registry`.
- Tao `raw_ingestion_batches`.
- Tao/cap nhat staging tables.
- Them metadata columns.
- Them indexes va unique constraints.

### Phase 4: Airflow

- Deploy DAG `sync_external_staging_to_dwh`.
- Test source AN truoc.
- Test BACH.
- Test HOANG.
- Test TUAN.
- Test retry.
- Test duplicate protection.
- Test quarantine.

### Phase 5: dbt

- Cap nhat source definitions.
- Cap nhat silver models.
- Cap nhat gold models.
- Them dbt tests.
- Chay source freshness truoc dbt build.

### Phase 6: Dashboard va alert

- Them ingestion batch dashboard.
- Alert batch failed.
- Alert schema mismatch.
- Alert source stale.
- Alert row count bat thuong.

## 18. Thu tu lam ngay

Thu tu thuc te:

```text
1. Chot contract v1
2. Dang ky bon source app
3. Chuyen BACH/HOANG/TUAN ve Parquet + manifest
4. Archive batch AN cu
5. Tao registry/audit/staging metadata tren DWH server
6. Deploy DAG ingestion
7. Test bon batch AN moi
8. Mo ingestion cho BACH
9. Mo ingestion cho HOANG
10. Mo ingestion cho TUAN
11. Trigger dbt silver/gold
12. Them dashboard va alert
```

Khong nen cho Airflow load toan bucket truoc khi hoan thanh contract va duplicate protection.
