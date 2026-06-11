# Agentic BI — 4-Week Sprint Plan

## Context

**Stack**: Airflow 3 · dbt · ClickHouse  
**Team**: 4 engineers  
**Goal**: A user asks a question in natural language → the agent fetches metadata → generates a query → returns a table, chart, or auto-generated dashboard.

---

## Central DWH Local Runbook

The current data-foundation slice follows `docs/dwh-design-proposal.md`:

```text
Sprint 1 Postgres DWH
  -> export JSONL batches to MinIO
  -> Airflow loads vsf_raw
  -> dbt builds vsf_stg and vsf_mart
  -> DataHub ingests schema, lineage, and freshness metadata
```

### Ports

| Service | URL |
|---------|-----|
| Airflow UI | discovered by `.\scripts\start-system.ps1` |
| Agentic ClickHouse HTTP | http://localhost:18123/ping |
| Agentic ClickHouse native | localhost:19000 |
| MinIO API | http://localhost:19001 |
| MinIO Console | http://localhost:19002 |
| DataHub UI | http://localhost:9002 |
| DataHub GMS API | http://localhost:28080 |

### Run Order

Run these from `agentic-bi\`:

```powershell
.\scripts\start-system.ps1
python .\scripts\seed_dwh.py
python .\scripts\export_dwh_to_minio.py
docker exec <agentic-bi-scheduler-container> airflow dags trigger agentic_bi_pipeline
.\metadata\scripts\run-ingestion.ps1
.\scripts\verify-system.ps1
```

The central warehouse contract is:
- Raw/audit: `vsf_raw.raw_ingestion_batches`, `vsf_raw.langfuse_generations`, `vsf_raw.user_interactions`, `vsf_raw.signoz_*`
- Silver: `vsf_stg.staging_llm_generations` and supporting `stg_*` models
- Gold: `vsf_mart.fct_llm_interactions`, `vsf_mart.gold_app_daily_cost`, `vsf_mart.data_quality_results`

---

## Sprint 1 — Data Foundation & Metadata (Week 1–2)

---

### Epic 1 — Sample Data Pipeline (Raw → Gold)

The team is provided with sample raw data. This epic covers building a local pipeline that takes it from raw ingestion to queryable gold tables — the dataset the agent will ultimately reason over.

**Key Outcomes**
- Raw data lands in a defined storage layer
- Airflow 3 orchestrates the pipeline end-to-end
- dbt (or a transformation engine the team chooses) transforms raw to silver and silver to gold
- Gold tables are queryable in ClickHouse

**Data Quality**
- The team must define and enforce at least 3 data quality rules on the gold layer — what these are is up to them to research and decide (e.g. freshness, completeness, uniqueness, referential integrity)
- Quality results must be visible somewhere (logs, a UI, or a table) — not just silent pass/fail

**Deliverable**: Running local pipeline. Raw → gold transformation working. At least 3 data quality checks passing and results visible.

---

### Epic 2 — Metadata Store

This epic covers selecting, deploying, and wiring a metadata store so that schema, lineage, and discoverability are available as a service for the agent to call.

**Key Outcomes**
- A metadata store is running locally
- Schema and column metadata from dbt models is available via API
- Lineage from source through transformation to gold is visible
- Pipeline run history (freshness) is queryable
- The agent can call a metadata API to answer "what tables exist, what are their columns, when was this last updated, where did this data come from?"

**Research Questions**
- Which metadata store best fits this stack — DataHub, OpenMetadata, or another option? Evaluate on: connector support for Airflow 3 and dbt, local deployment simplicity, API quality for programmatic access.
- How does dbt expose lineage and schema metadata (manifest.json, catalog.json)? How does the chosen metadata store ingest this?
- How does Airflow 3 emit lineage events? What integration does the metadata store provide?
- What API or SDK does the metadata store expose for the agent to query at runtime?

**Deliverable**: Metadata store running locally, connected to dbt and Airflow. Schema, lineage, and freshness queryable via API. Team documents the chosen tool and why.

---

## Sprint 2 — Agent & Visualization (Week 3–4)

---

### Epic 3 — Observation Service & Agent Core

The Observation Service is the backend API layer sitting between the UI and all data sources. It owns the agent loop, executes queries, and serves results — the UI never queries data directly. The agent inside the service fetches metadata at runtime before generating any query.

**Key Outcomes**
- Observation Service exposes clear API contracts: `/chat`, `/query`, `/schema`
- Agent calls the metadata store before generating any query — schema knowledge is never hardcoded in prompts
- Agent generates valid ClickHouse SQL from natural language
- Agent routes queries to the correct data source based on question type (ClickHouse tables may span multiple schemas or serve different purposes — e.g. raw telemetry vs gold business tables)
- Agent self-corrects on SQL errors: feeds the DB error message and failed SQL back to the LLM, retries up to 2 times, and enforces a query timeout before giving up
- All requests are logged (user input, generated SQL, execution time, row count, token usage)

**Research Questions**
- Which agent framework should be used — LangGraph, Claude Agent SDK, LlamaIndex, raw function calling, or something else? What are the trade-offs?
- How should schema context be injected into the LLM prompt? What is the right granularity — full schema dump vs targeted table lookup per question?
- How should the agent decide which tables are relevant to a given question?
- What is the right structure for the error correction loop — what context should be fed back, how many retries, when to give up gracefully?
- How should the Observation Service be structured so that adding new data sources later (e.g. a second ClickHouse cluster or a live API) requires minimal changes?

**Deliverable**: Observation Service running locally with `/chat` endpoint. Agent answering at least 10 varied natural language questions correctly against the gold layer using live metadata context.

---

### Epic 4 — Visualization & Dashboard Generation

The agent returns results as visual outputs through a chat interface. For structured queries it produces tables or charts. For open-ended requests it auto-generates a multi-panel dashboard layout. Standard pre-built dashboards are embedded alongside the chat for reference.

**Key Outcomes**
- Query results are rendered as tables, bar charts, line charts, or pie charts — chart type inferred from result shape
- User can ask "show me a dashboard of X" and the agent generates a multi-panel layout automatically
- Agent responses stream progressively to the UI — user sees output as it arrives rather than waiting for the full response (SSE or equivalent)
- Pre-built dashboards (e.g. Metabase) are embedded in the UI alongside the chat panel for standard views
- Visualizations and chat are accessible from a single interface

**Research Questions**
- How should chart type be inferred from the result set — column count, data types, query intent?
- What visualization library or BI tool is most appropriate for rendering agent-generated dashboards? Options to explore: Chart.js, Apache ECharts, Metabase embedding, Evidence, or others.
- How should a "dashboard" be represented — a static layout the agent defines, an embedded BI tool, or a dynamic component tree?
- What is the right streaming approach for the chat interface — SSE, WebSocket, or polling? What are the trade-offs for a local POC?
- How should pre-built Metabase dashboards and agent-generated visualizations coexist in the same UI without feeling disconnected?

**Deliverable**: Chat interface with streaming responses rendering tables and charts. User can request a dashboard and receive a multi-visualization layout. At least one pre-built dashboard embedded alongside the chat.

---

## Demo Day

At the end of Sprint 2, the team runs a live demo covering:

1. **Pipeline walkthrough** — show raw data flowing through Airflow + dbt to gold tables in ClickHouse, with quality checks passing
2. **Metadata exploration** — show lineage from source to gold and schema discovery in the metadata store UI
3. **Simple query** — ask a plain lookup question, agent fetches schema, generates SQL, returns a table
4. **Aggregation query** — ask a GROUP BY or time-series question, agent returns a chart
5. **Dashboard generation** — ask "show me a dashboard of X", agent generates a multi-panel layout
6. **Error correction** — demonstrate the agent recovering from a bad SQL generation without user intervention
7. **Pre-built dashboard** — show an embedded Metabase (or equivalent) dashboard in the same UI

---

## Extra(s)

- Production / cloud deployment
- Authentication and multi-tenancy
- Security hardening *(team is expected to think about this independently)*

---

## Definition of Done

A user types a business question into the chat interface. The Observation Service agent fetches schema and lineage from the metadata store, generates a ClickHouse query, self-corrects if needed, and returns a streamed response as a table, chart, or auto-generated dashboard — all without the user writing any code or SQL.