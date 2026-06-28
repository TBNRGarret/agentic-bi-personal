from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...connectors.datahub_lineage import fetch_lineage
from ...connectors.falkordb import FalkorGraphConnector
from ..table_selector import TableSelector

if TYPE_CHECKING:
    from ...connectors.clickhouse import ClickHouseConnector
    from ...connectors.datahub import DataHubConnector
    from ...connectors.postgres import PostgresConnector
from .contracts import SchemaRetrievalResult
from .graph_retriever import GraphSchemaRetriever
from .schema_graph import SchemaGraph
from .utils import column_names, normalize_text, table_name


class SchemaRetriever:
    """QueryWeaver-style schema retrieval over Agentic BI governed metadata."""

    """
    Overview
    1. Natural language question + target datasource
    2. Route datasource: dwh -> Postgres; clickhouse -> DataHub/ClickHouse
    3. Metadata retrieval hierarchy
        a. Try DataHub catalog search (relevant_tables) -> enrich with schema fields
        b. Try FalkorDB graph query -> expand with lineage/bridge tables
        c. Fallback to TableSelector keyword+embedding ranking
        d. Fallback to ClickHouse/PG describe_tables
    4. Memory graph construction
        a. Build SchemaGraph from metadata + lineage edges
        b. Graph expansion: FK, SHARED_COLUMN, LINEAGE edges
        c. Find join paths between selected tables
    5. Return SchemaRetrievalResult with tables, graph_edges, join_paths

    Multiple retrieval strategies with fallback chain.
    """

    def __init__(
        self,
        settings: Settings,
        clickhouse: ClickHouseConnector,
        metadata: DataHubConnector,
        postgres: PostgresConnector | None = None,
    ) -> None:
        self.settings = settings
        self.clickhouse = clickhouse
        self.metadata = metadata
        self.postgres = postgres
        self.table_selector = TableSelector(settings)
        self.falkor_graph = FalkorGraphConnector(settings)
        self.graph_retriever = GraphSchemaRetriever()

    async def retrieve(
        self,
        question: str,
        *,
        target: str,
        request_plan: dict[str, Any],
        min_tables: int = 2,
        max_tables: int = 5,
    ) -> SchemaRetrievalResult:
        datasource = route_datasource(question, target)
        metadata_source = "datahub"
        warnings: list[str] = []

        if datasource == "dwh" and self.postgres is not None:
            raw_tables = await self.postgres.describe_tables(("ops",))
            metadata_source = "postgres_schema"
        else:
            try:
                raw_tables = await self.metadata.list_tables()
                metadata_source = str(getattr(self.metadata, "source_name", "datahub"))
            except Exception as exc:
                warnings.append(f"DataHub metadata failed: {str(exc)[:180]}")
                raw_tables = []
            if not raw_tables:
                raw_tables = await self.clickhouse.describe_tables(
                    tuple(self.settings.clickhouse_analytics_schema_list)
                )
                metadata_source = "clickhouse_fallback"
                datasource = "clickhouse"

        candidates = tables_for_datasource(raw_tables, datasource)
        if not candidates and datasource == "clickhouse":
            candidates = await self.clickhouse.describe_tables(
                tuple(self.settings.clickhouse_analytics_schema_list)
            )
            metadata_source = "clickhouse_fallback"

        catalog_result = await self._retrieve_from_datahub_catalog(
            question,
            datasource=datasource,
            metadata_source=metadata_source,
            candidates=candidates,
            request_plan=request_plan,
            min_tables=min_tables,
            max_tables=max_tables,
            warnings=warnings,
        )
        if catalog_result is not None:
            return catalog_result

        graph_result = await self._retrieve_from_falkordb(
            question,
            datasource=datasource,
            metadata_source=metadata_source,
            candidates=candidates,
            request_plan=request_plan,
            min_tables=min_tables,
            max_tables=max_tables,
            warnings=warnings,
        )
        if graph_result is not None:
            return graph_result

        ranked_tables: list[dict[str, Any]] = []
        selected_tables: list[dict[str, Any]] = []
        table_selection: dict[str, Any] | None = None
        if candidates:
            selection = await self.table_selector.select(
                question,
                candidates,
                datasource=datasource,
                min_tables=min_tables,
                max_tables=max_tables,
            )
            selected_tables = selection.tables
            ranked_tables = [item["table"] for item in selection.ranked_tables]
            table_selection = selection.to_dict()

        if not selected_tables:
            selected_tables = candidates[:max_tables]
        if not ranked_tables:
            ranked_tables = selected_tables

        lineage_edges = await self._fetch_datahub_lineage(candidates, warnings)
        graph_result = self._retrieve_from_memory_graph(
            question,
            datasource=datasource,
            metadata_source=metadata_source,
            candidates=candidates,
            selected_tables=selected_tables,
            ranked_tables=ranked_tables,
            lineage_edges=lineage_edges,
            request_plan=request_plan,
            max_tables=max_tables,
        )
        if graph_result is not None:
            graph_result.table_selection = table_selection or graph_result.table_selection
            graph_result.warnings = warnings
            return graph_result

        graph_edges = _graph_edges(selected_tables)
        return SchemaRetrievalResult(
            datasource=datasource,
            metadata_source=metadata_source,
            database_description=_database_description(datasource, selected_tables),
            tables=selected_tables,
            ranked_tables=ranked_tables,
            graph_edges=graph_edges,
            table_selection=table_selection,
            retrieval={
                "strategy": "metadata_table_selector_fallback",
                "candidateCount": len(candidates),
                "selectedCount": len(selected_tables),
                "requestPlan": request_plan,
            },
            warnings=warnings,
        )


    async def _retrieve_from_datahub_catalog(
        self,
        question: str,
        *,
        datasource: str,
        metadata_source: str,
        candidates: list[dict[str, Any]],
        request_plan: dict[str, Any],
        min_tables: int,
        max_tables: int,
        warnings: list[str],
    ) -> SchemaRetrievalResult | None:
        if datasource != "clickhouse" or metadata_source == "clickhouse_fallback":
            return None
        search_tables = getattr(self.metadata, "relevant_tables", None)
        if not callable(search_tables):
            return None
        try:
            searched = await search_tables(question, limit=max(max_tables * 3, min_tables))
        except Exception as exc:
            warnings.append(f"DataHub catalog search failed: {str(exc)[:180]}")
            return None

        ranked_tables = tables_for_datasource(searched, datasource)
        if not ranked_tables:
            return None

        inspected_tables = await self._inspect_schema_fields(ranked_tables, candidates, warnings)
        selected_tables = inspected_tables[: max(min_tables, min(max_tables, len(inspected_tables)))]
        if not selected_tables:
            return None

        lineage_edges = await self._fetch_datahub_lineage(candidates or inspected_tables, warnings)
        graph_result = self._retrieve_from_memory_graph(
            question,
            datasource=datasource,
            metadata_source=f"{metadata_source}+datahub_catalog",
            candidates=candidates or inspected_tables,
            selected_tables=selected_tables,
            ranked_tables=inspected_tables,
            lineage_edges=lineage_edges,
            request_plan=request_plan,
            max_tables=max_tables,
        )
        retrieval_context = {
            "strategy": "datahub_mcp_catalog_selector",
            "workflow": "data_discovery_query_construction",
            "searchTool": "datahub.search_tables",
            "schemaInspectionTool": "datahub.list_schema_fields",
            "candidateCount": len(candidates),
            "catalogCandidateCount": len(ranked_tables),
            "selectedCount": len(selected_tables),
            "inspectedDatasets": [table_name(table) for table in inspected_tables],
            "requestPlan": request_plan,
        }
        table_selection = {
            "strategy": "datahub_mcp_catalog_selector",
            "primaryTable": table_name(selected_tables[0]) if selected_tables else None,
            "selectedTables": [table_name(table) for table in selected_tables],
            "rankedTables": [table_name(table) for table in inspected_tables],
            "confidence": float(selected_tables[0].get("retrievalConfidence") or 0.0)
            if selected_tables
            else 0.0,
        }
        if graph_result is not None:
            graph_result.metadata_source = f"{metadata_source}+datahub_catalog+memory_graph"
            graph_result.table_selection = table_selection
            graph_result.warnings = warnings
            graph_result.retrieval = {
                **retrieval_context,
                "graphStrategy": graph_result.retrieval.get("strategy"),
                "bridgeTables": graph_result.retrieval.get("bridgeTables", []),
                "joinPathCount": len(graph_result.join_paths),
                "graphEdgeCount": len(graph_result.graph_edges),
                "lineageEdgeCount": len(lineage_edges),
            }
            return graph_result

        return SchemaRetrievalResult(
            datasource=datasource,
            metadata_source=f"{metadata_source}+datahub_catalog",
            database_description=_database_description(datasource, selected_tables),
            tables=selected_tables,
            ranked_tables=inspected_tables,
            graph_edges=_graph_edges(selected_tables),
            table_selection=table_selection,
            retrieval=retrieval_context,
            warnings=warnings,
        )

    async def _inspect_schema_fields(
        self,
        ranked_tables: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        by_name = {table_name(table): table for table in candidates}
        inspect_fields = getattr(self.metadata, "list_schema_fields", None)
        inspected: list[dict[str, Any]] = []
        for table in ranked_tables:
            name = table_name(table)
            merged = {**by_name.get(name, {}), **table}
            if not merged.get("columns") and callable(inspect_fields):
                try:
                    merged["columns"] = await inspect_fields(str(merged.get("urn") or name))
                except Exception as exc:
                    warnings.append(
                        f"DataHub schema field inspection failed for {name}: {str(exc)[:120]}"
                    )
            inspected.append(merged)
        return inspected

    async def _fetch_datahub_lineage(
        self,
        candidates: list[dict[str, Any]],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        if not candidates or not self.settings.datahub_gms_url:
            return []
        datahub_tables = [table for table in candidates if table.get("urn")]
        if not datahub_tables:
            return []
        try:
            return await fetch_lineage(
                self.settings.datahub_gms_url,
                self.settings.datahub_token,
                datahub_tables,
                timeout_seconds=min(self.settings.datahub_timeout_seconds, 5.0),
            )
        except Exception as exc:
            warnings.append(f"DataHub lineage retrieval failed: {str(exc)[:180]}")
            return []

    def _retrieve_from_memory_graph(
        self,
        question: str,
        *,
        datasource: str,
        metadata_source: str,
        candidates: list[dict[str, Any]],
        selected_tables: list[dict[str, Any]],
        ranked_tables: list[dict[str, Any]],
        lineage_edges: list[dict[str, Any]],
        request_plan: dict[str, Any],
        max_tables: int,
    ) -> SchemaRetrievalResult | None:
        if not candidates or not selected_tables:
            return None
        schema_graph = SchemaGraph()
        schema_graph.build_from_metadata(candidates, lineage=lineage_edges)
        graph = self.graph_retriever.enhance(
            question,
            initial_tables=selected_tables,
            ranked_tables=ranked_tables or selected_tables,
            candidates=candidates,
            schema_graph=schema_graph,
            max_tables=max_tables,
        )
        if not graph.graph_edges and not graph.join_paths:
            return None
        graph_tables = graph.tables or selected_tables
        graph_ranked = graph.ranked_tables or ranked_tables or graph_tables
        return SchemaRetrievalResult(
            datasource=datasource,
            metadata_source=f"{metadata_source}+memory_graph",
            database_description=_database_description(datasource, graph_tables),
            tables=graph_tables,
            ranked_tables=graph_ranked,
            graph_edges=graph.graph_edges,
            join_paths=graph.join_paths,
            retrieval={
                "strategy": "memory_queryweaver_schema_graph",
                "candidateCount": len(candidates),
                "selectedCount": len(graph_tables),
                "bridgeTables": graph.bridge_tables,
                "joinPathCount": len(graph.join_paths),
                "graphEdgeCount": len(graph.graph_edges),
                "lineageEdgeCount": len(lineage_edges),
                "requestPlan": request_plan,
            },
        )

    async def _retrieve_from_falkordb(
        self,
        question: str,
        *,
        datasource: str,
        metadata_source: str,
        candidates: list[dict[str, Any]],
        request_plan: dict[str, Any],
        min_tables: int,
        max_tables: int,
        warnings: list[str],
    ) -> SchemaRetrievalResult | None:
        if not candidates:
            return None
        try:
            sync = await self.falkor_graph.sync_schema(
                candidates,
                datasource=datasource,
            )
            if sync.get("status") not in {"success", "cached"}:
                warnings.append(
                    "FalkorDB graph sync unavailable: "
                    f"{sync.get('reason') or sync.get('status')}"
                )
                return None
            graph_ranked = await self.falkor_graph.retrieve_tables(
                question,
                datasource=datasource,
                limit=max(max_tables * 3, min_tables),
            )
        except Exception as exc:
            warnings.append(f"FalkorDB graph retrieval failed: {str(exc)[:180]}")
            return None

        if not graph_ranked:
            warnings.append("FalkorDB graph returned no table candidates; using metadata fallback.")
            return None

        initial_tables = graph_ranked[: max(min_tables, min(max_tables, len(graph_ranked)))]
        schema_graph = SchemaGraph()
        schema_graph.build_from_metadata(graph_ranked)
        graph = self.graph_retriever.enhance(
            question,
            initial_tables=initial_tables,
            ranked_tables=graph_ranked,
            candidates=graph_ranked,
            schema_graph=schema_graph,
            max_tables=max_tables,
        )
        selected_tables = graph.tables or initial_tables
        ranked_tables = graph.ranked_tables or graph_ranked
        return SchemaRetrievalResult(
            datasource=datasource,
            metadata_source=f"{metadata_source}+falkordb",
            database_description=_database_description(datasource, selected_tables),
            tables=selected_tables,
            ranked_tables=ranked_tables,
            graph_edges=graph.graph_edges,
            join_paths=graph.join_paths,
            table_selection={
                "strategy": "falkordb_graph_rank",
                "primaryTable": table_name(selected_tables[0]) if selected_tables else None,
                "selectedTables": [table_name(table) for table in selected_tables],
                "rankedTables": [table_name(table) for table in ranked_tables],
                "confidence": float(selected_tables[0].get("retrievalConfidence") or 0)
                if selected_tables
                else 0.0,
            },
            retrieval={
                "strategy": "falkordb_queryweaver_schema_graph",
                "candidateCount": len(candidates),
                "graphCandidateCount": len(graph_ranked),
                "selectedCount": len(selected_tables),
                "bridgeTables": graph.bridge_tables,
                "joinPathCount": len(graph.join_paths),
                "graphEdgeCount": len(graph.graph_edges),
                "requestPlan": request_plan,
            },
            warnings=warnings,
        )


def route_datasource(question: str, target: str) -> str:
    requested = target.lower().strip()
    if requested in {"dwh", "postgres", "postgresql"}:
        return "dwh"
    if requested == "clickhouse":
        return "clickhouse"
    text = normalize_text(question)
    operational = (
        "query audit",
        "agent audit",
        "agent query",
        "agent queries",
        "agent request",
        "agent requests",
        "expensive agent",
        "costly agent",
        "truy van agent",
        "dbt run",
        "dbt test result",
        "ops table",
        "operational table",
        "dwh table",
        "dwh database",
        "postgres",
        "postgresql",
    )
    return "dwh" if any(term in text for term in operational) else "clickhouse"


def tables_for_datasource(
    tables: list[dict[str, Any]],
    datasource: str,
) -> list[dict[str, Any]]:
    if datasource == "dwh":
        return [
            table
            for table in tables
            if str(table.get("database") or table.get("schema") or "").lower()
            == "ops"
        ]
    allowed = {"vsf_mart"}
    return [
        table
        for table in tables
        if str(table.get("database") or table.get("schema") or "").lower()
        in allowed
    ]


def _database_description(datasource: str, tables: list[dict[str, Any]]) -> str:
    names = ", ".join(table_name(table) for table in tables[:5])
    if datasource == "dwh":
        return f"Agentic BI operational Postgres metadata and audit tables: {names}"
    return f"Agentic BI ClickHouse analytics warehouse tables: {names}"


def _graph_edges(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    by_name = {table_name(table): set(column_names(table)) for table in tables}
    names = list(by_name)
    for index, source in enumerate(names):
        source_columns = by_name[source]
        for target in names[index + 1 :]:
            shared = sorted(
                column
                for column in source_columns & by_name[target]
                if _looks_like_join_column(column)
            )
            if shared:
                edges.append(
                    {
                        "from": source,
                        "to": target,
                        "type": "SHARED_COLUMN",
                        "columns": shared[:4],
                    }
                )
    return edges


def _looks_like_join_column(column: str) -> bool:
    lowered = column.lower()
    return lowered.endswith("_id") or lowered in {
        "id",
        "run_id",
        "batch_id",
        "trace_id",
        "session_id",
        "user_id",
        "metric_date",
        "event_date",
    }
