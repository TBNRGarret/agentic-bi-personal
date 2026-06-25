# C4 Agent Workflow

## C1 - System Context

- User asks a natural-language BI or platform-health question.
- Observation Service owns the agent loop and never asks the UI to pull raw data.
- DataHub is the runtime metadata API for schemas, lineage, and freshness.
- Airflow orchestrates the MinIO-to-ClickHouse data pipeline.
- ClickHouse stores raw, staging, and mart analytics tables.

## C2 - Containers

| Container | Responsibility |
| --- | --- |
| Web UI | Chat, clarification choices, result preview, and workflow graph. |
| Observation Service | Guardrails, agent planning, DataHub lookup, SQL generation, audit logging. |
| DataHub GMS | Metadata API for tables, columns, lineage, freshness, and partition properties. |
| MinIO | Raw JSONL batch landing zone and manifest store. |
| Airflow | `agentic_bi_pipeline` orchestration: ingest, dbt build, data quality gate. |
| ClickHouse | `vsf_raw`, `vsf_stg`, and `vsf_mart` queryable data. |
| dbt | Raw-to-silver-to-gold transformations and model documentation. |

## C3 - Agent Components

| Component | Responsibility |
| --- | --- |
| Clarification guardrail | Checks missing/low-confidence slots and asks a follow-up before routing. |
| Agent planner | Selects chart, insight, forecast, QA, partition, dashboard, or full-report agents. |
| Chart agent | Uses DataHub-backed table selection and guarded ClickHouse SQL. |
| QA agent | Checks ingestion health, failed batches, quarantined batches, and query audit status. |
| Partition agent | Detects partition strategy, pruning window, skew, empty partitions, and recommendations. |
| Summarizer | Produces the final grounded answer from specialist outputs. |

## C4 - Data Pipeline And Runtime Flow

1. Source APIs or DWH exports write raw JSONL batches and manifests to MinIO.
2. Airflow reads MinIO manifests, validates records, and loads `vsf_raw.*`.
3. Failed imports are recorded in `vsf_raw.raw_ingestion_batches`.
4. dbt transforms `vsf_raw` to `vsf_stg` and `vsf_mart`.
5. Data quality checks persist visible results and fail the DAG on critical issues.
6. DataHub ingests ClickHouse/dbt metadata for runtime agent discovery.
7. The agent calls DataHub first, then queries ClickHouse only for the selected result path.
8. The UI renders `workflowGraph` as a C4-lite agent workflow graph.
