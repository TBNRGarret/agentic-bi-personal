# Metadata Store Research Report

Researched on 2026-06-16 for the Agentic BI stack: Airflow 3, dbt, ClickHouse, MinIO, FastAPI Observation Service.

## Recommendation

Use DataHub as the metadata store for this project.

DataHub is the best fit because this repo already contains DataHub recipes, start scripts, ingestion scripts, and a thin GraphQL client, and the current DataHub docs line up well with the stack:

- Airflow: the DataHub Airflow plugin requires Airflow 3.0+ and Python 3.10+, captures DAGs, tasks, task runs, task status, ownership/tags, manual `inlets`/`outlets`, and native Airflow Assets/Datasets. It builds on Airflow's OpenLineage support and adds DataHub-specific SQL parsing.
- dbt: DataHub's dbt source ingests `manifest.json`, `catalog.json`, `sources.json`, and `run_results.json`, with table-level lineage, column-level lineage, test results, freshness, and sibling relationships to the underlying warehouse tables when warehouse ingestion also runs.
- ClickHouse: DataHub has a ClickHouse ingestion source that extracts tables, views, schema fields, containers, view lineage, column lineage for views, optional profiling, optional query log usage, and optional query-log lineage.
- Local deployment: `datahub docker quickstart` is a single-command local deployment path. The official quickstart states a tested local configuration of 2 CPUs, 8 GB RAM, 2 GB swap, and 12 GB disk, and this repo already wraps local/VPS startup through `metadata/scripts`.
- Programmatic API: DataHub exposes GraphQL for UI-like reads and search, plus Python/Java SDKs and REST/Kafka emitters for metadata writes. The existing `metadata/client/datahub_client.py` already uses GraphQL patterns the Observation Service can build on.

OpenMetadata is a credible alternative, but it fits this repo less cleanly. It has production connectors for ClickHouse and dbt, an Airflow connector, REST APIs, and SDKs, but the local Docker quickstart bundles OpenMetadata Server, Ingestion, Elasticsearch, a database, and an Airflow container. That overlaps with this project's own Airflow 3 runtime and makes the local stack more crowded. Its REST API is strong, but DataHub's GraphQL model is more ergonomic for runtime agent reads like search, schema, freshness, lineage, and UI-parity queries.

Marquez/OpenLineage is useful if the only goal is lineage event storage, but it is not a full schema/catalog API for an agent. dbt docs alone is too static. Amundsen/Stemma-style catalogs are weaker for this lineage-heavy Airflow/dbt workflow.

## Decision Matrix

| Criteria | DataHub | OpenMetadata | Marquez/OpenLineage |
| --- | --- | --- | --- |
| Airflow 3 support | Strong. Airflow plugin requires Airflow 3.0+ and Python 3.10+, captures DAG/task/run metadata and uses OpenLineage. | Good. Airflow connector supports officially supported Airflow versions and can ingest through REST API or metadata DB. | Strong for OpenLineage events, but not a full catalog. |
| dbt support | Strong. Ingests manifest, catalog, sources, run results; emits table/column lineage, tests, freshness, dbt-to-warehouse siblings. | Good. dbt connector is listed as production in OpenMetadata connector catalog. | Limited; dbt lineage needs separate event/artifact wiring and lacks rich catalog UX. |
| ClickHouse support | Strong enough for this stack. Certified source; schema, views, containers, lineage, usage/profiling options. | Good. ClickHouse connector listed as production. | Not a schema/catalog solution. |
| Local deployment | `datahub docker quickstart`; repo already has scripts and recipes. Heavier than a single DB, but already integrated. | Straightforward Docker Compose, but includes an ingestion Airflow service and can conflict with this repo's Airflow. | Usually simpler, but incomplete for agent metadata needs. |
| Runtime API for agent | GraphQL is good for search/read patterns; SDKs/emitters for writes. Existing repo client already uses GraphQL. | REST + Python/Java/Go SDKs are solid and typed. | Event API is lineage-first, not table/schema-first. |
| Best fit | Recommended. | Good if the team prefers REST-first catalog APIs and OpenMetadata UX. | Use only as an OpenLineage backend sidecar, not the main store. |

## How dbt Exposes Metadata

dbt emits machine-readable artifacts in the project's `target/` directory.

`manifest.json` is produced by dbt commands that parse the project. It contains a full representation of dbt resources: models, tests, sources, macros, docs, node configuration, resource properties, `parent_map`, and `child_map`. For lineage, the critical pieces are `nodes`, `sources`, `parent_map`, `child_map`, `ref`, and `source` relationships.

`catalog.json` is produced by `dbt docs generate`. It contains warehouse-derived metadata for dbt models, seeds, snapshots, and sources: database/schema/name, relation type, comments, owner, columns, data types, column comments, ordinal index, and database-specific statistics. This is the primary artifact for column schema and table statistics.

