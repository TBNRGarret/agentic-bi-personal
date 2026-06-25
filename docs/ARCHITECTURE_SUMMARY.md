# Agentic BI — Tổng Quan Kiến Trúc

> **Agentic BI** là hệ thống Business Intelligence (BI) đầu cuối, cho phép người dùng đặt câu hỏi bằng tiếng Việt → AI agent truy xuất metadata → sinh SQL → truy vấn kho dữ liệu → trả về bảng, biểu đồ hoặc dashboard tự động.
> **Đã deployed trên VPS** tại IP **47.131.228.222**.

---

## 1. Kiến Trúc Tổng Quan

```
+-------------------------------------------------------------+
|                      Agent (Sprint 2+)                      |
|  Nhận câu hỏi NL → Fetch metadata → Generate SQL → Trả     |
|  kết quả (bảng/biểu đồ/dashboard)                           |
+-------+--------------+--------------+-----------------------+
        |              |              |
        v              v              v
+----------+  +------------+  +--------------+
| DataHub  |  | ClickHouse |  |   Airflow    |
| GraphQL  |  | HTTP API   |  |  REST API    |
| :28080   |  | :18123     |  |  :18080      |
+----------+  +------------+  +--------------+
        ^              ^
        |              |
        +--------------+
         MinIO S3 API :19001
         (dữ liệu thô JSONL)
```

---

## 2. Các Giao Diện Đang Chạy (Production)

| Dịch vụ | URL | Tài khoản | Mật khẩu |
|---------|-----|-----------|----------|
| **DataHub UI** | http://47.131.228.222:9002 | `datahub` | `datahub` |
| **Airflow UI** | http://47.131.228.222:18080 | `admin` | `admin` |
| **MinIO Console** | http://47.131.228.222:19002 | `minioadmin` | `minioadmin` |

---

## 3. API Endpoints

### DataHub GraphQL API (Metadata)
- **Endpoint:** `POST http://47.131.228.222:28080/api/graphql`
- **Công dụng:** Truy xuất schema bảng, columns, lineage, freshness
- **Client Python:** `metadata/client/datahub_client.py` (class `DataHubClient`)

### ClickHouse HTTP API (Dữ liệu)
- **Endpoint:** `GET/POST http://47.131.228.222:18123/?query=...`
- **Auth:** Basic `default:securepassword`
- **Cổng Native:** 19000 (cho clickhouse-client Python)

### MinIO S3 API (Dữ liệu thô)
- **Endpoint:** http://47.131.228.222:19001
- **Bucket:** `agentic-bi-raw`
- **Auth:** AWS Signature V4 (`minioadmin:minioadmin`)

### Airflow REST API (Pipeline)
- **Endpoint:** http://47.131.228.222:18080/api/v2
- **Auth:** Không yêu cầu (Anonymous)

---

## 4. Luồng Dữ Liệu

```
Source Apps (AN, BACH, HOANG, TUAN)
       |
       v
Postgres DWH ---export---> MinIO (JSONL) ---airflow---> ClickHouse vsf_raw
                                                              |
                                                              | dbt build
                                                              v
                                                      ClickHouse vsf_stg
                                                              |
                                                              | dbt build
                                                              v
                                                      ClickHouse vsf_mart
                                                              |
                                              +---------------+---------------+
                                              |               |               |
                                              v               v               v
                                        Observation     Metabase        DataHub
                                        Service         Dashboards      Metadata
                                        (Agent API)                     Store
```

### Pipeline chính: `agentic_bi_pipeline` (chạy hàng ngày)

1. **ingest_minio_to_clickhouse**: Đọc JSONL từ MinIO → nạp vào `vsf_raw.*`
2. **run_dbt_transformations**: `dbt build` biến đổi `vsf_raw` → `vsf_stg` → `vsf_mart`
3. **run_custom_data_quality_checks**: Kiểm tra 8 quy tắc chất lượng, ghi vào `vsf_mart.data_quality_results`

---

## 5. Lược Đồ CSDL

### ClickHouse

| Database | Bảng | Mục đích |
|----------|------|----------|
| `vsf_raw` | `raw_ingestion_batches`, `signoz_logs`, `signoz_traces`, `signoz_metrics`, `langfuse_generations`, `user_interactions` | Dữ liệu thô từ MinIO JSONL |
| `vsf_stg` | `staging_llm_generations`, `stg_langfuse_generations`, `stg_user_interactions`, `stg_signoz_logs` | Tầng silver đã làm sạch |
| `vsf_mart` | `fct_llm_interactions`, `gold_app_daily_cost`, `data_quality_results`, `source_apps` | Tầng gold sẵn sàng business |

### PostgreSQL (`central_dwh`) — đang chưa chạy

| Schema | Bảng | Mục đích |
|--------|------|----------|
| `raw` | `source_app_registry`, `ingestion_batches`, `llm_generations`, `llm_traces`, `system_logs`, `system_traces`, `system_metrics` | Tiếp nhận dữ liệu thô ban đầu |

---

## 6. Các Thành Phần Chính

### Airflow DAGs

| DAG ID | Lịch | Mô tả |
|--------|------|-------|
| `agentic_bi_pipeline` | `*/15 * * * *` (mỗi 15 phút) | Pipeline chính: MinIO → ClickHouse → dbt → Quality Gates |
| `smoke_dag` | Thủ công | Kiểm tra kết nối ClickHouse & MinIO |

