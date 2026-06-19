from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from ..security import Principal

from .monitoring_agent import MonitoringAgent


async def generate_dashboard(
    agent: MonitoringAgent,
    question: str,
    *,
    target: str = "auto",
    limit: int = 100,
    max_panels: int = 3,
    conversation: list[dict[str, Any]] | None = None,
    memory: dict[str, Any] | None = None,
    principal: Principal | None = None,
    include_synthesis: bool = False,
) -> dict[str, Any]:
    definitions = _panel_prompts(question)[: max(2, min(max_panels, 4))]

    async def build_panel(
        index: int, title: str, prompt: str
    ) -> dict[str, Any]:
        result = await agent.answer(
            prompt,
            target=target,
            limit=limit,
            conversation=conversation or [],
            memory=memory,
            principal=principal,
            include_synthesis=include_synthesis,
        )
        return {
            "id": f"panel-{index + 1}-{_slug(title)}",
            "title": title,
            "question": prompt,
            "layout": {
                "x": index % 2,
                "y": index // 2,
                "w": 1,
                "h": 1,
            },
            **result,
        }

    panels = await asyncio.gather(
        *(
            build_panel(index, title, prompt)
            for index, (title, prompt) in enumerate(definitions)
        )
    )
    successful = [panel for panel in panels if panel.get("status") == "success"]
    return {
        "status": "success" if successful else "error",
        "question": question,
        "target": target,
        "title": _title(question),
        "panelCount": len(panels),
        "successfulPanelCount": len(successful),
        "panels": panels,
        "layout": {"columns": 2},
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _panel_prompts(question: str) -> list[tuple[str, str]]:
    text = question.lower()
    if any(term in text for term in ("llm", "token", "model", "cost")):
        return [
            ("Daily LLM usage", "Show daily LLM token and cost trend for 30 days"),
            ("Model cost ranking", "Show top models by token cost for 30 days"),
            ("LLM errors", "Show daily LLM errors for 30 days"),
            ("LLM latency", "Show daily LLM latency for 30 days"),
        ]
    if any(term in text for term in ("ingestion", "pipeline", "batch", "etl")):
        return [
            ("Ingestion health", "Show ingestion health by source"),
            ("Loaded rows", "Show loaded rows by source"),
            ("Failed batches", "Show failed and quarantined ingestion batches"),
            ("Daily ingestion", "Show daily ingestion health trend"),
        ]
    return [
        ("Service health", "Show service errors and latency"),
        ("Trace health", "Show daily trace health trend"),
        ("LLM usage", "Show daily LLM token and cost trend"),
        ("Ingestion health", "Show ingestion health by source"),
    ]


def _title(question: str) -> str:
    clean = re.sub(
        r"\b(show|create|generate|build|me|a|an|dashboard|of|for)\b",
        " ",
        question,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"\s+", " ", clean).strip(" .?")
    return f"{clean.title() or 'Agentic BI'} Dashboard"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
