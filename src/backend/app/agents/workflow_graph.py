from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_workflow_graph(
    *,
    question: str,
    target: str,
    status: str,
    request_plan: dict[str, Any],
    agent_plan: dict[str, Any] | None = None,
    agent_names: list[str] | None = None,
    responses: list[Any] | None = None,
    primary: dict[str, Any] | None = None,
    selected_tables: list[str] | None = None,
    clarification: dict[str, Any] | None = None,
    execution_time_ms: int | None = None,
) -> dict[str, Any]:
    """Build a compact C4-style workflow graph for the UI."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()

    def add_node(
        node_id: str,
        label: str,
        *,
        subtitle: str = "",
        kind: str = "component",
        c4_level: str = "component",
        layer: str = "Agent workflow",
        node_status: str = "ready",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "sublabel": subtitle,
                "kind": kind,
                "c4Level": c4_level,
                "layer": layer,
                "status": node_status,
                "metadata": metadata or {},
            }
        )

    def add_edge(source: str, target_id: str, label: str, *, edge_status: str = "ready") -> None:
        key = (source, target_id)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(
            {
                "from": source,
                "to": target_id,
                "label": label,
                "status": edge_status,
            }
        )

    agents = agent_names or _agents_from_plan(agent_plan)
    selected = selected_tables or _selected_tables(primary, responses)
    metadata_source = _metadata_source(primary, responses)
    route_intent = str(
        (request_plan.get("routeIntent") if isinstance(request_plan, dict) else None)
        or (agent_plan or {}).get("intent")
        or _slot_value(request_plan, "intent_type")
        or "auto"
    )
    time_range = str(_slot_value(request_plan, "time_range") or "30d")
    target_label = target if target != "auto" else "auto target"

    add_node(
        "user",
        "User",
        subtitle=_short(question, 72),
        kind="person",
        c4_level="person",
        layer="C4 context",
    )
    add_node(
        "source_landing",
        "Source export",
        subtitle="APIs/DWH land raw JSONL batches in MinIO",
        kind="source",
        c4_level="system",
        layer="Data pipeline",
    )
    add_node(
        "minio",
        "MinIO raw landing",
        subtitle="agentic-bi-raw manifests and JSONL objects",
        kind="storage",
        c4_level="container",
        layer="Data pipeline",
    )
    add_node(
        "airflow",
        "Airflow DAG",
        subtitle="agentic_bi_pipeline: ingest -> dbt -> quality gate",
        kind="airflow",
        c4_level="container",
        layer="Data pipeline",
    )
    add_node(
        "clickhouse_raw",
        "ClickHouse raw",
        subtitle="vsf_raw tables and raw_ingestion_batches audit",
        kind="database",
        c4_level="data",
        layer="Data pipeline",
    )
    add_node(
        "dbt",
        "dbt transform",
        subtitle="vsf_raw -> vsf_stg -> vsf_mart",
        kind="transform",
        c4_level="component",
        layer="Data pipeline",
    )
    add_node(
        "clickhouse_mart",
        "ClickHouse analytics",
        subtitle=_table_summary(selected),
        kind="database",
        c4_level="data",
        layer="Data pipeline",
    )
    add_node(
        "datahub",
        "DataHub API",
        subtitle=f"{metadata_source}: schema, lineage, freshness",
        kind="datahub",
        c4_level="container",
        layer="Metadata",
        node_status="ready" if metadata_source == "datahub" else "fallback",
    )
    add_node(
        "observation_service",
        "Observation Service",
        subtitle=f"Target {target_label}",
        kind="service",
        c4_level="container",
        layer="Agent workflow",
    )
    add_node(
        "guardrail",
        "Clarification guardrail",
        subtitle=f"Intent {route_intent}, time {time_range}",
        kind="guardrail",
        c4_level="component",
        layer="Agent workflow",
        node_status="warning" if status == "needs_clarification" else "ready",
    )

    add_edge("source_landing", "minio", "writes raw batches")
    add_edge("minio", "airflow", "reads manifests")
    add_edge("airflow", "clickhouse_raw", "loads validated batch")
    add_edge("clickhouse_raw", "dbt", "feeds transforms")
    add_edge("dbt", "clickhouse_mart", "builds silver/gold")
    add_edge("clickhouse_mart", "datahub", "publishes metadata")
    add_edge("user", "observation_service", "asks question")
    add_edge("observation_service", "guardrail", "extracts slots")

    if clarification:
        add_node(
            "clarification",
            "Ask for clarification",
            subtitle=str(clarification.get("question") or "Need a clearer request"),
            kind="clarification",
            c4_level="component",
            layer="Agent workflow",
            node_status="warning",
            metadata={"slot": clarification.get("slot"), "reason": clarification.get("reason")},
        )
        add_edge("guardrail", "clarification", "missing or low-confidence slot", edge_status="warning")
        add_edge("clarification", "user", "asks follow-up", edge_status="warning")
        _add_result_node(
            add_node,
            add_edge,
            status=status,
            execution_time_ms=execution_time_ms,
            previous_node="clarification",
        )
        return _graph(nodes, edges)

    add_node(
        "planner",
        "Agent planner",
        subtitle=_planner_summary(agent_plan),
        kind="planner",
        c4_level="component",
        layer="Agent workflow",
    )
    add_edge("guardrail", "datahub", "fetches catalog context")
    add_edge("datahub", "planner", "returns metadata")

    previous = "planner"
    for agent in agents:
        node_id = f"agent_{agent}"
        add_node(
            node_id,
            _agent_label(agent),
            subtitle=_agent_subtitle(agent),
            kind=_agent_kind(agent),
            c4_level="component",
            layer="Agent workflow",
            node_status=_agent_status(agent, responses),
        )
        add_edge(previous, node_id, "routes task")
        previous = node_id

    if any(agent == "qa_agent" for agent in agents) or _has_batch_issues(responses):
        add_node(
            "batch_audit",
            "Batch fail audit",
            subtitle="raw_ingestion_batches and DQ result checks",
            kind="batch",
            c4_level="data",
            layer="Data quality",
            node_status="warning" if _has_batch_issues(responses) else "ready",
        )
        add_edge(previous, "batch_audit", "checks failed import batches")
        previous = "batch_audit"

    if any(agent == "partition_agent" for agent in agents):
        add_node(
            "partitioning",
            "Partitioning data",
            subtitle="detects keys, pruning, skew, empty partitions",
            kind="partition",
            c4_level="component",
            layer="Data quality",
        )
        add_edge(previous, "partitioning", "analyzes partition health")
        previous = "partitioning"

    if selected:
        add_edge(previous, "clickhouse_mart", "queries selected tables")
        previous = "clickhouse_mart"

    _add_result_node(
        add_node,
        add_edge,
        status=status,
        execution_time_ms=execution_time_ms,
        previous_node=previous,
    )
    return _graph(nodes, edges)


def _add_result_node(
    add_node: Any,
    add_edge: Any,
    *,
    status: str,
    execution_time_ms: int | None,
    previous_node: str,
) -> None:
    ok = status in {"success", "partial"}
    add_node(
        "result",
        "Agent response" if ok else "Agent response blocked",
        subtitle=_execution_summary(status, execution_time_ms),
        kind="result" if ok else "error",
        c4_level="component",
        layer="Agent workflow",
        node_status="ready" if ok else "error",
    )
    add_edge(
        previous_node,
        "result",
        "returns answer" if ok else "returns guardrail state",
        edge_status="ready" if ok else "warning",
    )


def _graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": "Agent workflow graph",
        "notation": "C4-lite",
        "nodes": nodes,
        "edges": edges,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _agents_from_plan(agent_plan: dict[str, Any] | None) -> list[str]:
    agents = (agent_plan or {}).get("agents")
    return [str(agent) for agent in agents or []]


def _selected_tables(primary: dict[str, Any] | None, responses: list[Any] | None) -> list[str]:
    names: list[str] = []
    for name in (primary or {}).get("selectedTables") or []:
        _append_unique(names, str(name))
    for response in responses or []:
        for artifact in getattr(response, "artifacts", []) or []:
            result = artifact.get("result") if isinstance(artifact, dict) else None
            if not isinstance(result, dict):
                continue
            for name in result.get("selectedTables") or []:
                _append_unique(names, str(name))
    return names


def _metadata_source(primary: dict[str, Any] | None, responses: list[Any] | None) -> str:
    context = (primary or {}).get("metadataContext")
    if isinstance(context, dict) and context.get("source"):
        return str(context["source"])
    for response in responses or []:
        for artifact in getattr(response, "artifacts", []) or []:
            result = artifact.get("result") if isinstance(artifact, dict) else None
            if isinstance(result, dict) and result.get("metadataSource"):
                return str(result["metadataSource"])
    return "datahub"


def _slot_value(request_plan: dict[str, Any], name: str) -> Any:
    slots = request_plan.get("slots") if isinstance(request_plan, dict) else {}
    slot = slots.get(name) if isinstance(slots, dict) else None
    return slot.get("value") if isinstance(slot, dict) else None


def _planner_summary(agent_plan: dict[str, Any] | None) -> str:
    if not agent_plan:
        return "deterministic planner"
    intent = str(agent_plan.get("intent") or "auto")
    confidence = agent_plan.get("confidence")
    if isinstance(confidence, (int, float)):
        return f"{intent}, confidence {confidence:.2f}"
    return intent


def _agent_label(agent: str) -> str:
    labels = {
        "chart_agent": "SQL/chart agent",
        "insight_agent": "Insight agent",
        "forecast_agent": "Forecast agent",
        "qa_agent": "QA agent",
        "partition_agent": "Partition agent",
    }
    return labels.get(agent, agent.replace("_", " ").title())


def _agent_subtitle(agent: str) -> str:
    subtitles = {
        "chart_agent": "generates guarded ClickHouse SQL",
        "insight_agent": "finds root-cause signals",
        "forecast_agent": "fits trend forecast",
        "qa_agent": "checks failed batches and audit health",
        "partition_agent": "checks partition strategy and pruning",
    }
    return subtitles.get(agent, "specialist task")


def _agent_kind(agent: str) -> str:
    if agent == "partition_agent":
        return "partition"
    if agent == "qa_agent":
        return "batch"
    return "agent"


def _agent_status(agent: str, responses: list[Any] | None) -> str:
    for response in responses or []:
        if getattr(response, "agent", "") == agent:
            status = str(getattr(response, "status", "ready"))
            return "ready" if status == "success" else "warning" if status == "partial" else "error"
    return "ready"


def _has_batch_issues(responses: list[Any] | None) -> bool:
    for response in responses or []:
        for artifact in getattr(response, "artifacts", []) or []:
            result = artifact.get("result") if isinstance(artifact, dict) else None
            if not isinstance(result, dict):
                continue
            for issue in result.get("issues") or []:
                signal = str(issue.get("signal") if isinstance(issue, dict) else "")
                if signal in {"failed_batches", "quarantined_batches"}:
                    return True
    return False


def _table_summary(tables: list[str]) -> str:
    if not tables:
        return "vsf_mart / vsf_stg query tables"
    head = ", ".join(tables[:2])
    return f"{head} +{len(tables) - 2} more" if len(tables) > 2 else head


def _execution_summary(status: str, execution_time_ms: int | None) -> str:
    if execution_time_ms is None:
        return status
    return f"{status}, {execution_time_ms} ms"


def _short(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
