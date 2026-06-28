"""Model 2 pipeline workflow graph for frontend AgentFlowGraph rendering.

Builds the node/edge graph shown in the UI after each Model 2 query.
Nodes represent pipeline stages (relevancy, selector, decomposer, etc.)
with kind, label, status, and a short description.
Edges represent data flow between stages.

Supports error highlighting: if a stage fails, its node and downstream
edges are marked with error status.
"""

from __future__ import annotations

from typing import Any


def build_model2_workflow_graph(
    *,
    question: str,
    target: str,
    status: str,
    datasource: str,
    selected_tables: list[str],
    execution_time_ms: int,
    failed_stage: str | None = None,
) -> dict[str, Any]:
    nodes = [
        _node("user", "User", question[:96], "person"),
        _node("model_router", "Model Router", "model2 hybrid selected", "planner"),
        _node("model2_relevancy", "Relevancy Agent", "off-topic and clarification gate", "guardrail"),
        _node("semantic_slots", "Semantic Slots", f"target={target}", "planner"),
        _node("metadata_catalog", "DataHub Metadata", "schema, lineage, freshness catalog", "datahub"),
        _node("graph_retrieval", "Graph Schema Retrieval", f"datasource={datasource}", "retriever"),
        _node("hybrid_selector", "Hybrid Selector", _table_summary(selected_tables), "selector"),
        _node("decomposer", "Decomposer Agent", "subquestions and SQL plan", "agent"),
        _node("sql_safety", "SQL Safety", "read-only, known-table, row-security", "guardrail"),
        _node("executor", "ClickHouse/Postgres Executor", datasource, "database"),
        _node("refiner", "Refiner Agent", "bounded SQL repair loop", "agent", "skipped"),
        _node("formatter", "Response Formatter", "answer and chart spec", "agent"),
        _node("response", "Response", f"{status} in {execution_time_ms}ms", "result"),
    ]
    for node in nodes:
        if node["id"] == failed_stage:
            node["status"] = "error"
        elif status == "error" and node["id"] == "response":
            node["status"] = "error"
    edges = [
        _edge("user", "model_router", "asks question"),
        _edge("model_router", "model2_relevancy", "routes to Model 2"),
        _edge("model2_relevancy", "semantic_slots", "passes BI request"),
        _edge("semantic_slots", "metadata_catalog", "requests governed catalog"),
        _edge("metadata_catalog", "graph_retrieval", "returns table and column metadata"),
        _edge("graph_retrieval", "hybrid_selector", "candidate schema graph"),
        _edge("hybrid_selector", "decomposer", "compact selected schema"),
        _edge("decomposer", "sql_safety", "candidate SQL"),
        _edge("sql_safety", "executor", "validated SQL"),
        _edge("executor", "refiner", "database error", "warning"),
        _edge("refiner", "sql_safety", "fixed SQL", "warning"),
        _edge("executor", "formatter", "rows"),
        _edge("formatter", "response", "final payload"),
    ]
    return {
        "title": "Agentic BI Model 2 Hybrid Workflow",
        "notation": "C4+agent-flow",
        "nodes": nodes,
        "edges": edges,
    }


def _node(
    node_id: str,
    label: str,
    sublabel: str,
    kind: str,
    status: str = "ready",
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "sublabel": sublabel,
        "kind": kind,
        "c4Level": "component",
        "layer": "Model 2 Hybrid",
        "status": status,
        "metadata": {},
    }


def _edge(
    source: str,
    target: str,
    label: str,
    status: str = "ready",
) -> dict[str, Any]:
    return {"from": source, "to": target, "label": label, "status": status}


def _table_summary(selected_tables: list[str]) -> str:
    if not selected_tables:
        return "no table selected"
    if len(selected_tables) <= 2:
        return ", ".join(selected_tables)
    return ", ".join(selected_tables[:2]) + f" +{len(selected_tables) - 2}"
