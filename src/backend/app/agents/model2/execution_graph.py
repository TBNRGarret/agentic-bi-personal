from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .contracts import DecompositionPlan, SchemaRetrievalResult, SelectorResult


"""Execution graph data model for Model 2 pipeline DAG.

Defines the DAG structure (ExecutionStep, ExecutionGraph) and the builder
function build_model2_execution_graph() that converts pipeline state into
a persisted graph for debugging and observability.

The execution graph captures:
- Each pipeline stage as a step with type/tool/dependencies
- Topological execution order
- Status, inputs, outputs, and error per step
- Full pipeline metadata (question, datasource, mode)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .contracts import DecompositionPlan, SchemaRetrievalResult, SelectorResult


@dataclass(frozen=True)
class ExecutionStep:
    id: str
    label: str
    type: str
    tool: str
    depends_on: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    status: str = "planned"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "tool": self.tool,
            "dependsOn": self.depends_on,
            "tables": self.tables,
            "status": self.status,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "error": self.error,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExecutionGraph:
    graph_id: str
    question: str
    datasource: str
    mode: str
    status: str
    steps: list[ExecutionStep]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        order = topological_order(self.steps)
        return {
            "graphId": self.graph_id,
            "question": self.question,
            "datasource": self.datasource,
            "mode": self.mode,
            "status": self.status,
            "createdAt": self.created_at,
            "executionOrder": order,
            "steps": [step.to_dict() for step in self.steps],
            "metadata": self.metadata,
        }


def build_model2_execution_graph(
    *,
    question: str,
    datasource: str,
    retrieval: SchemaRetrievalResult,
    selector: SelectorResult,
    plan: DecompositionPlan,
    sql: str,
    used_tables: list[str],
    attempts: list[dict[str, Any]],
    row_count: int,
    status: str,
    error: str = "",
) -> ExecutionGraph:
    graph_id = f"qpg_{uuid4().hex}"
    final_status = "success" if status == "success" and not error else "error"
    datasource_tool = "postgres" if datasource == "dwh" else datasource
    steps: list[ExecutionStep] = [
        ExecutionStep(
            id="schema_retrieval",
            label="Schema Graph Retrieval",
            type="schema_retrieval",
            tool="falkordb",
            status="success",
            tables=[_name(table) for table in retrieval.tables],
            outputs={
                "metadataSource": retrieval.metadata_source,
                "joinPathCount": len(retrieval.join_paths),
                "graphEdgeCount": len(retrieval.graph_edges),
            },
        ),
        ExecutionStep(
            id="hybrid_selector",
            label="Hybrid Schema Selector",
            type="selector",
            tool="model2_selector",
            depends_on=["schema_retrieval"],
            status="success",
            tables=selector.selected_table_names,
            outputs={
                "confidence": selector.confidence,
                "strategy": selector.strategy,
            },
        ),
    ]

    previous = "hybrid_selector"
    reasoning_steps = _reasoning_steps(plan, previous)
    steps.extend(reasoning_steps)
    if reasoning_steps:
        previous = reasoning_steps[-1].id

    steps.extend(
        [
            ExecutionStep(
                id="sql_generation",
                label="SQL Generation",
                type="sql_generation",
                tool="llm" if plan.source.startswith("llm") else "deterministic_sql_generator",
                depends_on=[previous],
                status="success" if plan.sql else "error",
                tables=selector.selected_table_names,
                outputs={
                    "source": plan.source,
                    "candidateCount": len(plan.candidates),
                    "decompositionStrategy": plan.decomposition_strategy,
                    "complexity": plan.complexity,
                },
                error="" if plan.sql else plan.rationale,
            ),
            ExecutionStep(
                id="sql_safety",
                label="SQL Safety and Row Security",
                type="guardrail",
                tool="sql_safety",
                depends_on=["sql_generation"],
                status="success" if sql and final_status == "success" else ("error" if error else "skipped"),
                tables=used_tables,
                outputs={"readonly": bool(sql), "rowSecurity": True},
                error=error if error and not sql else "",
            ),
            ExecutionStep(
                id="data_execution",
                label="Datasource Query",
                type="tool_call",
                tool=datasource_tool,
                depends_on=["sql_safety"],
                status=final_status,
                tables=used_tables,
                inputs={"sql": sql},
                outputs={"rowCount": row_count, "attemptCount": len(attempts)},
                error=error,
            ),
            ExecutionStep(
                id="result_composition",
                label="Result Composition",
                type="compose_answer",
                tool="response_formatter",
                depends_on=["data_execution"],
                status="success" if final_status == "success" else "skipped",
                tables=used_tables,
                outputs={"chartReady": row_count > 0},
            ),
        ]
    )

    validate_execution_graph(steps)
    return ExecutionGraph(
        graph_id=graph_id,
        question=question,
        datasource=datasource,
        mode="observed_pipeline",
        status=final_status,
        steps=steps,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata={
            "executor": "topological_deterministic",
            "planner": "model2_hybrid_queryweaver",
            "note": "Current release persists and validates the plan graph while executing the existing Model 2 pipeline.",
        },
    )


def topological_order(steps: list[ExecutionStep]) -> list[str]:
    by_id = {step.id: step for step in steps}
    indegree = {step.id: 0 for step in steps}
    children: dict[str, list[str]] = {step.id: [] for step in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep not in by_id:
                raise ValueError(f"Execution graph step {step.id} depends on missing step {dep}")
            indegree[step.id] += 1
            children[dep].append(step.id)

    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(order) != len(steps):
        raise ValueError("Execution graph contains a cycle")
    return order


def validate_execution_graph(steps: list[ExecutionStep]) -> None:
    ids = [step.id for step in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("Execution graph contains duplicate step ids")
    topological_order(steps)


def _reasoning_steps(plan: DecompositionPlan, previous: str) -> list[ExecutionStep]:
    steps: list[ExecutionStep] = []
    for index, item in enumerate(plan.sub_questions[:4], 1):
        step_id = _safe_step_id(str(item.get("id") or f"reasoning_{index}"))
        deps = item.get("dependsOn") or item.get("depends_on") or []
        dependency_ids = [_safe_step_id(str(dep)) for dep in deps if str(dep).strip()] or [previous]
        steps.append(
            ExecutionStep(
                id=step_id,
                label=str(item.get("question") or f"Reasoning step {index}"),
                type="reasoning_step",
                tool="llm" if plan.decomposition_strategy.startswith("llm") else "rule_based_decomposer",
                depends_on=dependency_ids,
                status="success",
                tables=[str(table) for table in item.get("tables") or plan.selected_tables],
                outputs={
                    "expectedOutput": item.get("expectedOutput") or item.get("expected_output") or "",
                    "evidence": item.get("evidence") or "",
                    "joinPath": item.get("joinPath") or item.get("join_path") or [],
                },
            )
        )
    return _repair_reasoning_dependencies(steps, previous)


def _repair_reasoning_dependencies(steps: list[ExecutionStep], first_dependency: str) -> list[ExecutionStep]:
    if not steps:
        return []
    ids = {step.id for step in steps}
    repaired: list[ExecutionStep] = []
    previous = first_dependency
    for step in steps:
        deps = [dep for dep in step.depends_on if dep in ids or dep == first_dependency]
        if not deps:
            deps = [previous]
        repaired.append(
            ExecutionStep(
                id=step.id,
                label=step.label,
                type=step.type,
                tool=step.tool,
                depends_on=deps,
                tables=step.tables,
                status=step.status,
                inputs=step.inputs,
                outputs=step.outputs,
                error=step.error,
                metadata=step.metadata,
            )
        )
        previous = step.id
    return repaired


def _safe_step_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value.strip().lower())
    return cleaned or "step"


def _name(table: dict[str, Any]) -> str:
    return str(table.get("tableName") or table.get("name") or "").strip()
