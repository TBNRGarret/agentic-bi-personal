"""Composable query-intent decomposition for deterministic SQL generation.

Replaces the hardcoded cascading if/elif branches in `_generate_sql()` with a
two-phase approach:

1. **Decompose** the natural-language question into a structured `QueryIntent`
   (metrics, dimensions, sort, filters, time window).
2. **Compose** a SQL string from the `QueryIntent` + available table columns.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SortIntent:
    """Describes the requested ordering."""
    metric: str            # canonical metric name (e.g. "cost", "latency")
    direction: str = "desc"  # "asc" | "desc"


@dataclass
class QueryIntent:
    """Structured representation of what the user is asking for."""
    metrics: list[str]              # ["cost", "tokens", "latency", ...]
    dimensions: list[str]           # ["model", "time", "source_app", ...]
    time_window_days: int = 30
    sort: SortIntent | None = None
    filters: dict[str, str] = field(default_factory=dict)  # {"model": "gpt-4o"}
    question_type: str = "overview"   # top_contributor | trend | comparison | detail | overview


# ---------------------------------------------------------------------------
# Metric detection helpers
# ---------------------------------------------------------------------------

_METRIC_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cost", ("cost", "chi phi", "spend", "usd", "money", "price")),
    ("latency", ("latency", "duration", "p95", "response time", "slow", "do tre", "cham")),
    ("error", ("error", "errors", "failed", "failure", "loi")),
    ("tokens", ("token", "tokens")),
    ("request_count", ("request", "requests", "so request", "query count",
                       "throughput", "traffic", "volume")),
    ("ingestion", ("ingestion", "batch", "loaded", "quarantine")),
)


def _detect_metrics(text: str) -> list[str]:
    """Detect which metrics the question is asking about."""
    metrics: list[str] = []
    for metric, keywords in _METRIC_SIGNALS:
        if any(kw in text for kw in keywords):
            metrics.append(metric)
    return metrics


# ---------------------------------------------------------------------------
# Dimension detection helpers
# ---------------------------------------------------------------------------

# Explicit "group by" style signals — user clearly says "by X", "per X", etc.
_EXPLICIT_DIM_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("time", ("theo ngay", "tung ngay", "moi ngay", "hang ngay",
              "per day", "each day", "daily", "by day", "over time",
              "trend", "theo gio", "tung gio", "hourly", "by hour")),
    ("model", ("theo model", "tung model", "moi model", "by model",
               "per model", "each model", "models")),
    ("source_app", ("source app", "source_app", "by source", "per source",
                    "each source", "theo source", "tung source")),
    ("service_name", ("theo service", "tung service", "by service",
                      "per service", "each service")),
    ("provider", ("theo provider", "tung provider", "by provider", "per provider")),
    ("environment", ("theo environment", "tung environment",
                     "by environment", "per environment")),
    ("status", ("theo status", "tung status", "by status", "per status")),
    ("user", ("theo user", "tung user", "by user", "per user")),
)

# Superlative / "which X" signals — user asks about a specific entity kind
# e.g. "which model costs the most?" → dimension=model, sort=cost desc
_SUPERLATIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    # English
    (r"\b(?:which|what)\s+(model)\b", "model"),
    (r"\b(?:which|what)\s+(service)\b", "service_name"),
    (r"\b(?:which|what)\s+(source|app)\b", "source_app"),
    (r"\b(?:which|what)\s+(provider)\b", "provider"),
    (r"\btop\s+(model)s?\b", "model"),
    (r"\btop\s+(service)s?\b", "service_name"),
    (r"\btop\s+(source|app)s?\b", "source_app"),
    (r"\btop\s+(provider)s?\b", "provider"),
    # Vietnamese
    (r"\b(model)\s+nao\b", "model"),
    (r"\b(service)\s+nao\b", "service_name"),
    (r"\b(source|app)\s+nao\b", "source_app"),
    (r"\b(provider)\s+nao\b", "provider"),
)

# Words that signal "give me the most/highest/largest"
_SUPERLATIVE_KEYWORDS = (
    "most", "highest", "largest", "biggest", "top",
    "least", "lowest", "smallest", "fewest",
    "nhieu nhat", "lon nhat", "cao nhat", "thap nhat", "it nhat",
    "contribute", "contributed", "contributing", "dong gop",
)

_ASCENDING_KEYWORDS = ("least", "lowest", "smallest", "fewest", "thap nhat", "it nhat")


def _detect_dimensions(
    text: str,
    *,
    request_plan_dimensions: list[str],
    metrics: list[str],
) -> list[str]:
    """Detect which dimensions the user wants to group by.

    Uses three signal sources, in priority order:
    1. Explicit dimensions from request_plan slots (already extracted by orchestrator)
    2. Explicit "by X" / "per X" phrasing in the question
    3. Implicit superlative / "which X" patterns
    """
    dimensions: list[str] = []
    seen: set[str] = set()

    def _add(dim: str) -> None:
        if dim not in seen:
            seen.add(dim)
            dimensions.append(dim)

    # 1. request_plan dimensions have highest priority (user may have said
    # "cost tung model theo tung ngay" → plan already extracted [time, model])
    for dim in request_plan_dimensions:
        _add(dim)

    # 2. Explicit "by X" / "per X" phrasing
    for dim, keywords in _EXPLICIT_DIM_SIGNALS:
        if any(kw in text for kw in keywords):
            _add(dim)

    # 3. Superlative / "which X" patterns
    for pattern, dim in _SUPERLATIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            _add(dim)

    # 4. If no dimensions detected but the question mentions a groupable entity
    #    alongside a metric, infer the dimension.
    #    e.g. "model cost in 30 days" → "model" is mentioned with "cost"
    if not dimensions and metrics:
        entity_hints = (
            ("model", ("model",)),
            ("service_name", ("service",)),
            ("source_app", ("source", "app")),
            ("provider", ("provider",)),
        )
        for dim, hints in entity_hints:
            if any(h in text for h in hints):
                # Only infer if the entity word is not just part of a filter value
                # (i.e. not "model gpt-4o" which is a filter, not a dimension)
                if _is_entity_dimension_not_filter(text, dim):
                    _add(dim)

    # 5. Default: if still no dimensions, fall back to time
    if not dimensions:
        _add("time")

    return dimensions


def _is_entity_dimension_not_filter(text: str, dim: str) -> bool:
    """Check that the entity word is a dimension request, not a specific value filter.

    "which model cost most" → True (model is a dimension)
    "model gpt-4o cost"     → False (model is a filter, specific value follows)
    """
    if dim == "model":
        # If a specific model name follows "model", it's a filter
        match = re.search(
            r"\bmodel\s+([a-z0-9][a-z0-9._/:+\-]{1,127})",
            text, re.IGNORECASE,
        )
        if match:
            candidate = match.group(1).rstrip(".,;:?")
            # Looks like a model name (has digits, dashes, dots)
            if any(c.isdigit() for c in candidate) or any(
                sep in candidate for sep in ("-", "/", ".", ":")
            ):
                return False
    return True


# ---------------------------------------------------------------------------
# Sort intent detection
# ---------------------------------------------------------------------------

def _detect_sort(text: str, metrics: list[str]) -> SortIntent | None:
    """Detect if the user wants results sorted by a specific metric."""
    has_superlative = any(kw in text for kw in _SUPERLATIVE_KEYWORDS)
    if not has_superlative:
        return None

    direction = "asc" if any(kw in text for kw in _ASCENDING_KEYWORDS) else "desc"

    # Pick the most relevant metric for sorting
    # Priority: cost > error > latency > tokens > request_count
    sort_priority = ["cost", "error", "latency", "tokens", "request_count"]
    for metric in sort_priority:
        if metric in metrics:
            return SortIntent(metric=metric, direction=direction)

    # If superlative is present but no specific metric detected, use the first metric
    if metrics:
        return SortIntent(metric=metrics[0], direction=direction)

    return None


# ---------------------------------------------------------------------------
# Question type detection
# ---------------------------------------------------------------------------

def _detect_question_type(
    text: str,
    dimensions: list[str],
    sort: SortIntent | None,
) -> str:
    """Classify the question type based on dimensions and sort intent."""
    if sort and len(dimensions) == 1 and dimensions[0] != "time":
        return "top_contributor"
    if "time" in dimensions and len(dimensions) == 1:
        return "trend"
    if len(dimensions) > 1:
        return "comparison"
    if any(kw in text for kw in ("detail", "chi tiet", "show", "xem", "liet ke")):
        return "detail"
    return "overview"


# ---------------------------------------------------------------------------
# Filter detection
# ---------------------------------------------------------------------------

def _detect_filters(question: str) -> dict[str, str]:
    """Extract entity-specific filters from the question."""
    filters: dict[str, str] = {}
    # Specific model name
    match = re.search(
        r"\bmodel\s*(?:=|:|named\s+|la\s+|là\s+)?"
        r"[`'\"]?([a-z0-9][a-z0-9._/:+\-]{1,127})",
        question, re.IGNORECASE,
    )
    if match:
        candidate = match.group(1).rstrip("`'\".,;:?")
        if any(c.isdigit() for c in candidate) or any(
            sep in candidate for sep in ("-", "/", ".", ":")
        ):
            filters["model"] = candidate

    # Source app / service patterns
    for key, patterns in (
        ("source_app", (
            r"\bsource(?:\s+app)?\s+([a-zA-Z0-9_.:-]+)",
            r"\bapp\s+([a-zA-Z0-9_.:-]+)",
        )),
        ("service_name", (
            r"\bservice\s+([a-zA-Z0-9_.:-]+)",
        )),
    ):
        for pattern in patterns:
            m = re.search(pattern, question, re.IGNORECASE)
            if m:
                value = m.group(1).strip(".,;:!?()[]{}\"'")
                stop_words = {
                    "for", "of", "by", "has", "have", "with", "in",
                    "trong", "cua", "last", "ngay", "days", "day",
                    "nao", "most", "highest", "lowest", "top", "least",
                    "cost", "error", "errors", "latency", "duration",
                    "request", "requests", "token", "tokens", "p95",
                }
                if value.lower() not in stop_words and len(value) > 1:
                    filters[key] = value
                    break

    return filters


# ---------------------------------------------------------------------------
# Time window detection
# ---------------------------------------------------------------------------

def _detect_time_window(text: str) -> int:
    """Extract time window in days from the question text."""
    match = re.search(r"(\d+)\s*(day|days|d|ngay)", text)
    if match:
        return max(1, min(int(match.group(1)), 365))
    if re.search(r"\b(24h|today|hom nay)\b", text):
        return 1
    if re.search(r"\b(7d|week|tuan)\b", text):
        return 7
    return 30


# ---------------------------------------------------------------------------
# Public API: decompose
# ---------------------------------------------------------------------------

def decompose_query_intent(
    question: str,
    columns: set[str],
    request_plan: dict[str, Any] | None = None,
) -> QueryIntent:
    """Decompose a natural-language question into a structured QueryIntent.

    Parameters
    ----------
    question : str
        The user's question.
    columns : set[str]
        Column names available in the selected table.
    request_plan : dict
        The request plan from `build_request_plan()`.
    """
    text = _normalize_for_decomposer(question)

    # Extract request_plan dimensions (highest-priority signal)
    plan_dims = _extract_plan_dimensions(request_plan)

    metrics = _detect_metrics(text)
    dimensions = _detect_dimensions(
        text,
        request_plan_dimensions=plan_dims,
        metrics=metrics,
    )

    # Filter dimensions to only those that have a matching column in the table
    dimensions = _filter_dimensions_by_columns(dimensions, columns)

    sort = _detect_sort(text, metrics)
    filters = _detect_filters(question)  # Use original case for regex
    time_window = _detect_time_window(text)
    question_type = _detect_question_type(text, dimensions, sort)

    return QueryIntent(
        metrics=metrics,
        dimensions=dimensions,
        time_window_days=time_window,
        sort=sort,
        filters=filters,
        question_type=question_type,
    )


# ---------------------------------------------------------------------------
# Public API: compose SQL
# ---------------------------------------------------------------------------

# Maps metric names → (column_candidates, aggregate_fn, alias)
_METRIC_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "cost": [
        ("total_cost_usd", "round(sum(total_cost_usd), 6)", "total_cost_usd"),
        ("estimated_cost_usd", "round(sum(estimated_cost_usd), 6)", "total_cost_usd"),
        ("cost_usd", "round(sum(cost_usd), 6)", "total_cost_usd"),
    ],
    "tokens": [
        ("total_tokens", "sum(total_tokens)", "total_tokens"),
        ("total_tokens_used", "sum(total_tokens_used)", "total_tokens"),
    ],
    "latency": [
        ("avg_latency_ms", "round(avg(avg_latency_ms), 2)", "avg_latency_ms"),
        ("p95_latency_ms", "round(avg(p95_latency_ms), 2)", "p95_latency_ms"),
        ("latency_ms", "round(avg(latency_ms), 2)", "avg_latency_ms"),
        ("duration_ms", "round(avg(duration_ms), 2)", "avg_latency_ms"),
        ("avg_span_duration_ms", "round(avg(avg_span_duration_ms), 2)", "avg_latency_ms"),
        ("p95_span_duration_ms", "round(avg(p95_span_duration_ms), 2)", "p95_latency_ms"),
    ],
    "error": [
        ("error_count", "sum(error_count)", "error_count"),
        ("failed_requests", "sum(failed_requests)", "error_count"),
        ("error_log_count", "sum(error_log_count)", "error_count"),
        ("error_span_count", "sum(error_span_count)", "error_count"),
        ("failed_batch_count", "sum(failed_batch_count)", "failed_batch_count"),
    ],
    "request_count": [
        ("request_count", "sum(request_count)", "request_count"),
        ("total_requests", "sum(total_requests)", "request_count"),
        ("generation_count", "sum(generation_count)", "generation_count"),
        ("trace_count", "sum(trace_count)", "trace_count"),
        ("span_count", "sum(span_count)", "span_count"),
        ("query_count", "sum(query_count)", "request_count"),
    ],
    "ingestion": [
        ("batch_count", "sum(batch_count)", "batch_count"),
        ("loaded_batch_count", "sum(loaded_batch_count)", "loaded_batch_count"),
        ("failed_batch_count", "sum(failed_batch_count)", "failed_batch_count"),
        ("quarantined_batch_count", "sum(quarantined_batch_count)", "quarantined_batch_count"),
        ("loaded_row_count", "sum(loaded_row_count)", "loaded_row_count"),
    ],
}

# Maps sort metric name → list of column candidates to ORDER BY
_SORT_COLUMN_MAP: dict[str, list[str]] = {
    "cost": ["total_cost_usd"],
    "tokens": ["total_tokens"],
    "latency": ["p95_latency_ms", "avg_latency_ms"],
    "error": ["error_count", "failed_batch_count"],
    "request_count": ["request_count", "generation_count", "trace_count", "span_count"],
    "ingestion": ["loaded_row_count", "batch_count"],
}

# Dimension → (column_candidates, select_expr, group_expr)
_DIMENSION_MAP: dict[str, list[tuple[str, str, str]]] = {
    "time": [
        ("metric_date", "metric_date", "metric_date"),
        ("date", "date AS metric_date", "date"),
        ("event_time", "toDate(event_time, 'UTC') AS metric_date", "metric_date"),
        ("timestamp", "toDate(timestamp) AS metric_date", "metric_date"),
        ("started_at", "toDate(started_at) AS metric_date", "metric_date"),
        ("created_at", "toDate(created_at) AS metric_date", "metric_date"),
    ],
    "model": [("model", "model", "model")],
    "source_app": [
        ("source_app", "source_app", "source_app"),
        ("source_app_id", "source_app_id AS source_app", "source_app_id"),
    ],
    "service_name": [("service_name", "service_name", "service_name")],
    "provider": [("provider", "provider", "provider")],
    "environment": [("environment", "environment", "environment")],
    "status": [("status", "status", "status")],
    "user": [
        ("user_id", "user_id", "user_id"),
        ("username", "username", "username"),
        ("user_id_hash", "user_id_hash", "user_id_hash"),
    ],
}


def compose_sql(
    table_name: str,
    columns: set[str],
    intent: QueryIntent,
    limit: int = 1000,
) -> str:
    """Compose a SQL query from a table schema and a QueryIntent.

    Returns a complete ClickHouse SQL string.
    """
    # --- Resolve dimensions ---
    select_parts: list[str] = []
    group_parts: list[str] = []
    dim_aliases: list[str] = []
    has_time_dimension = False

    for dim in intent.dimensions:
        candidates = _DIMENSION_MAP.get(dim, [])
        for col, select_expr, group_expr in candidates:
            if col in columns:
                alias = select_expr.split(" AS ")[-1].strip() if " AS " in select_expr else select_expr
                select_parts.append(select_expr)
                group_parts.append(group_expr)
                dim_aliases.append(alias)
                if dim == "time":
                    has_time_dimension = True
                break

    # --- Resolve aggregates ---
    agg_parts: list[str] = []
    agg_aliases: list[str] = []

    if intent.metrics:
        for metric in intent.metrics:
            metric_candidates = _METRIC_COLUMNS.get(metric, [])
            for col, agg_expr, alias in metric_candidates:
                if col in columns and alias not in agg_aliases:
                    agg_parts.append(f"{agg_expr} AS {alias}")
                    agg_aliases.append(alias)
                    break
            # For latency, include both avg and p95 if available
            if metric == "latency":
                for col, agg_expr, alias in metric_candidates:
                    if col in columns and alias not in agg_aliases:
                        agg_parts.append(f"{agg_expr} AS {alias}")
                        agg_aliases.append(alias)
    else:
        # No specific metric → include all available common aggregates
        default_metrics = ["request_count", "tokens", "cost", "error"]
        for metric in default_metrics:
            metric_candidates = _METRIC_COLUMNS.get(metric, [])
            for col, agg_expr, alias in metric_candidates:
                if col in columns and alias not in agg_aliases:
                    agg_parts.append(f"{agg_expr} AS {alias}")
                    agg_aliases.append(alias)
                    break

    if not agg_parts:
        # Last resort — count rows
        agg_parts.append("count() AS row_count")
        agg_aliases.append("row_count")

    # --- WHERE clause ---
    where_parts: list[str] = []
    time_col = _find_time_column(columns)
    if time_col:
        if time_col in ("metric_date", "date"):
            where_parts.append(
                f"{time_col} >= today() - {intent.time_window_days}"
            )
        else:
            where_parts.append(
                f"{time_col} >= now() - INTERVAL {intent.time_window_days} DAY"
            )

    # Entity filters
    for filter_key, filter_value in intent.filters.items():
        filter_col = _resolve_filter_column(filter_key, columns)
        if filter_col:
            escaped = filter_value.replace("'", "''")
            where_parts.append(
                f"positionCaseInsensitiveUTF8({filter_col}, '{escaped}') > 0"
            )

    where_clause = " AND ".join(where_parts) if where_parts else "1 = 1"

    # --- ORDER BY clause ---
    if has_time_dimension and (
        intent.question_type == "trend"
        or (intent.question_type == "comparison" and "time" in intent.dimensions)
    ):
        # Time-series: order by time
        order_parts = [a for a in dim_aliases if a in ("metric_date",)]
        if not order_parts:
            order_parts = dim_aliases[:1]
    elif intent.sort:
        # Sort by the requested metric
        sort_aliases = _SORT_COLUMN_MAP.get(intent.sort.metric, [])
        sort_col = next(
            (alias for alias in sort_aliases if alias in agg_aliases),
            agg_aliases[0] if agg_aliases else None,
        )
        direction = intent.sort.direction.upper()
        order_parts = [f"{sort_col} {direction}"] if sort_col else [f"{agg_aliases[0]} DESC"]
    else:
        # Default: order by first aggregate descending (unless time is a dimension)
        if has_time_dimension:
            order_parts = ["metric_date"]
        else:
            order_parts = [f"{agg_aliases[0]} DESC"] if agg_aliases else ["1"]

    select_list = ", ".join(select_parts + agg_parts)
    group_by = ", ".join(group_parts)
    order_by = ", ".join(order_parts)
    bounded_limit = min(limit, 1000)

    return (
        f"SELECT {select_list} FROM {table_name} "
        f"WHERE {where_clause} "
        f"GROUP BY {group_by} "
        f"ORDER BY {order_by} "
        f"LIMIT {bounded_limit}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_for_decomposer(value: str) -> str:
    """Unicode-normalize and lowercase for matching."""
    ascii_value = "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    ).replace("\u0111", "d").replace("\u0110", "D")
    return (
        ascii_value.replace("\u0111", "d").replace("\u0110", "D")
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("chi phi", "cost")
        .replace("do tre", "latency")
        .replace("cham", "slow")
        .replace("loi", "error")
    )


def _extract_plan_dimensions(request_plan: dict[str, Any] | None) -> list[str]:
    """Extract dimension list from request_plan slots."""
    if not isinstance(request_plan, dict):
        return []
    slots = request_plan.get("slots")
    if not isinstance(slots, dict):
        return []
    dim_slot = slots.get("dimensions")
    if not isinstance(dim_slot, dict):
        return []
    value = dim_slot.get("value")
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def _filter_dimensions_by_columns(
    dimensions: list[str], columns: set[str]
) -> list[str]:
    """Remove dimensions that have no matching column in the table."""
    result: list[str] = []
    for dim in dimensions:
        candidates = _DIMENSION_MAP.get(dim, [])
        if any(col in columns for col, _, _ in candidates):
            result.append(dim)
    # If all dimensions were filtered out, fall back to time
    if not result and dimensions:
        time_candidates = _DIMENSION_MAP.get("time", [])
        if any(col in columns for col, _, _ in time_candidates):
            result.append("time")
    return result


def _find_time_column(columns: set[str]) -> str | None:
    """Find the best time column for WHERE filtering."""
    for col in ("metric_date", "date", "event_time", "timestamp",
                "started_at", "created_at"):
        if col in columns:
            return col
    return None


def _resolve_filter_column(filter_key: str, columns: set[str]) -> str | None:
    """Map a filter key to an actual column name."""
    direct_map: dict[str, list[str]] = {
        "model": ["model"],
        "source_app": ["source_app", "source_app_id"],
        "service_name": ["service_name"],
        "provider": ["provider"],
        "environment": ["environment"],
    }
    for col in direct_map.get(filter_key, []):
        if col in columns:
            return col
    return None
