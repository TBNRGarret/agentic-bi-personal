from __future__ import annotations

from collections import Counter
from math import log
import re
from time import monotonic
from typing import Any
import unicodedata

import httpx

from ..config import Settings
from .clickhouse import ClickHouseConnector


class DataHubConnector:
    """Read server metadata from DataHub with a live ClickHouse fallback."""

    source_name = "datahub"

    def __init__(self, settings: Settings, clickhouse: ClickHouseConnector) -> None:
        self.settings = settings
        self.clickhouse = clickhouse
        self._cache: list[dict[str, Any]] = []
        self._cache_until = 0.0
        self._last_source = "clickhouse_fallback"
        self._last_error: str | None = None

    async def health(self) -> dict[str, Any]:
        tables = await self.list_tables()
        return {
            "status": "up" if tables else "degraded",
            "source": self._last_source,
            "tableCount": len(tables),
            "url": self.settings.datahub_gms_url,
            "error": self._last_error,
        }

    async def list_tables(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and self._cache and monotonic() < self._cache_until:
            return self._cache
        try:
            tables = await self._datahub_tables()
            if tables:
                live_tables = await self.clickhouse.describe_tables(
                    tuple(self.settings.clickhouse_analytics_schema_list)
                )
                merged = {table["tableName"]: table for table in live_tables}
                merged.update({table["tableName"]: table for table in tables})
                tables = sorted(merged.values(), key=lambda table: table["tableName"])
                self._cache = tables
                self._cache_until = monotonic() + 30
                self._last_source = "datahub"
                self._last_error = None
                return tables
            self._last_error = "DataHub returned no ClickHouse datasets"
        except Exception as exc:
            self._last_error = str(exc)[:300]

        schemas = tuple(self.settings.clickhouse_analytics_schema_list)
        tables = await self.clickhouse.describe_tables(schemas)
        self._cache = tables
        self._cache_until = monotonic() + 10
        self._last_source = "clickhouse_fallback"
        return tables

    async def relevant_tables(
        self, question: str, limit: int = 4
    ) -> list[dict[str, Any]]:
        tables = await self.list_tables()
        if not tables:
            return []

        query_terms = _terms(question)
        documents = [_table_document(table) for table in tables]
        document_terms = [Counter(_terms(document)) for document in documents]
        average_length = max(
            1.0,
            sum(sum(counter.values()) for counter in document_terms)
            / len(document_terms),
        )
        document_frequency = {
            term: sum(1 for counter in document_terms if term in counter)
            for term in query_terms
        }
        question_concepts = _semantic_concepts(question)
        scored: list[tuple[float, dict[str, Any], list[str]]] = []
        for table, term_counts in zip(tables, document_terms):
            score = _bm25_score(
                query_terms,
                term_counts,
                document_frequency,
                len(tables),
                average_length,
            )
            reasons: list[str] = []
            table_name = _normalize(str(table.get("tableName") or ""))
            column_names = {
                _normalize(str(column.get("name") or ""))
                for column in table.get("columns") or []
            }
            exact_name_hits = sum(term in table_name for term in query_terms)
            exact_column_hits = sum(
                any(term in column for column in column_names)
                for term in query_terms
            )
            if exact_name_hits:
                score += exact_name_hits * 2.5
                reasons.append(f"{exact_name_hits} table-name match(es)")
            if exact_column_hits:
                score += exact_column_hits * 1.75
                reasons.append(f"{exact_column_hits} column match(es)")

            table_concepts = _table_concepts(table)
            concept_hits = sorted(question_concepts & table_concepts)
            if concept_hits:
                score += len(concept_hits) * 5
                reasons.append("semantic: " + ", ".join(concept_hits))
            if str(table.get("database")) in {"gold", "vsf_mart"}:
                score += 1.5
                reasons.append("curated analytics table")
            scored.append((score, table, reasons or ["catalog relevance"]))

        scored.sort(
            key=lambda item: (item[0], item[1].get("tableName", "")),
            reverse=True,
        )
        top_score = scored[0][0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        ranked: list[dict[str, Any]] = []
        for position, (score, table, reasons) in enumerate(scored[:limit]):
            relative = score / max(top_score, 1.0) if score > 0 else 0.0
            confidence = min(1.0, max(0.0, relative * 0.75))
            if position == 0 and score > 0:
                margin = max(0.0, score - second_score) / max(score, 1.0)
                confidence = min(1.0, confidence + margin * 0.25)
            ranked.append(
                {
                    **table,
                    "retrievalScore": round(score, 4),
                    "retrievalConfidence": round(confidence, 4),
                    "retrievalReasons": reasons,
                }
            )
        return ranked

    async def _datahub_tables(self) -> list[dict[str, Any]]:
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
                  properties { description }
                  schemaMetadata {
                    fields { fieldPath type description nullable }
                  }
                }
              }
            }
          }
        }
        """
        headers = {"Content-Type": "application/json"}
        if self.settings.datahub_token:
            headers["Authorization"] = f"Bearer {self.settings.datahub_token}"
        timeout = httpx.Timeout(self.settings.datahub_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.settings.datahub_gms_url.rstrip('/')}/api/graphql",
                headers=headers,
                json={
                    "query": query,
                    "variables": {
                        "input": {
                            "type": "DATASET",
                            "query": "clickhouse",
                            "start": 0,
                            "count": 200,
                        }
                    },
                },
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"]))
        results = (
            payload.get("data", {})
            .get("search", {})
            .get("searchResults", [])
        )
        allowed = set(self.settings.clickhouse_analytics_schema_list)
        tables: list[dict[str, Any]] = []
        for item in results:
            entity = item.get("entity") or {}
            platform = str((entity.get("platform") or {}).get("name") or "")
            if platform.lower() != "clickhouse":
                continue
            identifier = _dataset_identifier(str(entity.get("urn") or ""))
            schema, name = _schema_and_name(identifier, allowed)
            if not schema or not name:
                continue
            fields = ((entity.get("schemaMetadata") or {}).get("fields") or [])
            columns = []
            for field in fields:
                field_type = field.get("type") or "UNKNOWN"
                if isinstance(field_type, dict):
                    field_type = field_type.get("type") or field_type.get("name") or "UNKNOWN"
                columns.append(
                    {
                        "name": str(field.get("fieldPath") or "").split(".")[-1],
                        "type": str(field_type),
                        "description": field.get("description") or "",
                    }
                )
            tables.append(
                {
                    "urn": entity.get("urn"),
                    "tableName": f"{schema}.{name}",
                    "database": schema,
                    "name": name,
                    "datasource": "clickhouse",
                    "description": (
                        (entity.get("editableProperties") or {}).get("description")
                        or (entity.get("properties") or {}).get("description")
                        or ""
                    ),
                    "columns": columns,
                }
            )
        if tables and any(not table["columns"] for table in tables):
            live = {
                table["tableName"]: table
                for table in await self.clickhouse.describe_tables(tuple(allowed))
            }
            for table in tables:
                if not table["columns"] and table["tableName"] in live:
                    table["columns"] = live[table["tableName"]]["columns"]
        return sorted(tables, key=lambda table: table["tableName"])


def _dataset_identifier(urn: str) -> str:
    if "," not in urn:
        return urn
    return urn.split(",", 2)[1]


def _schema_and_name(identifier: str, allowed: set[str]) -> tuple[str, str]:
    parts = [part for part in identifier.split(".") if part]
    for index, part in enumerate(parts[:-1]):
        if part in allowed:
            return part, parts[index + 1]
    return "", ""



def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    ascii_value = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    compact = re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()
    replacements = {
        "chi phi": "cost",
        "do tre": "latency",
        "loi": "error",
        "nguon": "source",
        "dich vu": "service",
        "mo hinh": "model",
        "xu huong": "trend",
    }
    for source, target in replacements.items():
        compact = compact.replace(source, target)
    return compact


_STOP_TERMS = {
    "a",
    "an",
    "and",
    "by",
    "cho",
    "cua",
    "for",
    "from",
    "in",
    "la",
    "of",
    "show",
    "the",
    "to",
    "va",
    "voi",
}

_CONCEPT_ALIASES = {
    "cost": {"cost", "price", "spend", "usd"},
    "error": {"error", "failed", "failure", "status"},
    "ingestion": {"batch", "ingestion", "loaded", "quarantine"},
    "latency": {"duration", "latency", "p95", "response time", "slow"},
    "model": {"llm", "model", "provider"},
    "service": {"service", "application", "app"},
    "source": {"environment", "owner", "source"},
    "time": {"date", "day", "daily", "event time", "time", "trend", "week"},
    "tokens": {"completion tokens", "prompt tokens", "token", "tokens"},
    "trace": {"span", "trace", "tracing"},
}


def _terms(value: str) -> list[str]:
    return [
        term
        for term in _normalize(value).split()
        if len(term) > 1 and term not in _STOP_TERMS
    ]


def _table_document(table: dict[str, Any]) -> str:
    columns = table.get("columns") or []
    return " ".join(
        [
            str(table.get("tableName") or ""),
            str(table.get("description") or ""),
            " ".join(str(tag) for tag in table.get("tags") or []),
            " ".join(str(owner) for owner in table.get("owners") or []),
            " ".join(
                " ".join(
                    [
                        str(column.get("name") or ""),
                        str(column.get("description") or ""),
                        str(column.get("type") or ""),
                    ]
                )
                for column in columns
            ),
        ]
    )


def _bm25_score(
    query_terms: list[str],
    document_terms: Counter[str],
    document_frequency: dict[str, int],
    document_count: int,
    average_length: float,
) -> float:
    score = 0.0
    document_length = max(1, sum(document_terms.values()))
    for term in set(query_terms):
        frequency = document_terms.get(term, 0)
        if not frequency:
            continue
        frequency_in_documents = document_frequency.get(term, 0)
        inverse_frequency = log(
            1
            + (document_count - frequency_in_documents + 0.5)
            / (frequency_in_documents + 0.5)
        )
        denominator = frequency + 1.2 * (
            0.25 + 0.75 * document_length / average_length
        )
        score += inverse_frequency * frequency * 2.2 / denominator
    return score


def _semantic_concepts(value: str) -> set[str]:
    normalized = _normalize(value)
    normalized_terms = set(normalized.split())

    def matches(alias: str) -> bool:
        return alias in normalized if " " in alias else alias in normalized_terms

    return {
        concept
        for concept, aliases in _CONCEPT_ALIASES.items()
        if any(matches(alias) for alias in aliases)
    }


def _table_concepts(table: dict[str, Any]) -> set[str]:
    return _semantic_concepts(_table_document(table))
