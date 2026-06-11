"""
DataHub Metadata Client — wraps DataHub GraphQL API for the Observation Service.

Answers runtime metadata questions for the Agentic BI agent:
  1. list_tables()                          → What tables exist?
  2. get_columns(dataset_urn)               → What are their columns?
  3. get_freshness(dataset_urn)             → When was this last updated?
  4. get_lineage(dataset_urn)               → Where did this data come from?
  5. get_schema_context_for_question(q)     → Build schema context for LLM prompt
"""

from __future__ import annotations

import os
from typing import Any

import httpx


DATAHUB_GMS_URL = os.getenv("DATAHUB_GMS_URL", "http://localhost:28080")
DATAHUB_TOKEN = os.getenv("DATAHUB_TOKEN", "")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _graphql_request(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against DataHub GMS."""
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
    """Convert epoch milliseconds to ISO-8601 string."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------

class DataHubClient:
    """Thin wrapper around DataHub GraphQL API."""

    # ---- 1. list_tables ----

    def list_tables(
        self,
        platform: str = "clickhouse",
        schema: str = "vsf_mart",
    ) -> list[dict]:
        """
        List all datasets for a given platform and schema.

        Returns:
            [{"urn": "...", "name": "fct_llm_interactions",
              "platform": "clickhouse", "description": "..."}, ...]
        """
        query = """
        query listDatasets($input: SearchInput!) {
          search(input: $input) {
            searchResults {
              entity {
                urn
                ... on Dataset {
                  name
                  platform { name }
                  editableProperties { description }
                  properties { description }
                }
              }
            }
          }
        }
        """
        variables = {
            "input": {
                "type": "DATASET",
                "query": f"platform:{platform} AND schema:{schema}",
                "start": 0,
                "count": 100,
            }
        }
        data = _graphql_request(query, variables)
        results = []
        for item in data.get("search", {}).get("searchResults", []):
            entity = item.get("entity", {})
            desc = (
                (entity.get("editableProperties") or {}).get("description")
                or (entity.get("properties") or {}).get("description", "")
            )
            results.append(
                {
                    "urn": entity["urn"],
                    "name": entity.get("name", ""),
                    "platform": (entity.get("platform") or {}).get("name", ""),
                    "description": desc,
                }
            )
        return results

    # ---- 2. get_columns ----

    def get_columns(self, dataset_urn: str) -> list[dict]:
        """
        Get column schema for a dataset.

        Returns:
            [{"name": "interaction_id", "type": "STRING",
              "description": "Primary key", "nullable": False}, ...]
        """
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
        fields = (
            data.get("dataset", {}).get("schemaMetadata", {}).get("fields", [])
        )
        return [
            {
                "name": f["fieldPath"].split(".")[-1],
                "type": f.get("type", "UNKNOWN"),
                "description": f.get("description", ""),
                "nullable": f.get("nullable", True),
            }
            for f in fields
        ]

    # ---- 3. get_freshness ----

    def get_freshness(self, dataset_urn: str) -> dict:
        """
        Get last ingestion/update time for a dataset.

        Returns:
            {"last_updated_ms": 1749301234000,
             "last_updated_iso": "2026-06-07T10:00:34+00:00"}
        """
        query = """
        query getFreshness($urn: String!) {
          dataset(urn: $urn) {
            lastIngested
          }
        }
        """
        data = _graphql_request(query, {"urn": dataset_urn})
        dataset = data.get("dataset", {})
        last_ingested = dataset.get("lastIngested")
        return {
            "last_updated_ms": last_ingested,
            "last_updated_iso": (
                _ms_to_iso(last_ingested) if last_ingested else None
            ),
        }

    # ---- 4. get_lineage ----

    def get_lineage(self, dataset_urn: str, depth: int = 3) -> dict:
        """
        Get upstream lineage for a dataset.

        Returns:
            {"entity": "fct_llm_interactions",
             "upstream": ["stg_user_interactions", "stg_signoz_logs"]}
        """
        query = """
        query getLineage(
          $urn: String!,
          $direction: LineageDirection!,
          $depth: Int!
        ) {
          searchAcrossLineage(
            input: {
              urn: $urn
              direction: $direction
              count: 50
              start: 0
              maxHops: $depth
            }
          ) {
            searchResults {
              entity {
                urn
                ... on Dataset { name }
              }
            }
          }
        }
        """
        data = _graphql_request(
            query,
            {"urn": dataset_urn, "direction": "UPSTREAM", "depth": depth},
        )
        results = (
            data.get("searchAcrossLineage", {}).get("searchResults", [])
        )
        names = [
            r["entity"].get("name", "")
            for r in results
            if r.get("entity")
        ]
        table_name = (
            dataset_urn.split(",")[1].split(".")[-1]
            if "," in dataset_urn
            else dataset_urn
        )
        return {"entity": table_name, "upstream": names}

    # ---- 5. get_schema_context_for_question ----

    def get_schema_context_for_question(
        self,
        question: str,
        platform: str = "clickhouse",
        schema: str = "vsf_mart",
    ) -> str:
        """
        Build a formatted schema context string for the most relevant
        tables given a natural language question.

        Used by the Agent (Epic 3) for prompt injection.
        """
        tables = self.list_tables(platform=platform, schema=schema)

        # Keyword-based relevance scoring
        # (upgrade to embedding similarity in a future iteration)
        question_lower = question.lower()
        keywords = set(question_lower.replace("?", "").split())
        scored: list[tuple[int, dict]] = []
        for t in tables:
            name_words = set(t["name"].replace("_", " ").split())
            desc_words = set((t.get("description") or "").lower().split())
            score = len(keywords & (name_words | desc_words))
            scored.append((score, t))

        scored.sort(key=lambda x: -x[0])
        relevant = [t for _, t in scored[:5]] or tables[:3]

        context_parts: list[str] = []
        for table in relevant:
            cols = self.get_columns(table["urn"])
            col_list = ", ".join(
                f"{c['name']} ({c['type']})" for c in cols
            )
            context_parts.append(
                f"Table: {table['name']}\n"
                f"Description: {table['description']}\n"
                f"Columns: {col_list}"
            )

        return "\n\n".join(context_parts)
