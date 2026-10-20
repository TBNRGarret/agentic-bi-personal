from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

import httpx

from ..config import Settings
from .semantics import (
    _CAPABILITY_COLUMNS,
    _STOP_TERMS,
    _asks_agent_cost,
    _asks_latency_root_cause,
    _asks_log_error_detail,
    _normalize,
    _question_capabilities,
    _semantic_domain,
    _semantic_domain_column_score,
)



@dataclass
class TableSelection:
    primary_table: dict[str, Any] | None
    tables: list[dict[str, Any]]
    ranked_tables: list[dict[str, Any]]
    confidence: float
    confidence_level: str
    strategy: str
    required_capabilities: list[str]
    semantic_used: bool = False
    embedding_error: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primaryTable": (
                self.primary_table.get("tableName") if self.primary_table else None
            ),
            "selectedTables": [
                str(table.get("tableName") or "") for table in self.tables
            ],
            "confidence": self.confidence,
            "confidenceLevel": self.confidence_level,
            "strategy": self.strategy,
            "requiredCapabilities": self.required_capabilities,
            "semanticEmbeddingUsed": self.semantic_used,
            "embeddingError": self.embedding_error,
            "candidates": self.candidates,
        }


@dataclass(frozen=True)
class SqlLinkProfile:
    capabilities: tuple[str, ...]
    required_groups: tuple[frozenset[str], ...]
    preferred_columns: frozenset[str]
    grain_columns: frozenset[str]
    operation: str


