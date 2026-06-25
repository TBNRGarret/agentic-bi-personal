# Agentic BI — Agent Architecture

## Tổng quan

Agentic BI sử dụng **Multi-Agent Orchestrator** để xử lý câu hỏi tiếng Việt từ người dùng → phân tích ngữ nghĩa → điều phối các agent chuyên biệt → sinh SQL → truy vấn dữ liệu → trả về bảng/biểu đồ/dashboard.

Luồng xử lý: `HTTP Request → Router → Orchestrator → Semantic Slots → Agent Plan → Specialist Agents → Summarizer → Response`

---

## 1. Entry point — `main.py`

`FastAPI` app khởi tạo các connectors và services:

```
ClickHouseConnector → truy vấn analytics
PostgresConnector  → audit, sessions, auth
DataHubConnector   → metadata & lineage
MetabaseConnector  → dashboard embedding
AuthService        → JWT authentication
ContextManager     → session & memory
```

Routers:
- `/api/agent/chat` — endpoint chính (`routers/query.py`)
- `/api/sessions` — quản lý session (`routers/sessions.py`)
- `/api/dashboard` — dashboard (`routers/dashboard.py`)
- `/api/auth` — auth (`routers/auth.py`)

---

## 2. HTTP Flow — `routers/query.py`

```
Request → ChatRequest → load context/memory từ session
       → MultiAgentOrchestrator.answer()
       → persist turn vào Postgres
       → audit log
       → Response
```

Mỗi session lưu conversation history và memory (structured request trước đó) để agent có context liên tục.

---

## 3. Multi-Agent Orchestrator — `agents/multi_agent.py`

`MultiAgentOrchestrator` là **control-plane agent**, điều phối toàn bộ quy trình.

### 3.1 Luồng chính

```
answer(question)
  │
  ├─ 1. Semantic Slots: trích xuất intent/domain/metric/time_range/layer
  │     (dùng LLM classify_request_slots nếu có, skip nếu không)
  │
  ├─ 2. Request Plan: build_request_plan() kết hợp rule-based + LLM slots
  │     ├─ intent_type: dashboard | time_series | aggregation | lookup | full_report
  │     ├─ domain: observability | llm_usage | ingestion | agent_cost
  │     ├─ metric: latency | errors | cost | tokens | ...
  │     ├─ time_range: 24h | 7days | 30days
  │     └─ layer: auto | gold | silver
  │
  ├─ 3. Clarification Gate: nếu thiếu slot → trả về needs_clarification
  │     yêu cầu người dùng chọn option (VD: "Bạn muốn xem metric nào?")
  │
  ├─ 4. Agent Plan: chọn specialist agents
  │     ├─ LLM-based: llm.plan_agents() (nếu có API key)
  │     └─ Fallback: deterministic_agent_plan() (rule-based)
  │
  ├─ 5. Routing & Execution: chạy các specialist agents song song (asyncio.gather)
  │     ├─ chart_agent      → NL2SQL + biểu đồ
  │     ├─ insight_agent    → phân tích root-cause
  │     ├─ forecast_agent   → dự báo
  │     ├─ qa_agent         → kiểm tra chất lượng dữ liệu
  │     └─ partition_agent  → phân tích partition strategy & health
  │
  ├─ 6. Summarizer: tổng hợp kết quả từ các specialist agents
  │
  └─ 7. Response: trả về answer + chartSpec + rows + sql + metadata
```

### 3.2 Routing table (`agent_planner.py`)

| Intent | Agents | Mô tả |
|--------|--------|-------|
| `chart` | `chart_agent` | NL2SQL + biểu đồ |
| `insight` | `insight_agent` | Root-cause analysis |
| `forecast` | `forecast_agent` | Dự báo |
| `qa` | `qa_agent` | Data quality checks |
| `partition` | `partition_agent` | Partition strategy & health analysis |
| `dashboard` | `insight_agent + chart_agent + qa_agent` | Dashboard tổng hợp |
| `full_report` | All 4 agents | Full report |

### 3.3 Confidence levels (`request_orchestrator.py`)

| Level | Range | Hành vi |
|-------|-------|---------|
| `high` | ≥ 0.72 | Tự động route |
| `medium` | 0.45 – 0.72 | Route kèm ghi chú "có thể thay đổi nếu bạn làm rõ" |
| `low` | < 0.45 | Yêu cầu clarification |

