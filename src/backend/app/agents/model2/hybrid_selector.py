from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import SchemaRetrievalResult, SelectorResult
from .utils import column_names, compact_table, normalize_text, table_name


MAX_COLUMNS_PER_TABLE = 12


@dataclass(frozen=True)
class QueryProfile:
    text: str
    terms: set[str]
    metrics: set[str]
    dimensions: set[str]
    coverage: set[str]
    join_tables: set[str]
    join_columns_by_table: dict[str, set[str]]


@dataclass
class ColumnScore:
    name: str
    score: float = 0.0
    roles: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)

    def add(self, amount: float, role: str, reason: str) -> None:
        self.score += amount
        self.roles.add(role)
        if reason not in self.reasons:
            self.reasons.append(reason)


class HybridSelector:
    """MAC-SQL selector adapted to Agentic BI metadata tables.

    The selector now does two-stage pruning:
    1. table-level keep/drop decisions from request slots, graph context, and
       requested metric/dimension coverage;
    2. column-level pruning by semantic role, while preserving time filters,
       requested metrics/dimensions, and join keys.
    """

    """
    Overview
    1. Natural language question + SchemaRetrievalResult from schema_retriever
    2. Build QueryProfile
        a. Extract terms, metrics, dimensions from question + request_plan slots
        b. Infer join context from graph edges
    3. For each candidate table:
        a. Score columns by semantic role (metric, dimension, time, join_key, pk)
        b. Compute table-level keep/drop decision
        c. keep_all: small/highly relevant tables
        d. drop_all: irrelevant tables
        e. select_columns: large tables -> top N columns by role
    4. Build compact schema prompt string for LLM
    5. Fallback: if no tables survive, promote top-ranked table

    MAC-SQL style pruning: table-level then column-level.
    """

    def select(
        self,
        question: str,
        retrieval: SchemaRetrievalResult,
        *,
        request_plan: dict[str, Any],
    ) -> SelectorResult:
        profile = _query_profile(question, retrieval, request_plan)
        decisions: list[dict[str, Any]] = []
        selected_tables: list[dict[str, Any]] = []
        schema_sections: list[str] = []
        confidences: list[float] = []

        for position, table in enumerate(retrieval.tables):
            columns = column_names(table)
            scored_columns = _score_columns(table, profile)
            table_score, table_reasons = _table_score(
                table,
                position=position,
                profile=profile,
                scored_columns=scored_columns,
            )
            decision = _table_decision(
                table,
                position=position,
                table_score=table_score,
                profile=profile,
                scored_columns=scored_columns,
            )

            if decision == "drop_all":
                decisions.append(
                    {
                        "tableName": table_name(table),
                        "decision": decision,
                        "selectedColumns": [],
                        "droppedColumns": columns,
                        "reason": _decision_reason(table_reasons, "low schema relevance"),
                        "score": round(table_score, 4),
                        "requiredRoles": _role_summary(scored_columns),
                    }
                )
                continue

            if decision == "keep_all":
                selected_columns = columns
            else:
                selected_columns = _selected_columns(
                    columns,
                    scored_columns,
                    max_columns=MAX_COLUMNS_PER_TABLE,
                )

            if not selected_columns and columns:
                selected_columns = _fallback_columns(columns, scored_columns)

            compact = compact_table(table, columns=selected_columns)
            selected_tables.append(compact)
            schema_sections.append(_schema_section(compact, scored_columns))
            confidences.append(max(0.2, table_score))
            decisions.append(
                {
                    "tableName": table_name(table),
                    "decision": decision,
                    "selectedColumns": selected_columns,
                    "droppedColumns": [column for column in columns if column not in set(selected_columns)],
                    "reason": _decision_reason(table_reasons, _column_reason(scored_columns, selected_columns)),
                    "score": round(table_score, 4),
                    "requiredRoles": _role_summary(scored_columns),
                    "selectedColumnRoles": {
                        column: _sorted_roles(scored_columns[column].roles)
                        for column in selected_columns
                        if column in scored_columns and scored_columns[column].roles
                    },
                }
            )

        if not selected_tables and retrieval.tables:
            fallback = _fallback_table(retrieval.tables[0], profile)
            selected_tables.append(fallback["table"])
            schema_sections.append(fallback["schema"])
            decisions.append(fallback["decision"])
            confidences.append(0.35)

        confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        return SelectorResult(
            selected_tables=selected_tables,
            selected_table_names=[table_name(table) for table in selected_tables],
            schema_prompt="\n\n".join(schema_sections),
            decisions=decisions,
            confidence=confidence,
            strategy="macsql_deep_schema_pruning",
            graph_edges=retrieval.graph_edges,
        )


