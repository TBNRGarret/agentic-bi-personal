# Agentic BI — Workflow & 10-Day Accelerated Plan

> **Stack**: Airflow 3 · dbt · PostgreSQL (Airflow Meta + DWH) · FastAPI · Next.js · Metabase
> **Source**: MinIO S3 (`47.131.228.222:9000`) · **Bucket**: `telemetry-landing`
> **Data**: OTel traces + Langfuse LLM logs (Parquet/JSONL) từ 4 staging environments
> **Start Date**: ~June 10, 2026 (Wed) · **Demo Day**: ~June 21, 2026 (Sun)

---

## 1. Tổng quan Workflow

```mermaid
flowchart TB
    subgraph User["👤 Người dùng"]
        NL[("Câu hỏi NL\n(ví dụ: 'LLM cost tháng trước?')")]
    end

    subgraph Frontend["🎨 Frontend – Chat UI"]
        UI["Chat Interface\n(Streaming SSE)"]
        DASH["Dashboard Embed\n(Metabase)"]
    end

    subgraph Service["⚙️ Observation Service (Backend Agent)"]
        CHAT["POST /chat"]
        QUERY["POST /query"]
        SCHEMA["GET /schema"]
        AGENT_CORE["🤖 Agent Core\n(OpenAI SDK)"]
        SQL_GEN["NL -> SQL"]
        SAFETY["Self-Correction Loop\n(retry up to 2, timeout)"]
        LOGGER["📝 Request Logger"]
    end

    subgraph Metadata["📚 Metadata API\n(information_schema)"]
        MS_API["GET /api/schema\n(tables, columns, types)"]
    end

    subgraph Source["☁️ Remote Data Source"]
        MINIO[("MinIO S3\n47.131.228.222:9000\nbucket: telemetry-landing")]
        ARCHIVE[("🗄️ Archive\narchive/legacy/")]
    end

    subgraph Pipeline["🔁 Data Pipeline (Airflow + dbt)"]
        SENSOR["🕒 S3 Sensor\n(phát hiện file .parquet mới)"]
        INGEST["⬇️ S3 → PostgreSQL\n(PyArrow đọc Parquet)"]
        SILVER[("🥈 Silver Layer\nPostgreSQL staging")]
        GOLD[("🥇 Gold Layer\nPostgreSQL marts")]
        DQ["✅ Data Quality\n(freshness, completeness)"]
        AIRFLOW_DAG["Airflow hourly DAG"]
        DBT["dbt transform"]
    end

    subgraph Output["📊 Agent Output"]
        TABLE[("📋 Table")]
        CHART[("📈 Chart\nbar/line/pie")]
    end

    %% Source flow
    MINIO --> SENSOR
    ARCHIVE --> SENSOR
    SENSOR --> INGEST
    INGEST -->|raw.llm_generations| SILVER
    INGEST -->|raw.llm_traces| SILVER
    INGEST -->|raw.system_logs| SILVER
    INGEST -->|raw.system_traces| SILVER
    INGEST -->|raw.system_metrics| SILVER
    SILVER -->|dbt build| GOLD
    GOLD --> DQ
    AIRFLOW_DAG --> SENSOR
    AIRFLOW_DAG --> INGEST
    AIRFLOW_DAG --> DBT

    %% Metadata
    GOLD -.->|schema sync| MS_API

    %% Agent flow
    NL -->|HTTP /chat| CHAT
    CHAT --> AGENT_CORE
    AGENT_CORE -->|1. fetch schema| SCHEMA
    SCHEMA -->|information_schema| MS_API
    MS_API -->|tables, columns| SCHEMA
    SCHEMA -->|schema context| AGENT_CORE
    AGENT_CORE -->|2. generate SQL| SQL_GEN
    SQL_GEN -->|3. execute| GOLD
    GOLD -->|result set| AGENT_CORE
    AGENT_CORE -->|error? retry| SQL_GEN
    AGENT_CORE -->|4. format output| CHAT
    AGENT_CORE -.->|log| LOGGER

    CHAT --> TABLE
    CHAT --> CHART
    CHAT -->|streaming| UI
    UI --> DASH
```

---

## 2. Kiến trúc tổng thể & Data Pipeline

### 2.1 Data Structure trên MinIO

```
telemetry-landing/
├── {OWNER}_staging/                    # AN_staging, BACH_staging, HOANG_staging, TUAN_staging
│   └── {source}/                       # local_dwh, bach_local_dwh, hoang_local_dwh, tuan_local_dwh
│       └── prod/
│           ├── staging_llm_generations/       ← LLM generation records (Langfuse)
│           │   └── {table}_{timestamp}_{id}/
│           │       ├── data.parquet            # 18 columns, ~191 rows/batch
│           │       └── manifest.json           # Batch metadata
│           ├── staging_llm_traces/             ← Trace-level grouping
│           │   └── {table}_{timestamp}_{id}/
│           │       ├── data.parquet
│           │       └── manifest.json
│           ├── staging_system_logs/            ← OTel system logs
│           │   └── ...
│           ├── staging_system_traces/          ← OTel system traces (lớn nhất ~12.7MB)
│           │   └── ...
│           └── staging_system_metrics/         ← (chỉ HOANG có)
│               └── ...
└── archive/
    └── legacy/                                 ← Legacy JSONL files
```

### 2.2 Kiến trúc Local / VPS