---

## 4. Semantic Slots — `agents/request_orchestrator.py`

Trích xuất ngữ nghĩa từ câu hỏi bằng **rule-based** với fallback keyword matching. Các slots:

```
intent_type  → dashboard | time_series | aggregation | lookup | full_report
domain       → observability | llm_usage | ingestion | agent_cost
metric       → latency | errors | cost | tokens | requests | health | …
time_range   → 24h | 7days | 30days
layer        → auto | gold | silver
dimensions   → service | model | source | …
coverage     → complete | top_n
```

Mỗi slot có `value`, `confidence`, `source` (llm/rule/default).

---

## 5. Table Selector — `agents/table_selector.py`

Chọn bảng phù hợp từ metadata (DataHub) dựa trên câu hỏi:

```
select(question, tables)
  ├─ Lọc bảng theo layer (gold > silver)
  ├─ Keyword ranking: matching tên bảng/cột với câu hỏi
  ├─ Embedding ranking (optional): similarity search
  └─ Trả về primary_table + candidates
```

Chiến lược: `sql_link_keyword_metadata_ranking` → hybrid với embedding nếu cần.

---

## 6. Specialist Agents

### 6.1 Chart Agent
- Nhận: question + selected tables + slots
- Xử lý: `LlmService.generate_sql()` → validation → execute → chart spec
- Trả về: sql + rows + chartSpec (type: line|bar|pie|table|...)

### 6.2 Insight Agent
- Phân tích root-cause: latency, errors theo service/model
- So sánh: trước/sau, service A vs B

### 6.3 Forecast Agent
- Dự báo từ historical metrics
- Sử dụng Simple Exponential Smoothing hoặc LLM-based

### 6.4 QA Agent
- Kiểm tra ingestion health, data quality, failed batches
- Audit pipeline status

---

## 7. SQL Generation — `services/llm.py`

`LlmService` là OpenAI-compatible client.

```
generate_sql(question, tables, datasource, limit, conversation, memory)
  ├─ System prompt với dialect (ClickHouse SQL / PostgreSQL SQL)
  ├─ Context: tables schema + conversation history + structured memory
  ├─ LLM call với retry & timeout
  ├─ Parse JSON response → sql, candidates, visualization
  └─ Fallback: "disabled" nếu không có API key

correct_sql(failed_sql, database_error) → sửa SQL lỗi
```

### SQL Safety (`agents/sql_safety.py`)
- Kiểm tra read-only: chặn DDL/DML
- Validate cú pháp trước khi execute

### Row Security (`agents/row_security.py`)
- Lọc dữ liệu theo principal (user/role/team)

---

## 8. Summarizer Agent

Tổng hợp kết quả từ nhiều specialist agents thành một câu trả lời duy nhất bằng LLM.

---

## 9. Terminal Agent — `agent.py`

Agent riêng cho terminal commands (observation service):
- `answer(message)` → `_plan(message)` → `terminal.run(command)` → `_summarize()`
- Rule-based planning: map keyword → command
- Allowlist security: chỉ chạy lệnh được phép
- Langfuse tracing cho mỗi generation

---

## 10. Observability

- **Langfuse**: ghi lại mỗi LLM generation (input, output, token usage, cost)
- **OpenTelemetry**: trace toàn bộ request từ HTTP → orchestrator → specialist → DB
- **Postgres audit**: ghi lại mỗi turn chat (question, result, timing)

---

## Sơ đồ kiến trúc