def _query_profile(
    question: str,
    retrieval: SchemaRetrievalResult,
    request_plan: dict[str, Any],
) -> QueryProfile:
    text = normalize_text(question)
    slots = request_plan.get("slots") if isinstance(request_plan, dict) else {}
    terms = {term for term in text.split() if len(term) > 2}
    terms.update(_slot_terms(slots))
    metrics = _slot_values(slots, "metric") | _slot_values(slots, "metrics")
    metrics |= _infer_metrics(text)
    dimensions = _slot_values(slots, "dimensions") | _infer_dimensions(text)
    coverage = _slot_values(slots, "coverage")
    join_tables, join_columns_by_table = _join_context(retrieval)
    return QueryProfile(
        text=text,
        terms={term for term in terms if term},
        metrics=metrics,
        dimensions=dimensions,
        coverage=coverage,
        join_tables=join_tables,
        join_columns_by_table=join_columns_by_table,
    )


def _slot_values(slots: Any, name: str) -> set[str]:
    if not isinstance(slots, dict):
        return set()
    slot = slots.get(name)
    value = slot.get("value") if isinstance(slot, dict) else slot
    values = value if isinstance(value, list) else [value]
    return {
        normalize_text(str(item)).replace(" ", "_")
        for item in values
        if item not in (None, "", [])
    }


def _slot_terms(slots: Any) -> set[str]:
    terms: set[str] = set()
    if not isinstance(slots, dict):
        return terms
    for slot in slots.values():
        value = slot.get("value") if isinstance(slot, dict) else slot
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item not in (None, "", []):
                terms.update(normalize_text(str(item)).split())
    return {term for term in terms if len(term) > 2}


def _infer_metrics(text: str) -> set[str]:
    metrics: set[str] = set()
    for metric, signals in _METRIC_SIGNALS.items():
        if any(signal in text for signal in signals):
            metrics.add(metric)
    return metrics


def _infer_dimensions(text: str) -> set[str]:
    dimensions: set[str] = set()
    for dimension, signals in _DIMENSION_SIGNALS.items():
        if any(signal in text for signal in signals):
            dimensions.add(dimension)
    return dimensions


def _join_context(retrieval: SchemaRetrievalResult) -> tuple[set[str], dict[str, set[str]]]:
    join_tables: set[str] = set()
    columns_by_table: dict[str, set[str]] = {}

    for path in retrieval.join_paths or []:
        for table in path.get("tables") or []:
            if table:
                join_tables.add(str(table))
        for edge in path.get("edges") or []:
            _add_join_edge(edge, join_tables, columns_by_table)

    for edge in retrieval.graph_edges or []:
        if edge.get("type") not in {"FK", "SHARED_COLUMN"}:
            continue
        _add_join_edge(edge, join_tables, columns_by_table)

    return join_tables, columns_by_table


def _add_join_edge(
    edge: dict[str, Any],
    join_tables: set[str],
    columns_by_table: dict[str, set[str]],
) -> None:
    source = str(edge.get("from") or edge.get("source") or "")
    target = str(edge.get("to") or edge.get("target") or "")
    columns = {str(column) for column in edge.get("columns") or [] if str(column)}
    for table in (source, target):
        if not table:
            continue
        join_tables.add(table)
        columns_by_table.setdefault(table, set()).update(columns)


def _score_columns(table: dict[str, Any], profile: QueryProfile) -> dict[str, ColumnScore]:
    scores = {column: ColumnScore(name=column) for column in column_names(table)}
    table_join_columns = profile.join_columns_by_table.get(table_name(table), set())
    for column in table.get("columns") or []:
        name = str(column.get("name") or "").strip()
        if not name or name not in scores:
            continue
        score = scores[name]
        normalized = normalize_text(name)
        description = normalize_text(str(column.get("description") or ""))

        if name in table_join_columns or normalized in {normalize_text(col) for col in table_join_columns}:
            score.add(1.2, "join_key", "graph join key")
        elif _is_key_column(normalized):
            score.add(0.25, "key", "identifier useful for joins or drilldown")

        if _is_time_column(normalized):
            score.add(0.65, "time_filter", "time column needed for bounded BI SQL")
            if "time" in profile.dimensions:
                score.add(0.45, "dimension:time", "requested time grain")

        for metric in profile.metrics:
            if _matches_hints(normalized, _METRIC_COLUMN_HINTS.get(metric, ())):
                score.add(1.0, f"metric:{metric}", f"requested metric {metric}")

        for dimension in profile.dimensions | profile.coverage:
            if _matches_hints(normalized, _DIMENSION_COLUMN_HINTS.get(dimension, ())):
                score.add(0.95, f"dimension:{dimension}", f"requested dimension {dimension}")

        if _is_filter_candidate(normalized, profile):
            score.add(0.35, "filter_candidate", "likely filter/grouping column")

        lexical_hits = [term for term in profile.terms if _term_matches_column(term, normalized, description)]
        if lexical_hits:
            amount = min(0.55, 0.18 * len(lexical_hits))
            score.add(amount, "lexical_match", "matched query terms: " + ", ".join(lexical_hits[:4]))

        if _is_noisy_column(normalized) and not _has_required_role(score):
            score.score -= 0.25
            score.reasons.append("penalized noisy raw/debug column")

    return scores