```
┌───────────────── REMOTE (47.131.228.222) ─────────────────┐
│  MinIO S3                                                   │
│  └─ bucket: telemetry-landing/                              │
│     ├─ AN_staging/      (14.4 MB - 8 files - có data thật)  │
│     ├─ BACH_staging/    (0.1 MB - 8 files)                  │
│     ├─ HOANG_staging/   (0.1 MB - 10 files)                 │
│     ├─ TUAN_staging/    (0.1 MB - 6 files)                  │
│     └─ archive/legacy/  (15.5 MB - 20 files - JSONL)        │
└─────────────────────────────────────────────────────────────┘
        │  S3 API (port 9000) — access_key: minio_admin
        ▼
┌───────────────── LOCAL / VPS ───────────────────────────────┐
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Airflow (hourly DAG)                                  │  │
│  │  1. S3Sensor → detect .parquet mới trên MinIO          │  │
│  │  2. PythonOperator → PyArrow đọc Parquet → INSERT raw  │  │
│  │  3. BashOperator → dbt build (silver → gold)           │  │
│  │  4. BashOperator → dbt test (data quality)             │  │
│  └────────────────────────────────────────────────────────┘  │
│                        │                                     │
│                        ▼                                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  PostgreSQL DWH (port 5433)                            │  │
│  │  ├─ raw.llm_generations  (191 rows - AN sample)         │  │
│  │  ├─ raw.llm_traces                                      │  │
│  │  ├─ raw.system_logs                                     │  │
│  │  ├─ raw.system_traces  (lớn nhất ~12.7MB AN)            │  │
│  │  ├─ raw.system_metrics                                  │  │
│  │  ├─ silver.llm_generations   (cleaned, typed, JSON parsed)│  │
│  │  ├─ gold.daily_llm_costs     (cost, tokens theo ngày)    │  │
│  │  └─ gold.model_performance   (latency, error rate)       │  │
│  └────────────────────────────────────────────────────────┘  │
│                        │                                     │
│                        ▼                                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Observation Service (FastAPI, port 8000)               │  │
│  │  ├─ GET  /api/schema  → information_schema của gold     │  │
│  │  ├─ POST /api/query   → execute SQL, return JSON       │  │
│  │  └─ POST /api/chat    → NL → SQL → result → chart     │  │
│  └────────────────────────────────────────────────────────┘  │
│                        │                                     │
│                        ▼                                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Frontend (Next.js, port 3000)                         │  │
│  │  ├─ ChatPanel (SSE streaming)                          │  │
│  │  ├─ ChartRenderer (Recharts - bar/line/pie)            │  │
│  │  └─ Metabase iframe (pre-built dashboards)             │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 Schema thực tế — tất cả 5 tables

#### `staging_llm_generations` — LLM generation records (Langfuse)

Owners: **AN** (191 rows), **BACH** (24 rows), **HOANG** (7 rows) — TUAN không có

| Column | AN Type | BACH/HOANG | Ghi chú |
|--------|---------|-------------|---------|
| `id` | string | string | UUID, PK |
| `langfuse_id` | string | string | Langfuse reference |
| `trace_id` | string | string | FK → llm_traces |
| `generation_name` | string | string | Loại generation |
| `model` | string | string | Model name |
| `model_parameters` | string (JSON) | string | Model config |
| `prompt` | string (JSON) | string | Prompt input |
| `completion` | string | string | LLM response |
| `prompt_tokens` | int64 | int64 | Token prompt |
| `completion_tokens` | int64 | int64 | Token completion |
| `total_tokens` | int64 | int64 | Tổng tokens |
| `cost_usd` | double | double | Chi phí USD |
| `latency_ms` | double | double | Latency |
| `status` | string | string | Status |
| `start_time` | timestamptz | timestamptz | Start time |
| `end_time` | timestamptz | timestamptz | End time |
| `metadata` | string (JSON) | string | Extra metadata |
| `ingested_at` | **có** | **không** | ⚠️ Chỉ AN có |

> **⚠️ Schema diff**: `ingested_at` chỉ có ở AN. Các owner khác không có column này.

#### `staging_llm_traces` — Trace-level grouping

Owners: **AN** (188 rows), **BACH** (4), **HOANG** (7), **TUAN** (47)

| Column | AN Type | BACH | HOANG | TUAN | Ghi chú |
|--------|---------|------|-------|------|---------|
| `id` | string | string | string | string | PK |
| `langfuse_id` | string | string | string | string | Langfuse ref |
| `trace_name` | string | string | string | string | Tên trace |
| `user_id` | string | string | string | string | User |
| `session_id` | string | string | string | string | Session |
| `input` | string (JSON) | string | string | string | Input |
| `output` | string | string | string | string | Output |
| `metadata` | string (JSON) | string | string | string | Metadata |
| `tags` | string | string | string | string | Tags |
| `start_time` | timestamptz | timestamptz | timestamptz | timestamptz | Start |
| `end_time` | timestamptz | timestamptz | timestamptz | timestamptz | End |
| `latency_ms` | **int64** | **None** | **int64** | **int64** | Latency |
| `total_cost_usd` | double | **None** | double | double | Cost |
| `ingested_at` | **có** | **không** | **không** | **không** | ⚠️ Chỉ AN |

> **⚠️ Schema diff**: `ingested_at` chỉ AN có. BACH thiếu `latency_ms` và `total_cost_usd`.

#### `staging_system_logs` — OTel system logs

Owners: **AN** (23,651 rows — lớn), **BACH** (65), **HOANG** (20), **TUAN** (620)

| Column | AN Type | BACH | HOANG | TUAN | Ghi chú |
|--------|---------|------|-------|------|---------|
| `id` | string | string | string | string | PK |
| `timestamp` | timestamptz | timestamptz | timestamptz | timestamptz | Log time |
| `severity_text` | string | string | string | string | INFO, ERROR... |
| `severity_number` | int64 | int64 | int64 | int64 | Severity code |
| `body` | string | string | string | string | Log message |
| `service_name` | string | string | string | string | Service |
| `trace_id` | string | string | string | string | Trace ref |
| `span_id` | string | string | string | string | Span ref |
| `attributes` | string (JSON) | string | string | string | Attributes |
| `resource_attrs` | string (JSON) | string | string | string | Resource |
| `ingested_at` | **có** | **không** | **không** | **không** | ⚠️ Chỉ AN |

#### `staging_system_traces` — OTel distributed traces

Owners: **AN** (175,823 rows — **13MB!**), **BACH** (63), **HOANG** (30), **TUAN** (251)

| Column | AN Type | BACH | HOANG | TUAN | Ghi chú |
|--------|---------|------|-------|------|---------|
| `id` | string | string | string | string | PK |
| `trace_id` | string | string | string | string | Trace ID |
| `span_id` | string | string | string | string | Span ID |
| `parent_span_id` | string | string | string | string | Parent |
| `service_name` | string | string | string | string | Service |
| `operation_name` | string | string | string | string | Operation |
| `span_kind` | **int64 (1)** | **string (Internal)** | **None** | **None** | ⚠️ Khác type! |
| `status_code` | string | string | string | `OK` | Status |
| `status_message` | string | string | string | string | Message |
| `duration_ns` | int64 | int64 | int64 | int64 | Duration (ns) |
| `start_time` | timestamptz | timestamptz | timestamptz | timestamptz | Start |
| `end_time` | timestamptz | timestamptz | timestamptz | timestamptz | End |
| `attributes` | string (JSON) | string | string | string | Attributes |
| `resource_attrs` | string (JSON) | string | string | string | Resource |
| `ingested_at` | **có** | **không** | **không** | **không** | ⚠️ Chỉ AN |

> **⚠️ Schema diff**: `span_kind` là **int64** ở AN nhưng **string** ở BACH, **None** ở HOANG/TUAN. Cần cast về string chung khi ingest.

#### `staging_system_metrics` — System metrics

Owners: Chỉ **HOANG** (52 rows)

| Column | Type | Ví dụ |
|--------|------|-------|
| `metric_id` | string | `metric-4001` |
| `timestamp` | timestamptz | `2026-06-10 04:23:17+00` |
| `service_name` | string | `hoangvl-ai-chat` |
| `metric_name` | string | `chat.turns` |
| `metric_value` | double | `1.0` |
| `unit` | string | `count` |
| `attributes` | string (JSON) | `{"status":"completed"}` |
| `raw_payload` | string (JSON) | JSON gốc |

#### Tổng quan Matrix — Table × Owner

| Table | AN | BACH | HOANG | TUAN | Total Rows | Total Size |
|-------|----|------|-------|------|-----------|------------|
| `llm_generations` | ✅ 191 | ✅ 24 | ✅ 7 | ❌ | 222 | 0.4 MB |
| `llm_traces` | ✅ 188 | ✅ 4 | ✅ 7 | ✅ 47 | 246 | 0.2 MB |
| `system_logs` | ✅ 23,651 | ✅ 65 | ✅ 20 | ✅ 620 | 24,356 | 1.2 MB |
| `system_traces` | ✅ 175,823 | ✅ 63 | ✅ 30 | ✅ 251 | 176,167 | 12.8 MB |
| `system_metrics` | ❌ | ❌ | ✅ 52 | ❌ | 52 | 0.0 MB |

> **⚠️ Lưu ý quan trọng**: Schema có sự khác biệt giữa các owners:
> 1. `ingested_at` — chỉ AN có ở hầu hết tables
> 2. `span_kind` — AN là int64, BACH là string, HOANG/TUAN là null
> 3. `latency_ms` / `total_cost_usd` — BACH traces thiếu
> 4. **Giải pháp**: Raw tables dùng TEXT cho mọi column, casting ở silver layer

### 2.4 Luồng xử lý dữ liệu

```
MinIO (Parquet) 
    │ Airflow S3Sensor (phát hiện file .parquet mới mỗi giờ)
    ▼
PythonOperator (dùng PyArrow đọc Parquet, thêm source_owner từ path)
    │ INSERT INTO raw.llm_generations (batch insert, idempotent)
    ▼
PostgreSQL raw schema
    │ dbt build
    ▼
PostgreSQL silver schema (JSON parsed, type cast, enrich)
    │ dbt build
    ▼
PostgreSQL gold schema (aggregations: cost theo ngày, model perf, env comparison)
    │ Agent query (NL → SQL)
    ▼
