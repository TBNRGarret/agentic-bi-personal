from __future__ import annotations

from typing import Any

from ..request_orchestrator import clarification_for
from .contracts import RelevancyDecision
from .utils import normalize_text


class RelevancyAgent:
    """QueryWeaver-style gate for BI answerability before SQL planning."""

    def check(
        self,
        question: str,
        *,
        request_plan: dict[str, Any],
        available_tables: list[dict[str, Any]] | None = None,
    ) -> RelevancyDecision:
        clarification = clarification_for(request_plan)
        if clarification:
            return RelevancyDecision(
                status="needs_clarification",
                reason=str(clarification.get("reason") or "Missing request slot"),
                needs_clarification=True,
                clarification=clarification,
                confidence=0.35,
            )

        text = normalize_text(question)
        if _is_smalltalk(text):
            return RelevancyDecision(
                status="off_topic",
                reason="The question is conversational and does not ask for BI data.",
                confidence=0.85,
            )

        if not available_tables:
            return RelevancyDecision(
                status="on_topic",
                reason="No catalog was loaded yet; continue to metadata retrieval.",
                confidence=0.55,
            )

        if _has_bi_signal(text, request_plan):
            return RelevancyDecision(
                status="on_topic",
                reason="The request matches analytics, monitoring, ingestion, or cost data.",
                confidence=0.88,
            )

        return RelevancyDecision(
            status="on_topic",
            reason="The request is not clearly off-topic; continue with governed schema retrieval.",
            confidence=0.62,
        )


def _is_smalltalk(text: str) -> bool:
    return text in {
        "hi",
        "hello",
        "hey",
        "xin chao",
        "chao",
        "cam on",
        "thanks",
        "thank you",
    }


def _has_bi_signal(text: str, request_plan: dict[str, Any]) -> bool:
    slots = request_plan.get("slots") if isinstance(request_plan, dict) else {}
    if isinstance(slots, dict):
        for name in ("intent_type", "domain", "metric"):
            slot = slots.get(name) or {}
            if slot.get("value") not in (None, "", []):
                return True
    return any(
        term in text
        for term in (
            "dashboard",
            "latency",
            "error",
            "cost",
            "token",
            "request",
            "model",
            "service",
            "pipeline",
            "ingestion",
            "airflow",
            "dbt",
            "data quality",
            "clickhouse",
        )
    )
