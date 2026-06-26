from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ..table_selector import TableSelector

if TYPE_CHECKING:
    from ...connectors.clickhouse import ClickHouseConnector
    from ...connectors.datahub import DataHubConnector
    from ...connectors.postgres import PostgresConnector
from .contracts import SchemaRetrievalResult
from .utils import column_names, normalize_text, table_name


class SchemaRetriever:
    """QueryWeaver-style schema retrieval over Agentic BI governed metadata."""

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
                "strategy": "datahub_keyword_embedding_graph_expansion",
                "candidateCount": len(candidates),
                "selectedCount": len(selected_tables),
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
    allowed = {"gold", "silver", "vsf_mart", "vsf_stg"}
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
