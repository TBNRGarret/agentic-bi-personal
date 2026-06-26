"""Summarizer specialist agent."""

from __future__ import annotations

from typing import Any

from ...services.llm import LlmService
from .specialist_utils import AgentResponse, _llm_usage_from_result


class SummarizerAgent:
    def __init__(self, llm: LlmService) -> None:
        self.llm = llm
        self.last_usage: dict[str, Any] = {}

    async def summarize(
        self,
        question: str,
        responses: list[AgentResponse],
        *,
        fallback: str | None = None,
    ) -> str:
        self.last_usage = {}
        successful = [
            response for response in responses if response.status in {"success", "partial"}
        ]
        if len(successful) == 1 and fallback:
            return fallback
        if not successful:
            if fallback:
                return fallback
            for response in responses:
                text = response.text.strip()
                if text:
                    return text
            return "No specialist agent completed successfully."
        summarizer = getattr(self.llm, "summarize_agent_responses", None)
        if summarizer is not None:
            try:
                summary = await summarizer(
                    question=question,
                    responses=[response.to_dict() for response in responses],
                )
                if isinstance(summary, dict):
                    self.last_usage = _llm_usage_from_result(summary)
                    answer = str(summary.get("answer") or "").strip()
                    if answer:
                        return answer
                if isinstance(summary, str) and summary.strip():
                    return summary.strip()
            except Exception:
                pass
        lines = ["TL;DR:"]
        for response in successful:
            label = response.agent.replace("_agent", "").replace("_", " ").title()
            lines.append(
                f"- {label} ({response.confidence:.0%}): {response.text}"
            )
        warnings = [
            warning
            for response in responses
            for warning in response.warnings
            if warning
        ]
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in warnings[:5])
        return "\n".join(lines)
