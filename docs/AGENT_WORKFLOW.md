# Agentic BI — Multi-Agent Workflow

## 1. Tổng quan Multi-Agent Architecture

```mermaid
graph TB
    subgraph Client["Client Layer"]
        UI["Web UI (React)"]
    end

    subgraph Gateway["API Gateway"]
        API["FastAPI"]
        Auth["JWT Auth"]
        Router["Routers:<br/>/chat /sessions /dashboard"]
    end

    subgraph Orchestration["Orchestration Layer"]
        SlotExtract["Semantic Slot Extractor<br/>Phân tích intent, metric, time_range"]
        AgentPlan["Agent Planner<br/>LLM-based / Rule-based routing"]
        TableSelect["Table Selector<br/>BM25 + Keyword Ranking"]
        Orchestrator["Multi-Agent Orchestrator<br/>Điều phối, clarification, aggregation"]
        ContextMgr["Context Manager<br/>Session history + Memory"]
    end

    subgraph Metadata["Metadata Path"]
        DHConn["DataHubConnector"]
        DH["DataHub GraphQL API<br/>schema, tags, description"]
        CHFallback["ClickHouse system.columns<br/>(fallback)"]
    end

    subgraph Agents["Specialist Agents"]
        Chart["Chart Agent<br/>NL2SQL + Chart"]
        Insight["Insight Agent<br/>Root-cause"]
        Forecast["Forecast Agent<br/>Dự báo"]
        QA["QA Agent<br/>Data quality"]
        Partition["Partition Agent<br/>Phân tích partition"]
        DashGen["Dashboard Generator<br/>Multi-panel"]
    end

    subgraph LLM["LLM Service"]
        LLM_SQL["SQL Generation"]
        LLM_Correct["SQL Correction"]
        LLM_Synth["Synthesis"]
        LLM_Plan["Agent Planning"]
    end

    subgraph Storage["Data Layer"]
        CH["ClickHouse<br/>vsf_mart / stg / raw"]
        PG["PostgreSQL<br/>sessions, auth, audit"]
        MinIO["MinIO<br/>Raw JSONL"]
    end

    UI -->|HTTP REST| API
    API --> Auth
    Auth --> Router
    Router --> Orchestrator

    Orchestrator --> SlotExtract
    SlotExtract --> AgentPlan
    AgentPlan --> TableSelect
    Orchestrator --> ContextMgr

    Orchestrator -.->|metadata request| DHConn
    DHConn -->|GraphQL API| DH
    DHConn -.->|fallback| CHFallback
    DHConn -->|merged metadata| TableSelect

    Orchestrator -->|chart intent| Chart
    Orchestrator -->|insight intent| Insight
    Orchestrator -->|forecast intent| Forecast
    Orchestrator -->|qa intent| QA
    Orchestrator -->|partition intent| Partition
    Orchestrator -->|dashboard intent| DashGen

    Chart --> LLM_SQL
    Insight --> LLM_SQL
    Forecast --> LLM_SQL
    QA --> LLM_SQL
    Partition --> LLM_SQL

    LLM_SQL -->|SQL error| LLM_Correct
    LLM_Correct -->|retry| LLM_SQL
    LLM_SQL -->|SQL| CH
    LLM_SQL -->|SQL| PG
    LLM_Synth -->|synthesize answer| Orchestrator
    LLM_Plan --> AgentPlan

    Chart -->|select tables from| TableSelect
    Insight -->|select tables from| TableSelect
    Forecast -->|select tables from| TableSelect
```

---

## 2. Orchestrator Flow chi tiết

