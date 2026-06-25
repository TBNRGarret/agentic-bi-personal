from __future__ import annotations

from typing import Any


ROUTING_TABLE: dict[str, list[str]] = {
    "insight": ["insight_agent"],
    "chart": ["chart_agent"],
    "forecast": ["forecast_agent"],
    "qa": ["qa_agent"],
    "partition": ["partition_agent"],
    "dashboard": ["insight_agent", "chart_agent", "qa_agent"],
    "full_report": [
        "insight_agent",
        "chart_agent",
        "forecast_agent",
        "qa_agent",
        "partition_agent",
    ],
}
_KNOWN_AGENTS = {agent for agents in ROUTING_TABLE.values() for agent in agents}


def deterministic_agent_plan(
    question: str, request_plan: dict[str, Any]
) -> dict[str, Any]:
    text = question.lower()
    slot_intent = str(
        (request_plan.get("slots") or {}).get("intent_type", {}).get("value") or ""
    )
    domain = str(
        (request_plan.get("slots") or {}).get("domain", {}).get("value") or ""
    )
    if "full report" in text or "bao cao day du" in text or slot_intent == "full_report":
        intent = "full_report"
    elif "dashboard" in text or slot_intent == "dashboard":
        intent = "dashboard"
    elif slot_intent == "partition" or any(
        term in text
        for term in ("partition", "phan vung", "phan chia du lieu", "data partitioning")
    ):
        intent = "partition"
    elif any(
        term in text
        for term in ("forecast", "du bao", "capacity", "next 7", "next 30")
    ):
        intent = "forecast"
    elif domain in {"ingestion", "data_quality"} or any(
        term in text
        for term in ("quality", "chat luong", "failed batch", "quarantine", "qa ")
    ):
        intent = "qa"
    elif domain == "observability" and any(
        term in text
        for term in ("why", "root cause", "nguyen nhan", "bat thuong", "anomaly")
    ):
        intent = "insight"
    else:
        intent = "chart"
    return {
        "status": "success",
        "intent": intent,
        "agents": ROUTING_TABLE[intent],
        "confidence": 0.72,
        "rationale": "Deterministic server fallback; LLM planner is optional.",
    }


def validate_agent_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {"status": "failed", "reason": "Planner did not return an object."}
    if str(plan.get("status") or "success").lower() not in {"success", "ok"}:
        return {
            "status": "failed",
            "reason": str(plan.get("reason") or plan.get("error") or "Planner failed."),
        }
    intent = str(plan.get("intent") or plan.get("intent_type") or "").strip().lower()
    raw_agents = plan.get("agents")
    agents = [str(agent).strip().lower() for agent in raw_agents or []]
    agents = [agent for agent in agents if agent in _KNOWN_AGENTS]
    if not agents and intent in ROUTING_TABLE:
        agents = ROUTING_TABLE[intent]
    if intent not in ROUTING_TABLE:
        intent = _intent_from_agents(agents)
    confidence = _clamp_float(plan.get("confidence"), default=0.0)
    if not agents:
        return {
            "status": "failed",
            "reason": "Planner did not select any known specialist agent.",
        }
    if confidence < 0.45:
        return {
            "status": "low_confidence",
            "reason": str(plan.get("rationale") or "Planner confidence is too low."),
            "confidence": confidence,
        }
    return {
        "status": "success",
        "intent": intent,
        "agents": agents,
        "confidence": confidence,
        "rationale": str(plan.get("rationale") or ""),
    }


def agent_plan_clarification(plan: dict[str, Any]) -> dict[str, Any]:
    reason = str(plan.get("reason") or "The semantic planner could not choose agents.")
    return {
        "tool": "multi_agent_orchestrator",
        "slot": "agent_plan",
        "question": (
            "I could not choose the right agent set with enough confidence. "
            "Please clarify whether you want a chart, root-cause insight, forecast, "
            "QA check, partition analysis, dashboard, or full report."
        ),
        "reason": reason,
        "options": [
            _option("Chart", "agent chart", "Run NL2SQL and return a chart-ready result."),
            _option("Insight", "agent insight", "Analyze latency/error drivers."),
            _option("Forecast", "agent forecast", "Fit a forecast from historical metrics."),
            _option("QA", "agent qa", "Check ingestion and audit health."),
            _option("Partition", "agent partition", "Analyze partition health and pruning."),
            _option("Dashboard", "agent dashboard", "Run insight, chart, and QA agents."),
            _option("Full report", "agent full_report", "Run all specialist agents."),
        ],
    }


def _option(label: str, value: str, description: str) -> dict[str, str]:
    return {"label": label, "value": value, "description": description}


def _intent_from_agents(agents: list[str]) -> str:
    for intent, routed_agents in ROUTING_TABLE.items():
        if agents == routed_agents:
            return intent
    return "chart" if agents == ["chart_agent"] else "full_report"


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))
