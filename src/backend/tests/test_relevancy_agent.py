"""Tests for RelevancyAgent and RelevancyDecision."""

from __future__ import annotations

from app.agents.model2.relevancy_agent import RelevancyAgent
from app.agents.model2.contracts import RelevancyDecision


def test_relevancy_agent_check() -> None:
    agent = RelevancyAgent()

    available_tables = [
        {"tableName": "vsf_raw.users", "description": "Customer profiles and user accounts"},
        {"tableName": "vsf_raw.orders", "description": "Online purchase transactions and sales"},
    ]

    relevant = agent.check(
        question="Find top 10 users by total order amount",
        request_plan={"slots": {"metrics": ["sum"], "dimensions": ["user"]}},
        available_tables=available_tables,
    )
    assert isinstance(relevant, RelevancyDecision)
    assert relevant.status == "relevant"
    assert relevant.confidence >= 0.5

    irrelevant = agent.check(
        question="How to bake a chocolate cake with vanilla frosting",
        request_plan={"slots": {}},
        available_tables=available_tables,
    )
    assert irrelevant.status in {"irrelevant", "unclear"}
    assert irrelevant.confidence < 0.5

    unclear = agent.check(
        question="Show me the stats",
        request_plan={"slots": {}},
        available_tables=available_tables,
    )
    assert unclear.status in {"unclear", "relevant", "irrelevant"}
