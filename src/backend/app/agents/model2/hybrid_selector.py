from __future__ import annotations

from typing import Any

from .contracts import SchemaRetrievalResult, SelectorResult
from .utils import column_names, compact_table, normalize_text, table_name


class HybridSelector:
    """MAC-SQL selector adapted to Agentic BI metadata tables."""

    def select(
        self,
        question: str,
        retrieval: SchemaRetrievalResult,
        *,
        request_plan: dict[str, Any],
    ) -> SelectorResult:
        text = normalize_text(question)
        slots = request_plan.get("slots") if isinstance(request_plan, dict) else {}
        query_terms = _query_terms(text, slots)
        decisions: list[dict[str, Any]] = []
        selected_tables: list[dict[str, Any]] = []
        schema_sections: list[str] = []
        confidences: list[float] = []

        for position, table in enumerate(retrieval.tables):
            columns = column_names(table)
            scored_columns = _score_columns(columns, query_terms)
            retrieval_confidence = float(table.get("retrievalConfidence") or 0)
            table_score = _table_score(table, text, position, retrieval_confidence)
            decision = "keep_all" if len(columns) <= 10 or table_score >= 0.75 else "select_columns"
            if table_score < 0.18 and position > 1:
                decision = "drop_all"

            if decision == "drop_all":
                decisions.append(
                    {
                        "tableName": table_name(table),
                        "decision": decision,
                        "selectedColumns": [],
                        "reason": "low semantic and metadata relevance",
                    }
                )
                continue

            if decision == "keep_all":
                selected_columns = columns
            else:
                selected_columns = _selected_columns(columns, scored_columns, max_columns=8)

            if not selected_columns and columns:
                selected_columns = columns[: min(len(columns), 8)]

            compact = compact_table(table, columns=selected_columns)
            selected_tables.append(compact)
            schema_sections.append(_schema_section(compact))
            confidences.append(max(0.2, table_score))
            decisions.append(
                {
                    "tableName": table_name(table),
                    "decision": decision,
                    "selectedColumns": selected_columns,
                    "reason": _decision_reason(table, selected_columns, query_terms),
                    "score": round(table_score, 4),
                }
            )

        if not selected_tables and retrieval.tables:
            fallback = compact_table(retrieval.tables[0])
            selected_tables.append(fallback)
            schema_sections.append(_schema_section(fallback))
            decisions.append(
                {
                    "tableName": table_name(retrieval.tables[0]),
                    "decision": "keep_all",
                    "selectedColumns": column_names(retrieval.tables[0]),
                    "reason": "fallback to top retrieved table",
                    "score": 0.35,
                }
            )
            confidences.append(0.35)

        confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        return SelectorResult(
            selected_tables=selected_tables,
            selected_table_names=[table_name(table) for table in selected_tables],
            schema_prompt="\n\n".join(schema_sections),
            decisions=decisions,
            confidence=confidence,
            strategy="macsql_selector_over_datahub_candidates",
            graph_edges=retrieval.graph_edges,
        )


def _query_terms(text: str, slots: Any) -> set[str]:
    terms = {term for term in text.split() if len(term) > 2}
    if isinstance(slots, dict):
        for slot in slots.values():
            value = slot.get("value") if isinstance(slot, dict) else None
            if isinstance(value, list):
                terms.update(normalize_text(str(item)) for item in value)
            elif value not in (None, "", []):
                terms.update(normalize_text(str(value)).split())
    return {term for term in terms if term}


def _table_score(
    table: dict[str, Any],
    text: str,
    position: int,
    retrieval_confidence: float,
) -> float:
    name = normalize_text(table_name(table))
    description = normalize_text(str(table.get("description") or ""))
    columns = [normalize_text(column) for column in column_names(table)]
    score = max(0.0, 0.45 - position * 0.05)
    if retrieval_confidence:
        score = max(score, retrieval_confidence)
    for token in text.split():
        if len(token) <= 2:
            continue
        if token in name:
            score += 0.08
        if token in description:
            score += 0.04
        if any(token in column for column in columns):
            score += 0.06
    return min(score, 1.0)


def _score_columns(columns: list[str], query_terms: set[str]) -> list[tuple[str, float]]:
    scored: list[tuple[str, float]] = []
    for position, column in enumerate(columns):
        normalized = normalize_text(column)
        score = max(0.0, 0.2 - position * 0.002)
        if _is_important_column(column):
            score += 0.4
        for term in query_terms:
            if term and term in normalized:
                score += 0.35
        scored.append((column, score))
    scored.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return scored


def _selected_columns(
    columns: list[str],
    scored_columns: list[tuple[str, float]],
    *,
    max_columns: int,
) -> list[str]:
    selected: list[str] = []
    for column in columns:
        if _is_important_column(column) and column not in selected:
            selected.append(column)
    for column, _score in scored_columns:
        if len(selected) >= max_columns:
            break
        if column not in selected:
            selected.append(column)
    return [column for column in columns if column in set(selected)]


def _is_important_column(column: str) -> bool:
    lowered = column.lower()
    return (
        lowered == "id"
        or lowered.endswith("_id")
        or any(token in lowered for token in ("date", "time", "model", "service", "source", "cost", "token", "error", "latency", "count", "status"))
    )


def _schema_section(table: dict[str, Any]) -> str:
    lines = [f"# Table: {table_name(table)}"]
    for column in table.get("columns") or []:
        name = column.get("name")
        ctype = column.get("type") or "unknown"
        description = column.get("description") or ""
        suffix = f" -- {description}" if description else ""
        lines.append(f"- {name} ({ctype}){suffix}")
    return "\n".join(lines)


def _decision_reason(
    table: dict[str, Any],
    selected_columns: list[str],
    query_terms: set[str],
) -> str:
    matched = [
        column
        for column in selected_columns
        if any(term in normalize_text(column) for term in query_terms)
    ][:4]
    if matched:
        return "matched columns: " + ", ".join(matched)
    reasons = table.get("retrievalReasons") or []
    if reasons:
        return "; ".join(str(reason) for reason in reasons[:2])
    return "selected from metadata retrieval candidate set"
