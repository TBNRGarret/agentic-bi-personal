# Agentic BI Project Guide

## What This Repo Is

Agentic BI is a local-first BI system that keeps one operational data path:
MinIO raw landing -> Airflow orchestration -> ClickHouse raw/silver/gold ->
DataHub metadata -> FastAPI observation service -> React UI and Metabase.

The repo is intentionally split into two concerns:
- data foundation and metadata workflows
- observation / query / dashboard workflows

Anything that does not support those paths directly has been removed or kept
only if it still carries operational value.

## Canonical Runtime Paths

### Start / Verify
- `scripts/start-system.ps1`
- `scripts/start-system.sh`
- `scripts/verify-system.ps1`
- `scripts/verify_system.py`

### Core orchestration
- `dags/agentic_bi_pipeline.py`
- `dags/smoke_dag.py`
- `dags/export_postgres_to_minio.py`
- `dags/generate_data_stream_dag.py`

### Metadata
- `metadata/mcp_server.py`
- `metadata/mcp_service.py`
- `metadata/client/datahub_client.py`
- `metadata/scripts/start-datahub.ps1`
- `metadata/scripts/start-datahub.sh`
- `metadata/scripts/run-ingestion.ps1`
- `metadata/scripts/run-ingestion.sh`

### Observation service
- `src/backend/app/main.py`
- `src/backend/app/routers/query.py`
- `src/backend/app/routers/dashboard.py`
- `src/backend/app/routers/sessions.py`
- `src/backend/app/terminal.py`

### UI
- `src/frontend/src/App.tsx`
- `src/frontend/src/components/Dashboard/*`
- `src/frontend/src/components/Auth/*`

## Single Environment Model

Use one root `.env` file copied from `.env.example`.

The shared env covers:
- MinIO
- PostgreSQL DWH
- ClickHouse
- Observation service
- Auth
- LLM providers
- Telemetry / Langfuse / SigNoz
- Vite frontend env variables
- DataHub MCP defaults
- DataHub VPS stack variables

If a workflow needs environment values, read them from `.env` or from the
runtime environment injected by Docker Compose / Astro / the shell script.
Do not create new per-surface env templates unless a future workflow truly
needs a separate secret boundary.

## Local Runbook

### Normal local stack
1. Copy `.env.example` to `.env` and adjust secrets if needed.
2. Run `scripts/start-system.ps1` or `scripts/start-system.sh`.
3. Seed / export data with `scripts/seed_dwh.py`, `scripts/export_dwh_to_minio.py`,
   and `metadata/scripts/run-ingestion.*` as needed.
4. Verify with `scripts/verify-system.ps1` or `scripts/verify_system.py`.

### DataHub-only stack
1. Copy `.env.example` to `.env`.
2. Run `metadata/scripts/start-datahub.ps1` or `metadata/scripts/start-datahub.sh`.
3. Run `metadata/scripts/run-ingestion.ps1` or `metadata/scripts/run-ingestion.sh`.

### Observation service UI stack
- `docker compose -f docker-compose.agentic-ui.yml up -d --build`

## Operational Ports

| Service | Local URL |
|---|---|
| Observation Service | `http://localhost:18000` |
| ClickHouse HTTP | `http://localhost:18123` |
| MinIO API | `http://localhost:19001` |
| MinIO Console | `http://localhost:19002` |
| Metabase | `http://localhost:3000` |
| FalkorDB Browser | `http://localhost:3004` |
| DataHub GMS | `http://localhost:28080` |
| DataHub UI | `http://localhost:9002` |

## Data Foundation Contract

The pipeline must preserve these layers:
- `vsf_raw`: landing/audit/raw telemetry
- `vsf_stg`: cleaned staging / silver
- `vsf_mart`: gold marts and quality results

The main DAG still performs:
1. manifest validation
2. JSONL ingest from MinIO to ClickHouse raw
3. transform to silver and gold
4. data quality gates
5. metadata ingestion for schema, lineage, and freshness

## Observation Service Contract

The backend API must keep these operational entrypoints:
- `POST /chat`
- `POST /terminal/run`
- `POST /query`
- `GET /schema`
- `POST /api/query/chat`
- `POST /api/query/chat/stream`
- `POST /api/query/dashboard`
- `GET /api/dashboard/overview`
- `GET /api/dashboard/data-quality`

The service should continue to:
- fetch metadata before query generation
- execute read-only ClickHouse SQL
- apply row security / safety guards
- stream results where the UI expects streaming
- log request metadata and telemetry when configured

## Metadata Contract

DataHub is the metadata source of truth for:
- table discovery
- schema and columns
- lineage
- freshness

The MCP service exposes a bounded contract for agent and IDE usage:
- `health`
- `search_tables`
- `get_table_schema`
- `list_schema_fields`
- `get_lineage`
- `get_freshness`
- `get_table_context_for_question`

The payloads should stay compact and stable; avoid exposing huge raw metadata
objects in tool responses.

## UI Contract

The UI should keep the operational surfaces that already exist:
- overview
- alerts
- data quality
- AI console
- Metabase embeds
- graph database browser

The AI console is a shell that groups the real panels. It should not duplicate
behavior that already lives in the child panels.

## What Was Removed

- daily reports moved out of the active repo path
- per-surface env templates were collapsed into one root `.env.example`
- redundant docs were consolidated into this guide
- `.benchmarks` is not present in this workspace

## When To Update This File

Update this guide only when one of these changes:
- a runtime path changes
- a public API contract changes
- a port changes
- a workflow is added or removed
- the single-env model gains a truly separate security boundary