### DataHub (Metadata Store)
- **Phiên bản:** v1.4.0
- **Thành phần:** MySQL, OpenSearch, Kafka, GMS (port 28080), Frontend (port 9002), Actions
- **API:** GraphQL tại http://47.131.228.222:28080/api/graphql
- **Nguồn ingestion:** dbt artifacts + ClickHouse physical schema

### dbt Project
- **Vị trí:** `include/dbt_project/`
- **Target:** ClickHouse (`vsf_raw` → `vsf_stg` → `vsf_mart`)
- **Profile:** `dbt_project` (clickhouse adapter)

### Observation Service (Sprint 2)
- **Framework:** FastAPI
- **Endpoints dự kiến:** POST /chat, POST /query, GET /schema, POST /terminal/run
- **Hiện tại:** Chưa được triển khai

---

## 7. Cấu Trúc Thư Mục

```
agentic-bi/
+-- docker-compose.yml              # Dịch vụ chính (Postgres, MinIO, Metabase, Airflow)
+-- docker-compose.override.yml     # Override local (ClickHouse, Observation)
+-- docker-compose-datahub.yml      # Stack DataHub
+-- Makefile                         # Lệnh: up/down/reset/logs/status
+-- dags/                            # Airflow DAGs (Airflow 3)
|   +-- agentic_bi_pipeline.py      # Pipeline chính
|   +-- smoke_dag.py                # Smoke test
+-- include/dbt_project/            # Dự án dbt chính (ClickHouse)
|   +-- models/
|       +-- sources.yml
|       +-- schema.yml
|       +-- silver/                 # Staging models
|       +-- gold/                   # Mart models
+-- metadata/                       # Cấu hình DataHub
|   +-- client/datahub_client.py    # Python client
|   +-- recipes/                    # Ingestion recipes (dbt, ClickHouse)
+-- scripts/                        # Script vận hành
|   +-- seed_dwh.py                 # Seed dữ liệu mẫu
|   +-- export_dwh_to_minio.py      # Export DWH → MinIO JSONL
|   +-- seed_minio.py               # Seed MinIO trực tiếp
|   +-- verify_system.py            # Kiểm tra toàn hệ thống
+-- docs/                           # Tài liệu
+-- backend/                        # Observation Service (FastAPI) - chưa phát triển
+-- frontend/                       # Next.js scaffold - chưa phát triển
+-- dwh/init/                       # SQL khởi tạo Postgres DWH
+-- include/clickhouse/             # SQL khởi tạo ClickHouse
```

---

## 8. Thông Tin Đăng Nhập Chi Tiết

| Dịch vụ | URL | Username | Password | Ghi chú |
|---------|-----|----------|----------|---------|
| **DataHub UI** | http://47.131.228.222:9002 | `datahub` | `datahub` | Giao diện web metadata |
| **DataHub GMS API** | http://47.131.228.222:28080 | — | — | GraphQL endpoint, không auth |
| **Airflow UI** | http://47.131.228.222:18080 | `admin` | `admin` | Dùng port 18080 (không phải 8080) |
| **Airflow API** | http://47.131.228.222:18080/api/v2 | — | — | REST API v2, không auth |
| **MinIO Console** | http://47.131.228.222:19002 | `minioadmin` | `minioadmin` | Dùng port 19002 (không phải 9001) |
| **MinIO S3 API** | http://47.131.228.222:19001 | `minioadmin` | `minioadmin` | S3-compatible, SigV4 |
| **ClickHouse HTTP** | http://47.131.228.222:18123 | `default` | `securepassword` | Basic Auth |

---

## 9. Trạng Thái Dịch Vụ

| Dịch vụ | Port | Trạng thái |
|---------|------|-----------|
| DataHub UI | 9002 | **Đang chạy** |
| DataHub GMS API | 28080 | **Đang chạy** |
| Airflow UI + API | 18080 | **Đang chạy** |
| MinIO Console | 19002 | **Đang chạy** |
| MinIO S3 API | 19001 | **Đang chạy** |
| ClickHouse HTTP | 18123 | **Đang chạy** |
| ClickHouse Native | 19000 | **Đang chạy** (trong VPS) |
| Metabase | 3000 | **Chưa chạy** |
| Observation Service | 18000 | **Chưa chạy** (Sprint 2) |
| Postgres DWH | 5433 | **Chưa chạy** |

---

## 10. Công Nghệ Sử Dụng

| Danh mục | Công nghệ |
|----------|-----------|
| Điều phối | Apache Airflow 3 |
| Biến đổi dữ liệu | dbt (ClickHouse adapter) |
| Kho dữ liệu | ClickHouse 24.8 |
| Lưu trữ đối tượng | MinIO (S3-compatible) |
| Kho Metadata | DataHub v1.4.0 |
| Backend API | FastAPI (Python) — Sprint 2 |
| Frontend | Next.js (React) — Sprint 2 |
| BI Visualization | Metabase (chưa chạy) |
| LLM Telemetry | Langfuse |
| Giám sát | OpenTelemetry / SigNoz |
| Container | Docker Compose trên VPS Ubuntu |
