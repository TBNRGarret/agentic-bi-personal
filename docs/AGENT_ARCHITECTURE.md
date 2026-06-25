# Agentic BI — Agent Architecture (High-Level Design)

## 1. Tổng quan hệ thống

Agentic BI là hệ thống **Natural Language to Analytics** cho phép người dùng đặt câu hỏi bằng tiếng Việt và nhận câu trả lời dưới dạng bảng, biểu đồ, hoặc dashboard.

Kiến trúc sử dụng mô hình **Multi-Agent Orchestrator**: một orchestrator trung tâm điều phối nhiều agent chuyên biệt, mỗi agent đảm nhận một nhiệm vụ cụ thể trong pipeline xử lý.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                                  │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Web UI (React) — Chat / Bảng / Biểu đồ / Dashboard             │    │
│  └──────────────────────────┬──────────────────────────────────────┘    │
└─────────────────────────────┼──────────────────────────────────────────┘
                              │ HTTP (REST)
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     API GATEWAY (FastAPI)                               │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐    │
│  │ Chat Router │  │  Sessions   │  │  Dashboard   │  │    Auth     │    │
│  │ /api/query  │  │ /api/session│  │ /api/dashbrd │  │ /api/auth   │    │
│  └──────┬──────┘  └─────────────┘  └──────────────┘  └─────────────┘    │
│  Middleware: JWT Auth · CORS · Rate Limit · OpenTelemetry               │
└─────────┼─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATION LAYER                                  │
│                                                                         │
│  ┌──────────────────┐    ┌────────────────┐    ┌───────────────────┐    │
│  │ Semantic Slot    │───▶│ Agent Planner  │───▶│   Table Selector  │    │
│  │ Extractor        │    │ (LLM / Rules)  │    │ (BM25 + Keyword)  │    │
│  └──────────────────┘    └────────────────┘    └─────────┬─────────┘    │
│                                                          │              │
│  ┌──────────────────────────────────────────────────────┐│              │
│  │           Multi-Agent Orchestrator                  │◀┘              │
│  │  Điều phối · Clarification · Aggregation            │                │
│  └────────────┬──────────────────┬──────────────────────┘               │
└───────────────┼──────────────────┼──────────────────────────────────────┘
                │                  │
                │                  │  METADATA PATH
                │                  │
                │                  │  ┌────────────────────────┐
                │                  │  │  DataHubConnector      │
                │                  │  │                        │
                │                  │  │  ┌──────────────────┐  │
                │                  ├──│  │  DataHub         │  │
                │                  │  │  │  GraphQL API     │  │
                │                  │  │  │  (schema, tags,  │  │
                │                  │  │  │   description)   │  │
                │                  │  │  └────────┬─────────┘  │
                │                  │  │           │ fallback   │
                │                  │  │  ┌────────▼─────────┐  │
                │                  │  │  │  ClickHouse      │  │
                │                  │  │  │  system.columns  │  │
                │                  │  │  └──────────────────┘  │
                │                  │  └────────────────────────┘
                ▼                  │
┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐
│  CHART AGENT    │  │  INSIGHT AGENT  │  │  FORECAST / QA /    │
│  NL2SQL + Chart │  │  Root-cause     │  │  PARTITION Agent    │
└────────┬────────┘  └────────┬────────┘  └──────────┬───────────┘
         │                    │                       │
         └────────────────────┼───────────────────────┘
                              │
                              ▼
          ┌──────────────────────────────────────────┐
          │         LLM SERVICE (SQL Generation)     │
          │  OpenAI / OpenRouter · SQL Correction    │
          └────────────────────┬─────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
          ┌──────────────────┐  ┌────────────────────┐
          │   ClickHouse     │  │    PostgreSQL      │
          │  (Analytics DB)  │  │  (DWH / Ops)       │
          │  vsf_mart        │  │  sessions · auth   │
          │  vsf_stg         │  │  audit · memory    │
          │  vsf_raw         │  └────────────────────┘
          └──────────────────┘