Kết quả → Table / Chart (Recharts) / Metabase Dashboard
```

---

## 3. Kế hoạch 10 Ngày (Accelerated)

> **Local dev**: Ngày 1–6 · **VPS Deploy**: Ngày 7–8 · **Demo Prep**: Ngày 9–10

---

### Phase 1 — Local Development (Ngày 1–6)

---

#### 🗓️ Ngày 1 — Scaffold project + Local Infrastructure

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Docker Compose chạy được toàn bộ stack local |
| **Trạng thái** | Đã có sẵn `docker-compose.yml` với Postgres DWH + MinIO + Metabase — **cần bổ sung Airflow** |

**Công việc cụ thể**:

1. **Cập nhật `docker-compose.yml`** — thêm Airflow services:
   ```yaml
   postgres-airflow:
     image: postgres:16-alpine
     container_name: agentic-bi-postgres-airflow
     environment:
       POSTGRES_DB: airflow
       POSTGRES_USER: airflow
       POSTGRES_PASSWORD: airflow_password
     ports: ["5432:5432"]
     volumes: [postgres_airflow_data:/var/lib/postgresql/data]
     healthcheck:
       test: ["CMD-SHELL", "pg_isready -U airflow -d airflow"]
       interval: 10s
       timeout: 5s
       retries: 5

   airflow-webserver:
     image: apache/airflow:2.10.0
     container_name: agentic-bi-airflow-webserver
     depends_on: [postgres-airflow]
     environment: &airflow_env
       AIRFLOW__CORE__EXECUTOR: LocalExecutor
       AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow_password@postgres-airflow:5432/airflow
       AIRFLOW__CORE__LOAD_EXAMPLES: "False"
       AIRFLOW__CORE__FERNET_KEY: ""
     ports: ["8080:8080"]
     volumes: [./airflow/dags:/opt/airflow/dags, ./airflow/plugins:/opt/airflow/plugins, ./scripts:/opt/airflow/scripts]
     entrypoint: ["/bin/bash", "-c", "airflow db init && airflow users create --username admin --password admin --firstname Admin --lastname User --role Admin --email admin@example.com && airflow webserver"]
     restart: unless-stopped

   airflow-scheduler:
     image: apache/airflow:2.10.0
     container_name: agentic-bi-airflow-scheduler
     depends_on: [postgres-airflow]
     environment: *airflow_env
     volumes: [./airflow/dags:/opt/airflow/dags, ./airflow/plugins:/opt/airflow/plugins, ./scripts:/opt/airflow/scripts]
     entrypoint: ["airflow", "scheduler"]
     restart: unless-stopped
   ```

2. **Tạo cấu trúc thư mục**:
   ```bash
   mkdir -p infra/{airflow,postgres-airflow,postgres-dwh} \
            airflow/{dags,plugins,logs} \
            data/{raw,external,processed,samples} \
            dbt/{models/{staging,marts},tests,macros} \
            backend/app/{api,agent,services,schemas} \
            frontend/src/{app,components,lib} \
            metadata/{ingestion,contracts,examples} \
            scripts tests/{backend,dbt,e2e} \
            docs/decisions
   ```

3. **Tạo `infra/postgres-dwh/init.sql`**:
   ```sql
   CREATE SCHEMA IF NOT EXISTS raw;
   CREATE SCHEMA IF NOT EXISTS silver;
   CREATE SCHEMA IF NOT EXISTS gold;
   ```

4. **Tạo `Makefile`**:
   ```makefile
   .PHONY: up down reset seed psql-dwh psql-airflow logs

   up:     docker compose up -d
   down:   docker compose down
   reset:  docker compose down -v && docker compose up -d
   inspect: python scripts/inspect_minio_data.py
   logs:   docker compose logs -f
   psql-dwh:   docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh
   psql-airflow: docker exec -it agentic-bi-postgres-airflow psql -U airflow -d airflow
   ```

5. **Tạo `.env`**:
   ```
   # PostgreSQL DWH
   POSTGRES_DWH_DB=central_dwh
   POSTGRES_DWH_USER=dwh_admin
   POSTGRES_DWH_PASSWORD=dwh_admin_password
   POSTGRES_DWH_PORT=5433

   # Airflow metadata DB
   POSTGRES_AIRFLOW_DB=airflow
   POSTGRES_AIRFLOW_USER=airflow
   POSTGRES_AIRFLOW_PASSWORD=airflow_password

   # MinIO (remote - data source)
   MINIO_ENDPOINT=http://47.131.228.222:9000
   MINIO_ACCESS_KEY=minio_admin
   MINIO_SECRET_KEY=minio_admin_password
   MINIO_BUCKET=telemetry-landing

   # OpenAI
   OPENAI_API_KEY=sk-...        # API key cho agent
   ```

6. **Tạo `airflow/dags/smoke_test.py`** — kiểm tra kết nối DWH + Airflow DB + MinIO S3:
   ```python
   from airflow import DAG
   from airflow.operators.python import PythonOperator
   from airflow.providers.amazon.aws.hooks.s3 import S3Hook
   from datetime import datetime

   def check_s3():
       hook = S3Hook(aws_conn_id="minio_s3")
       buckets = hook.list_buckets()
       print(f"✅ MinIO buckets: {buckets}")

   def check_dwh():
       import psycopg2, os
       conn = psycopg2.connect(host="postgres-dwh", dbname="central_dwh",
                               user="dwh_admin", password="dwh_admin_password")
       cur = conn.cursor()
       cur.execute("SELECT 1")
       print(f"✅ DWH connected: {cur.fetchone()}")
       cur.close(); conn.close()

   with DAG(dag_id="smoke_test", start_date=datetime(2026,1,1), catchup=False):
       PythonOperator(task_id="check_s3", python_callable=check_s3)
       PythonOperator(task_id="check_dwh", python_callable=check_dwh)
   ```

7. **Kiểm tra**: `make up` → Airflow UI tại `http://localhost:8080` (admin/admin), DWH tại `localhost:5433`, Metabase tại `http://localhost:3000`.

---

#### 🗓️ Ngày 2 — MinIO S3 Ingestion (Parquet) + dbt Project Init

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Kết nối MinIO remote, đọc Parquet files, insert vào PostgreSQL `.raw` schema; dbt project init + staging models |

**Công việc cụ thể**:

1. **Tạo raw tables trong DWH** — `infra/postgres-dwh/init.sql`:
   ```sql
   CREATE SCHEMA IF NOT EXISTS raw;
   CREATE SCHEMA IF NOT EXISTS silver;
   CREATE SCHEMA IF NOT EXISTS gold;

   -- LLM generations (all columns as TEXT để handle schema diff giữa các owners)
   CREATE TABLE IF NOT EXISTS raw.llm_generations (
       id TEXT PRIMARY KEY,
       langfuse_id TEXT,
       trace_id TEXT,
       generation_name TEXT,
       model TEXT,
       model_parameters TEXT,
       prompt TEXT,
       completion TEXT,
       prompt_tokens TEXT,
       completion_tokens TEXT,
       total_tokens TEXT,
       cost_usd TEXT,
       latency_ms TEXT,
       status TEXT,
       start_time TEXT,
       end_time TEXT,
       metadata TEXT,
       ingested_at TEXT,
       source_owner TEXT,
       _batch_id TEXT,           -- từ manifest.json
       _ingested_at TIMESTAMPTZ DEFAULT NOW()
   );

   -- LLM traces (13-14 columns tuỳ owner)
   CREATE TABLE IF NOT EXISTS raw.llm_traces (
       id TEXT PRIMARY KEY,
       langfuse_id TEXT,
       trace_name TEXT,
       user_id TEXT,
       session_id TEXT,
       input TEXT,
       output TEXT,
       metadata TEXT,
       tags TEXT,
       start_time TEXT,
       end_time TEXT,
       latency_ms TEXT,
       total_cost_usd TEXT,
       ingested_at TEXT,
       source_owner TEXT,
       _batch_id TEXT,
       _ingested_at TIMESTAMPTZ DEFAULT NOW()
   );

   -- System logs (10-11 columns tuỳ owner)
   CREATE TABLE IF NOT EXISTS raw.system_logs (
       id TEXT PRIMARY KEY,
       timestamp TEXT,
       severity_text TEXT,
       severity_number TEXT,
       body TEXT,
       service_name TEXT,
       trace_id TEXT,
       span_id TEXT,
       attributes TEXT,
       resource_attrs TEXT,
       ingested_at TEXT,
       source_owner TEXT,
       _batch_id TEXT,
       _ingested_at TIMESTAMPTZ DEFAULT NOW()
   );

   -- System traces (14-15 columns, span_kind có thể là int hoặc string!)
   CREATE TABLE IF NOT EXISTS raw.system_traces (
       id TEXT PRIMARY KEY,
       trace_id TEXT,
       span_id TEXT,
       parent_span_id TEXT,
       service_name TEXT,
       operation_name TEXT,
       span_kind TEXT,          -- AN: int, BACH: string, HOANG/TUAN: null → unified as TEXT
       status_code TEXT,
       status_message TEXT,
       duration_ns TEXT,
       start_time TEXT,
       end_time TEXT,
       attributes TEXT,
       resource_attrs TEXT,
       ingested_at TEXT,
       source_owner TEXT,
       _batch_id TEXT,
       _ingested_at TIMESTAMPTZ DEFAULT NOW()
   );

   -- System metrics (chỉ HOANG có)
   CREATE TABLE IF NOT EXISTS raw.system_metrics (
       metric_id TEXT PRIMARY KEY,
       timestamp TEXT,
       service_name TEXT,
       metric_name TEXT,
       metric_value TEXT,
       unit TEXT,
       attributes TEXT,
       raw_payload TEXT,
       source_owner TEXT,
       _batch_id TEXT,
       _ingested_at TIMESTAMPTZ DEFAULT NOW()
   );
   ```

