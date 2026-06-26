"""Utility functions and common data structures for specialist agents."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal

from ...config import Settings
from ...security import Principal
from ...services.llm import LlmService
from ..agent_planner import ROUTING_TABLE
from ..row_security import apply_row_security
from ..sql_safety import validate_readonly_sql
from ..table_selector import TableSelector

EventSink = Callable[[dict[str, Any]], None]
AgentStatus = Literal["success", "partial", "failed"]


@dataclass
class AgentTask:
    task_id: str
    agent: str
    objective: str
    context: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    timeout_seconds: int = 120
    max_tool_calls: int = 4


@dataclass
class Evidence:
    source: str
    reference: str
    summary: str
    observed_at: str | None = None


@dataclass
class AgentResponse:
    task_id: str
    agent: str
    status: AgentStatus
    text: str
    evidence: list[Evidence] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialistTableResolution:
    table: dict[str, Any] | None
    metadata_source: str
    table_selection: dict[str, Any] | None
    candidate_count: int
    required_columns: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def table_name(self) -> str | None:
        if not self.table:
            return None
        return str(self.table.get("tableName") or "") or None


async def _run_llm_synthesis(
    awaitable: Awaitable[dict[str, Any]],
    *,
    settings: Settings | None,
    label: str,
) -> tuple[dict[str, Any], str | None]:
    timeout_seconds = _agent_synthesis_timeout(settings)
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except TimeoutError:
        return {}, (
            f"{label} LLM synthesis timed out after "
            f"{timeout_seconds:g}s; using deterministic specialist summary."
        )
    except Exception as exc:
        return {}, (
            f"{label} LLM synthesis failed: {str(exc)[:180]}; "
            "using deterministic specialist summary."
        )
    if result.get("status") == "success" and result.get("answer"):
        return result, None
    status = str(result.get("status") or "unavailable")
    error = str(result.get("error") or status)
    return result, (
        f"{label} LLM synthesis returned {status}: {error[:160]}; "
        "using deterministic specialist summary."
    )


def _agent_synthesis_timeout(settings: Settings | None) -> float:
    configured = (
        float(settings.agent_synthesis_timeout_seconds)
        if settings is not None
        else 120.0
    )
    return max(0.05, configured)


def _llm_usage_from_result(result: dict[str, Any]) -> dict[str, Any]:
    usage = result.get("usage") or {}
    total_tokens = int(usage.get("total_tokens") or 0)
    return {
        "total_tokens": total_tokens,
        "totalTokens": total_tokens,
        "estimatedCostUsd": result.get("totalCostUsd", 0.0),
        "model": result.get("model"),
        "provider": result.get("provider"),
    }


_SYSTEM_PRINCIPAL = Principal(
    user_id="system",
    username="system",
    role="admin",
    team="system",
)


async def _resolve_specialist_table(
    question: str,
    *,
    clickhouse: Any,
    metadata: Any | None,
    table_selector: TableSelector | None,
    required_columns: set[str],
    any_columns: set[str] | None = None,
    preferred_columns: set[str] | None = None,
) -> SpecialistTableResolution:
    warnings: list[str] = []
    metadata_tables, metadata_source = await _load_specialist_metadata_tables(
        question,
        metadata=metadata,
        clickhouse=clickhouse,
        warnings=warnings,
    )
    clickhouse_tables = _clickhouse_metadata_tables(metadata_tables)
    required = {column.lower() for column in required_columns}
    any_of = {column.lower() for column in any_columns or set()}
    preferred = {column.lower() for column in preferred_columns or set()}
    candidates = [
        table
        for table in clickhouse_tables
        if required <= _table_column_names(table)
        and (not any_of or bool(any_of & _table_column_names(table)))
    ]
    if preferred and any(preferred <= _table_column_names(table) for table in candidates):
        candidates = [
            table
            for table in candidates
            if preferred <= _table_column_names(table)
        ]
    elif preferred:
        warnings.append(
            "No live candidate table contains all preferred columns: "
            f"{', '.join(sorted(preferred))}."
        )
    candidates = _prefer_mart_then_silver(candidates)
    if not candidates:
        missing = sorted(required | any_of)
        warnings.append(
            "No live ClickHouse table matched the specialist schema contract "
            f"for columns: {', '.join(missing)}."
        )
        return SpecialistTableResolution(
            table=None,
            metadata_source=metadata_source,
            table_selection=None,
            candidate_count=0,
            required_columns=missing,
            warnings=warnings,
        )
    if table_selector is None:
        warnings.append("TableSelector is not configured; using live metadata order.")
        selected = candidates[0]
        return SpecialistTableResolution(
            table=selected,
            metadata_source=metadata_source,
            table_selection={
                "primaryTable": selected.get("tableName"),
                "selectedTables": [selected.get("tableName")],
                "strategy": "live_metadata_order",
                "confidence": 0.45,
                "confidenceLevel": "medium",
            },
            candidate_count=len(candidates),
            required_columns=sorted(required | any_of),
            warnings=warnings,
        )
    selection = await table_selector.select(
        question,
        candidates,
        datasource="clickhouse",
        min_tables=1,
        max_tables=1,
    )
    if selection.primary_table is None:
        warnings.append("TableSelector did not return a primary table.")
    return SpecialistTableResolution(
        table=selection.primary_table,
        metadata_source=metadata_source,
        table_selection=selection.to_dict(),
        candidate_count=len(candidates),
        required_columns=sorted(required | any_of),
        warnings=warnings,
    )


async def _load_specialist_metadata_tables(
    question: str,
    *,
    metadata: Any | None,
    clickhouse: Any,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], str]:
    if metadata is not None:
        metadata_source = str(getattr(metadata, "source_name", "openmetadata"))
        list_tables = getattr(metadata, "list_tables", None)
        if callable(list_tables):
            try:
                tables = await list_tables()
                if tables:
                    return tables, metadata_source
                warnings.append("Metadata service returned no tables.")
            except Exception as exc:
                warnings.append(f"Metadata list_tables failed: {str(exc)[:160]}")
        relevant_tables = getattr(metadata, "relevant_tables", None)
        if callable(relevant_tables):
            try:
                tables = await relevant_tables(question, limit=50)
                if tables:
                    return tables, metadata_source
                warnings.append("Metadata relevant_tables returned no tables.")
            except Exception as exc:
                warnings.append(
                    f"Metadata relevant_tables failed: {str(exc)[:160]}"
                )
    describe_tables = getattr(clickhouse, "describe_tables", None)
    if callable(describe_tables):
        try:
            configured_schemas = tuple(
                getattr(
                    getattr(clickhouse, "settings", None),
                    "clickhouse_analytics_schema_list",
                    ("gold", "silver", "vsf_mart", "vsf_stg"),
                )
            )
            tables = await describe_tables(configured_schemas)
            if tables:
                return tables, "clickhouse_schema"
            warnings.append("ClickHouse schema lookup returned no tables.")
        except Exception as exc:
            warnings.append(f"ClickHouse schema lookup failed: {str(exc)[:160]}")
    return [], "unavailable"


def _clickhouse_metadata_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_schemas = {"gold", "silver", "vsf_mart", "vsf_stg"}
    return [
        table
        for table in tables
        if _table_schema(table) in allowed_schemas
        and str(table.get("tableName") or "").strip()
    ]


def _prefer_mart_then_silver(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    curated = [
        table
        for table in tables
        if _table_schema(table) in {"vsf_mart", "gold"}
    ]
    if curated:
        return sorted(
            curated,
            key=lambda table: (
                _table_schema(table) != "vsf_mart",
                str(table.get("tableName") or ""),
            ),
        )
    return [
        table
        for table in tables
        if _table_schema(table) in {"silver", "vsf_stg"}
    ]


def _table_schema(table: dict[str, Any]) -> str:
    schema = str(
        table.get("database")
        or table.get("schema")
        or table.get("databaseSchema")
        or ""
    ).strip()
    if schema:
        return schema.lower()
    table_name = str(table.get("tableName") or "")
    return table_name.split(".", 1)[0].lower() if "." in table_name else ""


def _table_column_names(table: dict[str, Any] | None) -> set[str]:
    if not table:
        return set()
    return {
        str(column.get("name") or "").strip().lower()
        for column in table.get("columns") or []
        if str(column.get("name") or "").strip()
    }


def _filter_warnings(
    filters: dict[str, str],
    allowed_filters: set[str],
    table_columns: set[str],
) -> list[str]:
    missing = sorted(
        column
        for column in allowed_filters
        if filters.get(column) and column not in table_columns
    )
    if not missing:
        return []
    return [
        "Ignored filter column(s) not present in selected table: "
        f"{', '.join(missing)}."
    ]


def _metadata_evidence(
    resolution: SpecialistTableResolution,
    contract_name: str,
) -> Evidence:
    return Evidence(
        source="metadata",
        reference=resolution.metadata_source,
        summary=(
            f"Resolved {resolution.candidate_count} live candidate table(s) "
            f"for {contract_name}."
        ),
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


async def _clickhouse_query(
    connector: Any,
    sql: str,
    *,
    limit: int,
    principal: Principal | None,
) -> tuple[list[dict[str, Any]], str]:
    safe_sql = validate_readonly_sql(" ".join(sql.split()), limit)
    if principal is not None and not principal.is_admin:
        configured_schemas = tuple(
            getattr(
                getattr(connector, "settings", None),
                "clickhouse_analytics_schema_list",
                ("gold", "silver", "vsf_mart", "vsf_stg"),
            )
        )
        tables = await connector.describe_tables(configured_schemas)
        safe_sql = apply_row_security(
            safe_sql,
            dialect="clickhouse",
            tables=tables,
            principal=principal,
        )
        safe_sql = validate_readonly_sql(safe_sql, limit)
    rows = await connector.query(safe_sql)
    return rows, safe_sql


def _legacy_intent(request_plan: dict[str, Any]) -> str:
    route = str(
        (request_plan.get("slots") or {})
        .get("intent_type", {})
        .get("value")
        or "lookup"
    )
    return "aggregation" if route in {"insight", "forecast", "qa"} else route


def _artifact_row_count(result: dict[str, Any]) -> int:
    try:
        explicit = int(result.get("rowCount") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    rows = result.get("rows")
    return len(rows) if isinstance(rows, list) else 0


def _primary_artifact_result(
    responses: list[AgentResponse],
) -> dict[str, Any]:
    for response in responses:
        if response.agent == "chart_agent" and response.artifacts:
            result = response.artifacts[0].get("result")
            if isinstance(result, dict) and _artifact_row_count(result) > 0:
                return result
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict) and _artifact_row_count(result) > 0:
                return result
    for response in responses:
        if response.agent == "chart_agent" and response.artifacts:
            result = response.artifacts[0].get("result")
            if isinstance(result, dict):
                return result
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict):
                return result
    return {}


def _result_evidence(result: dict[str, Any]) -> list[Evidence]:
    if result.get("sql"):
        return [
            Evidence(
                source="sql",
                reference=str(result.get("sql") or "")[:500],
                summary=(
                    f"Returned {result.get('rowCount', 0)} rows from "
                    f"{', '.join(result.get('selectedTables') or [])}"
                ),
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
        ]
    return []


def _sql_evidence(sql: str, row_count: int, table_name: str) -> Evidence:
    return Evidence(
        source="sql",
        reference=sql[:500],
        summary=f"Returned {row_count} rows from {table_name}",
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def _merge_selected_tables(
    responses: list[AgentResponse],
    primary: dict[str, Any],
) -> list[str]:
    names: list[str] = []
    for name in primary.get("selectedTables") or []:
        if name not in names:
            names.append(name)
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if not isinstance(result, dict):
                continue
            for name in result.get("selectedTables") or []:
                if name not in names:
                    names.append(name)
    return names


def _merge_table_selection(
    responses: list[AgentResponse],
    primary: dict[str, Any],
) -> dict[str, Any] | None:
    if primary.get("tableSelection"):
        return primary.get("tableSelection")
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict) and result.get("tableSelection"):
                return result.get("tableSelection")
    return None


def _merge_metadata_context(
    responses: list[AgentResponse],
    primary: dict[str, Any],
) -> dict[str, Any] | None:
    if primary.get("metadataContext"):
        return primary.get("metadataContext")
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict) and result.get("metadataContext"):
                return result.get("metadataContext")
    return None


def _merge_sql_generation(
    responses: list[AgentResponse],
    primary: dict[str, Any],
) -> dict[str, Any] | None:
    if primary.get("sqlGeneration"):
        return primary.get("sqlGeneration")
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict) and result.get("sqlGeneration"):
                return result.get("sqlGeneration")
    return None


def _merge_searched_tables(
    responses: list[AgentResponse],
    primary: dict[str, Any],
) -> list[str] | None:
    if primary.get("searchedTables"):
        return primary.get("searchedTables")
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict) and result.get("searchedTables"):
                return result.get("searchedTables")
    return None


def _merge_attempts(
    responses: list[AgentResponse],
    primary: dict[str, Any],
) -> list[Any]:
    if primary.get("attempts"):
        return primary.get("attempts", [])
    for response in responses:
        for artifact in response.artifacts:
            result = artifact.get("result")
            if isinstance(result, dict) and result.get("attempts"):
                return result.get("attempts", [])
    return []


def _force_request_plan_route(
    question: str,
    request_plan: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    slots = request_plan.get("slots") if isinstance(request_plan, dict) else {}
    intent_slot = slots.get("intent_type") if isinstance(slots, dict) else {}
    requested_intent = str(
        intent_slot.get("value") if isinstance(intent_slot, dict) else ""
    )
    if requested_intent in {"dashboard", "full_report"}:
        expected_agents = ROUTING_TABLE[requested_intent]
        if plan.get("intent") != requested_intent or plan.get("agents") != expected_agents:
            rationale = str(plan.get("rationale") or "").strip()
            override_note = (
                "Server override: explicit dashboard or report request uses "
                "the matching specialist agent set."
            )
            plan = {
                **plan,
                "intent": requested_intent,
                "agents": expected_agents,
                "confidence": max(float(plan.get("confidence") or 0), 0.9),
                "rationale": f"{rationale} {override_note}".strip(),
            }
    return _force_chart_for_log_error_lookup(question, plan)


def _force_chart_for_log_error_lookup(
    question: str, plan: dict[str, Any]
) -> dict[str, Any]:
    if not _asks_log_error_lookup(question):
        return plan
    if plan.get("agents") == ["chart_agent"] and plan.get("intent") == "chart":
        return plan
    rationale = str(plan.get("rationale") or "").strip()
    override_note = "Server override: explicit log-error lookup needs log-detail SQL."
    return {
        **plan,
        "intent": "chart",
        "agents": ["chart_agent"],
        "confidence": max(float(plan.get("confidence") or 0), 0.9),
        "rationale": f"{rationale} {override_note}".strip(),
    }


def _asks_log_error_lookup(question: str) -> bool:
    text = _normalize_token(question)
    has_log_error = "log" in text and any(
        term in text for term in ("error", "errors", "loi", "failed", "failure")
    )
    if not has_log_error:
        return False
    asks_detail = any(
        term in text
        for term in (
            "message",
            "detail",
            "details",
            "underlying",
            "ve gi",
            "la gi",
            "noi dung",
            "show",
            "xem",
            "liet ke",
            "what",
            "which",
        )
    )
    asks_count = any(
        term in text for term in ("count", "so luong", "bao nhieu", "trend", "tong quan", "overview")
    )
    return asks_detail or not asks_count


def _merge_agent_usage(
    responses: list[AgentResponse],
    primary: dict[str, Any],
    extra_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_tokens = 0
    total_cost = 0.0
    model = provider = None
    for usage in [response.usage for response in responses]:
        total_tokens += int(usage.get("totalTokens") or usage.get("total_tokens") or 0)
        total_cost += float(usage.get("estimatedCostUsd") or 0)
        model = usage.get("model") or model
        provider = usage.get("provider") or provider
    if extra_usage:
        total_tokens += int(extra_usage.get("totalTokens") or extra_usage.get("total_tokens") or 0)
        total_cost += float(extra_usage.get("estimatedCostUsd") or 0)
        model = extra_usage.get("model") or model
        provider = extra_usage.get("provider") or provider
    if not total_tokens and primary.get("llmUsage"):
        return primary["llmUsage"]
    return {
        "provider": provider,
        "model": model,
        "totalTokens": total_tokens,
        "estimatedCostUsd": round(total_cost, 8),
    }


def _slot_value(
    slots: dict[str, Any],
    name: str,
    default: Any = None,
) -> Any:
    slot = slots.get(name) if isinstance(slots, dict) else None
    if isinstance(slot, dict):
        value = slot.get("value")
        return default if value in (None, "", []) else value
    return default if slot in (None, "", []) else slot


def _metric_values(slots: dict[str, Any], question: str) -> set[str]:
    raw_metric = _slot_value(slots, "metric", default=[])
    values = raw_metric if isinstance(raw_metric, list) else [raw_metric]
    normalized = {_normalize_token(value) for value in values if value}
    text = _normalize_token(question)
    if any(term in text for term in ("p95", "tail latency", "do tre p95")):
        normalized.add("p95_latency")
    if any(term in text for term in ("avg latency", "average latency", "trung binh")):
        normalized.add("avg_latency")
    if any(term in text for term in ("error", "errors", "loi", "failed", "failure")):
        normalized.add("error")
    if any(term in text for term in ("trace count", "traffic", "throughput", "volume", "so trace")):
        normalized.add("trace_count")
    if any(term in text for term in ("cost", "price", "usd", "money", "chi phi")):
        normalized.add("cost")
    if any(term in text for term in ("token", "tokens", "luong token")):
        normalized.add("tokens")
    return normalized


def _parse_days(value: Any, *, default: int) -> int:
    text = _normalize_token(value)
    if not text:
        return default
    if text in {"today", "hom nay", "24h"}:
        return 1
    if text in {"previous_week", "last week", "tuan truoc"}:
        return 7
    match = re.search(r"(\d+)\s*(h|hour|hours|d|day|days|ngay|w|week|weeks|tuan)?", text)
    if not match:
        return default
    amount = max(1, int(match.group(1)))
    unit = match.group(2) or "d"
    if unit in {"h", "hour", "hours"}:
        return max(1, math.ceil(amount / 24))
    if unit in {"w", "week", "weeks", "tuan"}:
        return min(amount * 7, 365)
    return min(amount, 365)


def _extract_entity_filters(question: str) -> dict[str, str]:
    filters: dict[str, str] = {}
    patterns = {
        "service_name": (
            r"\bservice\s+([a-zA-Z0-9_.:-]+)",
            r"\bdich\s+vu\s+([a-zA-Z0-9_.:-]+)",
            r"\bdichvu\s+([a-zA-Z0-9_.:-]+)",
        ),
        "source_app": (
            r"\bsource(?:\s+app)?\s+([a-zA-Z0-9_.:-]+)",
            r"\bsource_app\s+([a-zA-Z0-9_.:-]+)",
            r"\bapp\s+([a-zA-Z0-9_.:-]+)",
        ),
        "model": (
            r"\bmodel\s+([a-zA-Z0-9_./:-]+)",
            r"\bmo\s+hinh\s+([a-zA-Z0-9_./:-]+)",
        ),
    }
    for key, key_patterns in patterns.items():
        for pattern in key_patterns:
            match = re.search(pattern, question, flags=re.IGNORECASE)
            if not match:
                continue
            value = _clean_entity_value(match.group(1))
            if value:
                filters[key] = value
                break
    return filters


def _clickhouse_where_clause(
    *,
    days: int,
    filters: dict[str, str],
    allowed_columns: set[str],
) -> str:
    clauses = [f"metric_date >= today() - {max(1, min(days, 365))}"]
    for column in sorted(allowed_columns):
        value = filters.get(column)
        if not value:
            continue
        clauses.append(
            f"positionCaseInsensitiveUTF8({column}, {_sql_string(value)}) > 0"
        )
    return " AND ".join(clauses)


def _insight_order_clause(question: str, slots: dict[str, Any]) -> str:
    metrics = _metric_values(slots, question)
    if "error" in metrics:
        return "error_rate_pct DESC, error_span_count DESC, p95_latency_ms DESC"
    if "trace_count" in metrics or "throughput" in metrics:
        return "span_count DESC, p95_latency_ms DESC"
    return "p95_latency_ms DESC, error_rate_pct DESC"


def _forecast_metric_plan(
    question: str,
    slots: dict[str, Any],
    *,
    service_scoped: bool,
) -> dict[str, str]:
    del service_scoped
    metrics = _metric_values(slots, question)
    if "error" in metrics:
        return {
            "targetMetric": "error_count",
            "unit": "events",
            "metricFamily": "error",
        }
    if "trace_count" in metrics or "throughput" in metrics:
        return {
            "targetMetric": "trace_count",
            "unit": "traces",
            "metricFamily": "throughput",
        }
    if "avg_latency" in metrics:
        return {
            "targetMetric": "avg_latency_ms",
            "unit": "ms",
            "metricFamily": "avg_latency",
        }
    if "cost" in metrics:
        return {
            "targetMetric": "total_cost_usd",
            "unit": "usd",
            "metricFamily": "cost",
        }
    if "tokens" in metrics:
        return {
            "targetMetric": "total_tokens",
            "unit": "tokens",
            "metricFamily": "tokens",
        }
    return {
        "targetMetric": "p95_latency_ms",
        "unit": "ms",
        "metricFamily": "p95_latency",
    }


def _forecast_metric_columns(metric_plan: dict[str, str]) -> set[str]:
    family = metric_plan.get("metricFamily")
    column_map = {
        "error": {"error_count", "error_span_count"},
        "throughput": {"trace_count", "span_count", "generation_count"},
        "avg_latency": {"avg_latency_ms", "avg_span_duration_ms"},
        "p95_latency": {"p95_latency_ms", "p95_span_duration_ms"},
        "cost": {"total_cost_usd", "estimated_cost_usd"},
        "tokens": {"total_tokens"},
    }
    return column_map.get(family, {metric_plan["targetMetric"]})


def _forecast_domain_preferred_columns(metric_plan: dict[str, str]) -> set[str]:
    family = metric_plan.get("metricFamily")
    if family in {"error", "avg_latency", "p95_latency"}:
        return {"trace_count"}
    return set()


def _forecast_expression_for_table(
    metric_plan: dict[str, str],
    table: dict[str, Any],
) -> str | None:
    columns = _table_column_names(table)
    family = metric_plan.get("metricFamily")
    expression_map = {
        "error": [
            ("error_count", "sum(error_count)"),
            ("error_span_count", "sum(error_span_count)"),
        ],
        "throughput": [
            ("trace_count", "sum(trace_count)"),
            ("span_count", "sum(span_count)"),
            ("generation_count", "sum(generation_count)"),
        ],
        "avg_latency": [
            ("avg_latency_ms", "round(avg(avg_latency_ms), 2)"),
            ("avg_span_duration_ms", "round(avg(avg_span_duration_ms), 2)"),
        ],
        "p95_latency": [
            ("p95_latency_ms", "round(avg(p95_latency_ms), 2)"),
            ("p95_span_duration_ms", "round(avg(p95_span_duration_ms), 2)"),
        ],
        "cost": [
            ("total_cost_usd", "sum(total_cost_usd)"),
            ("estimated_cost_usd", "sum(estimated_cost_usd)"),
        ],
        "tokens": [
            ("total_tokens", "sum(total_tokens)"),
        ],
    }
    for column, expression in expression_map.get(family, []):
        if column in columns:
            return expression
    target_metric = metric_plan.get("targetMetric")
    if target_metric and target_metric in columns:
        return f"sum({target_metric})"
    return None


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _clean_entity_value(value: str) -> str:
    cleaned = value.strip().strip(".,;:!?()[]{}\"'")
    stop_words = {
        "for",
        "of",
        "by",
        "trong",
        "cua",
        "vung",
        "last",
        "ngay",
        "days",
        "day",
    }
    return "" if cleaned.lower() in stop_words else cleaned[:120]


def _normalize_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    try:
        import unicodedata

        text = "".join(
            char
            for char in unicodedata.normalize("NFD", text)
            if unicodedata.category(char) != "Mn"
        )
    except Exception:
        pass
    return text


def _normalize_insight_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_name": str(row.get("service_name") or "unknown"),
        "source_app": str(row.get("source_app") or "unknown"),
        "span_count": _to_int(row.get("span_count")),
        "error_span_count": _to_int(row.get("error_span_count")),
        "error_rate_pct": _to_float(row.get("error_rate_pct")),
        "avg_latency_ms": _to_float(row.get("avg_latency_ms")),
        "p95_latency_ms": _to_float(row.get("p95_latency_ms")),
    }


def _normalize_qa_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_app": str(row.get("source_app") or "unknown"),
        "batch_count": _to_int(row.get("batch_count")),
        "failed_batch_count": _to_int(row.get("failed_batch_count")),
        "quarantined_batch_count": _to_int(row.get("quarantined_batch_count")),
        "loaded_row_count": _to_int(row.get("loaded_row_count")),
        "failed_batch_rate_pct": _to_float(row.get("failed_batch_rate_pct")),
    }


def _forecast_history(
    rows: list[dict[str, Any]],
    *,
    metric_name: str,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for row in rows:
        metric_date = _date_string(row.get("metric_date"))
        value = _to_float(row.get("forecast_value") or row.get(metric_name))
        if not metric_date or value <= 0:
            continue
        point = {
            "metric_date": metric_date,
            "value": value,
            metric_name: value,
            "avg_latency_ms": _to_float(row.get("avg_latency_ms")),
            "trace_count": _to_int(row.get("trace_count")),
            "error_count": _to_int(row.get("error_count")),
        }
        for k, v in row.items():
            if k not in point and k not in ("metric_date", "forecast_value"):
                if v is not None:
                    try:
                        point[k] = _to_int(v) if "count" in k or "total" in k else _to_float(v)
                    except Exception:
                        point[k] = v
        history.append(point)
    return sorted(history, key=lambda item: item["metric_date"])


def _fit_linear_regression(x_values: list[float], y_values: list[float]) -> dict[str, float]:
    n = len(x_values)
    mean_x = sum(x_values) / n
    mean_y = sum(y_values) / n
    variance_x = sum((x - mean_x) ** 2 for x in x_values)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_values, y_values))
    slope = covariance / variance_x if variance_x else 0.0
    intercept = mean_y - slope * mean_x
    predictions = [intercept + slope * x for x in x_values]
    ss_res = sum((actual - predicted) ** 2 for actual, predicted in zip(y_values, predictions))
    ss_tot = sum((actual - mean_y) ** 2 for actual in y_values)
    r_squared = 1 - ss_res / ss_tot if ss_tot else 1.0
    residual_rmse = math.sqrt(ss_res / n) if n else 0.0
    return {
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "rSquared": round(max(0.0, min(1.0, r_squared)), 6),
        "rmse": round(residual_rmse, 6),
    }


def _select_forecast_model(
    history: list[dict[str, Any]],
    *,
    horizon_days: int,
    metric_name: str,
    unit: str = "ms",
) -> dict[str, Any]:
    values = [point["value"] for point in history]
    candidates = [
        _forecast_candidate("recent_trend", values, horizon_days),
        _forecast_candidate("linear_regression", values, horizon_days),
        _forecast_candidate("moving_average", values, horizon_days),
        _forecast_candidate("ewma", values, horizon_days),
    ]
    candidates = sorted(
        candidates,
        key=lambda candidate: (
            candidate["validation"]["mae"],
            _model_priority(str(candidate["model"])),
        ),
    )
    selected = candidates[0]
    forecast_points = _dated_forecast_points(
        history,
        selected["predictions"],
        metric_name=metric_name,
    )
    trend_prediction = _predict_next_trend(
        history,
        forecast_points,
        selected,
        horizon_days=horizon_days,
        metric_name=metric_name,
        unit=unit,
    )
    return {
        "selectedModel": selected["model"],
        "parameters": selected["parameters"],
        "forecast": forecast_points,
        "trendPrediction": trend_prediction,
        "modelCandidates": [
            {
                "model": candidate["model"],
                "validation": candidate["validation"],
                "parameters": candidate["parameters"],
            }
            for candidate in candidates
        ],
    }


def _forecast_candidate(
    model_name: str,
    values: list[float],
    horizon_days: int,
) -> dict[str, Any]:
    predictions, parameters = _forecast_values(model_name, values, horizon_days)
    return {
        "model": model_name,
        "predictions": predictions,
        "parameters": parameters,
        "validation": _rolling_validation(values, model_name),
    }


def _forecast_values(
    model_name: str,
    values: list[float],
    horizon_days: int,
) -> tuple[list[float], dict[str, Any]]:
    if model_name == "linear_regression":
        model = _fit_linear_regression(
            [float(index) for index in range(len(values))],
            values,
        )
        predictions = [
            max(0.0, model["intercept"] + model["slope"] * (len(values) - 1 + offset))
            for offset in range(1, horizon_days + 1)
        ]
        return predictions, model
    if model_name == "recent_trend":
        deltas = [
            values[index] - values[index - 1]
            for index in range(1, len(values))
        ]
        window = min(5, len(deltas))
        recent_delta = sum(deltas[-window:]) / window if window else 0.0
        predictions = [
            max(0.0, values[-1] + recent_delta * offset)
            for offset in range(1, horizon_days + 1)
        ]
        return predictions, {
            "recentDeltaMsPerDay": round(recent_delta, 6),
            "window": window,
        }
    if model_name == "moving_average":
        window = min(7, max(2, len(values)))
        average = sum(values[-window:]) / window
        return [max(0.0, average) for _ in range(horizon_days)], {
            "window": window,
            "average": round(average, 6),
        }
    if model_name == "ewma":
        alpha = 0.45
        smoothed = values[0]
        for value in values[1:]:
            smoothed = alpha * value + (1 - alpha) * smoothed
        return [max(0.0, smoothed) for _ in range(horizon_days)], {
            "alpha": alpha,
            "level": round(smoothed, 6),
        }
    return [values[-1] for _ in range(horizon_days)], {}


def _rolling_validation(values: list[float], model_name: str) -> dict[str, Any]:
    errors: list[float] = []
    start = 2 if len(values) >= 3 else 1
    for index in range(start, len(values)):
        train = values[:index]
        if len(train) < 2 and model_name in {"linear_regression", "recent_trend"}:
            predicted = train[-1]
        else:
            predicted = _forecast_values(model_name, train, 1)[0][0]
        errors.append(abs(predicted - values[index]))
    mae = sum(errors) / len(errors) if errors else 0.0
    return {
        "mae": round(mae, 6),
        "backtestPoints": len(errors),
    }


def _dated_forecast_points(
    history: list[dict[str, Any]],
    predictions: list[float],
    *,
    metric_name: str,
) -> list[dict[str, Any]]:
    last_date = _parse_date(history[-1]["metric_date"])
    points: list[dict[str, Any]] = []
    for offset, predicted in enumerate(predictions, start=1):
        points.append(
            {
                "metric_date": (last_date + timedelta(days=offset)).isoformat(),
                "value": round(max(0.0, predicted), 2),
                metric_name: round(max(0.0, predicted), 2),
            }
        )
    return points


def _predict_next_trend(
    history: list[dict[str, Any]],
    forecast_points: list[dict[str, Any]],
    selected: dict[str, Any],
    *,
    horizon_days: int,
    metric_name: str,
    unit: str,
) -> dict[str, Any]:
    last_actual = history[-1]["value"]
    next_value = forecast_points[0]["value"]
    endpoint = forecast_points[-1]["value"]
    expected_change = endpoint - last_actual
    expected_change_pct = (
        expected_change / last_actual * 100
        if last_actual
        else 0.0
    )
    recent_noise = _median(
        [
            abs(history[index]["value"] - history[index - 1]["value"])
            for index in range(1, len(history))
        ]
    )
    model_mae = float(selected.get("validation", {}).get("mae") or 0)
    threshold = max(1.0, last_actual * 0.03, recent_noise * 0.5, model_mae * 0.5)
    if abs(expected_change) <= threshold:
        direction = "stable"
    else:
        direction = "increasing" if expected_change > 0 else "decreasing"
    next_change = next_value - last_actual
    if abs(next_change) <= max(1.0, threshold / horizon_days):
        next_direction = "stable"
    else:
        next_direction = "increasing" if next_change > 0 else "decreasing"
    strength = min(abs(expected_change) / threshold, 3.0) / 3.0
    normalized_error = model_mae / max(last_actual, 1.0)
    confidence = 0.45 + 0.35 * strength + 0.2 * max(0.0, 1.0 - normalized_error)

    unit_suffix = "Ms" if unit == "ms" else ("Events" if unit == "events" else unit.title().replace(" ", ""))
    metric_suffix = "".join(part.title() for part in metric_name.split("_") if part)

    res = {
        "direction": direction,
        "nextPeriodDirection": next_direction,
        "expectedChange": round(expected_change, 2),
        "expectedChangeMs": round(expected_change, 2),  # Compatibility key
        "expectedChangePct": round(expected_change_pct, 2),
        "lastObserved": round(last_actual, 2),
        "lastObservedMs": round(last_actual, 2),  # Compatibility key
        "nextPredicted": round(next_value, 2),
        "nextPredictedMs": round(next_value, 2),  # Compatibility key
        "horizonEndpoint": round(endpoint, 2),
        "horizonEndpointMs": round(endpoint, 2),  # Compatibility key
        "horizonDays": horizon_days,
        "thresholdMs": round(threshold, 2),  # Compatibility key
        "confidence": round(max(0.3, min(confidence, 0.92)), 2),
    }

    # Add dynamic keys based on unit
    res[f"expectedChange{unit_suffix}"] = round(expected_change, 2)
    res[f"lastObserved{unit_suffix}"] = round(last_actual, 2)
    res[f"nextPredicted{unit_suffix}"] = round(next_value, 2)
    res[f"horizonEndpoint{unit_suffix}"] = round(endpoint, 2)
    res[f"threshold{unit_suffix}"] = round(threshold, 2)

    # Add dynamic keys based on metric name
    res[f"expectedChange{metric_suffix}"] = round(expected_change, 2)
    res[f"lastObserved{metric_suffix}"] = round(last_actual, 2)
    res[f"nextPredicted{metric_suffix}"] = round(next_value, 2)
    res[f"horizonEndpoint{metric_suffix}"] = round(endpoint, 2)
    res[f"threshold{metric_suffix}"] = round(threshold, 2)

    return res


def _forecast_confidence(forecast_result: dict[str, Any], points: int) -> float:
    sample_factor = min(points / 14, 1.0)
    trend_confidence = float(
        forecast_result.get("trendPrediction", {}).get("confidence") or 0.5
    )
    best_validation = (
        forecast_result.get("modelCandidates", [{}])[0].get("validation", {})
        if forecast_result.get("modelCandidates")
        else {}
    )
    mae = float(best_validation.get("mae") or 0)
    last_value = float(
        (forecast_result.get("forecast") or [{"value": 1}])[0].get(
            "value"
        )
        or 1
    )
    error_score = max(0.0, 1.0 - mae / max(last_value, 1.0))
    score = 0.3 + 0.35 * sample_factor + 0.2 * trend_confidence + 0.15 * error_score
    return round(max(0.35, min(score, 0.92)), 2)


def _model_priority(model_name: str) -> int:
    priorities = {
        "recent_trend": 0,
        "linear_regression": 1,
        "ewma": 2,
        "moving_average": 3,
    }
    return priorities.get(model_name, 99)


def _detect_anomalies(
    rows: list[dict[str, Any]],
    metric: str,
    z_threshold: float = 2.0,
    method: str = "hybrid",
    iqr_factor: float = 1.5,
) -> list[dict[str, Any]]:
    values = [
        _to_float(row.get(metric))
        for row in rows
        if row.get(metric) not in (None, "")
    ]
    values = [value for value in values if value > 0]
    if len(values) < 3:
        return []

    # 1. Z-score anomalies
    z_anomalies = {}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    if std > 0 and mean > 0:
        for row in rows:
            value = _to_float(row.get(metric))
            if value <= 0:
                continue
            z_score = (value - mean) / std
            if abs(z_score) >= z_threshold:
                key = (row.get("service_name", "unknown"), row.get("source_app", "unknown"))
                deviation_pct = (value - mean) / mean * 100
                z_anomalies[key] = {
                    "z_score": round(z_score, 2),
                    "anomaly_type": "spike" if z_score > 0 else "drop",
                    "deviation_from_mean_pct": round(deviation_pct, 1),
                    "severity": _anomaly_severity(abs(z_score)),
                    "baseline_mean": round(mean, 4),
                    "baseline_std": round(std, 4),
                }

    # 2. IQR anomalies
    iqr_anomalies = {}
    if len(values) >= 4:
        sorted_val = sorted(values)

        def get_p(p: float) -> float:
            k = (len(sorted_val) - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_val[int(k)]
            return sorted_val[f] * (c - k) + sorted_val[c] * (k - f)

        q1 = get_p(0.25)
        q3 = get_p(0.75)
        iqr = q3 - q1
        if iqr > 0:
            lower_bound = q1 - iqr_factor * iqr
            upper_bound = q3 + iqr_factor * iqr
            for row in rows:
                value = _to_float(row.get(metric))
                if value <= 0:
                    continue
                if value < lower_bound or value > upper_bound:
                    key = (row.get("service_name", "unknown"), row.get("source_app", "unknown"))
                    deviation_pct = (value - (q1 + q3)/2) / ((q1 + q3)/2) * 100 if (q1 + q3) > 0 else 0.0
                    anomaly_type = "spike" if value > upper_bound else "drop"
                    dist = value - upper_bound if value > upper_bound else lower_bound - value
                    severity = "critical" if dist > 2 * iqr else "high" if dist > iqr else "medium"
                    iqr_anomalies[key] = {
                        "iqr_q1": round(q1, 4),
                        "iqr_q3": round(q3, 4),
                        "iqr": round(iqr, 4),
                        "anomaly_type": anomaly_type,
                        "deviation_from_mean_pct": round(deviation_pct, 1),
                        "severity": severity,
                    }

    # Merge anomalies based on method
    merged_anomalies: list[dict[str, Any]] = []

    for row in rows:
        key = (row.get("service_name", "unknown"), row.get("source_app", "unknown"))
        value = _to_float(row.get(metric))
        if value <= 0:
            continue

        in_z = key in z_anomalies
        in_iqr = key in iqr_anomalies

        is_anomaly = False
        actual_in_z = False
        actual_in_iqr = False

        if method == "z_score" and in_z:
            is_anomaly = True
            actual_in_z = True
        elif method == "iqr" and in_iqr:
            is_anomaly = True
            actual_in_iqr = True
        elif method == "hybrid" and (in_z or in_iqr):
            is_anomaly = True
            actual_in_z = in_z
            actual_in_iqr = in_iqr

        if is_anomaly:
            anomaly_data = {
                **row,
                "metric": metric,
                "metric_value": round(value, 4),
                "detection_method": "z_score" if (actual_in_z and not actual_in_iqr) else "iqr" if (actual_in_iqr and not actual_in_z) else "both",
            }

            # Merge fields from z-score
            if in_z and method in ("z_score", "hybrid"):
                anomaly_data.update(z_anomalies[key])

            # Merge fields from iqr
            if in_iqr and method in ("iqr", "hybrid"):
                # If both, use the maximum severity
                if in_z and method == "hybrid":
                    z_sev = z_anomalies[key]["severity"]
                    iqr_sev = iqr_anomalies[key]["severity"]
                    sevs = ["medium", "high", "critical"]
                    if sevs.index(iqr_sev) > sevs.index(z_sev):
                        anomaly_data["severity"] = iqr_sev
                else:
                    anomaly_data["severity"] = iqr_anomalies[key]["severity"]
                    anomaly_data["anomaly_type"] = iqr_anomalies[key]["anomaly_type"]
                    anomaly_data["deviation_from_mean_pct"] = iqr_anomalies[key]["deviation_from_mean_pct"]

                anomaly_data.update({
                    "iqr_q1": iqr_anomalies[key]["iqr_q1"],
                    "iqr_q3": iqr_anomalies[key]["iqr_q3"],
                    "iqr": iqr_anomalies[key]["iqr"],
                })

            merged_anomalies.append(anomaly_data)

    return sorted(
        merged_anomalies,
        key=lambda item: abs(float(item.get("z_score") or item.get("deviation_from_mean_pct") or 0)),
        reverse=True,
    )


def _merge_anomaly_sets(
    anomaly_sets: list[tuple[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for signal, anomalies in anomaly_sets:
        for anomaly in anomalies:
            merged.append({**anomaly, "signal": signal})
    return sorted(
        merged,
        key=lambda item: (
            _severity_rank(str(item.get("severity") or "")),
            abs(float(item.get("z_score") or 0)),
        ),
        reverse=True,
    )


def _anomaly_severity(abs_z_score: float) -> str:
    if abs_z_score >= 3:
        return "critical"
    if abs_z_score >= 2.5:
        return "high"
    return "medium"


def _severity_rank(severity: str) -> int:
    return {"critical": 3, "high": 2, "medium": 1}.get(severity, 0)


def _insight_summary(
    top_latency: dict[str, Any],
    top_error: dict[str, Any],
    latency_ratio: float,
    anomalies: list[dict[str, Any]] | None = None,
) -> str:
    parts = [
        (
            f"Highest p95 latency is {top_latency['p95_latency_ms']:.2f} ms "
            f"on service {top_latency['service_name']} / source {top_latency['source_app']} "
            f"({latency_ratio:.2f}x median)."
        )
    ]
    if top_error["error_rate_pct"] > 0:
        parts.append(
            (
                f"Highest error rate is {top_error['error_rate_pct']:.2f}% "
                f"on service {top_error['service_name']} / source {top_error['source_app']}."
            )
        )
    if anomalies:
        lead = anomalies[0]
        parts.append(
            (
                f"Detected {len(anomalies)} z-score anomaly signal(s); strongest is "
                f"{lead['metric']} {lead['anomaly_type']} on service "
                f"{lead['service_name']} / source {lead['source_app']} "
                f"(z={lead['z_score']:.2f}, {lead['severity']})."
            )
        )
    return " ".join(parts)


def _qa_summary(
    issues: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
) -> str:
    if not rows and not audit_rows:
        return "QA agent did not find ingestion or audit health rows for the current window."
    if not issues:
        return "QA agent found no failed batches, quarantined batches, or failed agent queries in the checked windows."
    signals = ", ".join(f"{issue['signal']}={issue['count']}" for issue in issues)
    return f"QA agent found data-health issues: {signals}."


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    midpoint = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[midpoint]
    return (sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2


def _date_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return datetime.now(timezone.utc).date()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _elapsed_ms(started: datetime) -> int:
    return round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