def _table_score(
    table: dict[str, Any],
    *,
    position: int,
    profile: QueryProfile,
    scored_columns: dict[str, ColumnScore],
) -> tuple[float, list[str]]:
    name = table_name(table)
    normalized_name = normalize_text(name)
    description = normalize_text(str(table.get("description") or ""))
    retrieval_confidence = float(table.get("retrievalConfidence") or 0.0)
    retrieval_score = float(table.get("retrievalScore") or 0.0)
    score = max(0.05, 0.3 - position * 0.05)
    reasons: list[str] = []

    if retrieval_confidence:
        score += min(0.3, retrieval_confidence * 0.3)
        reasons.append("retrieval confidence")
    if retrieval_score:
        score += min(0.2, retrieval_score * 0.1)
        reasons.append("retrieval score")

    table_hits = [term for term in profile.terms if term in normalized_name or term in description]
    if table_hits:
        score += min(0.25, len(table_hits) * 0.06)
        reasons.append("matched table metadata: " + ", ".join(table_hits[:4]))

    if name in profile.join_tables:
        score += 0.22
        reasons.append("participates in graph join path")

    metric_coverage = _covered_requested_roles(scored_columns, "metric", profile.metrics)
    dimension_coverage = _covered_requested_roles(scored_columns, "dimension", profile.dimensions)
    if metric_coverage:
        score += min(0.3, 0.14 * len(metric_coverage))
        reasons.append("metric coverage: " + ", ".join(sorted(metric_coverage)))
    if dimension_coverage:
        score += min(0.25, 0.1 * len(dimension_coverage))
        reasons.append("dimension coverage: " + ", ".join(sorted(dimension_coverage)))

    if profile.metrics and metric_coverage == profile.metrics:
        score += 0.08
        reasons.append("covers all requested metrics")
    if profile.dimensions and dimension_coverage >= {dim for dim in profile.dimensions if dim != "time"}:
        score += 0.06
        reasons.append("covers requested non-time grain")

    strong_columns = [column for column in scored_columns.values() if column.score >= 0.75]
    if strong_columns:
        score += min(0.15, 0.04 * len(strong_columns))
        reasons.append("strong column role matches")

    return min(score, 1.0), reasons


def _table_decision(
    table: dict[str, Any],
    *,
    position: int,
    table_score: float,
    profile: QueryProfile,
    scored_columns: dict[str, ColumnScore],
) -> str:
    columns = column_names(table)
    name = table_name(table)
    has_join_role = name in profile.join_tables or any("join_key" in score.roles for score in scored_columns.values())
    has_requested_role = any(_has_required_role(score) for score in scored_columns.values())
    has_metric = bool(_covered_requested_roles(scored_columns, "metric", profile.metrics))
    has_dimension = bool(_covered_requested_roles(scored_columns, "dimension", profile.dimensions))

    if has_join_role and table_score >= 0.24:
        return "keep_all" if len(columns) <= 4 and not _has_noisy_column(columns) else "select_columns"

    if profile.metrics and not has_metric and table_score < 0.45 and position > 0:
        return "drop_all"
    if profile.dimensions and not has_dimension and not has_metric and table_score < 0.42 and position > 0:
        return "drop_all"
    if not has_requested_role and table_score < 0.34 and position > 0:
        return "drop_all"
    if table_score < 0.22 and position > 0:
        return "drop_all"

    if len(columns) <= 10 and table_score >= 0.68 and not _has_many_noisy_columns(columns):
        return "keep_all"
    return "select_columns"


def _selected_columns(
    columns: list[str],
    scored_columns: dict[str, ColumnScore],
    *,
    max_columns: int,
) -> list[str]:
    selected: list[str] = []
    for column in columns:
        score = scored_columns.get(column)
        if score and _must_keep_column(score):
            selected.append(column)

    ranked = sorted(
        scored_columns.values(),
        key=lambda item: (item.score, len(item.roles), item.name),
        reverse=True,
    )
    for score in ranked:
        if len(selected) >= max_columns:
            break
        if score.name in selected:
            continue
        if score.score >= 0.42 or not selected:
            selected.append(score.name)

    selected_set = set(selected[:max_columns])
    return [column for column in columns if column in selected_set]