2. **Cấu hình MinIO S3 connection trong Airflow**:
   ```bash
   # Thêm vào airflow-webserver environment trong docker-compose.yml
   AIRFLOW_CONN_MINIO_S3: '{
     "conn_type": "aws",
     "login": "minio_admin",
     "password": "minio_admin_password",
     "extra": {
       "endpoint_url": "http://47.131.228.222:9000",
       "region_name": "us-east-1"
     }
   }'
   ```

3. **Cài đặt thêm packages** — thêm vào `infra/airflow/requirements.txt`:
   ```
   apache-airflow-providers-amazon
   boto3
   pyarrow
   smart-open
   ```

4. **Viết DAG ingestion từ MinIO** `airflow/dags/ingest_minio_dag.py`:
   ```python
   """
   Airflow DAG: Ingest Parquet từ MinIO S3 vào PostgreSQL DWH.
   Xử lý tất cả 5 tables × 4 owners. Dùng TEXT cho mọi column do schema diff.
   Schedule: @hourly.
   """
   from airflow import DAG
   from airflow.operators.python import PythonOperator
   from airflow.providers.amazon.aws.hooks.s3 import S3Hook
   from datetime import datetime, timedelta
   import pyarrow.parquet as pq
   import pyarrow as pa
   import io, json, hashlib
   import psycopg2
   import os

   S3_BUCKET = "telemetry-landing"
   # Mọi owner prefix
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

   def ingest_new_files(**context):
       hook = S3Hook(aws_conn_id="minio_s3")
       conn = psycopg2.connect(
           host=os.getenv("POSTGRES_HOST", "postgres-dwh"),
           port=os.getenv("POSTGRES_PORT", "5432"),
           dbname=os.getenv("POSTGRES_DB", "central_dwh"),
           user=os.getenv("POSTGRES_USER", "dwh_admin"),
           password=os.getenv("POSTGRES_PASSWORD", "dwh_admin_password"),
       )
       cur = conn.cursor()
       total = 0

       for prefix in SOURCE_PREFIXES:
           owner = prefix.split("_")[0]  # AN, BACH, HOANG, TUAN
           keys = hook.list_keys(Bucket=S3_BUCKET, Prefix=prefix)
           parquet_files = [k for k in keys if k.endswith("data.parquet")]

           for s3_key in parquet_files:
               parts = s3_key.split("/")
               folder_name = parts[3]  # staging_llm_generations
               raw_table = TABLE_MAP.get(folder_name)
               if not raw_table:
                   continue

               # Đọc manifest.json để lấy batch_id (dùng để tránh re-ingest)
               manifest_key = s3_key.replace("data.parquet", "manifest.json")
               try:
                   obj = hook.get_key(manifest_key, S3_BUCKET)
                   manifest = json.loads(obj.get()["Body"].read())
                   batch_id = manifest["batch_id"]
               except:
                   batch_id = parts[4]  # fallback: dùng folder name

               # Kiểm tra batch này đã ingest chưa (idempotent)
               cur.execute(
                   f"SELECT COUNT(*) FROM raw.{raw_table} WHERE _batch_id = %s",
                   (batch_id,)
               )
               if cur.fetchone()[0] > 0:
                   print(f"⏭️  Already ingested: {s3_key}")
                   continue

               # Đọc Parquet
               obj = hook.get_key(s3_key, S3_BUCKET)
               data = obj.get()["Body"].read()
               table = pq.read_table(io.BytesIO(data))

               # Thêm source_owner + _batch_id
               owner_col = pa.array([owner] * table.num_rows, type=pa.string())
               batch_col = pa.array([batch_id] * table.num_rows, type=pa.string())
               table = table.append_column("source_owner", owner_col)
               table = table.append_column("_batch_id", batch_col)

               # Convert tất cả sang string để tránh schema diff
               new_columns = []
               for col_name in table.column_names:
                   col = table.column(col_name)
                   # Chỉ convert numeric/timestamp columns → string
                   if pa.types.is_integer(col.type) or pa.types.is_floating(col.type) or pa.types.is_timestamp(col.type):
                       str_arr = pa.array([str(v) if v is not None else None for v in col], type=pa.string())
                       new_columns.append(str_arr)
                   else:
                       new_columns.append(col)
               table = pa.table(dict(zip(table.column_names, new_columns)))

               # Batch insert
               columns = table.column_names
               placeholders = ", ".join(["%s"] * len(columns))
               cols_sql = ", ".join(columns)
               sql = f"INSERT INTO raw.{raw_table} ({cols_sql}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"

               for i in range(table.num_rows):
                   row = [table.column(j)[i].as_py() for j in range(len(columns))]
                   row = [None if v is None or (isinstance(v, float) and str(v) == "nan") else v for v in row]
                   try:
                       cur.execute(sql, row)
                   except Exception as e:
                       print(f"⚠️  Error row {i} from {s3_key}: {e}")
                       continue

               conn.commit()
               total += table.num_rows
               print(f"✅ {s3_key}: {table.num_rows} rows → raw.{raw_table}")

       cur.close()
       conn.close()
       print(f"🎉 Total inserted: {total} rows")

   with DAG(
       dag_id="ingest_minio_to_dwh",
       schedule="@hourly",
       start_date=datetime(2026, 6, 10),
       catchup=False,
       default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
   ) as dag:
       ingest = PythonOperator(
           task_id="ingest_all_parquet_files",
           python_callable=ingest_new_files,
       )
   ```

5. **Cấu hình Airflow Connection**: Vào Airflow UI → Admin → Connections → Add:
   - Conn Id: `minio_s3`
   - Conn Type: `Amazon Web Services`
   - Login: `minio_admin`
   - Password: `minio_admin_password`
   - Extra: `{"endpoint_url": "http://47.131.228.222:9000", "region_name": "us-east-1"}`

6. **Init dbt project**:
   ```bash
   cd dbt && pip install dbt-postgres && dbt init agentic_bi --adapter postgres
   ```

7. **Tạo `dbt/profiles.yml`**:
   ```yaml
   agentic_bi:
     outputs:
       dev:
         type: postgres
         threads: 4
         host: localhost
         port: 5433
         user: dwh_admin
         password: dwh_admin_password
         dbname: central_dwh
         schema: silver
     target: dev
   ```

8. **Tạo `dbt/models/sources.yml`**:
   ```yaml
   version: 2
   sources:
     - name: raw
       database: central_dwh
       schema: raw
       tables:
         - name: llm_generations
           description: "LLM generation records từ Langfuse (AN:191, BACH:24, HOANG:7 rows)"
         - name: llm_traces
           description: "Trace-level grouping (AN:188, BACH:4, HOANG:7, TUAN:47 rows)"
         - name: system_logs
           description: "OTel system logs (AN:23651, BACH:65, HOANG:20, TUAN:620 rows)"
         - name: system_traces
           description: "OTel distributed traces (AN:175823, BACH:63, HOANG:30, TUAN:251 rows)"
         - name: system_metrics
           description: "System metrics (HOANG:52 rows)"
   ```

9. **Viết staging models cho tất cả tables**:

   `dbt/models/staging/stg_llm_generations.sql`:
   ```sql
   SELECT
       id,
       langfuse_id,
       trace_id,
       generation_name,
       model,
       model_parameters,
       prompt,
       completion,
       prompt_tokens::bigint AS prompt_tokens,
       completion_tokens::bigint AS completion_tokens,
       total_tokens::bigint AS total_tokens,
       cost_usd::numeric(12,6) AS cost_usd,
       latency_ms::numeric(12,2) AS latency_ms,
       status,
       start_time::timestamptz AS start_time,
       end_time::timestamptz AS end_time,
       metadata,
       source_owner,
       _batch_id,
       _ingested_at
   FROM {{ source('raw', 'llm_generations') }}
   WHERE id IS NOT NULL
   ```

   `dbt/models/staging/stg_system_traces.sql`:
   ```sql
   SELECT
       id,
       trace_id,
       span_id,
       parent_span_id,
       service_name,
       operation_name,
       span_kind,                    -- unified as TEXT (AN: "1", BACH: "Internal")
       status_code,
       status_message,
       duration_ns::bigint AS duration_ns,
       duration_ns::numeric / 1e6 AS duration_ms,  -- nanosecond → millisecond
       start_time::timestamptz AS start_time,
       end_time::timestamptz AS end_time,
       attributes,
       resource_attrs,
       source_owner,
       _batch_id,
       _ingested_at
   FROM {{ source('raw', 'system_traces') }}
   WHERE id IS NOT NULL
   ```

