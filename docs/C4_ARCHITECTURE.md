# C4 Architecture — Agentic BI System

Tài liệu kiến trúc theo chuẩn [C4 Model](https://c4model.com) cho hệ thống Agentic BI.

---

## Level 1 — System Context Diagram

Hệ thống Agentic BI trong bối cảnh tương tác với người dùng và hệ thống bên ngoài.

```mermaid
graph TB
    User["👤 End User<br/>(Business Analyst)"]
    Admin["🔧 Admin<br/>(Data Engineer)"]

    subgraph AgenticBI ["Agentic BI System"]
        System["Agentic BI<br/>─────────────────<br/>Hệ thống phân tích dữ liệu<br/>thông minh sử dụng AI Agent"]
    end

    ExternalLLM["☁️ External LLM API<br/>(OpenRouter / OpenAI / Google)"]
    SourceDWH["🗄️ Source Postgres DWH<br/>(Dữ liệu nghiệp vụ từ các thành viên)"]

    User -->|"Hỏi câu hỏi bằng<br/>ngôn ngữ tự nhiên"| System
    Admin -->|"Quản lý pipeline,<br/>monitor metadata"| System
    System -->|"Gọi API sinh SQL,<br/>phân tích ngữ cảnh"| ExternalLLM
    SourceDWH -->|"Export JSONL batches<br/>(automated every 3h)"| System

    style System fill:#4A90D9,stroke:#fff,stroke-width:2px,color:#fff
    style ExternalLLM fill:#FF6B6B,stroke:#fff,stroke-width:2px,color:#fff
    style SourceDWH fill:#336791,stroke:#fff,stroke-width:2px,color:#fff
```

---

## Level 2 — Container Diagram

Chi tiết các container (services) bên trong hệ thống.

```mermaid
graph TB
    User["👤 End User"]
    Admin["🔧 Admin"]

    subgraph AgenticBI ["Agentic BI System"]

        subgraph Orchestration ["Orchestration Layer"]
            Airflow["Airflow 3<br/>(Astro CLI)<br/>─────────────<br/>DAG Scheduler"]
        end

        subgraph Storage ["Storage Layer"]
            MinIO["MinIO<br/>─────────────<br/>Object Storage<br/>Raw JSONL Landing<br/>Port: 19001/19002"]
            ClickHouse["ClickHouse<br/>─────────────<br/>Columnar DWH<br/>vsf_raw → vsf_stg → vsf_mart<br/>Port: 18123"]
        end

        subgraph Transform ["Transformation Layer"]
            dbt["dbt Core<br/>─────────────<br/>SQL Transforms<br/>Silver + Gold models"]
        end

        subgraph Metadata ["Metadata & Catalog"]
            DataHub["DataHub<br/>─────────────<br/>Metadata Catalog<br/>Schema, Lineage, Freshness<br/>Port: 9002 / 28080"]
            Metabase["Metabase<br/>─────────────<br/>BI Dashboards<br/>Pre-built Visualizations<br/>Port: 3000"]
        end

        subgraph App ["Application Layer"]
            ObsService["Observation Service<br/>─────────────<br/>FastAPI Backend<br/>/chat /query /schema<br/>Port: 18000"]
        end

        subgraph Observability ["Observability Layer"]
            SigNoz["SigNoz<br/>─────────────<br/>APM & Traces<br/>OpenTelemetry"]
            Langfuse["Langfuse<br/>─────────────<br/>LLM Observability<br/>Generations & Costs"]
        end
    end

    ExternalLLM["☁️ External LLM API"]
    SourceDWH["🗄️ Source Postgres DWH"]

    %% External flows
    User -->|"NL Questions"| ObsService
    Admin -->|"Monitor"| Airflow
    Admin -->|"Explore Metadata"| DataHub
    Admin -->|"View Dashboards"| Metabase
    SourceDWH -.->|"export_postgres_to_minio DAG<br/>(every 3h)"| MinIO

    %% Internal flows
    Airflow -->|"1. Load JSONL"| MinIO
    MinIO -->|"raw batches"| ClickHouse
    Airflow -->|"2. dbt run staging"| dbt
    Airflow -->|"3. dbt run mart"| dbt
    dbt -->|"transforms"| ClickHouse
    Airflow -->|"4. Quality gate"| ClickHouse

    ObsService -->|"Fetch schema & lineage"| DataHub
    ObsService -->|"Execute SQL"| ClickHouse
    ObsService -->|"Generate SQL"| ExternalLLM
    ObsService -.->|"Traces"| SigNoz
    ObsService -.->|"LLM Generations"| Langfuse

    dbt -.->|"manifest.json"| DataHub
    ClickHouse -.->|"table metadata"| DataHub
    Metabase -->|"queries"| ClickHouse

    style Airflow fill:#017CEE,stroke:#fff,color:#fff
    style MinIO fill:#C72C48,stroke:#fff,color:#fff
    style ClickHouse fill:#FFCC00,stroke:#333,color:#333
    style dbt fill:#FF694B,stroke:#fff,color:#fff
    style DataHub fill:#5B5EA6,stroke:#fff,color:#fff
    style Metabase fill:#509EE3,stroke:#fff,color:#fff
    style ObsService fill:#FFB347,stroke:#333,color:#333
    style SigNoz fill:#E5484D,stroke:#fff,color:#fff
    style Langfuse fill:#7C3AED,stroke:#fff,color:#fff
```

---

## Level 3 — Component Diagram: Airflow Pipeline

Chi tiết các task groups bên trong DAG `agentic_bi_pipeline`.

```mermaid
graph LR
    subgraph ExportDAG ["export_postgres_to_minio (every 3h)"]
        E1["export_and_upload<br/>─────────────<br/>Postgres → JSONL → MinIO"]
        E2["trigger_agentic_bi_pipeline"]
        E1 --> E2
    end

    subgraph PipelineDAG ["agentic_bi_pipeline (daily + triggered)"]
        subgraph TG1 ["TaskGroup: raw_loading"]
            R1["init_clickhouse_schemas<br/>─────────────<br/>Tạo DB + tables<br/>nếu chưa tồn tại"]
            R2["load_raw_batches<br/>─────────────<br/>Validate manifests<br/>Load JSONL → vsf_raw<br/>Per-batch error handling"]
            R3["audit_raw_summary<br/>─────────────<br/>Tổng kết success/fail<br/>Fail nếu tất cả lỗi"]
            R1 --> R2 --> R3
        end

        subgraph TG2 ["TaskGroup: staging_transform"]
            S1["dbt_run_staging<br/>─────────────<br/>dbt run --select tag:staging<br/>vsf_raw → vsf_stg"]
            S2["dbt_test_staging<br/>─────────────<br/>dbt test --select tag:staging<br/>Validate silver models"]
            S1 --> S2
        end

        subgraph TG3 ["TaskGroup: mart_transform"]
            M1["dbt_run_mart<br/>─────────────<br/>dbt run --select tag:mart<br/>vsf_stg → vsf_mart"]
            M2["dbt_test_mart<br/>─────────────<br/>dbt test --select tag:mart<br/>Validate gold models"]
            M3["data_quality_gate<br/>─────────────<br/>8 business rules<br/>Freshness, completeness,<br/>uniqueness, validity"]
            M1 --> M2 --> M3
        end

        TG1 --> TG2 --> TG3
    end

    E2 -.->|"trigger"| TG1

    style TG1 fill:#F08080,stroke:#333
    style TG2 fill:#ADD8E6,stroke:#333
    style TG3 fill:#90EE90,stroke:#333
    style ExportDAG fill:#FFF3CD,stroke:#333
```

---

## Level 3 — Component Diagram: Data Warehouse Layers

```mermaid
graph TB
    subgraph Bronze ["vsf_raw — Bronze Layer"]
        direction LR
        B1["raw_ingestion_batches<br/>(Audit log)"]
        B2["signoz_logs ⚡<br/>PARTITION BY toYYYYMM"]
        B3["signoz_traces ⚡<br/>PARTITION BY toYYYYMM"]
        B4["signoz_metrics ⚡<br/>PARTITION BY toYYYYMM"]
        B5["langfuse_generations"]
        B6["user_interactions"]
    end

    subgraph Silver ["vsf_stg — Silver Layer"]
        direction LR
        S1["stg_signoz_logs"]
        S2["stg_signoz_traces"]
        S3["stg_signoz_metrics"]
        S4["stg_langfuse_generations"]
        S5["stg_user_interactions"]
        S6["staging_llm_generations<br/>(enriched with domain,<br/>provider, cost split)"]
    end

    subgraph Gold ["vsf_mart — Gold Layer"]
        direction LR
        G1["source_apps<br/>(dbt seed)"]
        G2["fct_llm_interactions<br/>(Fact table)"]
        G3["gold_app_daily_cost<br/>(Daily aggregate)"]
        G4["agg_daily_llm_metrics"]
        G5["data_quality_results<br/>(DQ history)"]
    end

    B2 --> S1
    B3 --> S2
    B4 --> S3
    B5 --> S4
    B6 --> S5
    S4 --> S6
    S5 --> S6

    S5 --> G2
    S1 --> G2
    S6 --> G3
    S6 --> G4
    G1 --> G2
    G1 --> S6
    G2 --> G3

    style Bronze fill:#F08080,stroke:#333
    style Silver fill:#ADD8E6,stroke:#333
    style Gold fill:#90EE90,stroke:#333
```

---

## Level 3 — Component Diagram: Metadata Ingestion

```mermaid
graph LR
    subgraph Sources ["Ingestion Sources"]
        dbt_artifacts["dbt artifacts<br/>manifest.json<br/>catalog.json<br/>sources.json<br/>run_results.json"]
        CH["ClickHouse<br/>Physical schema"]
        MB["Metabase<br/>Dashboards & Charts"]
    end

    subgraph DataHub ["DataHub Catalog"]
        GMS["DataHub GMS<br/>Port: 28080<br/>─────────────<br/>GraphQL API<br/>REST API"]
        Frontend["DataHub Frontend<br/>Port: 9002<br/>─────────────<br/>UI for lineage,<br/>schema exploration"]
    end

    dbt_artifacts -->|"dbt_recipe.yml"| GMS
    CH -->|"clickhouse_recipe.yml"| GMS
    MB -->|"metabase_recipe.yml"| GMS

    GMS --> Frontend

    style GMS fill:#5B5EA6,stroke:#fff,color:#fff
    style Frontend fill:#7B7FB5,stroke:#fff,color:#fff
```

---

## Deployment Topology

| Container | Image | Port | Network |
|:---|:---|:---|:---|
| `agentic_clickhouse` | `clickhouse-server:24.8-alpine` | 18123, 19000 | airflow |
| `minio` | `minio/minio` | 19001, 19002 | airflow |
| `agentic_observation_service` | Custom (FastAPI) | 18000 | airflow |
| Airflow Scheduler | Astro CLI managed | 18080 | airflow |
| Airflow Webserver | Astro CLI managed | - | airflow |
| DataHub GMS | DataHub stack | 28080 | datahub |
| DataHub Frontend | DataHub stack | 9002 | datahub |
| Metabase | `metabase/metabase` | 3000 | airflow |

---

## Key Design Decisions

1. **ClickHouse over PostgreSQL for analytics**: Columnar storage provides 10-100x faster aggregation queries on telemetry data.
2. **MinIO as landing zone**: Decouples data producers from the warehouse. JSONL+manifest pattern enables idempotent re-processing.
3. **DataHub over OpenMetadata**: Better dbt and ClickHouse connector support, mature GraphQL API for agent runtime queries.
4. **Astro CLI for Airflow**: Simplifies local development with Docker-based runtime, built-in scheduler/webserver management.
5. **ReplacingMergeTree engine**: Provides eventual deduplication for idempotent batch loads without manual UPSERT logic.
6. **Per-batch error handling**: Pipeline resilience — individual batch failures don't block the entire data refresh.
