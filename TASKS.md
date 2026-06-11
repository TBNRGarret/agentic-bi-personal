# Agentic BI — Task Breakdown

Story-level breakdown of the [4-Week Sprint Plan](./README.md). Each epic is split into
user-story-sized tasks with acceptance criteria. Tasks are sized for a 4-engineer team.

**Legend**: `[ ]` not started · `[~]` in progress · `[x]` done
**Suggested labels**: `🟢 parallelizable` · `🔗 blocked-by` dependencies noted inline.

---

## Current Central DWH Hardening — 2026-06-10

- [x] Add local MinIO raw landing service and JSONL export contract
- [x] Add `vsf_raw`, `vsf_stg`, `vsf_mart` ClickHouse schema contract
- [x] Rework Airflow DAG code path from direct Postgres load to MinIO batch load
- [x] Add dbt seed/model contract for `source_apps`, `staging_llm_generations`, and `gold_app_daily_cost`
- [x] Update DataHub client/recipes to target `vsf_mart` and ingest `vsf_*` schemas
- [ ] Run live Docker/Airflow/dbt/DataHub end-to-end verification and attach final row-count evidence

---

## Sprint 1 — Data Foundation & Metadata (Week 1–2)

### Epic 1 — Sample Data Pipeline (Raw → Gold)

- [x] **S1.1 — Stand up local infrastructure** 🟢
  - **Assignee**: _______
  - Choose local Airflow 3 runtime: **Docker Compose** (hand-rolled) or **Astro CLI** (`astro dev`) — decide and document
  - Bring up Airflow 3 + ClickHouse (+ object/file storage for raw landing); wire ClickHouse into the chosen runtime
  - Services start cleanly, ClickHouse reachable, Airflow UI accessible
  - **AC**: One command (`docker compose up` or `astro dev start`) brings the full stack online; a smoke DAG runs end-to-end.

- [x] **S1.2 — Raw ingestion layer** 🔗 (needs S1.1)
  - **Assignee**: _______
  - Define the raw storage layer and land the provided sample data into it
  - Ingestion is orchestrated by an Airflow DAG (not a manual load)
  - **AC**: Raw data lands in the defined layer via an Airflow task; re-runnable / idempotent.

- [x] **S1.3 — dbt project: raw → silver → gold** 🔗 (needs S1.2)
  - **Assignee**: _______
  - Initialize dbt project against ClickHouse; build staging (silver) and mart (gold) models
  - Document sources and models in dbt
  - **AC**: Gold tables are queryable in ClickHouse; `dbt build` succeeds.

- [x] **S1.4 — Airflow ⇄ dbt orchestration** 🔗 (needs S1.3)
  - **Assignee**: _______
  - Airflow runs the dbt transformations as part of the end-to-end pipeline
  - Define schedule/trigger and task dependencies (raw → silver → gold)
  - **AC**: A single DAG run takes raw all the way to gold.

- [x] **S1.5 — Data quality checks on gold** 🔗 (needs S1.3)
  - **Assignee**: _______
  - Research and choose ≥3 quality rules (e.g. freshness, completeness, uniqueness, referential integrity)
  - Implement them (dbt tests or equivalent) and surface results (table/log/UI — not silent)
  - **AC**: ≥3 checks run and pass; results are visible somewhere queryable.

- [x] **S1.6 — Epic 1 deliverable verification**
  - **Assignee**: _______
  - End-to-end demo run: raw → gold with quality checks passing and visible
  - **AC**: Matches README deliverable for Epic 1.

### Epic 2 — Metadata Store

- [ ] **S2.1 — Evaluate & choose a metadata store**
  - **Assignee**: _______
  - Compare DataHub / OpenMetadata / alternatives on: Airflow 3 + dbt connector support, local deploy simplicity, API quality
  - **AC**: Short written decision doc with the chosen tool and rationale.

- [ ] **S2.2 — Deploy the metadata store locally** 🔗 (needs S2.1)
  - **Assignee**: _______
  - Add it to the local stack; bring it online reproducibly
  - **AC**: Metadata store UI + API reachable locally.

- [ ] **S2.3 — Ingest dbt schema & lineage** 🔗 (needs S2.2, S1.3)
  - **Assignee**: _______
  - Wire dbt `manifest.json` / `catalog.json` ingestion
  - **AC**: Schema and column metadata for gold models visible via the store's API.

- [ ] **S2.4 — Ingest Airflow lineage & run history** 🔗 (needs S2.2, S1.4)
  - **Assignee**: _______
  - Emit/ingest Airflow 3 lineage events and pipeline run/freshness history
  - **AC**: Source→transform→gold lineage visible; freshness/run history queryable.

