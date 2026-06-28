from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import SchemaRetrievalResult, SelectorResult
from .entity_detector import BusinessEntityDetector, EntityDetectionResult
from .utils import column_names, normalize_text, table_name


@dataclass(frozen=True)
class JoinStep:
    left_table: str
    right_table: str
    edge_type: str
    columns: list[str]
    condition: str
    confidence: float
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["leftTable"] = payload.pop("left_table")
        payload["rightTable"] = payload.pop("right_table")
        payload["edgeType"] = payload.pop("edge_type")
        payload["requiresConfirmation"] = payload.pop("requires_confirmation")
        return payload


@dataclass(frozen=True)
class PlannedJoinPath:
    tables: list[str]
    join_steps: list[JoinStep]
    bridge_tables: list[str]
    score: float
    source: str = "graph"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "joinSteps": [step.to_dict() for step in self.join_steps],
            "bridgeTables": self.bridge_tables,
            "score": round(self.score, 4),
            "source": self.source,
            "edges": [
                {
                    "from": step.left_table,
                    "to": step.right_table,
                    "type": step.edge_type,
                    "columns": step.columns,
                    "condition": step.condition,
                }
                for step in self.join_steps
            ],
        }


@dataclass(frozen=True)
class JoinPlanningResult:
    status: str
    entities: EntityDetectionResult
    selected_paths: list[PlannedJoinPath]
    hint_block: str
    decomposition_hints: list[dict[str, Any]]
    validation: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "entities": self.entities.to_dict(),
            "selectedPaths": [path.to_dict() for path in self.selected_paths],
            "hintBlock": self.hint_block,
            "decompositionHints": self.decomposition_hints,
            "validation": self.validation,
            "warnings": self.warnings,
        }


class JoinPlanner:
    """Build an explicit multi-table join plan before SQL decomposition."""

    def __init__(self, entity_detector: BusinessEntityDetector | None = None) -> None:
        self.entity_detector = entity_detector or BusinessEntityDetector()

    def plan(
        self,
        question: str,
        *,
        retrieval: SchemaRetrievalResult,
        selector: SelectorResult,
        request_plan: dict[str, Any],
        max_paths: int = 3,
    ) -> JoinPlanningResult:
        tables = retrieval.tables or selector.selected_tables
        entities = self.entity_detector.detect(
            question,
            tables=tables,
            request_plan=request_plan,
        )
        table_lookup = {table_name(table): table for table in tables if table_name(table)}
        selected_names = selector.selected_table_names or [table_name(table) for table in selector.selected_tables]
        raw_paths = _candidate_paths(retrieval, selected_names)
        planned_paths = [
            _planned_path(path, table_lookup, entities, selected_names)
            for path in raw_paths
        ]
        planned_paths = [path for path in planned_paths if path.tables and path.join_steps]
        planned_paths.sort(key=lambda path: (-path.score, len(path.tables), path.tables))
        selected_paths = planned_paths[:max_paths]
        warnings = _warnings(selected_paths, planned_paths)
        status = "ready" if selected_paths else "no_join_needed"
        decomposition_hints = _decomposition_hints(selected_paths)
        validation = _validation(selected_paths, selected_names)
        hint_block = _hint_block(entities, selected_paths, decomposition_hints, validation)
        return JoinPlanningResult(
            status=status,
            entities=entities,
            selected_paths=selected_paths,
            hint_block=hint_block,
            decomposition_hints=decomposition_hints,
            validation=validation,
            warnings=warnings,
        )


def _candidate_paths(
    retrieval: SchemaRetrievalResult,
    selected_names: list[str],
) -> list[dict[str, Any]]:
    paths = [path for path in retrieval.join_paths or [] if path.get("tables")]
    if paths:
        return paths
    selected = set(selected_names)
    direct_paths: list[dict[str, Any]] = []
    for edge in retrieval.graph_edges or []:
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if not source or not target:
            continue
        if selected and not ({source, target} & selected):
            continue
        direct_paths.append(
            {
                "tables": [source, target],
                "edges": [edge],
                "bridgeTables": [],
                "totalWeight": edge.get("weight") or 0.7,
            }
        )
    return direct_paths