```mermaid
flowchart TD
    Start["Người dùng gửi câu hỏi"] --> LoadContext["ContextManager.load_context()<br/>Lấy session history + memory"]
    LoadContext --> ExtractSlots["Semantic Slot Extraction<br/>Rule-based + optional LLM"]

    ExtractSlots --> CheckSlots{"Đủ slot?"}
    CheckSlots -->|"Thiếu"| Clarification["Trả về needs_clarification<br/>Hỏi lại người dùng"]
    CheckSlots -->|"Đủ"| BuildPlan["Build Request Plan<br/>intent, metric, time_range, domain"]

    BuildPlan --> AgentRouting{"Agent Planner"}
    AgentRouting -->|"LLM configured"| LLMRoute["llm.plan_agents()"]
    AgentRouting -->|"No LLM"| RuleRoute["deterministic_agent_plan()"]

    LLMRoute --> CheckConfidence{"Confidence?"}
    RuleRoute --> CheckConfidence

    CheckConfidence -->|"High >= 0.72"| AutoRoute["Tự động route"]
    CheckConfidence -->|"Medium 0.45-0.72"| RouteWithNote["Route + ghi chú"]
    CheckConfidence -->|"Low < 0.45"| AskClarify["Yêu cầu clarification"]

    AutoRoute --> ChartIntent{"Intent type?"}
    RouteWithNote --> ChartIntent
    AskClarify --> AskUser["Hỏi lại người dùng"]

    ChartIntent -->|chart / lookup| ChartAgent["Gọi Chart Agent"]
    ChartIntent -->|insight| InsightAgent["Gọi Insight Agent"]
    ChartIntent -->|forecast| ForecastAgent["Gọi Forecast Agent"]
    ChartIntent -->|qa| QAAgent["Gọi QA Agent"]
    ChartIntent -->|partition| PartitionAgent["Gọi Partition Agent"]
    ChartIntent -->|dashboard| DashGenAgent["Gọi Dashboard Generator"]
    ChartIntent -->|full_report| AllAgents["Gọi tất cả agents<br/>chạy song song"]

    AllAgents --> Parallel["asyncio.gather()<br/>chạy đồng thời"]
    Parallel --> Aggregate

    ChartAgent --> MonitorExec["MonitoringAgent.execute()<br/>Xử lý NL2SQL"]
    InsightAgent --> MonitorExec
    ForecastAgent --> MonitorExec
    QAAgent --> MonitorExec
    PartitionAgent --> PartitionExec["PartitionAgent.execute()<br/>Phân tích partition"]
    DashGenAgent --> DashGenExec["DashboardGenerator<br/>Tạo multi-panel"]

    MonitorExec --> Aggregate["Aggregation<br/>Gom kết quả từ agents"]
    PartitionExec --> Aggregate
    DashGenExec --> Aggregate

    Aggregate --> Summarize["Summarizer<br/>Tổng hợp câu trả lời"]
    Summarize --> Response["Response<br/>answer + chart + rows + sql"]
```

---

## 3. Monitoring Agent Execution Flow

```mermaid
flowchart TD
    Start["MonitoringAgent.execute()"] --> SelectDS["Chọn datasource<br/>ClickHouse / PostgreSQL"]
    SelectDS --> LoadMeta["DataHubConnector.list_tables()"]
    
    LoadMeta --> TryDataHub{"DataHub online?"}
    TryDataHub -->|"Yes"| GraphQL["Gọi DataHub GraphQL API<br/>searchDatasets query"]
    TryDataHub -->|"No"| FallbackCH["Query ClickHouse<br/>system.columns"]
    
    GraphQL --> MergeSchema["Merge với live schema<br/>từ ClickHouse"]
    FallbackCH --> CacheResult
    
    MergeSchema --> CacheResult["Cache metadata<br/>30s DataHub / 10s CH"]
    CacheResult --> TableSelect["TableSelector.select()<br/>BM25 + keyword ranking"]
    
    TableSelect --> GenSQL["LlmService.generate_sql()"]
    GenSQL --> CheckSQL{"Có LLM?"}
    
    CheckSQL -->|"Yes"| LLM_SQL["LLM sinh SQL candidates<br/>(OpenAI/OpenRouter)"]
    CheckSQL -->|"No"| DetSQL["Deterministic fallback SQL"]
    
    LLM_SQL --> ValidateSQL["sql_safety.validate()<br/>Kiểm tra read-only"]
    DetSQL --> ValidateSQL
    
    ValidateSQL --> ExecuteSQL["Execute trên ClickHouse / PG"]
    ExecuteSQL --> CheckError{"SQL lỗi?"}
    
    CheckError -->|"Yes, có LLM"| CorrectSQL["LlmService.correct_sql()<br/>Sửa SQL với error message"]
    CorrectSQL -->|"Retry"| ExecuteSQL
    
    CheckError -->|"Không"| CheckEmpty{"Empty result?"}
    ExecuteSQL --> CheckEmpty
    
    CheckEmpty -->|"Yes"| FallbackLayer["Fallback xuống silver layer"]
    FallbackLayer --> TableSelect
    
    CheckEmpty -->|"No"| InferChart["chart_spec.infer_chart_spec()<br/>Suy luận kiểu biểu đồ"]
    
    InferChart --> Synthesize["LLM synthesizes answer<br/>(nếu configured)"]
    Synthesize --> Return["Return: sql + rows + chartSpec"]
```

---

## 4. Agent Routing & Planning