- [ ] **S2.5 — Metadata API contract for the agent** 🔗 (needs S2.3, S2.4)
  - **Assignee**: _______
  - Document the query path the agent will use at runtime ("what tables, what columns, last updated, lineage")
  - **AC**: Documented API/SDK calls answering those four questions; example requests captured.

- [ ] **S2.6 — Epic 2 deliverable verification**
  - **Assignee**: _______
  - **AC**: Schema, lineage, freshness queryable via API; decision documented.

---

## Sprint 2 — Agent & Visualization (Week 3–4)

### Epic 3 — Observation Service & Agent Core

- [ ] **S3.1 — Choose agent framework & scaffold the service**
  - **Assignee**: _______
  - Evaluate LangGraph / Claude Agent SDK / LlamaIndex / raw function calling
  - Scaffold Observation Service with `/chat`, `/query`, `/schema` contracts (stubs OK)
  - **AC**: Service runs locally; endpoints respond; framework choice documented.

- [ ] **S3.2 — Runtime metadata fetch + schema injection** 🔗 (needs S3.1, S2.5)
  - **Assignee**: _______
  - Agent calls the metadata store before generating SQL (no hardcoded schema)
  - Decide injection granularity (targeted table lookup vs full dump) and table-relevance selection
  - **AC**: For a given question, agent fetches and uses live schema context.

- [ ] **S3.3 — NL → ClickHouse SQL generation** 🔗 (needs S3.2)
  - **Assignee**: _______
  - Generate valid ClickHouse SQL from natural language and execute via `/query`
  - **AC**: Simple lookup and aggregation questions return correct results.

- [ ] **S3.4 — Query routing across data sources** 🔗 (needs S3.3)
  - **Assignee**: _______
  - Route by question type (e.g. raw telemetry vs gold business tables / multiple schemas)
  - Structure for easy addition of future sources
  - **AC**: Questions hit the correct source/schema; adding a source is low-effort.

- [ ] **S3.5 — Self-correction & safety loop** 🔗 (needs S3.3)
  - **Assignee**: _______
  - Feed DB error + failed SQL back to the LLM, retry ≤2×, enforce query timeout, fail gracefully
  - **AC**: A deliberately bad generation recovers without user intervention.

- [ ] **S3.6 — Request logging** 🔗 (needs S3.3)
  - **Assignee**: _______
  - Log user input, generated SQL, execution time, row count, token usage
  - **AC**: Every request produces a structured log entry.

- [ ] **S3.7 — Epic 3 deliverable verification**
  - **Assignee**: _______
  - **AC**: ≥10 varied NL questions answered correctly against gold using live metadata.

### Epic 4 — Visualization & Dashboard Generation

- [ ] **S4.1 — Choose viz/BI approach** 🟢
  - **Assignee**: _______
  - Evaluate Chart.js / ECharts / Metabase embedding / Evidence; decide dashboard representation
  - **AC**: Documented choice for charts + dashboard model.

- [ ] **S4.2 — Chat UI shell + streaming** 🔗 (needs S3.1)
  - **Assignee**: _______
  - Single interface with chat panel; stream responses progressively (SSE or equivalent)
  - **AC**: Output appears incrementally as it arrives.

- [ ] **S4.3 — Result rendering with chart-type inference** 🔗 (needs S4.1, S3.3)
  - **Assignee**: _______
  - Render tables, bar/line/pie charts; infer type from result shape (column count, types, intent)
  - **AC**: Lookup → table; time-series/GROUP BY → appropriate chart.

- [ ] **S4.4 — Auto-generated multi-panel dashboards** 🔗 (needs S4.3)
  - **Assignee**: _______
  - "Show me a dashboard of X" → agent produces a multi-visualization layout
  - **AC**: A dashboard request returns a coherent multi-panel layout.

- [ ] **S4.5 — Embed a pre-built dashboard** 🔗 (needs S4.2)
  - **Assignee**: _______
  - Embed a Metabase (or equivalent) dashboard alongside the chat, integrated visually
  - **AC**: ≥1 pre-built dashboard embedded in the same UI.

- [ ] **S4.6 — Epic 4 deliverable verification**
  - **Assignee**: _______
  - **AC**: Streaming chat renders tables/charts; dashboard generation works; pre-built dashboard embedded.

---

## Demo Day Checklist

- [ ] Pipeline walkthrough (raw → gold, quality checks passing)
- [ ] Metadata exploration (lineage + schema discovery)
- [ ] Simple query → table
- [ ] Aggregation query → chart
- [ ] Dashboard generation → multi-panel layout
- [ ] Error correction recovery
- [ ] Pre-built dashboard embedded

---

## Cross-Cutting / Later (Extras)

- [ ] Production / cloud deployment
- [ ] Authentication & multi-tenancy
- [ ] Security hardening
