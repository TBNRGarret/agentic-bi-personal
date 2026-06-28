from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .utils import column_names, normalize_text, table_name


"""Business entity detection for join planning.

EntityDetector extracts business entities from natural language questions
using a built-in glossary of known terms (publisher, revenue, cost, etc.)
and fuzzy table/column name matching.

BusinessEntity: detected entity with kind (dimension/metric/time_filter),
canonical name, mapped tables/columns, and confidence.
EntityDetectionResult: aggregated result with entities, glossary hits,
table mentions, and column mentions.

Used by JoinPlanner to score join paths by entity coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .utils import column_names, normalize_text, table_name


@dataclass(frozen=True)
class BusinessEntity:
    raw_value: str
    normalized: str
    kind: str
    canonical: str
    mapped_tables: list[str] = field(default_factory=list)
    mapped_columns: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntityDetectionResult:
    entities: list[BusinessEntity]
    glossary_hits: list[str]
    table_mentions: list[str]
    column_mentions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [entity.to_dict() for entity in self.entities],
            "glossaryHits": self.glossary_hits,
            "tableMentions": self.table_mentions,
            "columnMentions": self.column_mentions,
        }


_GLOSSARY: dict[str, dict[str, Any]] = {
    "publisher": {
        "kind": "dimension",
        "canonical": "publisher",
        "columns": ["publisher", "publisher_id", "publisher_name"],
        "tables": ["publisher", "publishers", "dim_publisher", "dim_publishers"],
    },
    "doanh thu": {
        "kind": "metric",
        "canonical": "revenue",
        "columns": ["revenue", "total_revenue", "total_amount", "amount"],
        "tables": ["revenue", "sales", "orders", "fact_sales", "fct_sales"],
    },
    "revenue": {
        "kind": "metric",
        "canonical": "revenue",
        "columns": ["revenue", "total_revenue", "total_amount", "amount"],
        "tables": ["revenue", "sales", "orders", "fact_sales", "fct_sales"],
    },
    "chi phi": {
        "kind": "metric",
        "canonical": "cost",
        "columns": ["total_cost_usd", "estimated_cost_usd", "cost_usd"],
        "tables": ["cost", "llm", "interaction"],
    },
    "cost": {
        "kind": "metric",
        "canonical": "cost",
        "columns": ["total_cost_usd", "estimated_cost_usd", "cost_usd"],
        "tables": ["cost", "llm", "interaction"],
    },
    "thang nay": {
        "kind": "time_filter",
        "canonical": "current_month",
        "columns": ["date", "metric_date", "created_at", "event_time", "started_at"],
        "tables": [],
    },
    "this month": {
        "kind": "time_filter",
        "canonical": "current_month",
        "columns": ["date", "metric_date", "created_at", "event_time", "started_at"],
        "tables": [],
    },
    "model": {
        "kind": "dimension",
        "canonical": "model",
        "columns": ["model"],
        "tables": ["llm", "interaction", "cost"],
    },
    "source app": {
        "kind": "dimension",
        "canonical": "source_app",
        "columns": ["source_app", "source_app_id"],
        "tables": ["source_apps", "apps", "application"],
    },
}


class BusinessEntityDetector:
    """Detect business entities and map them to governed table/column hints."""

    def detect(
        self,
        question: str,
        *,
        tables: list[dict[str, Any]],
        request_plan: dict[str, Any] | None = None,
    ) -> EntityDetectionResult:
        text = normalize_text(question)
        table_lookup = {table_name(table): table for table in tables if table_name(table)}
        column_lookup: dict[str, list[str]] = {}
        for table in tables:
            name = table_name(table)
            for column in column_names(table):
                column_lookup.setdefault(column.lower(), []).append(name)

        entities: list[BusinessEntity] = []
        glossary_hits: list[str] = []
        for phrase, mapping in _GLOSSARY.items():
            normalized_phrase = normalize_text(phrase)
            if normalized_phrase and normalized_phrase in text:
                glossary_hits.append(phrase)
                entities.append(
                    BusinessEntity(
                        raw_value=phrase,
                        normalized=normalized_phrase,
                        kind=str(mapping["kind"]),
                        canonical=str(mapping["canonical"]),
                        mapped_tables=_matching_tables(mapping.get("tables") or [], table_lookup),
                        mapped_columns=_matching_columns(mapping.get("columns") or [], column_lookup),
                        confidence=0.86,
                        source="business_glossary",
                    )
                )

        slot_entities = _entities_from_request_plan(request_plan or {}, table_lookup, column_lookup)
        entities.extend(slot_entities)

        table_mentions = _mentioned_tables(text, table_lookup)
        column_mentions = _mentioned_columns(text, column_lookup)
        for column in column_mentions:
            entities.append(
                BusinessEntity(
                    raw_value=column,
                    normalized=normalize_text(column),
                    kind="column",
                    canonical=column,
                    mapped_tables=column_lookup.get(column.lower(), []),
                    mapped_columns=[column],
                    confidence=0.72,
                    source="schema_lexical",
                )
            )

        return EntityDetectionResult(
            entities=_dedupe_entities(entities),
            glossary_hits=glossary_hits,
            table_mentions=table_mentions,
            column_mentions=column_mentions,
        )


def _matching_tables(patterns: list[str], tables: dict[str, dict[str, Any]]) -> list[str]:
    matches: list[str] = []
    for name in tables:
        normalized_name = normalize_text(name.replace(".", " "))
        if any(normalize_text(pattern) in normalized_name for pattern in patterns):
            matches.append(name)
    return matches[:5]


def _matching_columns(patterns: list[str], columns: dict[str, list[str]]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        normalized = normalize_text(pattern).replace(" ", "_")
        for column in columns:
            if normalized == column or normalized in column:
                if column not in matches:
                    matches.append(column)
    return matches[:8]


def _entities_from_request_plan(
    request_plan: dict[str, Any],
    tables: dict[str, dict[str, Any]],
    columns: dict[str, list[str]],
) -> list[BusinessEntity]:
    slot_values: list[tuple[str, str]] = []
    slots = request_plan.get("slots") if isinstance(request_plan.get("slots"), dict) else {}
    for slot_name, slot in slots.items():
        value = slot.get("value") if isinstance(slot, dict) else slot
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item not in (None, "", []):
                slot_values.append((str(slot_name), str(item)))
    entities: list[BusinessEntity] = []
    for slot_name, value in slot_values:
        normalized = normalize_text(value)
        entities.append(
            BusinessEntity(
                raw_value=value,
                normalized=normalized,
                kind=slot_name,
                canonical=normalized.replace(" ", "_"),
                mapped_tables=_matching_tables([value], tables),
                mapped_columns=_matching_columns([value], columns),
                confidence=0.68,
                source="request_plan_slot",
            )
        )
    return entities


def _mentioned_tables(text: str, tables: dict[str, dict[str, Any]]) -> list[str]:
    mentions: list[str] = []
    for name in tables:
        short_name = name.split(".")[-1]
        if normalize_text(short_name).replace("_", " ") in text or normalize_text(short_name) in text:
            mentions.append(name)
    return mentions


def _mentioned_columns(text: str, columns: dict[str, list[str]]) -> list[str]:
    mentions: list[str] = []
    for column in columns:
        normalized = normalize_text(column).replace("_", " ")
        if len(normalized) > 2 and normalized in text:
            mentions.append(column)
    return mentions[:12]


def _dedupe_entities(entities: list[BusinessEntity]) -> list[BusinessEntity]:
    best: dict[tuple[str, str, str], BusinessEntity] = {}
    for entity in entities:
        key = (entity.kind, entity.canonical, entity.normalized)
        previous = best.get(key)
        if previous is None or entity.confidence > previous.confidence:
            best[key] = entity
    return sorted(best.values(), key=lambda item: (-item.confidence, item.kind, item.canonical))
