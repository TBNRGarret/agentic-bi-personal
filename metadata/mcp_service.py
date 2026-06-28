"""Production-facing service layer for the DataHub MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from metadata.client import DataHubClient


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


DEFAULT_SCHEMA = os.getenv("DATAHUB_MCP_DEFAULT_SCHEMA", "vsf_mart")
DEFAULT_PLATFORM = os.getenv("DATAHUB_MCP_DEFAULT_PLATFORM", "clickhouse")
MAX_TABLES = _env_int("DATAHUB_MCP_MAX_TABLES", 5)
MAX_COLUMNS = _env_int("DATAHUB_MCP_MAX_COLUMNS", 80)


def _tokens(text: str) -> set[str]:
    normalized = text.lower().replace("?", " ").replace("_", " ")
    return {token for token in normalized.split() if token}


def _safe_limit(value: int | None, default: int, upper_bound: int = 25) -> int:
    if value is None:
        return default
    return max(1, min(value, upper_bound))


def _score_table(question: str, table: dict[str, Any]) -> int:
    question_tokens = _tokens(question)
    if not question_tokens:
        return 0
    table_tokens = _tokens(
        " ".join(
            [
                str(table.get("name", "")),
                str(table.get("description", "")),
                " ".join(tag.get("tagFQN", "") for tag in table.get("tags", [])),
            ]
        )
    )
    return len(question_tokens & table_tokens)


@dataclass(frozen=True)
class DataHubMCPService:
    """Small, stable contract for metadata tools exposed through MCP."""

    client: DataHubClient

    def health(self, schema: str = DEFAULT_SCHEMA, platform: str = DEFAULT_PLATFORM) -> dict[str, Any]:
        """Check whether DataHub can answer metadata requests."""
        tables = self.client.list_tables(platform=platform, schema=schema)
        return {
            "status": "ok",
            "platform": platform,
            "schema": schema,
            "table_count": len(tables),
        }

    def search_tables(
        self,
        question: str,
        schema: str = DEFAULT_SCHEMA,
        platform: str = DEFAULT_PLATFORM,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return compact table cards relevant to a natural-language question."""
        safe_limit = _safe_limit(limit, MAX_TABLES)
        tables = self.client.list_tables(platform=platform, schema=schema)
        ranked = sorted(tables, key=lambda table: (-_score_table(question, table), table.get("name", "")))
        selected = ranked[:safe_limit]
        return {
            "question": question,
            "platform": platform,
            "schema": schema,
            "tables": [self._table_card(table) for table in selected],
        }

    def get_table_schema(self, dataset_urn: str, include_freshness: bool = True) -> dict[str, Any]:
        """Return a bounded schema payload for one executable dataset."""
        all_columns = self.client.get_columns(dataset_urn)
        columns = all_columns[:MAX_COLUMNS]
        response: dict[str, Any] = {
            "urn": dataset_urn,
            "columns": columns,
            "column_count": len(all_columns),
            "truncated": len(all_columns) > MAX_COLUMNS,
        }
        if include_freshness:
            response["freshness"] = self.client.get_freshness(dataset_urn)
        return response

    def list_schema_fields(self, dataset_urn: str) -> dict[str, Any]:
        """Return bounded field metadata for a dataset.

        This is the MCP tool shape Agent 2 expects after catalog search:
        search_tables -> list_schema_fields -> SQL planning.
        """
        schema_payload = self.get_table_schema(dataset_urn, include_freshness=False)
        return {
            "urn": dataset_urn,
            "fields": schema_payload["columns"],
            "field_count": schema_payload["column_count"],
            "truncated": schema_payload["truncated"],
        }

    def get_lineage(self, dataset_urn: str, depth: int = 3) -> dict[str, Any]:
        """Return upstream lineage for one dataset."""
        safe_depth = _safe_limit(depth, 3, upper_bound=5)
        return self.client.get_lineage(dataset_urn, depth=safe_depth)

    def get_freshness(self, dataset_urn: str) -> dict[str, Any]:
        """Return metadata ingestion and published data freshness for one dataset."""
        return self.client.get_freshness(dataset_urn)

    def get_table_context_for_question(
        self,
        question: str,
        schema: str = DEFAULT_SCHEMA,
        platform: str = DEFAULT_PLATFORM,
        limit: int | None = None,
        include_lineage: bool = False,
    ) -> dict[str, Any]:
        """Return table cards plus bounded columns for SQL planning."""
        search = self.search_tables(question=question, schema=schema, platform=platform, limit=limit)
        enriched_tables = []
        for table in search["tables"]:
            schema_payload = self.get_table_schema(table["urn"], include_freshness=True)
            enriched = {
                **table,
                "columns": schema_payload["columns"],
                "column_count": schema_payload["column_count"],
                "freshness": schema_payload.get("freshness", {}),
            }
            if include_lineage:
                enriched["lineage"] = self.get_lineage(table["urn"])
            enriched_tables.append(enriched)
        return {
            "question": question,
            "platform": platform,
            "schema": schema,
            "tables": enriched_tables,
        }

    @staticmethod
    def _table_card(table: dict[str, Any]) -> dict[str, Any]:
        return {
            "urn": table.get("urn", ""),
            "name": table.get("name", ""),
            "platform": table.get("platform", ""),
            "schema": table.get("schema", ""),
            "platform_instance": table.get("platform_instance", ""),
            "description": table.get("description", ""),
            "tags": table.get("tags", []),
            "partition_info": table.get("partition_info"),
        }


def build_service() -> DataHubMCPService:
    return DataHubMCPService(client=DataHubClient())
