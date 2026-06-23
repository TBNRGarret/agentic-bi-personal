# Agentic BI — Hướng Dẫn Cài Đặt & Vận Hành

> Dự án Agentic BI đã được triển khai trên VPS tại IP **47.131.228.222**.

---

##  Các Giao Diện Đang Chạy

| Dịch vụ | URL | Tài khoản | Mật khẩu |
|---------|-----|-----------|----------|
| **DataHub UI** | [http://47.131.228.222:9002](http://47.131.228.222:9002) | `datahub` | `datahub` |
| **Airflow UI** | [http://47.131.228.222:18080](http://47.131.228.222:18080) | `admin` | `admin` |
| **MinIO Console** | [http://47.131.228.222:19002](http://47.131.228.222:19002) | `minioadmin` | `minioadmin` |

---

##  API Endpoints (cho Sprint 2+ Agent)

### 1. DataHub GraphQL API — **Truy xuất metadata**

Endpoint chính để agent lấy schema bảng, columns, lineage, freshness.

```
POST http://47.131.228.222:28080/api/graphql
Content-Type: application/json
```

**Ví dụ các query:**

```bash
# Liệt kê datasets trong vsf_mart
curl -s -X POST http://47.131.228.222:28080/api/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"{ search(input: {type: DATASET, query: \"vsf_mart\", count: 20}) { searchResults { entity { urn ... on Dataset { name platform { name } } } } } }"}'
```

```bash
# Lấy schema columns của một bảng
curl -s -X POST http://47.131.228.222:28080/api/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"query($urn:String!){ dataset(urn:$urn) { schemaMetadata { fields { fieldPath type nullable } } } }", "variables":{"urn":"urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)"}}'
```

```bash
# Lấy lineage (nguồn dữ liệu upstream)
curl -s -X POST http://47.131.228.222:28080/api/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"query($urn:String!){ searchAcrossLineage(input:{urn:$urn direction:UPSTREAM count:10}){ searchResults { entity { urn ... on Dataset { name } } } } }", "variables":{"urn":"urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)"}}'
```

> **Client Python có sẵn**: `metadata/client/datahub_client.py` — class `DataHubClient` với các method `list_tables()`, `get_columns()`, `get_lineage()`, `get_freshness()`.

### 2. ClickHouse HTTP API — **Truy vấn dữ liệu**

```
GET/POST http://47.131.228.222:18123/?query=...
Auth: Basic default:securepassword
```

```bash
# Kiểm tra kết nối
curl -s http://47.131.228.222:18123/ping

# Liệt kê database
curl -s "http://47.131.228.222:18123/?query=SHOW+DATABASES" -u default:securepassword

# Query dữ liệu gold layer
curl -s "http://47.131.228.222:18123/?query=SELECT+count()+FROM+vsf_mart.fct_llm_interactions" -u default:securepassword

# Query chi phí LLM hàng ngày
curl -s "http://47.131.228.222:18123/?query=SELECT+date,source_app_id,round(sum(total_cost_usd),4)+as+cost+FROM+vsf_mart.gold_app_daily_cost+GROUP+BY+date,source_app_id+ORDER+BY+date+DESC+LIMIT+10" -u default:securepassword
```

> **ClickHouse Native port**: `19000` (dùng cho `clickhouse-client` hoặc `clickhouse-connect` Python)

### 3. MinIO S3 API — **Truy xuất dữ liệu thô**

```
Endpoint: http://47.131.228.222:19001
Bucket: agentic-bi-raw
```

```bash
# Dùng Python (boto3 / minio)
python3 -c "
from minio import Minio
client = Minio('47.131.228.222:19001', access_key='minioadmin', secret_key='minioadmin', secure=False)
for obj in client.list_objects('agentic-bi-raw', prefix='raw/', recursive=True):
    print(obj.object_name)
"
```

### 4. Airflow REST API — **Kích hoạt pipeline**

```
Base URL: http://47.131.228.222:18080/api/v2
Auth: không yêu cầu (Anonymous access được bật)
```

```bash
# Liệt kê DAGs
curl -s http://47.131.228.222:18080/api/v2/dags

# Trigger pipeline (cần trường logical_date)
curl -s -X POST http://47.131.228.222:18080/api/v2/dags/agentic_bi_pipeline/dagRuns \
  -H "Content-Type: application/json" \
  -d '{"logical_date":"2026-06-18T00:00:00Z"}'

# Kiểm tra trạng thái DAG run gần nhất
curl -s "http://47.131.228.222:18080/api/v2/dags/agentic_bi_pipeline/dagRuns?limit=3"

# Kiểm tra health (không cần auth)
curl -s http://47.131.228.222:18080/api/v2/monitor/health
```

### 5. Observation Service (Sprint 2) — **Agent chat API**

*Chưa chạy, sẽ triển khai ở Sprint 2:*

```
POST http://47.131.228.222:18000/chat
POST http://47.131.228.222:18000/query
GET  http://47.131.228.222:18000/schema
POST http://47.131.228.222:18000/terminal/run
GET  http://47.131.228.222:18000/health
```

---

##  Tổng Hợp Các Dịch Vụ

| Dịch vụ | URL / Port | Trạng thái | Ghi chú |
|---------|-----------|-----------|---------|
| **DataHub UI** | http://47.131.228.222:9002 |  Đang chạy | `datahub` / `datahub` |
| **DataHub GMS API** | http://47.131.228.222:28080 |  Đang chạy | GraphQL endpoint |
| **Airflow UI** | http://47.131.228.222:18080 |  Đang chạy | `admin` / `admin` |
| **MinIO Console** | http://47.131.228.222:19002 |  Đang chạy | `minioadmin` / `minioadmin` |
| **MinIO API (S3)** | http://47.131.228.222:19001 |  Đang chạy | Bucket: `agentic-bi-raw` |
| **ClickHouse HTTP** | http://47.131.228.222:18123 |  Đang chạy | `default` / `securepassword` |
| **ClickHouse Native** | localhost:19000 |  Đang chạy | Chỉ trong VPS |
| **Metabase** | http://47.131.228.222:3000 |  Chưa chạy | Cần khởi động |
| **Observation Service** | http://47.131.228.222:18000 |  Chưa chạy | Sprint 2 |
| **Postgres DWH** | localhost:5433 |  Chưa chạy | Chỉ trong VPS |

---

##  Vận Hành Hàng Ngày

### Kiểm tra sức khỏe hệ thống

```bash
# SSH vào VPS
ssh ubuntu@47.131.228.222

# Kiểm tra container
docker ps
docker compose -p datahub-vps ps

# Kiểm tra health
curl -s http://localhost:28080/health
curl -s http://localhost:18080/api/v2/monitor/health
curl -s http://localhost:18123/ping
curl -s http://localhost:19001/minio/health/live
```

### Kích hoạt pipeline dữ liệu

```bash
# Trigger từ Airflow UI
# http://47.131.228.222:18080 → DAGs → agentic_bi_pipeline → Play

# Hoặc từ CLI (trong VPS)
docker exec agentic-bi-airflow-scheduler airflow dags trigger agentic_bi_pipeline
```

### Kiểm tra dữ liệu trong ClickHouse

```bash
# Số dòng các bảng gold
curl -s "http://47.131.228.222:18123/?query=SELECT+'fct_llm_interactions',count()+FROM+vsf_mart.fct_llm_interactions+UNION+ALL+SELECT+'gold_app_daily_cost',count()+FROM+vsf_mart.gold_app_daily_cost+UNION+ALL+SELECT+'data_quality_results',count()+FROM+vsf_mart.data_quality_results&default_user=default&password=securepassword&format=TabSeparated"
```

---

##  Tham Khảo Nhanh

### SSH vào VPS

```bash
ssh ubuntu@47.131.228.222
cd ~/agentic-bi
source .venv/bin/activate
```

### Xem log

```bash
# DataHub
docker logs datahub-gms --tail 50
docker logs datahub-frontend --tail 50

# Airflow
docker logs agentic-bi-airflow-scheduler --tail 50
docker logs agentic-bi-airflow-webserver --tail 50

# ClickHouse
docker logs agentic_clickhouse --tail 20
```

### Restart service

```bash
docker compose restart airflow-webserver airflow-scheduler
docker compose -p datahub-vps restart datahub-gms datahub-frontend
```

---

##  Kiến Trúc Tổng Quan

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent (Sprint 2+)                      │
│  Nhận câu hỏi NL → Fetch metadata → Generate SQL → Trả kết  │
│  quả (bảng/biểu đồ/dashboard)                               │
└──────┬──────────────┬──────────────┬────────────────────────┘
       │              │              │
       ▼              ▼              ▼
┌──────────┐  ┌────────────┐  ┌──────────────┐
│ DataHub  │  │ ClickHouse │  │   Airflow    │
│ GraphQL  │  │ HTTP API   │  │  REST API    │
│ :28080   │  │ :18123     │  │  :18080      │
└──────────┘  └────────────┘  └──────────────┘
       ▲              ▲
       │              │
       └──────────────┘
         MinIO S3 API :19001
         (dữ liệu thô JSONL)
```

---

##  Lưu Ý

- **DataHub UI** port 9002 — cần mở AWS Security Group nếu chưa truy cập được
- **Airflow** đang dùng port **18080** (không phải 8080) do override mapping
- **MinIO** dùng port **19001/19002** (không phải 9000/9001) do override
- Các dịch vụ **Metabase**, **Observation Service**, **Postgres DWH** chưa được khởi động