```
┌─────────────┐     ┌───────────────────┐     ┌─────────────────┐
│   Client    │────▶│  FastAPI Router   │────▶│ ContextManager  │
│ (Web UI)    │     │  /api/agent/chat  │     │ (Session+Memory)│
└─────────────┘     └───────────────────┘     └─────────────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │  Multi-Agent    │
                   │  Orchestrator   │
                   └────────┬────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
       ┌──────────┐  ┌──────────┐  ┌──────────┐
       │ Semantic │  │  Agent   │  │  Table   │
       │  Slots   │  │  Plan    │  │ Selector │
       └──────────┘  └──────────┘  └──────────┘
                            │
          ┌─────────────────┼──────────────────────┐
          ▼         ▼       ▼       ▼              ▼
   ┌──────────┐ ┌──────────┐ ┌──────┐ ┌──────┐ ┌──────────┐
   │  Chart   │ │ Insight  │ │Fore- │ │  QA  │ │Partition │
   │  Agent   │ │  Agent   │ │cast  │ │Agent │ │  Agent   │
   └────┬─────┘ └────┬─────┘ └──┬───┘ └──┬───┘ └────┬─────┘
        │            │          │        │          │
        └────────────┼──────────┼────────┼──────────┘
                     ▼          ▼        ▼
              ┌────────────┐ ┌────────┐ ┌──────────┐
              │  LLM       │ │Click-  │ │ DataHub  │
              │  Service   │ │House   │ │ Metadata │
              └────────────┘ └────────┘ └──────────┘
                     │
                     ▼
              ┌─────────────┐
              │ Summarizer  │
              │   Agent     │
              └──────┬──────┘
                     │
                     ▼
              ┌─────────────┐
              │   Response  │
              └─────────────┘
```

---

## C4 Architecture — Airflow Pipeline

The pipeline is split into two DAGs for reliability:

```
┌─────────────────────────────────────────────────────────────┐
│  DAG 1: sync_external_staging_to_dwh (every 30 min)        │
│                                                             │
│  ┌──────────────┐   ┌─────────────────┐   ┌─────────────┐  │
│  │ Scan MinIO   │──▶│ Validate & Load │──▶│ Trigger     │  │
│  │ Manifests    │   │ (Stream, no     │   │ Transform   │  │
│  │              │   │  local download)│   │ DAG         │  │
│  └──────────────┘   └────────┬────────┘   └─────────────┘  │
│                              │                              │
│                     ┌────────▼────────┐                     │
│                     │ Quarantine      │                     │
│                     │ Failed Batches  │                     │
│                     │ (continue flow) │                     │
│                     └─────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
                              │
                    TriggerDagRunOperator
                              │
┌─────────────────────────────▼───────────────────────────────┐
│  DAG 2: transform_raw_to_gold (triggered)                   │
│                                                             │
│  ┌────────────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │ dbt build      │──▶│ Data Quality │──▶│ Quality Gate │  │
│  │ (raw→silver    │   │ Checks       │   │ Results      │  │
│  │  →gold)        │   │              │   │              │  │
│  └────────────────┘   └──────────────┘   └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

Key design decisions:
- **MinIO streaming**: Data is read directly via S3 API, never downloaded to local disk
- **Batch quarantine**: Failed batches are logged as `quarantined` but pipeline continues
- **Separated concerns**: Ingestion runs frequently; transform is triggered only on new data

---

## File structure

```
src/backend/app/
├── main.py                        # FastAPI app, wire everything
├── agent.py                       # TerminalAgent (observation)
├── config.py                      # Settings
├── security.py                    # Auth + JWT
├── observability.py               # Langfuse + OpenTelemetry
│
├── agents/
│   ├── multi_agent.py             # MultiAgentOrchestrator
│   ├── agent_planner.py           # Agent routing table + deterministic plan
│   ├── request_orchestrator.py    # Semantic slots extraction
│   ├── table_selector.py          # Table selection from metadata
│   ├── partition_manager.py       # Partition key detection + health analysis
│   ├── partition_agent.py         # Partition analysis specialist agent
│   ├── mock_partition_data.py     # Mock data for partition testing
│   ├── sql_safety.py              # SQL read-only validation
│   ├── row_security.py            # Row-level security
│   ├── chart_spec.py              # Chart visualization spec
│   ├── dashboard_generator.py     # Dashboard generation
│   └── monitoring_agent.py        # Base agent with connectors
│
├── services/
│   ├── llm.py                     # LLM client (OpenAI-compatible)
│   ├── context_manager.py         # Session + memory CRUD
│   └── __init__.py
│
├── routers/
│   ├── query.py                   # /chat endpoint
│   ├── sessions.py                # Session management
│   ├── dashboard.py               # Dashboard API
│   └── auth.py                    # Authentication
│
├── connectors/
│   ├── clickhouse.py              # ClickHouse client
│   ├── postgres.py                # Postgres client
│   ├── datahub.py                 # DataHub metadata client
│   └── metabase.py                # Metabase client
│
└── migrations/
    └── agentic_schema.sql         # DB schema

dags/
└── agentic_bi_pipeline.py         # 2 DAGs: Ingestion + Transform
```