---

#### 🗓️ Ngày 3 — dbt Gold Models (LLM Analytics) + Pipeline Orchestration

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | dbt build hoàn chỉnh raw → silver → gold với LLM analytics; Airflow orchestrate dbt |

**Công việc cụ thể**:

1. **Viết gold model** `dbt/models/marts/daily_llm_costs.sql`:
   ```sql
   -- Daily LLM cost và usage aggregated theo model và source_owner
   SELECT
       DATE(start_time) AS date,
       source_owner,
       model,
       COUNT(*) AS total_requests,
       COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error') AS error_count,
       ROUND(
           COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error')::numeric
           / NULLIF(COUNT(*), 0) * 100, 2
       ) AS error_rate_pct,
       SUM(prompt_tokens) AS total_prompt_tokens,
       SUM(completion_tokens) AS total_completion_tokens,
       SUM(total_tokens) AS total_tokens,
       ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd,
       ROUND(AVG(latency_ms)::numeric, 2) AS avg_latency_ms,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p50_latency_ms,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p95_latency_ms
   FROM {{ ref('stg_llm_generations') }}
   GROUP BY DATE(start_time), source_owner, model
   ORDER BY date DESC, total_cost_usd DESC
   ```

2. **Viết gold model** `dbt/models/marts/model_performance.sql`:
   ```sql
   -- Model performance metrics (latency, error rate, cost efficiency)
   SELECT
       model,
       source_owner,
       COUNT(*) AS total_calls,
       ROUND(AVG(latency_ms)::numeric, 2) AS avg_latency_ms,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p50_latency_ms,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p95_latency_ms,
       ROUND(AVG(total_tokens)::numeric, 0) AS avg_tokens_per_call,
       ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd,
       ROUND((SUM(cost_usd) / NULLIF(SUM(total_tokens), 0))::numeric, 6) AS cost_per_1k_tokens,
       COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error') AS error_count,
       ROUND(
           COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error')::numeric
           / NULLIF(COUNT(*), 0) * 100, 2
       ) AS error_rate_pct
   FROM {{ ref('stg_llm_generations') }}
   GROUP BY model, source_owner
   ORDER BY total_cost_usd DESC
   ```

3. **Viết gold model** `dbt/models/marts/owner_comparison.sql`:
   ```sql
   -- So sánh usage giữa các environment (AN, BACH, HOANG, TUAN)
   SELECT
       source_owner,
       DATE(MIN(start_time)) AS first_activity,
       DATE(MAX(start_time)) AS last_activity,
       COUNT(DISTINCT model) AS unique_models_used,
       COUNT(*) AS total_requests,
       SUM(total_tokens) AS total_tokens,
       ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd
   FROM {{ ref('stg_llm_generations') }}
   GROUP BY source_owner
   ORDER BY total_cost_usd DESC
   ```

4. **Tạo `dbt/models/schema.yml`** với tests:
   ```yaml
   version: 2

   models:
     - name: stg_llm_generations
       description: "Cleaned LLM generation records từ all owners"
       columns:
         - name: id
           tests: [unique, not_null]

     - name: stg_system_traces
       description: "Cleaned OTel system traces (175K+ rows từ AN)"
       columns:
         - name: id
           tests: [unique, not_null]

     - name: daily_llm_costs
       description: "Daily LLM cost and usage by model and owner"
       columns:
         - name: date
           tests: [not_null]
         - name: total_cost_usd
           tests: [not_null]

     - name: model_performance
       description: "Model performance metrics (latency, error rate)"
       columns:
         - name: model
           tests: [not_null]

     - name: owner_comparison
       description: "Environment usage comparison across AN, BACH, HOANG, TUAN"
       columns:
         - name: source_owner
           tests: [unique, not_null]

     - name: trace_summary
       description: "System trace summary per service"
       columns:
         - name: service_name
           tests: [not_null]
   ```

5. **Viết thêm gold model cho system traces** `dbt/models/marts/trace_summary.sql`:
   ```sql
   -- System trace summary per service (tận dụng 175K+ rows từ AN)
   SELECT
       service_name,
       source_owner,
       COUNT(DISTINCT trace_id) AS total_traces,
       COUNT(*) AS total_spans,
       COUNT(*) FILTER (WHERE status_code = 'Error' OR status_code = '') AS error_spans,
       ROUND(
           COUNT(*) FILTER (WHERE status_code = 'Error' OR status_code = '')::numeric
           / NULLIF(COUNT(*), 0) * 100, 2
       ) AS error_rate_pct,
       ROUND(AVG(duration_ns::numeric / 1e6), 2) AS avg_duration_ms,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ns::numeric / 1e6)::numeric, 2) AS p50_duration_ms,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ns::numeric / 1e6)::numeric, 2) AS p95_duration_ms,
       COUNT(DISTINCT operation_name) AS unique_operations
   FROM {{ ref('stg_system_traces') }}
   GROUP BY service_name, source_owner
   ORDER BY total_spans DESC
   ```

6. **Viết thêm gold model** `dbt/models/marts/service_logs_summary.sql`:
   ```sql
   -- System logs summary per service
   SELECT
       service_name,
       source_owner,
       DATE(timestamp::timestamptz) AS date,
       COUNT(*) AS total_logs,
       COUNT(*) FILTER (WHERE severity_text = 'ERROR' OR severity_text = 'FATAL') AS error_logs,
       COUNT(DISTINCT trace_id) AS distinct_traces
   FROM {{ source('raw', 'system_logs') }}
   GROUP BY service_name, source_owner, DATE(timestamp::timestamptz)
   ORDER BY date DESC, total_logs DESC
   ```

5. **Viết Airflow DAG orchestrate dbt** `airflow/dags/dbt_pipeline_dag.py`:
   ```python
   from airflow import DAG
   from airflow.operators.bash import BashOperator
   from datetime import datetime, timedelta

   DB_PROJECT_DIR = "/opt/airflow/dbt"

   with DAG(
       dag_id="dbt_pipeline",
       schedule="@hourly",
       start_date=datetime(2026, 6, 10),
       catchup=False,
       default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
   ) as dag:
       dbt_build = BashOperator(
           task_id="dbt_build",
           bash_command=f"cd {DB_PROJECT_DIR} && dbt build --profiles-dir .",
       )
       dbt_test = BashOperator(
           task_id="dbt_test",
           bash_command=f"cd {DB_PROJECT_DIR} && dbt test --profiles-dir .",
       )
       dbt_build >> dbt_test
   ```

   > **Lưu ý**: Cần mount `dbt/` vào container Airflow (thêm volume `./dbt:/opt/airflow/dbt`).

6. **Viết combined DAG** `airflow/dags/llm_pipeline_dag.py` (kết hợp ingestion + dbt):
   ```python
   from airflow import DAG
   from airflow.operators.python import PythonOperator
   from airflow.operators.bash import BashOperator
   from datetime import datetime, timedelta

   DB_PROJECT_DIR = "/opt/airflow/dbt"

   with DAG(
       dag_id="llm_pipeline",
       schedule="@hourly",
       start_date=datetime(2026, 6, 10),
       catchup=False,
       description="End-to-end: MinIO → raw → silver → gold → DQ",
   ) as dag:
       ingest = PythonOperator(
           task_id="ingest_from_minio",
           python_callable=lambda: print("Ingesting..."),  # placeholder
       )
       dbt_build = BashOperator(
           task_id="dbt_build",
           bash_command=f"cd {DB_PROJECT_DIR} && dbt build --profiles-dir .",
       )
       dbt_test = BashOperator(
           task_id="dbt_test",
           bash_command=f"cd {DB_PROJECT_DIR} && dbt test --profiles-dir .",
       )
       ingest >> dbt_build >> dbt_test
   ```

7. **Kiểm tra**: `dbt build` thành công, gold tables queryable trong DWH. Trigger DAG "llm_pipeline" để test end-to-end.

---

#### 🗓️ Ngày 4 — Backend Agent API (FastAPI)

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | FastAPI service với 3 endpoints: `/chat`, `/query`, `/schema` |

**Công việc cụ thể**:

1. **Tạo `backend/requirements.txt`**:
   ```
   fastapi==0.115.0
   uvicorn==0.30.0
   psycopg2-binary==2.9.9
   openai==1.50.0
   pydantic==2.9.0
   python-dotenv==1.0.1
   sqlparse==0.5.1
   ```