```

---

## 2. Kiến trúc tổng thể

Hệ thống gồm **5 tầng (layers)**:

### 2.1 Client Layer
- **Web UI** (React): giao diện chat, hiển thị bảng/biểu đồ/dashboard
- Giao tiếp với backend qua REST API

### 2.2 API Gateway Layer
- **FastAPI** application
- **Routers**: Chat (`/api/query/chat`), Sessions (`/api/sessions`), Dashboard (`/api/dashboard`), Auth (`/api/auth`), Terminal (`/api/terminal`)
- **Middleware**: JWT authentication, CORS, rate limiting
- **Observability**: OpenTelemetry tracing, JSON structured logging

### 2.3 Orchestration Layer
- **Multi-Agent Orchestrator**: control plane, điều phối toàn bộ flow xử lý
- **Semantic Slot Extractor**: phân tích câu hỏi → trích xuất intent, metric, time_range, domain
- **Agent Planner**: chọn specialist agents phù hợp dựa trên intent (LLM hoặc rule-based)
- **Table Selector**: chọn bảng dữ liệu phù hợp từ metadata (DataHub)
- **Context Manager**: quản lý session history + structured memory

### 2.4 Specialist Agent Layer
| Agent | Nhiệm vụ chính |
|-------|---------------|
| **Chart Agent** | NL2SQL + sinh biểu đồ (line, bar, pie, table, heatmap...) |
| **Insight Agent** | Phân tích root-cause, so sánh, tương quan |
| **Forecast Agent** | Dự báo xu hướng từ historical data |
| **QA Agent** | Kiểm tra data quality, ingestion health, failed batches |
| **Partition Agent** | Phân tích partition strategy & health |
| **Dashboard Generator** | Tạo multi-panel dashboard |
| **Terminal Agent** | Thực thi terminal commands (observation) |

### 2.5 Data Layer
- **ClickHouse**: Dữ liệu analytics (vsf_raw → vsf_stg → vsf_mart). Agent query trực tiếp qua HTTP interface
- **PostgreSQL (DWH)**: Operational data, audit logs, chat sessions, auth users
- **MinIO**: Raw JSONL object store (data lake)
- **DataHub**: Metadata store (schema, lineage, freshness).
  Agent gọi **GraphQL API** (`POST /api/graphql`) — searchDatasets query —
  để lấy danh sách tables, columns, tags, description.
  Không query trực tiếp vào DB để lấy metadata.
  Nếu DataHub offline → **fallback** query trực tiếp `system.columns` của ClickHouse
- **Metabase**: BI dashboard embedding

---

## 3. Modules & Components

### 3.1 Backend Modules

```
┌───────────────────────────────────────────────────────────┐
│  src/backend/app/                                         │
│                                                           │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────┐   │
│  │   routers/     │  │   agents/      │  │ services/  │   │
│  │                │  │                │  │            │   │
│  │  - query       │  │  - multi_agent │  │  - llm     │   │
│  │  - sessions    │  │  - agent_      │  │  - context_│   │
│  │  - dashboard   │  │    planner     │  │    manager │   │
│  │  - auth        │  │  - monitoring_ │  └────────────┘   │
│  │                │  │    agent       │                   │
│  │                │  │  - partition_  │  ┌────────────┐   │
│  │                │  │    agent       │  │connectors/ │   │
│  │                │  │  - dashboard_  │  │            │   │
│  │                │  │    generator   │  │  - clickh. │   │
│  │                │  │  - sql_safety  │  │  - postgres│   │
│  │                │  │  - row_security│  │  - datahub │   │
│  │                │  │  - chart_spec  │  │  - metabase│   │
│  │                │  │  - table_      │  └────────────┘   │
│  │                │  │    selector    │                   │
│  │                │  │  - workflow_   │  ┌────────────┐   │
│  │                │  │    graph       │  │  main.py   │   │
│  │                │  └────────────────┘  │  config.py │   │
│  └────────────────┘                      │ security   │   │
│                                          │ agent.py   │   │
│                                          │ terminal   │   │
│                                          └────────────┘   │
└───────────────────────────────────────────────────────────┘
```

### 3.2 Data Pipeline Modules (Airflow DAGs)

```
┌───────────────────────────────────────────────────────────┐
│  dags/                                                    │
│                                                           │
│  ┌──────────────────────┐  ┌───────────────────────────┐  │
│  │ Data Generation      │  │ Ingestion & Transform     │  │
│  │                      │  │                           │  │
│  │ generate_data_stream │  │ agentic_bi_pipeline.py    │  │
│  │ _dag.py (@hourly)    │  │ agentic_bi_staging        │  │
│  │                      │  │ agentic_bi_mart           │  │
│  └──────────────────────┘  └───────────────────────────┘  │
│                                                           │
│  ┌──────────────────────┐                                 │
│  │ Scripts              │                                 │
│  │                      │                                 │
│  │ generate_data_stream │                                 │
│  │ seed_minio/seed_dwh  │                                 │
│  │ inspect/verify       │                                 │
│  └──────────────────────┘                                 │
└───────────────────────────────────────────────────────────┘
```

---

## 4. Data Flow

### 4.1 User Question Flow

```
Người dùng: "Show P99 latency by service for last 24h"

[1] API Gateway nhận request → xác thực JWT
    │
[2] ContextManager load session history + memory
    │