`sources.json` is produced by `dbt source freshness`. It contains source freshness check results, including each source node's `max_loaded_at`, `snapshotted_at`, freshness criteria, status, and execution metadata.

`run_results.json` is produced by commands such as `dbt build`, `dbt run`, `dbt test`, and `dbt docs generate`. It contains executed node results, status, timing, adapter response, compiled status/code, and relation name. For this stack, it is useful for model/test run history and quality visibility.

Recommended artifact workflow for this repo:

```bash
dbt source freshness --project-dir include/dbt_project --profiles-dir include/dbt_project
dbt build --project-dir include/dbt_project --profiles-dir include/dbt_project
cp include/dbt_project/target/run_results.json /tmp/run_results.json
dbt docs generate --project-dir include/dbt_project --profiles-dir include/dbt_project
cp /tmp/run_results.json include/dbt_project/target/run_results.json
datahub ingest -c metadata/recipes/dbt_recipe.yml
datahub ingest -c metadata/recipes/clickhouse_recipe.yml
```

The copy step matters because `dbt docs generate` can replace `run_results.json`; the report should preserve the build/test result for DataHub ingestion.

## How DataHub Ingests dbt Metadata

DataHub's dbt source reads:

- `manifest_path`: model/source/test definitions and lineage.
- `catalog_path`: schema and table statistics from the warehouse.
- `sources_path`: source freshness information.
- `run_results_paths`: dbt execution and test results.
- `target_platform`: the warehouse platform, here `clickhouse`.
- `target_platform_instance`: the concrete warehouse instance, here the repo already uses `agentic_clickhouse`.

For this repo, `metadata/recipes/dbt_recipe.yml` already points to:

- `include/dbt_project/target/manifest.json`
- `include/dbt_project/target/catalog.json`
- `include/dbt_project/target/sources.json`
- `include/dbt_project/target/run_results.json`
- target platform `clickhouse`
- platform instance `agentic_clickhouse`

Run both dbt and ClickHouse ingestion. DataHub explicitly recommends ingesting both dbt and the target warehouse so it can build lineage between dbt logical nodes and executable warehouse datasets, and create sibling relationships between dbt entities and target platform tables/views. For the agent, this matters because the agent should execute ClickHouse SQL against ClickHouse URNs, while still reading dbt descriptions, tests, and lineage.

## How Airflow 3 Emits Lineage Events

Airflow 3 has two relevant lineage surfaces:

1. Native Airflow lineage collection through `HookLineageCollector`.
   Airflow 3 documentation describes this as experimental. Hooks can add input/output assets and extra metadata. If no `HookLineageReader` plugin is registered, Airflow uses a no-op collector, so no meaningful lineage is collected.

2. OpenLineage provider.
   The `apache-airflow-providers-openlineage` provider sends OpenLineage events to a configured backend. The docs state no DAG file changes are required once the provider and transport are configured. It collects task input/output metadata, task run-level metadata, task job-level metadata, and operator-specific metadata where supported.

For DataHub, the recommended path is the DataHub Airflow plugin:

- Install `acryl-datahub-airflow-plugin` in the Airflow environment.
- Add an Airflow connection of type `datahub-rest` pointing to DataHub GMS.
- Keep `[datahub] enabled = True`.
- Let the plugin capture Airflow DAGs, tasks, task runs, success/failure statuses, owners, tags, `inlets`/`outlets`, native Airflow Assets, and OpenLineage-derived lineage.

The DataHub plugin's docs say it builds on Airflow's built-in OpenLineage support and supports `SQLExecuteQueryOperator` and common SQL operator subclasses. For custom operators, either inherit from `SQLExecuteQueryOperator`, implement the OpenLineage interface, or set `inlets` and `outlets` manually.

Practical guidance for this repo:

- Short term: keep the current dbt and ClickHouse ingestion path, plus the repo's freshness publishing script, because it is already present and avoids expanding the Airflow dependency tree.
- Sprint 2 target: add the DataHub Airflow plugin when the Airflow image can tolerate the dependency tree. Then annotate non-SQL tasks with DataHub `Dataset`/`Urn` entities or Airflow Assets so the MinIO/raw/dbt/ClickHouse flow is visible end to end.
- Avoid duplicate lineage emission: the DataHub plugin has a `disable_openlineage_plugin` setting intended to avoid duplicate processing. Decide whether DataHub's plugin or a separate OpenLineage transport owns emission, not both.

## Runtime API for the Agent

Use DataHub GraphQL for runtime reads and the Python SDK/REST emitter for runtime writes only if needed.

Recommended runtime reads:

- Search candidate datasets: GraphQL `search` over `DATASET` with filters for platform `clickhouse`, schema/domain, and platform instance.
- Fetch schema: `dataset(urn) { schemaMetadata { fields { fieldPath type description nullable } } }`.
- Fetch descriptions and properties: `editableProperties`, `properties`, and `customProperties`.
- Fetch freshness: `lastIngested`, plus repo-published custom properties such as `last_data_updated_at`, `latest_successful_dag_run`, and `latest_dq_status`.
- Fetch lineage: `searchAcrossLineage` for upstream/downstream context.
- Fetch quality/test context: read assertion/test aspects from DataHub if dbt `run_results.json` ingestion is enabled; expose a small normalized quality summary to the agent rather than dumping raw assertion payloads.

The existing `metadata/client/datahub_client.py` already implements the start of this:

- `list_tables(schema="vsf_mart")`
- `get_columns(dataset_urn)`
- `get_freshness(dataset_urn)`
- `get_lineage(dataset_urn)`
- `get_schema_context_for_question(question)`

Recommended next changes:

- Move the Observation Service `/schema` endpoint from direct ClickHouse introspection to DataHub-backed metadata, with ClickHouse introspection as a fallback.
- Add a `MetadataProvider` interface so the service is not hardwired to DataHub forever.
- Cache DataHub reads for a short TTL, for example 60-300 seconds, because metadata changes slower than chat requests.
- Keep DataHub credentials in `DATAHUB_GMS_URL` and `DATAHUB_TOKEN`. Do not hardcode VPS IPs or passwords in recipes; use environment-specific recipe templates or env substitution.

## Proposed Agent Metadata Contract

The agent should never receive a full raw catalog dump by default. It should call a metadata API that returns compact "table cards" and expanded schema only for selected candidates.

Suggested response shape:

```json
{
  "tables": [
    {
      "urn": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)",
      "database": "vsf_mart",
      "name": "fct_llm_interactions",
      "description": "Gold fact table for LLM interactions.",
      "freshness": {
        "last_data_updated_at": "2026-06-16T00:00:00Z",
        "latest_dq_status": "pass"
      },
      "columns": [
        {"name": "event_date", "type": "Date", "description": "Interaction date"},
        {"name": "total_tokens", "type": "UInt64", "description": "Total tokens"}
      ],
      "upstream": ["vsf_stg.staging_llm_generations"],
      "quality": {"status": "pass", "latest_run_at": "2026-06-16T00:05:00Z"}
    }
  ]
}
```

## Implementation Plan

1. Keep DataHub as the selected store and preserve `metadata/recipes/dbt_recipe.yml` and `metadata/recipes/clickhouse_recipe.yml`.
2. Parameterize recipes for local vs VPS values instead of committing host/password values.
3. Make the ingestion script run dbt freshness, dbt build, preserve run results, docs generate, dbt ingestion, and ClickHouse ingestion in that order.
4. Add DataHub-backed `/schema` in the Observation Service using `DataHubClient`.
5. Add a metadata contract test: "what tables exist", "what columns exist", "when was table last updated", and "where did this table come from".
6. Add DataHub Airflow plugin in a separate task once dependency impact is tested in the Airflow image.

## Sources

- DataHub dbt ingestion: https://docs.datahub.com/docs/generated/ingestion/sources/dbt
- DataHub Airflow integration: https://docs.datahub.com/docs/lineage/airflow
- DataHub ClickHouse ingestion: https://docs.datahub.com/docs/generated/ingestion/sources/clickhouse
- DataHub quickstart: https://docs.datahub.com/docs/quickstart
- DataHub APIs and SDKs: https://docs.datahub.com/docs/api/datahub-apis
- DataHub Python emitter/SDK: https://docs.datahub.com/docs/metadata-ingestion/as-a-library
- dbt manifest artifact: https://docs.getdbt.com/reference/artifacts/manifest-json
- dbt catalog artifact: https://docs.getdbt.com/reference/artifacts/catalog-json
- dbt run results artifact: https://docs.getdbt.com/reference/artifacts/run-results-json
- dbt sources artifact: https://docs.getdbt.com/reference/artifacts/sources-json
- Airflow 3 lineage docs: https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/lineage.html
- Airflow OpenLineage provider docs: https://airflow.apache.org/docs/apache-airflow-providers-openlineage/stable/guides/user.html
- OpenMetadata local Docker deployment: https://docs.open-metadata.org/latest/quick-start/local-docker-deployment
- OpenMetadata connectors catalog: https://docs.open-metadata.org/v1.12.x/connectors
- OpenMetadata Airflow connector: https://docs.open-metadata.org/latest/connectors/pipeline/airflow
- OpenMetadata API reference: https://docs.open-metadata.org/v1.12.x/api-reference