class TableSelector:
    """Narrow live metadata to the only tables the SQL planner may see."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.disabled_tables = set(settings.disabled_analytics_table_list)
        self._embedding_cache: dict[str, list[float]] = {}
        self._embedding_error_until = 0.0

    async def select(
        self,
        question: str,
        tables: list[dict[str, Any]],
        *,
        datasource: str,
        min_tables: int = 2,
        max_tables: int = 5,
    ) -> TableSelection:
        tables = [
            table
            for table in _candidate_layer_tables(tables, datasource, question)
            if _table_name(table) not in self.disabled_tables
        ]
        bounded_max = max(1, min(max_tables, 5))
        bounded_min = max(1, min(min_tables, bounded_max, len(tables) or 1))
        profile = _sql_link_profile(question, datasource)
        ranked = _rank_keyword_tables(question, tables, datasource, profile)
        capabilities = list(profile.capabilities)
        keyword_confidence = _selection_confidence(ranked)
        semantic_used = False
        embedding_error: str | None = None
        strategy = "sql_link_keyword_metadata_ranking"

        if ranked and self._should_use_embeddings(question, keyword_confidence):
            try:
                semantic_ranked = await self._rank_with_embeddings(
                    question,
                    ranked,
                    datasource=datasource,
                )
                if semantic_ranked:
                    ranked = semantic_ranked
                    semantic_used = True
                    strategy = "sql_link_keyword_embedding_hybrid"
            except Exception as exc:
                embedding_error = str(exc)[:200]
                self._embedding_error_until = monotonic() + 60

        selected_tables = _selected_table_set(
            ranked,
            min_tables=bounded_min,
            max_tables=bounded_max,
        )
        confidence = _selection_confidence(ranked)
        return TableSelection(
            primary_table=ranked[0]["table"] if ranked else None,
            tables=selected_tables,
            ranked_tables=ranked,
            confidence=confidence,
            confidence_level=_confidence_level(confidence),
            strategy=strategy,
            required_capabilities=capabilities,
            semantic_used=semantic_used,
            embedding_error=embedding_error,
            candidates=[
                {
                    "tableName": item["table"].get("tableName"),
                    "score": round(float(item.get("score") or 0), 4),
                    "keywordScore": round(float(item.get("keywordScore") or 0), 4),
                    "sqlLinkScore": round(float(item.get("sqlLinkScore") or 0), 4),
                    "sqlLinkOperation": profile.operation,
                    "embeddingSimilarity": item.get("embeddingSimilarity"),
                    "reasons": item.get("reasons") or [],
                    "retrievalScore": item["table"].get("retrievalScore"),
                    "retrievalConfidence": item["table"].get(
                        "retrievalConfidence"
                    ),
                }
                for item in ranked[:5]
            ],
        )

    def _should_use_embeddings(self, question: str, keyword_confidence: float) -> bool:
        if not self.settings.table_selector_embedding_enabled:
            return False
        if not self.settings.openai_api_key:
            return False
        if monotonic() < self._embedding_error_until:
            return False
        text = _normalize(question)
        vague_root_cause = any(
            term in text
            for term in (
                "why",
                "tai sao",
                "nguyen nhan",
                "root cause",
                "what is happening",
                "happening",
                "system slow",
                "he thong slow",
            )
        )
        return keyword_confidence < 0.72 or vague_root_cause

    async def _rank_with_embeddings(
        self,
        question: str,
        ranked: list[dict[str, Any]],
        *,
        datasource: str,
    ) -> list[dict[str, Any]]:
        question_vector = await self._embed_text(question)
        if not question_vector:
            return ranked
        table_vectors = await self._embed_many(
            [_table_document(item["table"], datasource) for item in ranked]
        )
        fused: list[dict[str, Any]] = []
        for item, vector in zip(ranked, table_vectors):
            similarity = _cosine_similarity(question_vector, vector)
            keyword_score = float(item.get("score") or 0)
            capability_score = _capability_score(
                item["table"],
                _question_capabilities(question),
            )
            layer_score = _layer_priority(item["table"], datasource)
            sql_link_score = float(item.get("sqlLinkScore") or 0)
            fused_score = (
                keyword_score * 0.40
                + max(0.0, similarity) * 80 * 0.25
                + sql_link_score * 0.20
                + capability_score * 0.10
                + layer_score * 0.05
            )
            fused.append(
                {
                    **item,
                    "score": fused_score,
                    "keywordScore": keyword_score,
                    "embeddingSimilarity": round(similarity, 4),
                    "reasons": [
                        *(item.get("reasons") or []),
                        f"embedding similarity {similarity:.2f}",
                    ],
                }
            )
        fused.sort(
            key=lambda item: (
                item["score"],
                str(item["table"].get("tableName") or ""),
            ),
            reverse=True,
        )
        return fused

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        uncached = [text for text in texts if text not in self._embedding_cache]
        if uncached:
            vectors = await self._request_embeddings(uncached)
            for text, vector in zip(uncached, vectors):
                self._embedding_cache[text] = vector
        return [self._embedding_cache.get(text, []) for text in texts]

    async def _embed_text(self, text: str) -> list[float]:
        return (await self._embed_many([text]))[0]

    async def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        timeout = httpx.Timeout(
            max(5.0, self.settings.table_selector_embedding_timeout_seconds),
            connect=5.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.settings.openai_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json={
                    "model": self.settings.openai_embedding_model,
                    "input": texts,
                },
            )
        response.raise_for_status()
        payload = response.json()
        by_index = sorted(payload.get("data") or [], key=lambda item: item["index"])
        return [
            [float(value) for value in item.get("embedding") or []]
            for item in by_index
        ]


def _selected_table_set(
    ranked: list[dict[str, Any]],
    *,
    min_tables: int,
    max_tables: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in ranked:
        if len(selected) >= max_tables:
            break
        table = item["table"]
        if table not in selected:
            selected.append(table)
    return selected[:max_tables] if len(selected) >= min_tables else selected


def _candidate_layer_tables(
    tables: list[dict[str, Any]], datasource: str, question: str = ""
) -> list[dict[str, Any]]:
    """Map server mart/staging schemas onto the curated/detail layer policy."""
    normalized = datasource.lower().strip()
    if normalized == "clickhouse":
        candidates = [
            table
            for table in tables
            if _schema_name(table) == "vsf_mart"
        ]
        text = _normalize(question)
        if any(term in text for term in ("gold", "mart", "tong hop", "aggregate")):
            curated = [
                table
                for table in candidates
                if _schema_name(table) == "vsf_mart"
            ]
            return curated or candidates
        if any(
            term in text
            for term in ("silver", "staging", "raw", "event detail", "trace detail")
        ) or _asks_log_error_detail(text):
            detail = [
                table
                for table in candidates
                if _schema_name(table) == "vsf_mart"
            ]
            return detail or candidates
        return candidates
    if normalized == "dwh":
        return [table for table in tables if _schema_name(table) == "ops"]
    return [table for table in tables if _schema_name(table) != "raw"]


def _schema_name(table: dict[str, Any]) -> str:
    return str(
        table.get("database")
        or table.get("schema")
        or table.get("databaseSchema")
        or ""
    ).lower()


def _table_name(table: dict[str, Any]) -> str:
    return str(table.get("tableName") or table.get("name") or "").strip().lower()


def _rank_keyword_tables(
    question: str,
    tables: list[dict[str, Any]],
    datasource: str,
    profile: SqlLinkProfile,
) -> list[dict[str, Any]]:
    text = _normalize(question)
    capabilities = list(profile.capabilities)
    query_terms = {
        term for term in text.split() if len(term) > 2 and term not in _STOP_TERMS
    }
    ranked: list[dict[str, Any]] = []
    for position, table in enumerate(tables):
        document = _table_document(table, datasource)
        doc_text = _normalize(document)
        columns = _table_column_keys(table)
        score = max(0.0, 20.0 - position)
        reasons: list[str] = []
        retrieval_score = float(table.get("retrievalScore") or 0)
        if retrieval_score:
            score += min(30.0, retrieval_score * 2)
            reasons.extend(str(reason) for reason in table.get("retrievalReasons") or [])
        lexical_hits = sum(term in doc_text for term in query_terms)
        if lexical_hits:
            score += lexical_hits * 3
            reasons.append(f"{lexical_hits} keyword match(es)")
        keyword_score = score
        sql_link_score, sql_link_reasons = _schema_link_score(columns, profile)
        score += sql_link_score
        reasons.extend(sql_link_reasons)
        for capability in capabilities:
            matched = sorted(_CAPABILITY_COLUMNS[capability] & columns)
            if matched:
                score += 18 if capability in {"latency", "error"} else 12
                reasons.append(f"{capability}: {', '.join(matched[:3])}")
            else:
                score -= 12 if capability in {"latency", "error"} else 5
        score += _domain_score(text, table, datasource, columns, reasons)
        ranked.append(
            {
                "table": table,
                "score": score,
                "keywordScore": keyword_score,
                "sqlLinkScore": sql_link_score,
                "reasons": reasons or ["metadata relevance order"],
            }
        )
    ranked.sort(
        key=lambda item: (
            item["score"],
            str(item["table"].get("tableName") or ""),
        ),
        reverse=True,
    )
    return ranked


def _sql_link_profile(question: str, datasource: str) -> SqlLinkProfile:
    text = _normalize(question)
    capabilities = tuple(_question_capabilities(question))
    required_groups: list[frozenset[str]] = []
    preferred_columns: set[str] = set()
    grain_columns: set[str] = set()
    operation = "lookup"

    if datasource == "dwh":
        if any(term in text for term in ("audit", "request", "query")):
            required_groups.append(frozenset({"query_id", "created_at"}))
            preferred_columns.update({"username", "request_type", "status"})
            operation = "ops_audit"
        return SqlLinkProfile(
            capabilities=capabilities,
            required_groups=tuple(required_groups),
            preferred_columns=frozenset(preferred_columns),
            grain_columns=frozenset(grain_columns),
            operation=operation,
        )

    if _asks_log_error_detail(text):
        required_groups.append(frozenset({"event_time", "severity", "message"}))
        preferred_columns.update(
            {"event_time", "source_app", "service_name", "severity", "message", "trace_id", "span_id"}
        )
        grain_columns.update({"source_app", "service_name"})
        operation = "log_error_detail"

    if any(
        term in text
        for term in ("raw", "record", "records", "detail", "event", "underlying")
    ):
        required_groups.append(
            frozenset({"event_time", "generation_status", "span_status"})
        )
        preferred_columns.update(
            {"event_time", "generation_status", "model", "source_app", "service_name"}
        )
        operation = "detail_lookup"

    if "latency" in capabilities:
        required_groups.append(frozenset(_CAPABILITY_COLUMNS["latency"]))
        operation = "latency_root_cause" if _asks_latency_root_cause(text) else "latency"
        preferred_columns.update({"avg_latency_ms", "p95_latency_ms", "duration_ms"})
        if _asks_latency_root_cause(text):
            grain_columns.update({"service_name", "source_app"})
            preferred_columns.update(
                {"service_name", "source_app", "error_span_count", "error_count"}
            )
    if "cost" in capabilities:
        required_groups.append(frozenset(_CAPABILITY_COLUMNS["cost"]))
        operation = "cost"
        preferred_columns.update({"total_cost_usd", "estimated_cost_usd", "cost_usd"})
    if "tokens" in capabilities:
        required_groups.append(frozenset(_CAPABILITY_COLUMNS["tokens"]))
        operation = "tokens" if operation == "lookup" else operation
        preferred_columns.update({"total_tokens", "prompt_tokens", "completion_tokens"})
    if "request_count" in capabilities:
        required_groups.append(frozenset(_CAPABILITY_COLUMNS["request_count"]))
        operation = "request_count" if operation == "lookup" else operation
        preferred_columns.update(
            {"query_count", "generation_count", "trace_count", "span_count"}
        )
    if "error" in capabilities:
        required_groups.append(frozenset(_CAPABILITY_COLUMNS["error"]))
        operation = "error" if operation == "lookup" else operation
        preferred_columns.update(
            {"error_count", "error_log_count", "error_span_count", "severity", "message", "span_status"}
        )
    if "ingestion" in capabilities:
        required_groups.append(frozenset(_CAPABILITY_COLUMNS["ingestion"]))
        operation = "ingestion"
        preferred_columns.update({"batch_count", "failed_batch_count"})

    if "time" in capabilities:
        grain_columns.update({"metric_date", "event_time"})
    if "model" in capabilities:
        grain_columns.add("model")
    if "service" in capabilities:
        grain_columns.add("service_name")
    if "source" in capabilities:
        grain_columns.add("source_app")

    return SqlLinkProfile(
        capabilities=capabilities,
        required_groups=tuple(required_groups),
        preferred_columns=frozenset(preferred_columns),
        grain_columns=frozenset(grain_columns),
        operation=operation,
    )


def _schema_link_score(
    columns: set[str], profile: SqlLinkProfile
) -> tuple[float, list[str]]:
    if not profile.required_groups and not profile.grain_columns:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    for group in profile.required_groups:
        matched = sorted(group & columns)
        if matched:
            score += 24.0
            reasons.append(f"SQL-link measure columns: {', '.join(matched[:3])}")
        else:
            score -= 36.0
            reasons.append("missing SQL-link measure column group")

    grain_matches = sorted(profile.grain_columns & columns)
    if grain_matches:
        score += min(18.0, len(grain_matches) * 6.0)
        reasons.append(f"SQL-link grain columns: {', '.join(grain_matches[:4])}")
    elif profile.grain_columns:
        score -= 10.0
        reasons.append("missing SQL-link grain/filter columns")

    preferred_matches = sorted(profile.preferred_columns & columns)
    if preferred_matches:
        score += min(18.0, len(preferred_matches) * 3.0)
        reasons.append(
            f"SQL-link preferred columns: {', '.join(preferred_matches[:4])}"
        )
    return score, reasons


def _domain_score(
    text: str,
    table: dict[str, Any],
    datasource: str,
    columns: set[str],
    reasons: list[str],
) -> float:
    name = str(table.get("tableName") or "").lower()
    schema = str(table.get("database") or "")
    score = 0.0
    if datasource == "clickhouse":
        if _asks_log_error_detail(text):
            if {"event_time", "severity", "message"} <= columns:
                score += 90
                reasons.append("log-error detail needs severity and message columns")
            else:
                score -= 35
                reasons.append("missing log-error detail columns")
            if schema == "vsf_mart":
                score -= 25
                reasons.append("aggregate layer cannot explain log messages")
        elif _asks_latency_root_cause(text):
            has_latency = bool(_CAPABILITY_COLUMNS["latency"] & columns)
            has_dimension = bool(
                {"service_name", "source_app", "source_app_id"} & columns
            )
            if has_latency and has_dimension:
                score += 80 if schema == "vsf_mart" else 55
                reasons.append("system slowness maps to observability latency")
            if "llm_usage" in name and not has_dimension:
                score -= 80
                reasons.append("LLM usage lacks service/source root-cause dimensions")
        elif schema == "vsf_mart":
            score += 10
            reasons.append("curated analytics table")
        score += _semantic_domain_column_score(text, columns, reasons)
    if datasource == "dwh":
        for terms, fragment in (
            (("audit", "request", "query"), "agent_query_audit"),
            (("dbt", "quality", "test result"), "dbt_run"),
        ):
            if fragment in name and any(term in text for term in terms):
                score += 50
                reasons.append(f"operational intent matches {fragment}")
    return score


def _selection_confidence(ranked: list[dict[str, Any]]) -> float:
    if not ranked:
        return 0.0
    top = float(ranked[0].get("score") or 0)
    second = float(ranked[1].get("score") or 0) if len(ranked) > 1 else 0.0
    margin = max(0.0, top - second) / max(abs(top), 1.0)
    retrieval = float(ranked[0]["table"].get("retrievalConfidence") or 0)
    embedding = float(ranked[0].get("embeddingSimilarity") or 0)
    return round(min(1.0, max(retrieval, embedding, 0.45 + margin * 0.55)), 4)


def _confidence_level(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _capability_score(table: dict[str, Any], capabilities: list[str]) -> float:
    columns = _table_column_keys(table)
    score = 0.0
    for capability in capabilities:
        if _CAPABILITY_COLUMNS[capability] & columns:
            score += 12.0
    return score


def _table_column_keys(table: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for column in table.get("columns") or []:
        raw = str(column.get("name") or "").lower()
        if raw:
            names.add(raw)
            names.add(_normalize(raw))
    return names


def _layer_priority(table: dict[str, Any], datasource: str) -> float:
    schema = str(table.get("database") or "")
    if datasource == "dwh":
        return 8.0 if schema == "ops" else 4.0
    if schema == "vsf_mart":
        return 11.0
    if schema == "vsf_stg":
        return 5.0
    return 0.0


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _table_document(table: dict[str, Any], datasource: str) -> str:
    columns = table.get("columns") or []
    table_name = str(table.get("tableName") or "")
    return " ".join(
        [
            f"datasource {datasource}",
            table_name,
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