def _fallback_columns(columns: list[str], scored_columns: dict[str, ColumnScore]) -> list[str]:
    ranked = sorted(scored_columns.values(), key=lambda item: item.score, reverse=True)
    selected = {score.name for score in ranked[: min(len(ranked), 6)]}
    return [column for column in columns if column in selected]


def _fallback_table(table: dict[str, Any], profile: QueryProfile) -> dict[str, Any]:
    scores = _score_columns(table, profile)
    columns = _fallback_columns(column_names(table), scores)
    compact = compact_table(table, columns=columns)
    return {
        "table": compact,
        "schema": _schema_section(compact, scores),
        "decision": {
            "tableName": table_name(table),
            "decision": "select_columns",
            "selectedColumns": columns,
            "droppedColumns": [column for column in column_names(table) if column not in set(columns)],
            "reason": "fallback to top retrieved table after deep pruning removed all candidates",
            "score": 0.35,
            "requiredRoles": _role_summary(scores),
        },
    }


def _schema_section(table: dict[str, Any], scored_columns: dict[str, ColumnScore]) -> str:
    lines = [f"# Table: {table_name(table)}"]
    for column in table.get("columns") or []:
        name = str(column.get("name") or "")
        ctype = column.get("type") or "unknown"
        description = str(column.get("description") or "").strip()
        roles = _sorted_roles(scored_columns.get(name, ColumnScore(name)).roles)
        suffix_parts: list[str] = []
        if description:
            suffix_parts.append(description)
        if roles:
            suffix_parts.append("roles: " + ", ".join(roles[:5]))
        suffix = f" -- {'; '.join(suffix_parts)}" if suffix_parts else ""
        lines.append(f"- {name} ({ctype}){suffix}")
    return "\n".join(lines)


def _decision_reason(reasons: list[str], fallback: str) -> str:
    compact = [reason for reason in reasons if reason]
    if compact:
        return "; ".join(compact[:4])
    return fallback


def _column_reason(scored_columns: dict[str, ColumnScore], selected_columns: list[str]) -> str:
    matched: list[str] = []
    for column in selected_columns:
        score = scored_columns.get(column)
        if not score or not score.roles:
            continue
        matched.append(f"{column} ({', '.join(_sorted_roles(score.roles)[:3])})")
    return "selected role columns: " + ", ".join(matched[:5]) if matched else "selected highest-scoring columns"


def _role_summary(scored_columns: dict[str, ColumnScore]) -> list[str]:
    roles: set[str] = set()
    for score in scored_columns.values():
        roles.update(role for role in score.roles if role != "lexical_match")
    return _sorted_roles(roles)


def _sorted_roles(roles: set[str]) -> list[str]:
    priority = {
        "metric": 0,
        "dimension": 1,
        "time_filter": 2,
        "join_key": 3,
        "filter_candidate": 4,
        "key": 5,
        "lexical_match": 9,
    }

    def key(role: str) -> tuple[int, str]:
        family = role.split(":", 1)[0]
        return (priority.get(family, priority.get(role, 8)), role)

    return sorted(roles, key=key)


def _covered_requested_roles(
    scored_columns: dict[str, ColumnScore],
    role_prefix: str,
    requested: set[str],
) -> set[str]:
    covered: set[str] = set()
    if not requested:
        return covered
    prefix = f"{role_prefix}:"
    for score in scored_columns.values():
        for role in score.roles:
            if role.startswith(prefix):
                value = role.split(":", 1)[1]
                if value in requested:
                    covered.add(value)
    return covered


def _must_keep_column(score: ColumnScore) -> bool:
    return bool(
        score.roles
        & {
            "join_key",
            "time_filter",
            "filter_candidate",
        }
    ) or any(role.startswith(("metric:", "dimension:")) for role in score.roles)


def _has_required_role(score: ColumnScore) -> bool:
    return bool(score.roles & {"join_key", "time_filter", "filter_candidate"}) or any(
        role.startswith(("metric:", "dimension:")) for role in score.roles
    )


def _matches_hints(normalized_column: str, hints: tuple[str, ...]) -> bool:
    tokens = set(normalized_column.split("_"))
    for hint in hints:
        normalized_hint = normalize_text(hint).replace(" ", "_")
        if normalized_hint == normalized_column:
            return True
        if normalized_hint in normalized_column:
            return True
        if normalized_hint in tokens:
            return True
    return False


