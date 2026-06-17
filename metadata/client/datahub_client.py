"""
DataHub Metadata Client for the Agentic BI Observation Service.

The client returns executable ClickHouse datasets while using DataHub as the
runtime metadata source for tables, columns, freshness, and lineage.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


DATAHUB_GMS_URL = os.getenv("DATAHUB_GMS_URL", "http://47.131.228.222:28080")
DATAHUB_TOKEN = os.getenv("DATAHUB_TOKEN", "")
DEFAULT_PLATFORM_INSTANCE = os.getenv("DATAHUB_CLICKHOUSE_PLATFORM_INSTANCE", "agentic_clickhouse")


def _graphql_request(query: str, variables: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if DATAHUB_TOKEN:
        headers["Authorization"] = f"Bearer {DATAHUB_TOKEN}"

    resp = httpx.post(
        f"{DATAHUB_GMS_URL}/api/graphql",
        json={"query": query, "variables": variables or {}},
        headers=headers,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"DataHub GraphQL error: {data['errors']}")
    return data.get("data", {})


def _ms_to_iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _description(entity: dict[str, Any]) -> str:
    return (
        (entity.get("editableProperties") or {}).get("description")
        or (entity.get("properties") or {}).get("description")
        or ""
    )


def _dataset_identifier(urn: str) -> str:
    if "," not in urn:
        return urn
    return urn.split(",", 2)[1]


def _custom_properties(properties: dict[str, Any] | None) -> dict[str, str]:
    if not properties:
        return {}
    output: dict[str, str] = {}
    for item in properties.get("customProperties") or []:
        key = item.get("key")
        if key:
            output[key] = item.get("value", "")
    return output


class DataHubClient:
    """Thin wrapper around DataHub GraphQL API."""

    def _search_datasets(self, query_text: str, count: int = 100) -> list[dict[str, Any]]:
        query = """
        query searchDatasets($input: SearchInput!) {
          search(input: $input) {
            searchResults {
              entity {
                urn
                ... on Dataset {
                  name
                  platform { name }
                  editableProperties { description }
                  properties { description customProperties { key value } }
                }
              }
            }
          }
        }
        """
        data = _graphql_request(
            query,
            {"input": {"type": "DATASET", "query": query_text, "start": 0, "count": count}},
        )
        return [item.get("entity", {}) for item in data.get("search", {}).get("searchResults", [])]

    def _dbt_descriptions(self, schema: str) -> dict[str, str]:
        descriptions: dict[str, str] = {}
        for entity in self._search_datasets(f"dbt {schema}"):
            platform = (entity.get("platform") or {}).get("name", "")
            if platform != "dbt":
                continue
            identifier = _dataset_identifier(entity.get("urn", ""))
            if not identifier.startswith(f"{schema}."):
                continue
            desc = _description(entity)
            if desc:
                descriptions[entity.get("name", "")] = desc
        return descriptions

    def list_tables(
        self,
        platform: str = "clickhouse",
        schema: str = "vsf_mart",
        platform_instance: str = DEFAULT_PLATFORM_INSTANCE,
    ) -> list[dict[str, Any]]:
        """List executable physical datasets for a ClickHouse schema."""
        dbt_descriptions = self._dbt_descriptions(schema)
        expected_prefix = f"{platform_instance}.{schema}."
        results = []
        for entity in self._search_datasets(f"{platform} {schema}"):
            urn = entity.get("urn", "")
            identifier = _dataset_identifier(urn)
            platform_name = (entity.get("platform") or {}).get("name", "")
            if platform_name != platform or not identifier.startswith(expected_prefix):
                continue
            name = entity.get("name", "").split(".")[-1]
            results.append(
                {
                    "urn": urn,
                    "name": name,
                    "platform": platform_name,
                    "schema": schema,
                    "platform_instance": platform_instance,
                    "description": _description(entity) or dbt_descriptions.get(name, ""),
                }
            )
        return sorted(results, key=lambda item: item["name"])

    def get_columns(self, dataset_urn: str) -> list[dict[str, Any]]:
        """Get column schema for a dataset."""
        query = """
        query getSchema($urn: String!) {
          dataset(urn: $urn) {
            schemaMetadata {
              fields {
                fieldPath
                type
                description
                nullable
              }
            }
          }
        }
        """
        data = _graphql_request(query, {"urn": dataset_urn})
        fields = data.get("dataset", {}).get("schemaMetadata", {}).get("fields", []) or []
        columns = []
        for field in fields:
            field_type = field.get("type", "UNKNOWN")
            if isinstance(field_type, dict):
                field_type = field_type.get("type") or field_type.get("name") or "UNKNOWN"
            columns.append(
                {
                    "name": field.get("fieldPath", "").split(".")[-1],
                    "type": field_type,
                    "description": field.get("description") or "",
                    "nullable": field.get("nullable", True),
                }
            )
        return columns

    def get_freshness(self, dataset_urn: str) -> dict[str, Any]:
        """Get DataHub metadata ingestion time plus any published data freshness properties."""
        query = """
        query getFreshness($urn: String!) {
          dataset(urn: $urn) {
            lastIngested
            properties { customProperties { key value } }
          }
        }
        """
        data = _graphql_request(query, {"urn": dataset_urn})
        dataset = data.get("dataset", {}) or {}
        last_ingested = dataset.get("lastIngested")
        custom = _custom_properties(dataset.get("properties"))
        return {
            "last_updated_ms": last_ingested,
            "last_updated_iso": _ms_to_iso(last_ingested) if last_ingested else None,
            "last_data_updated_at": custom.get("last_data_updated_at"),
            "latest_successful_dag_run": custom.get("latest_successful_dag_run"),
            "latest_dq_status": custom.get("latest_dq_status"),
        }

    def get_lineage(self, dataset_urn: str, depth: int = 3) -> dict[str, Any]:
        """Get upstream lineage for a dataset."""
        query = """
        query getLineage($urn: String!, $direction: LineageDirection!) {
          searchAcrossLineage(
            input: {
              urn: $urn
              direction: $direction
              count: 50
              start: 0
            }
          ) {
            searchResults {
              entity {
                urn
                ... on Dataset { name platform { name } }
              }
            }
          }
        }
        """
        data = _graphql_request(query, {"urn": dataset_urn, "direction": "UPSTREAM"})
        results = data.get("searchAcrossLineage", {}).get("searchResults", []) or []
        upstream_entities = []
        for result in results:
            entity = result.get("entity") or {}
            if not entity.get("urn"):
                continue
            upstream_entities.append(
                {
                    "urn": entity.get("urn"),
                    "name": entity.get("name", ""),
                    "platform": (entity.get("platform") or {}).get("name", ""),
                }
            )
        table_name = dataset_urn.split(",")[1].split(".")[-1] if "," in dataset_urn else dataset_urn
        return {
            "entity": table_name,
            "upstream": [item["name"] for item in upstream_entities],
            "upstream_entities": upstream_entities,
            "requested_depth": depth,
        }

    def get_schema_context_for_question(
        self,
        question: str,
        platform: str = "clickhouse",
        schema: str = "vsf_mart",
    ) -> str:
        """Build a compact schema context string for the most relevant tables."""
        tables = self.list_tables(platform=platform, schema=schema)
        question_lower = question.lower()
        keywords = set(question_lower.replace("?", "").split())
        scored: list[tuple[int, dict[str, Any]]] = []
        for table in tables:
            name_words = set(table["name"].replace("_", " ").split())
            desc_words = set((table.get("description") or "").lower().split())
            scored.append((len(keywords & (name_words | desc_words)), table))
        scored.sort(key=lambda item: -item[0])
        relevant = [table for _, table in scored[:5]] or tables[:3]

        context_parts = []
        for table in relevant:
            cols = self.get_columns(table["urn"])
            col_list = ", ".join(f"{col['name']} ({col['type']})" for col in cols)
            context_parts.append(
                f"Table: {table['name']}\n"
                f"Description: {table['description']}\n"
                f"Columns: {col_list}"
            )
        return "\n\n".join(context_parts)