2. **Tạo `backend/app/main.py`**:
   ```python
   from fastapi import FastAPI
   from fastapi.middleware.cors import CORSMiddleware
   from app.api import chat, query, schema

   app = FastAPI(title="Agentic BI - Observation Service")
   app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
   app.include_router(chat.router)
   app.include_router(query.router)
   app.include_router(schema.router)
   ```

3. **Tạo `backend/app/api/schema.py`**:
   ```python
   from fastapi import APIRouter
   import psycopg2
   import os

   router = APIRouter(prefix="/api")

   @router.get("/schema")
   def get_schema():
       """Trả về danh sách tables + columns từ DWH (gold schema)."""
       conn = psycopg2.connect(
           host=os.getenv("POSTGRES_HOST", "localhost"),
           port=os.getenv("POSTGRES_PORT", "5433"),
           dbname=os.getenv("POSTGRES_DB", "central_dwh"),
           user=os.getenv("POSTGRES_USER", "dwh_admin"),
           password=os.getenv("POSTGRES_PASSWORD", "dwh_admin_password"),
       )
       cur = conn.cursor()
       cur.execute("""
           SELECT table_name, column_name, data_type
           FROM information_schema.columns
           WHERE table_schema = 'gold'
           ORDER BY table_name, ordinal_position
       """)
       rows = cur.fetchall()
       tables = {}
       for table, col, dtype in rows:
           tables.setdefault(table, []).append({"name": col, "type": dtype})
       return {"tables": tables}
   ```

4. **Tạo `backend/app/api/query.py`** — nhận SQL, execute, trả về JSON.

5. **Tạo `backend/app/api/chat.py`**:
   ```python
   from fastapi import APIRouter, HTTPException
   from pydantic import BaseModel
   from openai import OpenAI
   import json, psycopg2, os

   router = APIRouter(prefix="/api")
   client = OpenAI()

   class ChatRequest(BaseModel):
       message: str

   def get_dwh_connection():
       return psycopg2.connect(
           host=os.getenv("POSTGRES_HOST", "localhost"),
           port=os.getenv("POSTGRES_PORT", "5433"),
           dbname=os.getenv("POSTGRES_DB", "central_dwh"),
           user=os.getenv("POSTGRES_USER", "dwh_admin"),
           password=os.getenv("POSTGRES_PASSWORD", "dwh_admin_password"),
       )

   def get_schema():
       """Lấy schema từ DWH gold tables."""
       conn = get_dwh_connection()
       cur = conn.cursor()
       cur.execute("""
           SELECT table_name, column_name, data_type
           FROM information_schema.columns
           WHERE table_schema = 'gold'
           ORDER BY table_name, ordinal_position
       """)
       tables = {}
       for table, col, dtype in cur.fetchall():
           tables.setdefault(table, []).append({"name": col, "type": dtype})
       cur.close(); conn.close()
       return tables

   def execute_sql(sql: str):
       """Execute SQL và trả về columns + rows."""
       conn = get_dwh_connection()
       cur = conn.cursor()
       cur.execute(sql)
       columns = [desc[0] for desc in cur.description]
       rows = cur.fetchall()
       cur.close(); conn.close()
       return columns, rows

   @router.post("/chat")
   def chat(req: ChatRequest):
       try:
           # 1. Fetch schema
           schema = get_schema()
           # 2. Generate SQL via LLM
           prompt = (
               "You are a BI analyst for LLM operations. "
               "Given these PostgreSQL tables in the 'gold' schema:\n"
               f"{json.dumps(schema, indent=2)}\n\n"
               f"Convert this question to PostgreSQL SQL: {req.message}\n"
               "Return ONLY the raw SQL query, no markdown, no explanation."
           )
           resp = client.chat.completions.create(
               model="gpt-4o",
               messages=[{"role": "user", "content": prompt}],
               temperature=0,
           )
           sql = resp.choices[0].message.content.strip()
           # Clean markdown code fences
           sql = sql.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
           # 3. Execute
           columns, rows = execute_sql(sql)
           return {"sql": sql, "columns": columns, "rows": [list(r) for r in rows]}
       except Exception as e:
           raise HTTPException(status_code=500, detail=str(e))
   ```

