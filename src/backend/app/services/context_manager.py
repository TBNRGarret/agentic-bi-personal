from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..connectors.postgres import PostgresConnector
from ..security import Principal


class ContextManager:
    def __init__(self, postgres: PostgresConnector) -> None:
        self.postgres = postgres

    async def create_session(
        self, principal: Principal, title: str = "New conversation"
    ) -> dict[str, Any]:
        return await self.postgres.create_chat_session(principal.user_id, title)

    async def list_sessions(
        self, principal: Principal, limit: int = 30
    ) -> list[dict[str, Any]]:
        return await self.postgres.list_chat_sessions(principal.user_id, limit)

    async def get_session(
        self, principal: Principal, session_id: str
    ) -> dict[str, Any] | None:
        return await self.postgres.get_chat_session(session_id, principal.user_id)

    async def load_context(
        self, principal: Principal, session_id: str | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not session_id:
            return {}, []
        session = await self.get_session(principal, session_id)
        if not session:
            raise ValueError("Conversation session was not found")
        conversation = [
            {"role": message["role"], "content": message["content"]}
            for message in session.get("messages") or []
        ][-12:]
        return dict(session.get("structured_memory") or {}), conversation

    async def persist_turn(
        self,
        *,
        principal: Principal,
        session_id: str,
        question: str,
        result: dict[str, Any],
        previous_memory: dict[str, Any],
    ) -> dict[str, Any]:
        memory = extract_memory(question, result, previous_memory)
        suggestions = (
            []
            if result.get("status") == "needs_clarification"
            else suggest_followups(result, memory)
        )
        result["sessionId"] = session_id
        result["memory"] = memory
        result["suggestions"] = suggestions
        await self.postgres.append_chat_turn(
            session_id=session_id,
            user_id=principal.user_id,
            question=question,
            answer=str(result.get("answer") or ""),
            payload=_compact_payload(result),
            structured_memory=memory,
        )
        return result


def contextual_question(question: str, memory: dict[str, Any]) -> str:
    if not memory or not _is_followup(question):
        return question
    context_parts: list[str] = []
    for key in ("entities", "metrics", "filters"):
        if key == "metrics" and _metrics(_normalize_text(question)):
            continue
        values = memory.get(key)
        if isinstance(values, list) and values:
            context_parts.append(f"{key}: {', '.join(map(str, values[:8]))}")
    if memory.get("timeframe"):
        context_parts.append(f"timeframe: {memory['timeframe']}")
    return (
        f"{question}\nResolved conversation context: {'; '.join(context_parts)}"
        if context_parts
        else question
    )


def extract_memory(
    question: str,
    result: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _normalize_text(question)
    memory = dict(previous or {})
    entities = _ordered_union(
        memory.get("entities"),
        _entities(text, result),
    )
    metrics = _ordered_union(memory.get("metrics"), _metrics(text))
    filters = _ordered_union(
        memory.get("filters"),
        _filters(text, result, entities),
    )
    timeframe = _timeframe(text) or memory.get("timeframe")
    return {
        "entities": entities[-12:],
        "metrics": metrics[-8:],
        "filters": filters[-12:],
        "timeframe": timeframe,
        "lastIntent": result.get("intent"),
        "lastTarget": result.get("target"),
        "lastTables": result.get("selectedTables") or [],
    }


def suggest_followups(
    result: dict[str, Any], memory: dict[str, Any]
) -> list[str]:
    table = " ".join(result.get("selectedTables") or []).lower()
    entity = _suggestion_entity(memory)
    trend = _trend_suggestion(result, memory, entity)
    anomaly = _anomaly_suggestion(result)
    if "system_observability" in table:
        if _has_positive_metric(result, "error_log_count"):
            suggestions = [
                trend or f"Show the error log trend for {entity} over the same timeframe",
                anomaly or f"Compare log errors for {entity} with other services",
                f"Show error log messages for {entity} over the same timeframe",
            ]
            return _unique_suggestions(suggestions)
        suggestions = [
            trend or f"Show the error trend for {entity} over the same timeframe",
            anomaly or f"Compare latency of {entity} with other services",
            f"Show trace details behind the errors for {entity}",
        ]
        return _unique_suggestions(suggestions)
    if "trace_health" in table:
        suggestions = [
            trend or f"Compare trace errors for {entity} with the previous period",
            anomaly or "Which source app contributes the most trace errors?",
            "Show the slowest trace details for this result",
        ]
        return _unique_suggestions(suggestions)
    if "llm" in table:
        suggestions = [
            trend or "Compare token usage with the previous period",
            anomaly or "Which model contributed most to this cost?",
            "Show the most expensive agent queries",
        ]
        return _unique_suggestions(suggestions)
    if "ingestion" in table:
        suggestions = [
            trend or "Compare ingestion throughput with the previous period",
            anomaly or "Which source app has the highest failure rate?",
            "Show failed or quarantined batches",
        ]
        return _unique_suggestions(suggestions)
    return _unique_suggestions(
        [
            trend or "Compare this result with the previous period",
            anomaly or "Break this metric down by service or source app",
            "Show the underlying detail rows",
        ]
    )


def _has_positive_metric(result: dict[str, Any], metric: str) -> bool:
    return any(
        (_number(row.get(metric)) or 0) > 0
        for row in result.get("rows") or []
        if isinstance(row, dict)
    )


def _trend_suggestion(
    result: dict[str, Any],
    memory: dict[str, Any],
    entity: str,
) -> str | None:
    rows = [
        row for row in result.get("rows") or [] if isinstance(row, dict)
    ]
    dated = [
        row
        for row in rows
        if any(key in row for key in ("metric_date", "event_date", "day"))
    ]
    if len(dated) < 2:
        return None
    metric = _preferred_numeric_metric(dated, memory)
    if not metric:
        return None
    ordered = sorted(
        dated,
        key=lambda row: str(
            row.get("metric_date") or row.get("event_date") or row.get("day") or ""
        ),
    )
    previous = _number(ordered[-2].get(metric))
    current = _number(ordered[-1].get(metric))
    if previous is None or current is None or previous == 0:
        return None
    change = (current - previous) / abs(previous) * 100
    if abs(change) < 1:
        return f"{_metric_label(metric)} is stable. Break it down for {entity}?"
    direction = "increased" if change > 0 else "decreased"
    return (
        f"{_metric_label(metric)} {direction} {abs(change):.0f}% in the latest "
        f"period. Show the drivers for {entity}?"
    )


def _anomaly_suggestion(result: dict[str, Any]) -> str | None:
    rows = [
        row for row in result.get("rows") or [] if isinstance(row, dict)
    ]
    if not rows:
        return None
    dimension = next(
        (
            key
            for key in ("service_name", "source_app", "model")
            if any(row.get(key) not in (None, "") for row in rows)
        ),
        None,
    )
    metric = _preferred_numeric_metric(rows, {})
    if not dimension or not metric:
        return None
    candidates = [
        (row, _number(row.get(metric)))
        for row in rows
        if row.get(dimension) not in (None, "")
    ]
    candidates = [(row, value) for row, value in candidates if value is not None]
    if not candidates:
        return None
    top, value = max(candidates, key=lambda item: abs(item[1]))
    return (
        f"{top[dimension]} has the highest {_metric_label(metric)} "
        f"({value:g}). Investigate its underlying details?"
    )


def _preferred_numeric_metric(
    rows: list[dict[str, Any]], memory: dict[str, Any]
) -> str | None:
    preferred = [
        str(metric)
        for metric in memory.get("metrics") or []
    ] + [
        "error_rate",
        "error_rate_pct",
        "error_count",
        "error_log_count",
        "error_span_count",
        "p95_latency_ms",
        "p95_span_duration_ms",
        "total_cost_usd",
        "estimated_cost_usd",
        "total_tokens",
        "loaded_row_count",
        "trace_count",
    ]
    keys = {key for row in rows for key in row}
    for candidate in preferred:
        if candidate in keys and any(_number(row.get(candidate)) is not None for row in rows):
            return candidate
    for key in keys:
        if key.endswith(("_count", "_tokens", "_usd", "_ms", "_pct")) and any(
            _number(row.get(key)) is not None for row in rows
        ):
            return key
    return None


def _metric_label(metric: str) -> str:
    return metric.replace("_", " ")


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _unique_suggestions(values: list[str]) -> list[str]:
    suggestions: list[str] = []
    for value in values:
        if value and value not in suggestions:
            suggestions.append(value)
    return suggestions[:3]


def _entities(text: str, result: dict[str, Any]) -> list[str]:
    entities: list[str] = []
    for row in result.get("rows") or []:
        for key in ("service_name", "source_app", "model"):
            value = row.get(key) if isinstance(row, dict) else None
            if value and str(value).lower() in text:
                entities.append(str(value))
    match = re.search(r"\bservice\s+([a-z0-9_.-]+)", text)
    if match and _valid_entity(match.group(1)):
        entities.append(match.group(1))
    match = re.search(r"\b([a-z0-9_.-]+)\s+service\b", text)
    if match and _valid_entity(match.group(1)):
        entities.append(match.group(1))
    for pattern in (
        r"\bsource\s+app\s+([a-z0-9_.-]+)",
        r"\bmodel\s+([a-z0-9_./:-]+)",
    ):
        match = re.search(pattern, text)
        if match and _valid_entity(match.group(1)):
            entities.append(match.group(1))
    return entities


def _suggestion_entity(memory: dict[str, Any]) -> str:
    for value in memory.get("entities") or []:
        if _valid_entity(str(value)):
            return str(value)
    return "each service"


def _valid_entity(value: str) -> bool:
    return value.lower() not in {
        "a",
        "all",
        "each",
        "error",
        "errors",
        "latency",
        "other",
        "service",
        "services",
        "the",
        "this",
    }


def _metrics(text: str) -> list[str]:
    mapping = {
        "error": ("error", "loi", "failure"),
        "error_rate": ("error rate", "ty le loi"),
        "latency": ("latency", "do tre", "p95"),
        "cost": ("cost", "chi phi"),
        "tokens": ("token",),
        "throughput": ("throughput", "loaded row"),
        "trace_count": ("trace",),
    }
    return [
        name
        for name, terms in mapping.items()
        if any(term in text for term in terms)
    ]


def _filters(
    text: str,
    result: dict[str, Any],
    entities: list[str],
) -> list[str]:
    route = result.get("route") or {}
    values = [
        f"datasource={route.get('datasource')}" if route.get("datasource") else "",
        f"schema={route.get('schema')}" if route.get("schema") else "",
    ]
    rows = [
        row for row in result.get("rows") or [] if isinstance(row, dict)
    ]
    for column in ("service_name", "source_app", "model"):
        for row in rows:
            value = row.get(column)
            if value and _normalize_text(str(value)) in text:
                values.append(f"{column}={value}")
    if entities and "service" in text and not any(
        value.startswith("service_name=") for value in values
    ):
        values.append(f"service_name={entities[-1]}")
    return [value for value in values if value]


def _timeframe(text: str) -> str | None:
    match = re.search(r"\b(\d+)\s*(h|hour|hours|d|day|days|week|weeks|tuan|ngay)\b", text)
    if match:
        unit = match.group(2)
        normalized_unit = (
            "h" if unit in {"h", "hour", "hours"}
            else "d" if unit in {"d", "day", "days", "ngay"}
            else "w"
        )
        return f"{match.group(1)}{normalized_unit}"
    if any(term in text for term in ("last week", "tuan truoc")):
        return "previous_week"
    if any(term in text for term in ("today", "hom nay")):
        return "today"
    return None


def _is_followup(question: str) -> bool:
    text = _normalize_text(question)
    return len(text.split()) <= 8 or any(
        term in text
        for term in (
            "tang hay giam",
            "what about",
            "and latency",
            "so voi",
            "compare it",
            "chi tiet hon",
            "why",
            "tai sao",
        )
    )


def _ordered_union(current: Any, additions: list[str]) -> list[str]:
    values = [str(item) for item in current or []]
    for item in additions:
        if item and item not in values:
            values.append(item)
    return values


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    ).replace("đ", "d")


def _compact_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: result.get(key)
        for key in (
            "answer",
            "status",
            "target",
            "intent",
            "sql",
            "rowCount",
            "chartSpec",
            "selectedTables",
            "tableSelection",
            "clarification",
            "orchestration",
            "routeNote",
            "llmUsage",
            "agentsUsed",
            "suggestions",
        )
        if result.get(key) is not None
    }
    rows = [
        row for row in result.get("rows") or [] if isinstance(row, dict)
    ][:50]
    if rows:
        payload["rows"] = rows
    return payload