def _term_matches_column(term: str, normalized_column: str, description: str) -> bool:
    if len(term) <= 2:
        return False
    return term in normalized_column or term in description or term in normalized_column.split("_")


def _is_key_column(normalized_column: str) -> bool:
    return normalized_column == "id" or normalized_column.endswith("_id") or normalized_column.endswith("_key")


def _is_time_column(normalized_column: str) -> bool:
    return normalized_column in {
        "date",
        "metric_date",
        "event_date",
        "event_time",
        "timestamp",
        "started_at",
        "created_at",
        "updated_at",
    } or normalized_column.endswith("_date") or normalized_column.endswith("_time")


def _is_filter_candidate(normalized_column: str, profile: QueryProfile) -> bool:
    filter_dimensions = {
        "model",
        "source_app",
        "service_name",
        "provider",
        "environment",
        "status",
        "user",
    }
    for dimension in filter_dimensions:
        if _matches_hints(normalized_column, _DIMENSION_COLUMN_HINTS.get(dimension, ())) and (
            dimension in profile.dimensions
            or dimension in profile.coverage
            or any(term in profile.text for term in _DIMENSION_SIGNALS.get(dimension, ()))
        ):
            return True
    return False


def _is_noisy_column(normalized_column: str) -> bool:
    return any(
        token in normalized_column
        for token in (
            "payload",
            "raw_json",
            "debug",
            "blob",
            "stacktrace",
            "metadata_json",
            "extra_json",
            "properties",
            "attributes",
        )
    )


def _has_noisy_column(columns: list[str]) -> bool:
    return any(_is_noisy_column(normalize_text(column)) for column in columns)


def _has_many_noisy_columns(columns: list[str]) -> bool:
    return sum(1 for column in columns if _is_noisy_column(normalize_text(column))) >= 2


_METRIC_SIGNALS: dict[str, tuple[str, ...]] = {
    "cost": ("cost", "usd", "spend", "price", "expense", "chi phi"),
    "tokens": ("token", "tokens", "usage"),
    "latency": ("latency", "duration", "p95", "response time", "slow", "do tre"),
    "error": ("error", "errors", "failed", "failure", "loi"),
    "request_count": ("request", "requests", "traffic", "volume", "count"),
    "throughput": ("throughput", "loaded row", "row count"),
    "revenue": ("revenue", "sales", "amount", "gmv"),
}

_METRIC_COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "cost": ("cost", "cost_usd", "total_cost_usd", "estimated_cost_usd", "usd", "spend"),
    "tokens": ("token", "tokens", "total_tokens", "prompt_tokens", "completion_tokens", "total_tokens_used"),
    "latency": ("latency", "duration", "duration_ms", "avg_latency_ms", "p95_latency_ms", "avg_span_duration_ms", "p95_span_duration_ms"),
    "error": ("error", "errors", "error_count", "failed", "failed_requests", "error_log_count", "error_span_count", "severity"),
    "request_count": ("request_count", "total_requests", "generation_count", "trace_count", "span_count", "query_count", "count"),
    "throughput": ("loaded_row_count", "row_count", "throughput", "batch_count"),
    "revenue": ("revenue", "sales", "amount", "total_amount", "gmv"),
    "pipeline_health": ("batch_count", "failed_batch_count", "loaded_batch_count", "quarantined_batch_count", "loaded_row_count"),
    "partition_health": ("partition", "partition_count", "row_count", "bytes"),
    "health": ("status", "health", "error_count", "failed_count"),
    "trace_count": ("trace_count", "span_count"),
}

_DIMENSION_SIGNALS: dict[str, tuple[str, ...]] = {
    "time": ("daily", "by day", "over time", "trend", "ngay", "hourly"),
    "model": ("model", "models"),
    "source_app": ("source", "source app", "source_app", "app"),
    "service_name": ("service", "service name", "service_name"),
    "provider": ("provider",),
    "environment": ("environment", "env"),
    "status": ("status",),
    "user": ("user", "username"),
    "region": ("region",),
    "product": ("product", "sku"),
}

_DIMENSION_COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "time": ("date", "metric_date", "event_date", "event_time", "timestamp", "started_at", "created_at"),
    "model": ("model", "model_name"),
    "source_app": ("source_app", "source_app_id", "app", "app_id"),
    "service_name": ("service_name", "service"),
    "provider": ("provider", "provider_name"),
    "environment": ("environment", "env"),
    "status": ("status", "state", "severity"),
    "user": ("user", "user_id", "username", "user_id_hash"),
    "region": ("region", "country", "state", "city"),
    "product": ("product", "product_id", "sku", "category"),
}