def _planned_path(
    raw_path: dict[str, Any],
    table_lookup: dict[str, dict[str, Any]],
    entities: EntityDetectionResult,
    selected_names: list[str],
) -> PlannedJoinPath:
    path_tables = [str(table) for table in raw_path.get("tables") or [] if str(table)]
    raw_edges = list(raw_path.get("edges") or [])
    steps = [_join_step(edge, table_lookup) for edge in raw_edges]
    steps = [step for step in steps if step.left_table and step.right_table]
    if not path_tables:
        for step in steps:
            for name in (step.left_table, step.right_table):
                if name not in path_tables:
                    path_tables.append(name)
    bridge_tables = [str(table) for table in raw_path.get("bridgeTables") or [] if str(table)]
    score = float(raw_path.get("totalWeight") or 0.0)
    score += _entity_coverage_score(path_tables, entities)
    score += len(set(path_tables) & set(selected_names)) * 0.25
    score += sum(0.2 for step in steps if step.condition)
    score -= len([step for step in steps if step.requires_confirmation]) * 0.4
    score -= max(0, len(path_tables) - 3) * 0.1
    return PlannedJoinPath(
        tables=path_tables,
        join_steps=steps,
        bridge_tables=bridge_tables,
        score=score,
        source=str(raw_path.get("source") or "schema_graph"),
    )


def _join_step(edge: dict[str, Any], table_lookup: dict[str, dict[str, Any]]) -> JoinStep:
    left = str(edge.get("from") or edge.get("source") or "")
    right = str(edge.get("to") or edge.get("target") or "")
    edge_type = str(edge.get("type") or edge.get("edgeType") or "RELATIONSHIP")
    columns = [str(column) for column in edge.get("columns") or [] if str(column)]
    if not columns:
        columns = _infer_join_columns(left, right, table_lookup)
    condition = _condition(left, right, columns)
    return JoinStep(
        left_table=left,
        right_table=right,
        edge_type=edge_type,
        columns=columns,
        condition=condition,
        confidence=_edge_confidence(edge_type, columns),
        requires_confirmation=not bool(condition),
    )


def _infer_join_columns(
    left: str,
    right: str,
    table_lookup: dict[str, dict[str, Any]],
) -> list[str]:
    left_columns = set(column_names(table_lookup.get(left, {})))
    right_columns = set(column_names(table_lookup.get(right, {})))
    shared = [column for column in sorted(left_columns & right_columns) if _useful_join_column(column)]
    if shared:
        return shared[:3]
    right_base = right.split(".")[-1].lower()
    left_base = left.split(".")[-1].lower()
    candidates = [
        f"{right_base.rstrip('s')}_id",
        f"{left_base.rstrip('s')}_id",
        "id",
    ]
    return [column for column in candidates if column in left_columns and column in right_columns][:1]


def _useful_join_column(column: str) -> bool:
    lowered = column.lower()
    return lowered.endswith("_id") or lowered in {
        "id",
        "trace_id",
        "span_id",
        "session_id",
        "user_id",
        "source_app_id",
        "source_app",
        "metric_date",
        "date",
        "model",
    }


def _condition(left: str, right: str, columns: list[str]) -> str:
    if not columns:
        return ""
    return " AND ".join(f"{left}.{column} = {right}.{column}" for column in columns)


def _edge_confidence(edge_type: str, columns: list[str]) -> float:
    base = {"FK": 0.92, "SHARED_COLUMN": 0.76, "LINEAGE": 0.58}.get(edge_type, 0.55)
    if columns:
        base += 0.08
    return round(min(base, 0.98), 4)


def _entity_coverage_score(path_tables: list[str], entities: EntityDetectionResult) -> float:
    score = 0.0
    names = set(path_tables)
    for entity in entities.entities:
        if names & set(entity.mapped_tables):
            score += 0.25 * entity.confidence
    return score