```mermaid
flowchart LR
    subgraph Input["Input từ Orchestrator"]
        Q["Câu hỏi + Slots"]
    end

    subgraph Router["Agent Planner"]
        RTable["Routing Table"]
    end

    subgraph Output["Specialist Agents"]
        CA["Chart Agent<br/>→ SQL + Chart"]
        IA["Insight Agent<br/>→ Root-cause"]
        FA["Forecast Agent<br/>→ Dự báo"]
        QA_A["QA Agent<br/>→ Quality Check"]
        PA["Partition Agent<br/>→ Partition Health"]
    end

    Q --> RTable

    RTable -->|"chart / lookup"| CA
    RTable -->|"insight"| IA
    RTable -->|"forecast"| FA
    RTable -->|"qa / quality"| QA_A
    RTable -->|"partition"| PA
    RTable -->|"dashboard"| Combine1["Chạy IA + CA + QA_A"]
    RTable -->|"full_report"| Combine2["Chạy tất cả agents"]
```

---

## 5. Clarification Flow

```mermaid
sequenceDiagram
    actor User as Người dùng
    participant GW as API Gateway
    participant Orchestrator as Orchestrator
    participant SlotExt as Slot Extractor
    participant Planner as Agent Planner

    User->>GW: "Cho tôi xem metric"
    GW->>Orchestrator: ChatRequest(question)
    Orchestrator->>SlotExt: extract_slots()
    
    Note over SlotExt: Phân tích: intent=rõ (chart),<br/>metric=không rõ, time_range=không rõ

    SlotExt-->>Orchestrator: slots: {intent: chart, metric: null, time_range: null}
    
    Orchestrator->>Planner: build_request_plan()
    Note over Planner: Thiếu metric + time_range →<br/>confidence low
    Planner-->>Orchestrator: clarification needed

    Orchestrator-->>GW: needs_clarification
    GW-->>User: {
        "clarification": true,
        "message": "Bạn muốn xem metric nào?",
        "options": ["latency", "errors", "cost", "tokens"]
    }

    User->>GW: "latency 7 ngày qua"
    GW->>Orchestrator: ChatRequest(question, context=previous)
    Orchestrator->>SlotExt: extract_slots(question, context)
    
    Note over SlotExt: Kết hợp câu mới + context cũ<br/>→ intent=chart, metric=latency, time_range=7days
    
    SlotExt-->>Orchestrator: slots: {intent: chart, metric: latency, time_range: 7days}
    Orchestrator->>Planner: build_request_plan() → confidence high
    Planner-->>Orchestrator: agent_plan: [chart_agent]
    
    Orchestrator->>Orchestrator: Gọi Chart Agent với đầy đủ slots
    Orchestrator-->>GW: Response(chart, rows, sql)
    GW-->>User: Kết quả biểu đồ
```

---

## 6. Multi-Agent Data Flow (Context + Metadata)

```mermaid
sequenceDiagram
    participant User as Người dùng
    participant Ctx as ContextManager
    participant DH as DataHubConnector
    participant TS as TableSelector
    participant Agent as MonitoringAgent
    participant LLM as LLM Service
    participant DB as ClickHouse

    User->>Ctx: Câu hỏi + session_id
    
    Ctx->>Ctx: load_context(session_id)
    Note over Ctx: Lấy conversation history<br/>+ structured memory (entities, metrics, filters, timeframe)
    Ctx-->>Agent: context + question

    Agent->>DH: list_tables()
    
    DH->>DH: Gọi DataHub GraphQL API<br/>searchDatasets(clickhouse)
    alt DataHub online
        DH->>DH: Merge với ClickHouse system.columns
        DH-->>Agent: merged metadata (cached 30s)
    else DataHub offline
        DH->>DH: Query ClickHouse system.columns
        DH-->>Agent: live schema (cached 10s)
    end
    
    Agent->>TS: select(question, tables)
    TS->>TS: BM25 ranking + keyword + layer ưu tiên
    TS-->>Agent: primary_table + candidates
    
    Agent->>LLM: generate_sql(question, tables, context)
    LLM-->>Agent: SQL candidates
    
    Agent->>Agent: sql_safety.validate()
    
    Agent->>DB: Execute SQL
    DB-->>Agent: rows
    
    alt SQL error
        Agent->>LLM: correct_sql(sql, error)
        LLM-->>Agent: corrected SQL
        Agent->>DB: Re-execute
        DB-->>Agent: rows
    end
    
    alt empty results
        Agent->>TS: fallback silver layer
        TS-->>Agent: silver tables
        Agent->>DB: Re-query
        DB-->>Agent: rows
    end
    
    Agent->>Agent: infer_chart_spec(rows)
    Agent->>LLM: synthesize(rows, question)
    LLM-->>Agent: natural language answer
    
    Agent-->>User: Response(answer, chart, rows, sql)
```