6. **Tạo `backend/Dockerfile`**:
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```

7. **Thêm backend service vào `docker-compose.yml`**:
   ```yaml
   backend:
     build: ./backend
     container_name: agentic-bi-backend
     ports: ["8000:8000"]
     env_file: .env
     environment:
       POSTGRES_HOST: postgres-dwh
       POSTGRES_PORT: 5432
     depends_on: [postgres-dwh]
     restart: unless-stopped
   ```

8. **Kiểm tra**:
   ```bash
   curl http://localhost:8000/api/schema  # Danh sách gold tables
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "How much did we spend on LLM per day last week?"}'
   ```

---

#### 🗓️ Ngày 5 — Frontend Chat UI + Chart Rendering

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Chat UI với SSE streaming, hiển thị table + chart |

**Công việc cụ thể**:

1. **Scaffold Next.js project**:
   ```bash
   npx create-next-app@latest frontend --typescript --tailwind --app
   ```

2. **Tạo `frontend/src/lib/api.ts`**:
   ```typescript
   const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

   export async function sendChatMessage(message: string) {
     const res = await fetch(`${API_BASE}/api/chat`, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ message }),
     });
     return res.json();
   }

   export async function fetchSchema() {
     const res = await fetch(`${API_BASE}/api/schema`);
     return res.json();
   }
   ```

3. **Tạo `frontend/src/components/ChatPanel.tsx`**:
   - Text input + send button
   - Message list (user + agent)
   - Gọi API → hiển thị kết quả
   - Loading state

4. **Tạo `frontend/src/components/ResultTable.tsx`** — render kết quả dạng HTML table.

5. **Tạo `frontend/src/components/ChartRenderer.tsx`** (dùng **Recharts**):
   ```tsx
   import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

   export default function ChartRenderer({ columns, rows }: { columns: string[], rows: any[][] }) {
     const data = rows.map(r => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
     return (
       <ResponsiveContainer width="100%" height={300}>
         <BarChart data={data}>
           <XAxis dataKey={columns[0]} />
           <YAxis />
           <Tooltip />
           <Bar dataKey={columns[1]} fill="#8884d8" />
         </BarChart>
       </ResponsiveContainer>
     );
   }
   ```

6. **Cài đặt thư viện**: `npm install recharts`.

7. **Tạo `frontend/Dockerfile`**:
   ```dockerfile
   FROM node:20-alpine AS build
   WORKDIR /app
   COPY package*.json ./
   RUN npm ci
   COPY . .
   RUN npm run build

   FROM node:20-alpine AS run
   WORKDIR /app
   COPY --from=build /app/.next ./.next
   COPY --from=build /app/public ./public
   COPY --from=build /app/package.json ./
   RUN npm ci --production
   CMD ["npm", "start"]
   ```

8. **Kiểm tra**: Frontend tại `http://localhost:3000`, chat thử câu hỏi.

---

#### 🗓️ Ngày 6 — Integration Testing + Polish

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Toàn bộ local stack hoạt động end-to-end, bugfix, hoàn thiện |

**Checklist kiểm tra**:
- [ ] `make up` → tất cả containers đều green
- [ ] Airflow UI `http://localhost:8080` — DAG "llm_pipeline" chạy thành công
- [ ] DWH có dữ liệu trong `.gold` schema (daily_llm_costs, model_performance, owner_comparison)
- [ ] `curl localhost:8000/api/schema` → danh sách gold tables
- [ ] `curl -X POST localhost:8000/api/chat -d '{"message":"LLM cost by model this month"}'` → SQL + kết quả
- [ ] Frontend `http://localhost:3000` — chat gửi tin nhắn → hiển thị table/chart
- [ ] Metabase `http://localhost:3001` — connected to DWH
- [ ] Test ≥5 câu hỏi NL đa dạng:
  - "Total cost per owner"
  - "Which model has highest latency?"
  - "Daily token usage last 7 days"
  - "Error rate by model"
  - "Compare AN vs BACH usage"
- [ ] Test error recovery (SQL sai → tự sửa)

---

### Phase 2 — VPS Deployment (Ngày 7–8)

---

#### 🗓️ Ngày 7 — VPS Setup + Docker Deployment

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Deploy toàn bộ stack lên VPS (Ubuntu 22.04 / 24.04) |

**1. Chuẩn bị VPS (khuyến nghị cấu hình tối thiểu)**:
```
CPU: 2 cores
RAM: 4 GB
Storage: 50 GB SSD
OS: Ubuntu 22.04 LTS
Domain (optional): agentic-bi.yourdomain.com
```

**2. Kết nối và setup VPS**:
```bash
ssh root@<VPS_IP>

# Update system
apt update && apt upgrade -y
apt install -y docker.io docker-compose-v2 git nginx

# Enable Docker
systemctl enable --now docker

# Clone project
mkdir -p /opt/agentic-bi
# Copy project từ local lên (hoặc git clone)
```

**3. Copy project lên VPS**:
```bash
# Option A: Git
git clone https://github.com/your-repo/agentic-bi.git /opt/agentic-bi

# Option B: SCP từ local
cd /home/tuanvan03/Documents/VINAI/VSF_TTS/agentic-bi2/agentic-bi
scp -r .env docker-compose.yml infra/ airflow/ dbt/ backend/ frontend/ metadata/ scripts/ root@<VPS_IP>:/opt/agentic-bi/
```

**4. Cấu hình Docker Compose cho production** — tạo `docker-compose.prod.yml`:
```yaml
# Key changes for production:
# - Thêm restart policies
# - Gắn resource limits
# - Dùng network isolation
# - Dùng volume bền vững

version: "3.8"
services:
  postgres-dwh:
    extends:
      file: docker-compose.yml
      service: postgres-dwh
    restart: always
    deploy:
      resources:
        limits:
          memory: 1G

  # ... tương tự cho các services khác

  nginx:
    image: nginx:alpine
    container_name: agentic-bi-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./infra/nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - airflow-webserver
      - backend
      - frontend
      - metabase
    restart: always
```

**5. Tạo Nginx reverse proxy** `infra/nginx/nginx.conf`:
```nginx
events { worker_connections 1024; }

http {
    upstream backend { server backend:8000; }
    upstream frontend { server frontend:3000; }
    upstream airflow { server airflow-webserver:8080; }
    upstream metabase { server metabase:3000; }

    server {
        listen 80;
        server_name _;

        # Frontend
        location / {
            proxy_pass http://frontend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # Backend API
        location /api/ {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_buffering off;
            proxy_cache off;
        }

        # Airflow
        location /airflow/ {
            proxy_pass http://airflow/;
            proxy_set_header Host $host;
        }

        # Metabase
        location /metabase/ {
            proxy_pass http://metabase;
            proxy_set_header Host $host;
        }
    }
}
```

**6. Tạo deploy script** `scripts/deploy_vps.sh`:
```bash
#!/bin/bash
set -e

echo "🚀 Deploying Agentic BI to VPS..."

cd /opt/agentic-bi

# Pull latest (nếu dùng git)
git pull origin main

# Build images
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# Start services
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Wait for services
echo "⏳ Waiting for services..."
sleep 15

# Init Airflow
docker exec agentic-bi-airflow-webserver airflow db init
docker exec agentic-bi-airflow-webserver airflow users create \
    --username admin --password admin \
    --firstname Admin --lastname User \
    --role Admin --email admin@example.com || true

# Run dbt
docker exec agentic-bi-airflow-webserver bash -c "cd /opt/airflow/dbt && dbt build --profiles-dir ."

echo "✅ Deploy complete!"
echo "📊 Frontend:  http://$(curl -s ifconfig.me)"
echo "🔧 Airflow:   http://$(curl -s ifconfig.me)/airflow"
echo "📈 Metabase:  http://$(curl -s ifconfig.me)/metabase"
echo "⚙️ API:       http://$(curl -s ifconfig.me)/api/schema"
```

**7. Kiểm tra**: `bash scripts/deploy_vps.sh` → tất cả services hoạt động.

---

#### 🗓️ Ngày 8 — VPS Configuration + SSL + Domain

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | HTTPS, domain, firewall, monitoring, tối ưu |

**1. Firewall (UFW)**:
```bash
ufw allow 22/tcp        # SSH
ufw allow 80/tcp        # HTTP
ufw allow 443/tcp       # HTTPS
ufw --force enable
```

**2. SSL với Let's Encrypt (nếu có domain)**:
```bash
apt install -y certbot python3-certbot-nginx

# Nếu có domain: agentic-bi.yourdomain.com
certbot --nginx -d agentic-bi.yourdomain.com

# Auto-renew
echo "0 3 * * * root certbot renew --quiet" >> /etc/crontab
```

**3. Cập nhật Nginx cho SSL** `infra/nginx/nginx.conf`:
```nginx
server {
    listen 443 ssl http2;
    server_name agentic-bi.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/agentic-bi.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agentic-bi.yourdomain.com/privkey.pem;

    # ... proxy configs giống trên
}

server {
    listen 80;
    server_name agentic-bi.yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

**4. Cập nhật `.env` cho production**:
```env
# Thay localhost bằng tên container (Docker networking)
POSTGRES_HOST=postgres-dwh
POSTGRES_PORT=5432

# OpenAI key thật
OPENAI_API_KEY=sk-your-real-key

# Domain
NEXT_PUBLIC_API_URL=https://agentic-bi.yourdomain.com/api
```

**5. Backup script** `scripts/backup_vps.sh`:
```bash
#!/bin/bash
BACKUP_DIR="/backups/agentic-bi"
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d)

# Backup DWH
docker exec agentic-bi-postgres-dwh pg_dump -U dwh_admin central_dwh > $BACKUP_DIR/dwh_$DATE.sql

# Backup Airflow DB
docker exec agentic-bi-postgres-airflow pg_dump -U airflow airflow > $BACKUP_DIR/airflow_$DATE.sql

# Keep last 7 days
find $BACKUP_DIR -name "*.sql" -mtime +7 -delete

echo "✅ Backup completed: $BACKUP_DIR/dwh_$DATE.sql"
```

**6. Monitoring**:
```bash
# Docker health check
docker ps --format "table {{.Names}}\t{{.Status}}"

# Logs
docker compose logs --tail=100 -f

# Resource usage
docker stats --no-stream
```

**7. DNS Records (nếu có domain)**:
```
A record: agentic-bi.yourdomain.com → <VPS_IP>
```

---

### Phase 3 — Demo Preparation (Ngày 9–10)

---

#### 🗓️ Ngày 9 — Demo Rehearsal + Final Polish

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Hệ thống sẵn sàng demo, script demo hoàn chỉnh |

**Demo Checklist**:
- [ ] **Pipeline**: Airflow UI → DAG "llm_pipeline" → MinIO → raw → silver → gold
- [ ] **Metadata**: `GET /api/schema` → gold tables (daily_llm_costs, model_performance, owner_comparison)
- [ ] **Simple query**: "Show LLM cost by model" → table
- [ ] **Aggregation**: "Total cost by owner" → bar chart
- [ ] **Time series**: "Daily token usage last 7 days" → line chart
- [ ] **Comparison**: "Compare AN vs BACH latency" → grouped bar chart
- [ ] **Dashboard**: Metabase → pre-built LLM operations dashboard
- [ ] **Error recovery**: "SELECT typo..." → tự sửa → chạy thành công
- [ ] **VPS**: Truy cập từ browser bên ngoài → hoạt động

**Polish items**:
- [ ] UI: Loading spinner, error messages, responsive
- [ ] API: Rate limiting, timeout handling
- [ ] Docs: Cập nhật README.md

---

#### 🗓️ Ngày 10 — Demo Day 🎉

| Mục | Chi tiết |
|------|---------|
| **Mục tiêu** | Demo thành công cho stakeholders |

**Demo Script (15 phút)**:
```
1. (2 phút) Tổng quan: Kiến trúc MinIO → Airflow → dbt → Agent → Frontend
2. (3 phút) Pipeline: Airflow DAG "llm_pipeline" → MinIO Parquet → gold tables → DQ passes
3. (3 phút) Chat: "LLM cost trend last 7 days" → line chart hiển thị cost theo ngày
4. (3 phút) Comparison: "Which model is most expensive?" → bar chart + table
5. (2 phút) Error recovery: Gõ "show me cost by mdel" (typo) → agent tự sửa → chạy thành công
6. (2 phút) VPS: Truy cập production URL từ browser bên ngoài
```

---

## 4. Dependency Graph

```mermaid
flowchart LR
    D1["Ngày 1: Infra + Docker"] --> D2["Ngày 2: Ingestion + dbt Init"]
    D2 --> D3["Ngày 3: Gold Models + Orchestration"]
    D3 --> D4["Ngày 4: Backend API (FastAPI)"]
    D4 --> D5["Ngày 5: Frontend + Charts"]
    D5 --> D6["Ngày 6: Integration Testing"]
    
    D6 --> D7["Ngày 7: VPS Setup + Deploy"]
    D7 --> D8["Ngày 8: SSL + Domain + Config"]
    D8 --> D9["Ngày 9: Demo Rehearsal"]
    D9 --> D10["Ngày 10: Demo Day 🎉"]