[3] Orchestrator phân tích câu hỏi:
    ├─ Intent: chart
    ├─ Metric: latency (P99)
    ├─ Time range: 24h
    └─ Dimension: service
    │
[4] Agent Planner → chọn Chart Agent
    │
[5] DataHubConnector gọi DataHub GraphQL API:
    ├─ POST /api/graphql — query searchDatasets
    ├─ Lấy danh sách tables + columns + tags
    ├─ Merge với live schema từ ClickHouse system.columns
    └─ Cache kết quả (30s cho DataHub, 10s cho fallback)
    │
[6] Table Selector nhận merged metadata → rank bằng BM25 + keyword:
    ├─ Ưu tiên gold layer (vsf_mart)
    ├─ Match tên bảng/cột với câu hỏi
    └─ Chọn primary table: fct_llm_interactions
    │
[7] LLM Service sinh SQL từ schema + câu hỏi
    │
[8] ClickHouse thực thi truy vấn → trả về rows
    │
[9] Chart Spec inference → line chart (time-series data)
    │
[10] Summarizer tổng hợp → Response (answer + chart + rows + sql)

Metadata fallback: Nếu DataHub không khả dụng → DataHubConnector
query trực tiếp ClickHouse system.columns, cache 10s.
```

### 4.2 Data Pipeline Flow

```
                    Hourly
                       │
           ┌───────────▼───────────┐
           │  generate_data_stream │
           │  (@hourly)            │
           │  Sinh dữ liệu giả     │
           └───────────┬───────────┘
                       │
                       ▼
              ┌────────────────┐
              │  MinIO (JSONL) │
              │  raw/.../*.jsonl│
              └────────┬───────┘
                       │
          ┌────────────▼────────────┐
          │  agentic_bi_pipeline    │
          │  (Daily 00:00)          │
          │  MinIO → vsf_raw        │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │  agentic_bi_staging     │
          │  (triggered)            │
          │  vsf_raw → vsf_stg      │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │  agentic_bi_mart        │
          │  (triggered)            │
          │  vsf_stg → vsf_mart     │
          │  + Data Quality Checks  │
          └─────────────────────────┘
```

---

## 5. Các Agent và nhiệm vụ

### 5.1 Multi-Agent Orchestrator (Control Plane)
- Nhận câu hỏi → trích xuất semantic slots → lập kế hoạch → điều phối specialist agents
- Xử lý clarification khi thiếu thông tin
- Gom kết quả từ nhiều agents → tổng hợp response

### 5.2 Chart Agent
- Chuyển đổi câu hỏi tự nhiên → SQL → biểu đồ
- Tự động suy luận kiểu biểu đồ phù hợp (line, bar, pie, table, heatmap...)
- Xử lý các truy vấn: lookup, time-series, top N, thống kê

### 5.3 Insight Agent
- Phân tích root-cause: "Tại sao latency tăng?"
- So sánh: trước/sau, service A vs B, weekend vs weekday
- Phát hiện anomaly trong dữ liệu

### 5.4 Forecast Agent
- Dự báo xu hướng từ dữ liệu lịch sử
- LLM-based hoặc Simple Exponential Smoothing fallback

### 5.5 QA Agent
- Kiểm tra ingestion health và failed batches
- Đánh giá data quality
- Audit pipeline status

### 5.6 Partition Agent
- Phân tích partition strategy của ClickHouse tables
- Phát hiện partition key, type, health
- Đề xuất pruning filters

### 5.7 Dashboard Generator
- Tạo multi-panel dashboard từ yêu cầu tổng quan
- Kết hợp nhiều chart/insight/QA agents

### 5.8 Terminal Agent
- Thực thi terminal commands (observation)
- Rule-based planning, allowlist security
- Langfuse tracing

---

## 6. Các tình huống người dùng có thể hỏi

### 6.1 Analytics

| Loại | Ví dụ | Agent |
|------|-------|-------|
| Lookup | "Show P99 latency by service for last 24h" | Chart |
| So sánh | "Compare costs between yesterday and today" | Insight |
| Top N | "List top 5 users by token consumption" | Chart |
| Thống kê | "Show daily transaction volume and success rate" | Chart |
| Tương quan | "Compare CPU and memory usage across pods" | Chart/Insight |

### 6.2 Time-series & Xu hướng

| Loại | Ví dụ | Agent |
|------|-------|-------|
| Xu hướng | "Show hourly request distribution" | Chart |
| Dự báo | "Forecast next month cost based on current trends" | Forecast |
| Peak hours | "Show peak usage hours across all services" | Chart |
| Anomaly | "Detect anomalies in token usage patterns" | Insight |

### 6.3 Root-cause & Insight

| Loại | Ví dụ | Agent |
|------|-------|-------|
| Root cause | "Which services have the highest error budget burn?" | Insight |
| So sánh tuần | "Compare weekend vs weekday usage patterns" | Insight |
| Cost savings | "Calculate cost savings if we switch to cheaper models" | Insight/Forecast |

### 6.4 Data Quality & Operations

| Loại | Ví dụ | Agent |
|------|-------|-------|
| Ingestion health | "Check ingestion health and failed batches" | QA |
| Data quality | "Show data quality check results" | QA |
| Failed queries | "Find sessions with failed SQL generations" | QA |
| Pipeline status | "Summarize failed agent queries" | QA |

### 6.5 Partition & Schema

| Loại | Ví dụ | Agent |
|------|-------|-------|
| Partition health | "Analyze partition strategy for fct_llm_interactions" | Partition |
| Schema info | "What tables are available in vsf_mart?" | Schema API |

### 6.6 Dashboard & Tổng hợp

| Loại | Ví dụ | Agent |
|------|-------|-------|
| Dashboard | "Show me a dashboard of system health" | Dashboard Generator |
| Full report | "Give me a full report of this week's performance" | All agents |
| Tổng quan | "Tổng quan hệ thống hôm nay" | Dashboard Generator |

### 6.7 Follow-up & Clarification

| Loại | Ví dụ | Xử lý |
|------|-------|-------|
| Follow-up | "So sánh với tuần trước" (sau câu hỏi về latency) | ContextManager resolve memory |
| Clarification | "Cho tôi xem metric" (không rõ metric nào) | Orchestrator hỏi lại |
| Raw SQL | Người dùng tự nhập SQL | SQL guard + execute |

---

## 7. Technology Stack

| Layer | Technology | Mục đích |
|-------|-----------|----------|
| **API Framework** | FastAPI (Python) | REST API server |
| **Orchestration** | Airflow 3 | Data pipeline scheduling |
| **Data Warehouse** | ClickHouse | Analytics queries (OLAP) |
| **Operational DB** | PostgreSQL | Sessions, auth, audit |
| **Object Storage** | MinIO | Raw JSONL storage |
| **Metadata Store** | DataHub | Schema, lineage, freshness |
| **BI Tool** | Metabase | Dashboard embedding |
| **LLM Provider** | OpenAI / OpenRouter | SQL generation, synthesis |
| **Observability** | OpenTelemetry + Langfuse | Tracing, LLM monitoring |
| **Container** | Docker + Astro CLI | Local deployment |
| **Data Transform** | dbt (ClickHouse) | raw → stg → mart |

---

## 8. Infrastructure & Deployment

### 8.1 Deployment Architecture

```
┌────────────────────────────────────────────────────────┐
│  VPS (47.131.228.222) — Single Host                    │
│                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Airflow     │  │  Agent API   │  │  Data        │  │
│  │  (Astro CLI) │  │  (FastAPI)   │  │  Services    │  │
│  │              │  │              │  │              │  │
│  │  - Scheduler │  │  - Chat      │  │  - ClickHouse│  │
│  │  - Webserver │  │  - Sessions  │  │  - PostgreSQL│  │
│  │  - DB        │  │  - Dashboard │  │  - MinIO     │  │
│  └──────────────┘  └──────────────┘  │  - DataHub   │  │
│                                      │  - Metabase  │  │
│  ┌──────────────┐  ┌──────────────┐  └──────────────┘  │
│  │  Scripts     │  │  dbt Models  │                    │
│  │  Data Gen    │  │  Transform   │                    │
│  └──────────────┘  └──────────────┘                    │
└────────────────────────────────────────────────────────┘
```

### 8.2 Service Ports

| Service | Port |
|---------|------|
| Airflow UI | 18080 / 6563 |
| ClickHouse HTTP | 18123 |
| MinIO API | 19001 |
| MinIO Console | 19002 |
| Metabase | 3000 |
| Observation Service | 18000 |
| Postgres DWH | 5433 |

---

## 9. Security

- **Authentication**: JWT tokens, role-based (admin/team/service)
- **Row-Level Security**: Tự động thêm filters theo principal (service_name, source_app)
- **SQL Safety**: Chặn DDL/DML, chỉ cho phép SELECT/WITH
- **Rate Limiting**: Per-user và login rate limits
- **Terminal Allowlist**: Chỉ cho phép commands được định nghĩa trước

---

## 10. Observability

- **LLM Tracing**: Langfuse ghi lại mỗi generation (prompt, response, tokens, cost)
- **Distributed Tracing**: OpenTelemetry OTLP (HTTP request → orchestrator → DB query)
- **Audit Trail**: Postgres lưu mỗi turn chat (user, question, result, timing)
- **Logging**: JSON structured logging, auto-detect environment