---

## 7. Intent Classification Rules

```mermaid
flowchart TD
    Q["Câu hỏi người dùng"] --> Normalize["Chuẩn hóa: lowercase, remove accents"]
    Normalize --> Match{"Keyword matching"}
    
    Match -->|"vẽ, biểu đồ, chart, plot"| I1["Intent: chart"]
    Match -->|"dự báo, forecast, predict, xu hướng"| I2["Intent: forecast"]
    Match -->|"tại sao, nguyên nhân, root cause, insight"| I3["Intent: insight"]
    Match -->|"quality, failed, lỗi, health, kiểm tra"| I4["Intent: qa"]
    Match -->|"partition"| I5["Intent: partition"]
    Match -->|"dashboard, tổng quan, overview"| I6["Intent: dashboard"]
    Match -->|"báo cáo, report, tất cả"| I7["Intent: full_report"]
    Match -->|"No match"| Default["Mặc định: chart (lookup)"]

    I1 --> Domain1{"Domain?"}
    I2 --> Domain1
    I3 --> Domain1
    I4 --> Domain1
    I5 --> Domain1
    I6 --> Domain1
    I7 --> Domain1

    Domain1 -->|"latency, cpu, memory, pod"| D1["Domain: observability"]
    Domain1 -->|"cost, token, model"| D2["Domain: llm_usage / agent_cost"]
    Domain1 -->|"ingestion, batch, failed"| D3["Domain: ingestion"]
    Domain1 -->|"No match"| DDefault["Domain: auto"]
```

---

## 8. Component Interaction Summary

```mermaid
graph TB
    subgraph Legend["Chú thích"]
        L1["Luồng chính"]
        L2["Luồng metadata"]
        L3["Fallback"]
    end

    subgraph Core["Core Components"]
        MA["MultiAgentOrchestrator"]
        REQ["RequestOrchestrator<br/>(semantic slots)"]
        AP["AgentPlanner<br/>(routing table)"]
        TS["TableSelector<br/>(BM25 ranking)"]
        MON["MonitoringAgent<br/>(NL2SQL execution)"]
        CHART["ChartSpec<br/>(chart inference)"]
        SAFE["SQLSafety<br/>(read-only validation)"]
        ROW["RowSecurity<br/>(row-level filter)"]
        WF["WorkflowGraph<br/>(visualization)"]
    end

    subgraph External["External Services"]
        LLM_S["LLM Service<br/>(OpenAI/OpenRouter)"]
        DH_C["DataHubConnector"]
        CH_C["ClickHouseConnector"]
        PG_C["PostgresConnector"]
        META_C["MetabaseConnector"]
    end

    MA --> REQ
    MA --> AP
    MA --> TS
    MA --> MON
    MA --> WF

    MON --> SAFE
    MON --> ROW
    MON --> CHART
    MON --> LLM_S
    MON --> CH_C
    MON --> PG_C

    TS --> DH_C
    DH_C -->|primary| LLM_S
    DH_C -.->|fallback| CH_C

    REQ --> LLM_S
    AP --> LLM_S
```

---

## 9. File Structure Reference

```
src/backend/app/
├── main.py                     # FastAPI entrypoint
├── config.py                   # Env-driven settings
├── security.py                 # JWT auth + rate limiting
├── observability.py            # Langfuse + OpenTelemetry
├── sql_guard.py                # Raw SQL validation
├── terminal.py                 # Safe terminal runner
├── agent.py                    # TerminalAgent
│
├── agents/
│   ├── multi_agent.py          # MultiAgentOrchestrator
│   ├── agent_planner.py        # Routing table
│   ├── request_orchestrator.py # Semantic slots
│   ├── table_selector.py       # Table selection
│   ├── monitoring_agent.py     # Main NL2SQL agent
│   ├── partition_agent.py      # Partition analysis
│   ├── partition_manager.py    # Partition health
│   ├── sql_safety.py           # Agent SQL validation
│   ├── row_security.py         # Row-level security
│   ├── chart_spec.py           # Chart inference
│   ├── dashboard_generator.py  # Dashboard generation
│   └── workflow_graph.py       # Workflow visualization
│
├── services/
│   ├── llm.py                  # LLM client
│   └── context_manager.py      # Session + memory
│
├── connectors/
│   ├── clickhouse.py
│   ├── postgres.py
│   ├── datahub.py
│   └── metabase.py
│
└── routers/
    ├── query.py
    ├── sessions.py
    ├── dashboard.py
    └── auth.py
```
