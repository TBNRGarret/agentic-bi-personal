from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RelevancyDecision:
    status: str
    reason: str = ""
    needs_clarification: bool = False
    clarification: dict[str, Any] | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "needsClarification": self.needs_clarification,
            "clarification": self.clarification,
            "confidence": self.confidence,
        }


@dataclass
class SchemaRetrievalResult:
    datasource: str
    metadata_source: str
    database_description: str
    tables: list[dict[str, Any]]
    ranked_tables: list[dict[str, Any]]
    graph_edges: list[dict[str, Any]] = field(default_factory=list)
    join_paths: list[dict[str, Any]] = field(default_factory=list)
    table_selection: dict[str, Any] | None = None
    retrieval: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "datasource": self.datasource,
            "metadataSource": self.metadata_source,
            "databaseDescription": self.database_description,
            "tables": self.tables,
            "rankedTables": self.ranked_tables,
            "graphEdges": self.graph_edges,
            "joinPaths": self.join_paths,
            "tableSelection": self.table_selection,
            "retrieval": self.retrieval,
            "warnings": self.warnings,
        }


@dataclass
class SelectorResult:
    selected_tables: list[dict[str, Any]]
    selected_table_names: list[str]
    schema_prompt: str
    decisions: list[dict[str, Any]]
    confidence: float
    strategy: str
    graph_edges: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selectedTables": self.selected_table_names,
            "decisions": self.decisions,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "graphEdges": self.graph_edges,
        }


@dataclass
class DecompositionPlan:
    status: str
    sql: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    sub_questions: list[dict[str, Any]] = field(default_factory=list)
    selected_tables: list[str] = field(default_factory=list)
    visualization: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""
    source: str = "model2"
    raw_plan: dict[str, Any] = field(default_factory=dict)
    complexity: dict[str, Any] = field(default_factory=dict)
    intermediate_results: list[dict[str, Any]] = field(default_factory=list)
    decomposition_strategy: str = "single_shot"
    complexity_strategy: str = "rule_based"
    hybrid_signals: list[str] = field(default_factory=list)
    chain_of_thought: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["subQuestions"] = payload.pop("sub_questions")
        payload["selectedTables"] = payload.pop("selected_tables")
        payload["rawPlan"] = payload.pop("raw_plan")
        payload["intermediateResults"] = payload.pop("intermediate_results")
        payload["decompositionStrategy"] = payload.pop("decomposition_strategy")
        payload["complexityStrategy"] = payload.pop("complexity_strategy")
        payload["hybridSignals"] = payload.pop("hybrid_signals")
        payload["chainOfThought"] = payload.pop("chain_of_thought")
        return payload