def _decomposition_hints(paths: list[PlannedJoinPath]) -> list[dict[str, Any]]:
    if not paths:
        return []
    hints: list[dict[str, Any]] = []
    for path_index, path in enumerate(paths[:2], 1):
        steps = path.join_steps
        if len(path.tables) <= 3 and len(steps) <= 2:
            hints.append(
                {
                    "id": f"join_path_{path_index}",
                    "strategy": "single_query_join",
                    "tables": path.tables,
                    "joinConditions": [step.condition for step in steps if step.condition],
                }
            )
            continue
        for chunk_index, start in enumerate(range(0, len(steps), 2), 1):
            chunk = steps[start : start + 2]
            chunk_tables: list[str] = []
            for step in chunk:
                for name in (step.left_table, step.right_table):
                    if name not in chunk_tables:
                        chunk_tables.append(name)
            hints.append(
                {
                    "id": f"join_path_{path_index}_cte_{chunk_index}",
                    "strategy": "cte_subquery",
                    "tables": chunk_tables,
                    "joinConditions": [step.condition for step in chunk if step.condition],
                    "maxJoins": 2,
                }
            )
    return hints


def _validation(paths: list[PlannedJoinPath], selected_names: list[str]) -> dict[str, Any]:
    required_conditions = [
        step.condition
        for path in paths[:1]
        for step in path.join_steps
        if step.condition
    ]
    missing = [
        {"from": step.left_table, "to": step.right_table, "edgeType": step.edge_type}
        for path in paths[:1]
        for step in path.join_steps
        if not step.condition
    ]
    return {
        "selectedTables": selected_names,
        "requiredJoinConditions": required_conditions,
        "missingJoinConditions": missing,
        "noCrossJoin": True,
        "requireExplicitJoinKeys": bool(paths),
        "validateGrainBeforeAggregation": len(selected_names) > 1,
    }


def _hint_block(
    entities: EntityDetectionResult,
    paths: list[PlannedJoinPath],
    decomposition_hints: list[dict[str, Any]],
    validation: dict[str, Any],
) -> str:
    if not paths:
        return ""
    lines = [
        "/* MODEL2 JOIN HINTS",
        "Pipeline: detect_entities -> graph_join_paths -> join_hints -> join_aware_decomposition -> refiner_join_validation.",
        "Use these hints only for planning. Do not copy this comment block into final SQL.",
    ]
    if entities.entities:
        lines.append("Business entities:")
        for entity in entities.entities[:8]:
            mapped = ", ".join(entity.mapped_columns or entity.mapped_tables or [entity.canonical])
            lines.append(f"- {entity.raw_value} => {entity.kind}:{entity.canonical} ({mapped})")
    for index, path in enumerate(paths, 1):
        lines.append(f"Join path {index} score={path.score:.3f}: {' -> '.join(path.tables)}")
        if path.bridge_tables:
            lines.append(f"  bridge tables: {', '.join(path.bridge_tables)}")
        for step in path.join_steps:
            condition = step.condition or "NO EXPLICIT KEY - verify before joining"
            lines.append(
                f"  {step.edge_type}: {step.left_table} -> {step.right_table} ON {condition}"
            )
    if decomposition_hints:
        lines.append("Decomposition hints:")
        for hint in decomposition_hints[:4]:
            lines.append(
                f"- {hint['id']} {hint['strategy']} tables={', '.join(hint.get('tables') or [])}"
            )
    if validation.get("requiredJoinConditions"):
        lines.append("Required join conditions:")
        for condition in validation["requiredJoinConditions"][:8]:
            lines.append(f"- {condition}")
    lines.append("Never use CROSS JOIN unless explicitly requested by the user.")
    lines.append("*/")
    return "\n".join(lines)


def _warnings(paths: list[PlannedJoinPath], all_paths: list[PlannedJoinPath]) -> list[str]:
    warnings: list[str] = []
    if not paths:
        return warnings
    if any(step.requires_confirmation for step in paths[0].join_steps):
        warnings.append("Selected join path has relationship edges without explicit join columns.")
    if len(all_paths) > 1 and abs(all_paths[0].score - all_paths[1].score) < 0.15:
        warnings.append("Multiple join paths have similar scores; highest-ranked path was selected.")
    return warnings