```

---

## 5. Cấu trúc thư mục (Directory Structure)

```
agentic-bi/
├── 📄 docker-compose.yml           # Local development stack
├── 📄 docker-compose.prod.yml      # Production overrides (VPS)
├── 📄 .env                         # Environment variables
├── 📄 Makefile                     # Common commands
├── 📄 README.md                    # Setup guide
│
├── 🏗️ infra/
│   ├── airflow/
│   │   └── requirements.txt
│   ├── nginx/
│   │   ├── nginx.conf              # Reverse proxy config
│   │   └── ssl/                    # SSL certs (gitignored)
│   ├── postgres-airflow/
│   │   └── init.sql
│   └── postgres-dwh/
│       └── init.sql
│
├── 🔁 airflow/
│   ├── dags/
│   │   ├── smoke_test.py           # Health check
│   │   ├── ingest_raw_dag.py       # Raw ingestion
│   │   └── dbt_pipeline_dag.py     # dbt orchestration
│   └── plugins/
│
├── 🗃️ data/
│   ├── raw/                        # Raw data files
│   ├── external/                   # Reference data
│   ├── processed/                  # Intermediate data
│   └── samples/                    # Sample CSVs
│
├── 📊 dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── models/
│   │   ├── sources.yml
│   │   ├── staging/
│   │   │   ├── stg_llm_generations.sql   # Clean + type cast LLM generations
│   │   │   ├── stg_llm_traces.sql         # Trace-level staging
│   │   │   ├── stg_system_logs.sql        # OTel logs staging
│   │   │   ├── stg_system_traces.sql      # OTel traces staging (span_kind unified)
│   │   │   └── stg_system_metrics.sql     # Metrics staging (HOANG only)
│   │   └── marts/
│   │       ├── daily_llm_costs.sql        # Cost/usage theo ngày
│   │       ├── model_performance.sql      # Latency, error rate theo model
│   │       ├── owner_comparison.sql       # So sánh giữa các environment
│   │       ├── trace_summary.sql          # System trace per service
│   │       └── service_logs_summary.sql   # Logs summary per service
│   ├── tests/
│   └── macros/
│
├── ⚙️ backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── chat.py             # POST /api/chat
│   │   │   ├── query.py            # POST /api/query
│   │   │   └── schema.py           # GET /api/schema
│   │   ├── agent/
│   │   │   ├── sql_generator.py    # NL → SQL
│   │   │   └── self_correction.py  # Error recovery
│   │   ├── services/
│   │   │   ├── postgres_client.py
│   │   │   └── logger.py
│   │   └── schemas/
│   │       └── response.py
│   ├── requirements.txt
│   └── Dockerfile
│
├── 🎨 frontend/
│   ├── package.json
│   ├── Dockerfile
│   └── src/
│       ├── app/                    # Next.js App Router
│       ├── components/
│       │   ├── ChatPanel.tsx       # Chat interface
│       │   ├── ResultTable.tsx     # Table view
│       │   ├── ChartRenderer.tsx   # Recharts
│       │   └── DashboardEmbed.tsx  # Metabase iframe
│       └── lib/
│           └── api.ts
│
├── 📚 metadata/
│   ├── ingestion/
│   │   └── ingest_dbt_metadata.py
│   ├── contracts/
│   │   ├── schema_api.md
│   │   └── lineage_api.md
│   └── examples/
│       └── schema_response.json
│
├── 🛠️ scripts/
│   ├── deploy_vps.sh               # Deploy to VPS
│   ├── backup_vps.sh               # Backup databases
│   ├── inspect_minio_data.py       # Inspect MinIO data schema
│   ├── wait-for-it.sh
│   └── reset_local.sh
│
├── 🧪 tests/
│   ├── backend/
│   ├── dbt/
│   └── e2e/
│
└── 📝 docs/
    └── decisions/
```

---

## 6. Hướng dẫn Build Local → Deploy VPS

### 6.1 Build Local

```bash
# 1. Clone & setup
git clone <repo> /path/to/agentic-bi
cd /path/to/agentic-bi

# 2. Environment
cp .env.example .env
# Điền OPENAI_API_KEY vào .env

# 3. Start stack
make up
# hoặc: docker compose up -d

# 4. Airflow init (lần đầu)
docker exec agentic-bi-airflow-webserver airflow db init
docker exec agentic-bi-airflow-webserver airflow users create \
    --username admin --password admin \
    --firstname Admin --lastname User \
    --role Admin --email admin@example.com

# 5. Trigger ingestion từ MinIO
# Airflow UI → DAG "llm_pipeline" → Trigger
# Hoặc chạy manual:
docker exec agentic-bi-airflow-webserver airflow dags trigger llm_pipeline

# 6. Run dbt
docker exec agentic-bi-airflow-webserver bash -c "cd /opt/airflow/dbt && dbt build --profiles-dir ."

# 7. Verify
curl http://localhost:8000/api/schema    # Danh sách gold tables
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Total LLM cost by owner this month"}'

# 8. Frontend
open http://localhost:3000
```

### 6.2 Deploy to VPS

```bash
# ===== TRÊN LOCAL MÁY =====

# 1. Build & push images (hoặc copy code)
docker compose build
docker save agentic-bi-backend agentic-bi-frontend | gzip > images.tar.gz
scp images.tar.gz root@<VPS_IP>:/opt/agentic-bi/

# ===== TRÊN VPS =====

# 2. SSH vào VPS
ssh root@<VPS_IP>

# 3. Install Docker
apt update && apt install -y docker.io docker-compose-v2 nginx
systemctl enable --now docker

# 4. Copy code lên VPS (nếu chưa có)
# Từ local: scp -r * root@<VPS_IP>:/opt/agentic-bi/

# 5. Load images
cd /opt/agentic-bi
docker load < images.tar.gz

# 6. Cấu hình production env
cp .env.example .env
# Sửa .env cho production:
#   - POSTGRES_HOST=postgres-dwh (Docker networking)
#   - OPENAI_API_KEY=sk-...
#   - NEXT_PUBLIC_API_URL=https://<domain>/api

# 7. Start production stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 8. Init Airflow
docker exec agentic-bi-airflow-webserver airflow db init
docker exec agentic-bi-airflow-webserver airflow users create \
    --username admin --password admin \
    --firstname Admin --lastname User \
    --role Admin --email admin@example.com

# 9. Nginx SSL (nếu có domain)
certbot --nginx -d agentic-bi.yourdomain.com

# 10. Seed & run dbt
docker exec agentic-bi-airflow-webserver bash -c "cd /opt/airflow/dbt && dbt build --profiles-dir ."

# 11. Verify
curl http://localhost:8000/api/schema
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Total LLM cost by model this month"}'
```

### 6.3 Quick Reference

| Service | Local URL | Production URL | Credentials |
|---------|-----------|----------------|-------------|
| Frontend | `http://localhost:3000` | `https://<domain>` | — |
| Backend API | `http://localhost:8000/api` | `https://<domain>/api` | — |
| Airflow | `http://localhost:8080` | `https://<domain>/airflow` | admin/admin |
| Metabase | `http://localhost:3000` (cùng frontend) | `https://<domain>/metabase` | admin@admin.com/metabase1 |
| MinIO Console | `http://localhost:9001` | `https://<domain>:9001` | minio_admin/minio_admin_password |
| DWH (Postgres) | `localhost:5433` | Internal only | dwh_admin/dwh_admin_password |
| Airflow DB (Postgres) | `localhost:5432` | Internal only | airflow/airflow_password |

### 6.4 Useful Commands

```bash
# Local
make up          # Start all services
make down        # Stop all services
make reset       # Reset volumes + restart
make logs        # Tail all logs
make psql-dwh    # Query DWH directly

# VPS
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart <service>

# Troubleshooting
docker logs agentic-bi-backend -f
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh
docker exec -it agentic-bi-airflow-webserver bash
```

---

## 7. Tiêu chí hoàn thành (Definition of Done)

> A user types a business question into the chat interface. The Observation Service agent fetches schema from the DWH metadata, generates a PostgreSQL SQL query, self-corrects if needed, and returns a streamed response as a table, chart, or auto-generated dashboard — **all without the user writing any code or SQL**.
> 
> The entire system runs on a VPS, accessible via browser from anywhere with HTTPS.
