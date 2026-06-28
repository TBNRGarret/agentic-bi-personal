"""Chart specialist agent."""

from __future__ import annotations

from typing import Any

from ...security import Principal
from ..monitoring_agent import MonitoringAgent
from .specialist_utils import (
    AgentResponse,
    AgentStatus,
    AgentTask,
    EventSink,
    _result_evidence,
)


class ChartAgent:
    def __init__(self, monitoring_agent: MonitoringAgent) -> None:
        self.monitoring_agent = monitoring_agent

    async def run(
        self,
        task: AgentTask,
        *,
        question: str,
        target: str,
        limit: int,
        conversation: list[dict[str, Any]],
        memory: dict[str, Any],
        principal: Principal | None,
        include_synthesis: bool,
        event_sink: EventSink,
    ) -> AgentResponse:
        result = await self.monitoring_agent.answer(
            question,
            target=target,
            limit=limit,
            conversation=conversation,
            memory=memory,
            principal=principal,
            include_synthesis=include_synthesis,
            event_sink=event_sink,
        )
        status: AgentStatus = (
            "success" if result.get("status") == "success" else "failed"
        )
        artifact = {"type": "query_result", "result": result}
        evidence = _result_evidence(result)
        return AgentResponse(
            task_id=task.task_id,
            agent="chart_agent",
            status=status,
            text=str(result.get("answer") or "Chart agent completed."),
            evidence=evidence,
            artifacts=[artifact],
            confidence=0.9 if status == "success" else 0.0,
            warnings=[]
            if status == "success"
            else [str(result.get("error") or result.get("answer") or "Chart agent failed.")],
            usage=result.get("llmUsage") or {},
        )
