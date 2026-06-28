"""MultiAgentOrchestrator control-plane agent."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from ..config import Settings
from ..security import Principal
from ..services.llm import LlmService
from .agent_planner import (
    agent_plan_clarification as _agent_plan_clarification,
    deterministic_agent_plan as _deterministic_agent_plan,
    validate_agent_plan as _validate_agent_plan,
)
from .monitoring_agent import MonitoringAgent
from .partition_agent import PartitionAgent
from .request_orchestrator import (
    build_request_plan,
    clarification_for,
    medium_route_note,
    route_confidence_level,
)
from .specialists import (
    AgentResponse,
    AgentTask,
    ChartAgent,
    Evidence,
    ForecastAgent,
    InsightAgent,
    QaAgent,
    SpecialistTableResolution,
    SummarizerAgent,
    _agent_synthesis_timeout,
    _artifact_row_count,
    _asks_log_error_lookup,
    _clickhouse_where_clause,
    _elapsed_ms,
    _extract_entity_filters,
    _force_chart_for_log_error_lookup,
    _force_request_plan_route,
    _legacy_intent,
    _llm_usage_from_result,
    _merge_agent_usage,
    _merge_attempts,
    _merge_metadata_context,
    _merge_searched_tables,
    _merge_selected_tables,
    _merge_sql_generation,
    _merge_table_selection,
    _metric_values,
    _parse_days,
    _primary_artifact_result,
    _resolve_specialist_table,
    _result_evidence,
    _run_llm_synthesis,
    _slot_value,
    _sql_evidence,
)
from .workflow_graph import build_workflow_graph

EventSink = Callable[[dict[str, Any]], None]
AgentStatus = Literal["success", "partial", "failed"]

_SYSTEM_PRINCIPAL = Principal(
    user_id="system",
    username="system",
    role="admin",
    team="system",
)


class MultiAgentOrchestrator:
    """Control-plane agent that delegates to semantic specialist plans."""

    def __init__(
        self,
        settings: Settings,
        chart_agent: MonitoringAgent,
        llm: LlmService | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm or LlmService(settings)
        self.chart_agent = ChartAgent(chart_agent)
        metadata = getattr(chart_agent, "metadata", None)
        from .partition_agent import PartitionAgent

        self.specialists = {
            "chart_agent": self.chart_agent,
            "insight_agent": InsightAgent(
                chart_agent.clickhouse,
                chart_agent.postgres,
                self.settings,
                self.llm,
                metadata=metadata,
            ),
            "forecast_agent": ForecastAgent(
                chart_agent.clickhouse,
                self.settings,
                self.llm,
                metadata=metadata,
            ),
            "qa_agent": QaAgent(
                chart_agent.clickhouse,
                chart_agent.postgres,
                self.settings,
                self.llm,
                metadata=metadata,
            ),
            "partition_agent": PartitionAgent(
                chart_agent.clickhouse,
                self.settings,
                metadata=metadata,
            ),
        }
        self.summarizer = SummarizerAgent(self.llm)

    async def answer(
        self,
        question: str,
        *,
        target: str = "auto",
        limit: int = 100,
        conversation: list[dict[str, Any]] | None = None,
        memory: dict[str, Any] | None = None,
        principal: Principal | None = None,
        include_synthesis: bool = True,
        event_sink: EventSink | None = None,
    ) -> dict[str, Any]:
        emit = event_sink or (lambda _: None)
        started = datetime.now(timezone.utc)
        emit(
            {
                "type": "stage",
                "stage": "orchestrator",
                "message": "Extracting semantic slots and planning agents",
            }
        )
        llm_slots = await self._semantic_slots(question)
        request_plan = build_request_plan(
            question,
            memory=memory,
            target=target,
            llm_slots=llm_slots,
        )
        clarification = clarification_for(request_plan)
        if clarification:
            return self._clarification_result(
                question=question,
                target=target,
                started=started,
                request_plan=request_plan,
                clarification=clarification,
            )

        agent_plan = await self._agent_plan(
            question,
            target=target,
            limit=limit,
            conversation=conversation or [],
            memory=memory or {},
            request_plan=request_plan,
        )
        if agent_plan["status"] != "success":
            clarification = _agent_plan_clarification(agent_plan)
            return self._clarification_result(
                question=question,
                target=target,
                started=started,
                request_plan=request_plan,
                clarification=clarification,
                answer=str(clarification["question"]),
            )

        route_intent = str(agent_plan["intent"])
        agent_names = list(agent_plan["agents"])
        confidence = float(agent_plan.get("confidence") or 0)
        confidence_level = route_confidence_level(confidence)
        route_note = (
            medium_route_note(", ".join(agent_names), confidence)
            if confidence_level == "medium"
            else None
        )
        emit(
            {
                "type": "stage",
                "stage": "routing",
                "message": (
                    f"Semantic planner selected {', '.join(agent_names)} "
                    f"({confidence_level} confidence)"
                ),
            }
        )
        tasks = [
            AgentTask(
                task_id=uuid4().hex,
                agent=name,
                objective=question,
                context={
                    "routeIntent": route_intent,
                    "slots": request_plan["slots"],
                    "plannerRationale": agent_plan.get("rationale"),
                },
            )
            for name in agent_names
        ]
        responses = await asyncio.gather(
            *[
                self._run_task(
                    task,
                    question=question,
                    target=target,
                    limit=limit,
                    conversation=conversation or [],
                    memory=memory or {},
                    principal=principal,
                    include_synthesis=include_synthesis,
                    event_sink=emit,
                )
                for task in tasks
            ]
        )
        accepted = [
            response for response in responses if response.status in {"success", "partial"}
        ]
        warnings = [
            warning
            for response in responses
            for warning in response.warnings
        ] + [
            response.text
            for response in responses
            if response.status == "failed"
        ]
        primary = _primary_artifact_result(responses)
        selected_tables = _merge_selected_tables(responses, primary)
        status = (
            "success"
            if accepted and all(response.status == "success" for response in responses)
            else "partial"
            if accepted
            else "error"
        )
        answer = await self.summarizer.summarize(
            question,
            responses,
            fallback=primary.get("answer") if primary else None,
        )
        result: dict[str, Any] = {
            "agent": "multi_agent_orchestrator",
            "question": question,
            "target": primary.get("target", target) if primary else target,
            "intent": _legacy_intent(request_plan),
            "status": status,
            "answer": answer,
            "rows": primary.get("rows", []) if primary else [],
            "rowCount": _artifact_row_count(primary) if primary else 0,
            "chartSpec": primary.get("chartSpec", {"type": "table"})
            if primary
            else {"type": "table", "reason": "No chart artifact was returned"},
            "sql": primary.get("sql") if primary else None,
            "selectedTables": selected_tables,
            "searchedTables": _merge_searched_tables(responses, primary) if primary else None,
            "attempts": _merge_attempts(responses, primary) if primary else [],
            "metadataContext": _merge_metadata_context(responses, primary) if primary else None,
            "tableSelection": _merge_table_selection(responses, primary) if primary else None,
            "sqlGeneration": _merge_sql_generation(responses, primary) if primary else None,
            "routeNote": route_note or primary.get("routeNote") if primary else route_note,
            "orchestration": {
                **request_plan,
                "routeIntent": route_intent,
                "agents": agent_names,
                "tasks": [asdict(task) for task in tasks],
                "planner": {
                    key: value
                    for key, value in agent_plan.items()
                    if key not in {"agents"}
                },
            },
            "agentResponses": [response.to_dict() for response in responses],
            "agentsUsed": agent_names,
            "workflowGraph": build_workflow_graph(
                question=question,
                target=primary.get("target", target) if primary else target,
                status=status,
                request_plan={
                    **request_plan,
                    "routeIntent": route_intent,
                },
                agent_plan=agent_plan,
                agent_names=agent_names,
                responses=responses,
                primary=primary,
                selected_tables=selected_tables,
                execution_time_ms=_elapsed_ms(started),
            ),
            "warnings": warnings,
            "executionTimeMs": _elapsed_ms(started),
            "llmUsage": _merge_agent_usage(responses, primary, self.summarizer.last_usage),
        }
        if primary and primary.get("llmSynthesis") is not None:
            result["llmSynthesis"] = primary.get("llmSynthesis")
        return {key: value for key, value in result.items() if value is not None}

    async def _semantic_slots(self, question: str) -> dict[str, Any]:
        classifier = getattr(self.llm, "classify_request_slots", None)
        if classifier is None:
            return {}
        if not self.settings.llm_configured and isinstance(self.llm, LlmService):
            return {}
        try:
            return await classifier(question)
        except Exception:
            return {}

    async def _agent_plan(
        self,
        question: str,
        *,
        target: str,
        limit: int,
        conversation: list[dict[str, Any]],
        memory: dict[str, Any],
        request_plan: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.settings.llm_configured and isinstance(self.llm, LlmService):
            return _force_request_plan_route(
                question, request_plan, _deterministic_agent_plan(question, request_plan)
            )
        planner = getattr(self.llm, "plan_agents", None)
        if planner is None:
            return _force_request_plan_route(
                question, request_plan, _deterministic_agent_plan(question, request_plan)
            )
        try:
            raw_plan = await planner(
                question=question,
                target=target,
                limit=limit,
                slots=request_plan.get("slots") or {},
                conversation=conversation,
                memory=memory,
            )
        except Exception as exc:
            fallback = _deterministic_agent_plan(question, request_plan)
            fallback["reason"] = str(exc)[:240]
            return _force_request_plan_route(question, request_plan, fallback)
        validated = _validate_agent_plan(raw_plan)
        if validated.get("status") == "low_confidence":
            return validated
        plan = (
            validated
            if validated.get("status") == "success"
            else _deterministic_agent_plan(question, request_plan)
        )
        return _force_request_plan_route(question, request_plan, plan)

    def _clarification_result(
        self,
        *,
        question: str,
        target: str,
        started: datetime,
        request_plan: dict[str, Any],
        clarification: dict[str, Any],
        answer: str | None = None,
    ) -> dict[str, Any]:
        return {
            "agent": "multi_agent_orchestrator",
            "question": question,
            "target": target,
            "intent": _legacy_intent(request_plan),
            "status": "needs_clarification",
            "answer": answer or str(clarification["question"]),
            "clarification": clarification,
            "orchestration": request_plan,
            "rows": [],
            "rowCount": 0,
            "attempts": [],
            "selectedTables": [],
            "workflowGraph": build_workflow_graph(
                question=question,
                target=target,
                status="needs_clarification",
                request_plan=request_plan,
                clarification=clarification,
                execution_time_ms=_elapsed_ms(started),
            ),
            "executionTimeMs": _elapsed_ms(started),
        }

    async def _run_task(
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
        specialist = self.specialists[task.agent]
        try:
            event_sink(
                {
                    "type": "stage",
                    "stage": task.agent,
                    "message": f"Running {task.agent}",
                }
            )
            return await asyncio.wait_for(
                specialist.run(
                    task,
                    question=question,
                    target=target,
                    limit=limit,
                    conversation=conversation,
                    memory=memory,
                    principal=principal,
                    include_synthesis=include_synthesis,
                    event_sink=event_sink,
                ),
                timeout=task.timeout_seconds,
            )
        except TimeoutError:
            message = f"{task.agent} timed out after {task.timeout_seconds}s"
            return AgentResponse(
                task_id=task.task_id,
                agent=task.agent,
                status="failed",
                text=message,
                confidence=0.0,
                warnings=[message],
            )
        except Exception as exc:
            message = f"{task.agent} failed: {str(exc)[:240]}"
            return AgentResponse(
                task_id=task.task_id,
                agent=task.agent,
                status="failed",
                text=message,
                confidence=0.0,
                warnings=[message],
            )
