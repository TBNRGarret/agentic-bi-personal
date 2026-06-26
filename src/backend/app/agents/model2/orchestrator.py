from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from ...config import Settings
from ...connectors.clickhouse import ClickHouseConnector
from ...connectors.datahub import DataHubConnector
from ...connectors.postgres import PostgresConnector
from ...security import Principal
from ...services.context_manager import contextual_question
from ...services.llm import LlmService
from ..monitoring_agent import _intent, _prepare_sql_candidate
from ..request_orchestrator import build_request_plan
from .decomposer_agent import DecomposerAgent
from .hybrid_selector import HybridSelector
from .refiner_agent import RefinerAgent
from .relevancy_agent import RelevancyAgent
from .response_formatter import ResponseFormatter
from .schema_retriever import SchemaRetriever
from .workflow_graph import build_model2_workflow_graph

EventSink = Callable[[dict[str, Any]], None]


class HybridText2SQLOrchestrator:
    """Model 2: MAC-SQL selector/decomposer/refiner over QueryWeaver-style retrieval."""

    def __init__(
        self,
        settings: Settings,
        clickhouse: ClickHouseConnector,
        metadata: DataHubConnector,
        postgres: PostgresConnector | None = None,
        llm: LlmService | None = None,
    ) -> None:
        self.settings = settings
        self.clickhouse = clickhouse
        self.metadata = metadata
        self.postgres = postgres
        self.llm = llm or LlmService(settings)
        self.relevancy_agent = RelevancyAgent()
        self.schema_retriever = SchemaRetriever(settings, clickhouse, metadata, postgres)
        self.selector = HybridSelector()
        self.decomposer = DecomposerAgent()
        self.refiner = RefinerAgent()
        self.formatter = ResponseFormatter()

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
        started = perf_counter()
        emit = event_sink or (lambda _: None)
        active_principal = principal or Principal(
            user_id="system",
            username="system",
            role="admin",
            team="platform",
        )
        effective_question = contextual_question(question, memory or {})

        emit({"type": "stage", "stage": "model2.relevancy", "message": "Checking BI relevancy"})
        request_plan = await self._request_plan(effective_question, target, memory or {})
        relevancy = self.relevancy_agent.check(
            effective_question,
            request_plan=request_plan,
        )
        if relevancy.needs_clarification or relevancy.status == "off_topic":
            status = "needs_clarification" if relevancy.needs_clarification else "error"
            return self._early_result(
                question=question,
                target=target,
                status=status,
                answer=(
                    str((relevancy.clarification or {}).get("question") or "")
                    or relevancy.reason
                    or "The request needs clarification before Model 2 can query data."
                ),
                request_plan=request_plan,
                relevancy=relevancy.to_dict(),
                started=started,
                clarification=relevancy.clarification,
            )

        emit({"type": "stage", "stage": "model2.graph_retrieval", "message": "Retrieving governed schema graph"})
        retrieval = await self.schema_retriever.retrieve(
            effective_question,
            target=target,
            request_plan=request_plan,
        )
        if retrieval.datasource == "dwh" and not active_principal.is_admin:
            return self._error_result(
                question=question,
                target=target,
                datasource=retrieval.datasource,
                selected_tables=[],
                answer="Operational PostgreSQL data requires the admin role.",
                error="Operational PostgreSQL requires admin",
                started=started,
                failed_stage="metadata_catalog",
            )
        if not retrieval.tables:
            return self._error_result(
                question=question,
                target=target,
                datasource=retrieval.datasource,
                selected_tables=[],
                answer="Model 2 could not find queryable metadata for this request.",
                error="No queryable schema candidates",
                started=started,
                failed_stage="graph_retrieval",
            )

        emit({"type": "stage", "stage": "model2.selector", "message": "Pruning schema with MAC-SQL selector"})
        selector = self.selector.select(
            effective_question,
            retrieval,
            request_plan=request_plan,
        )
        emit(
            {
                "type": "stage",
                "stage": "model2.selector",
                "message": (
                    f"Selected {len(selector.selected_tables)} table(s): "
                    f"{', '.join(selector.selected_table_names[:3])}"
                ),
            }
        )

        emit({"type": "stage", "stage": "model2.decomposer", "message": "Decomposing request and generating SQL"})
        plan = await self.decomposer.plan(
            effective_question,
            selector=selector,
            datasource=retrieval.datasource,
            limit=limit,
            llm=self.llm,
            conversation=conversation or [],
            memory=memory or {},
            request_plan=request_plan,
        )
        if plan.status != "success" or not plan.sql:
            return self._error_result(
                question=question,
                target=target,
                datasource=retrieval.datasource,
                selected_tables=selector.selected_table_names,
                answer="Model 2 could not translate the request into SQL.",
                error=plan.rationale or "SQL generation failed",
                started=started,
                failed_stage="decomposer",
                request_plan=request_plan,
                retrieval=retrieval.to_dict(),
                selector=selector.to_dict(),
                plan=plan.to_dict(),
            )

        connector = self.postgres if retrieval.datasource == "dwh" else self.clickhouse
        if connector is None:
            return self._error_result(
                question=question,
                target=target,
                datasource=retrieval.datasource,
                selected_tables=selector.selected_table_names,
                answer="The selected datasource is not configured.",
                error="Datasource connector missing",
                started=started,
                failed_stage="executor",
            )

        attempts: list[dict[str, Any]] = []
        sql = ""
        rows: list[dict[str, Any]] = []
        last_error = ""
        candidates = plan.candidates or [{"sql": plan.sql, "rationale": plan.rationale}]
        for index, candidate in enumerate(candidates):
            raw_sql = str(candidate.get("sql") or "").strip()
            if not raw_sql:
                continue
            try:
                emit({"type": "stage", "stage": "model2.sql_safety", "message": "Validating read-only SQL and row security"})
                prepared_sql = _prepare_sql_candidate(
                    raw_sql,
                    limit=limit,
                    datasource=retrieval.datasource,
                    tables=retrieval.tables,
                    principal=active_principal,
                )
                emit({"type": "sql", "sql": prepared_sql, "intent": _intent(effective_question), "datasource": retrieval.datasource})
                emit({"type": "stage", "stage": "model2.executor", "message": f"Executing SQL candidate {index + 1}/{len(candidates)}"})
                candidate_rows = await connector.query(prepared_sql)
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "phase": "candidate",
                        "sql": prepared_sql,
                        "status": "success",
                        "rowCount": len(candidate_rows),
                    }
                )
                sql = prepared_sql
                rows = candidate_rows
                break
            except Exception as exc:
                last_error = str(exc)
                sql = raw_sql
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "phase": "candidate",
                        "sql": raw_sql,
                        "status": "error",
                        "error": last_error,
                    }
                )
                emit(
                    {
                        "type": "stage",
                        "stage": "model2.executor",
                        "status": "error",
                        "message": f"Candidate failed: {last_error[:180]}",
                    }
                )

        if not sql or (not rows and attempts and attempts[-1].get("status") == "error"):
            sql, rows, last_error = await self._refine_until_success(
                question=effective_question,
                failed_sql=sql or plan.sql,
                database_error=last_error,
                selector_tables=retrieval.tables,
                datasource=retrieval.datasource,
                limit=limit,
                connector=connector,
                principal=active_principal,
                request_plan=request_plan,
                attempts=attempts,
                emit=emit,
            )

        elapsed = round((perf_counter() - started) * 1000)
        if last_error and (not attempts or attempts[-1].get("status") != "success"):
            return self._error_result(
                question=question,
                target=target,
                datasource=retrieval.datasource,
                selected_tables=selector.selected_table_names,
                answer="Model 2 could not complete the query after refinement.",
                error=last_error,
                started=started,
                failed_stage="refiner",
                request_plan=request_plan,
                retrieval=retrieval.to_dict(),
                selector=selector.to_dict(),
                plan=plan.to_dict(),
                attempts=attempts,
                sql=sql,
            )

        emit({"type": "stage", "stage": "model2.formatter", "message": "Formatting answer and visualization"})
        formatted = await self.formatter.format(
            question=question,
            sql=sql,
            rows=rows,
            table_names=selector.selected_table_names,
            tables=retrieval.tables,
            llm=self.llm,
            intent=_intent(effective_question),
            planned_visualization=plan.visualization,
            conversation=conversation or [],
            memory=memory or {},
            include_synthesis=include_synthesis,
            event_sink=event_sink,
            synthesis_timeout_seconds=self.settings.agent_synthesis_timeout_seconds,
        )
        usage = _merge_usage(plan.raw_plan, formatted.get("llmSynthesis") or {})
        return {
            "agent": "model2_hybrid_text2sql",
            "model": "model2",
            "question": question,
            "target": retrieval.datasource,
            "intent": _intent(effective_question),
            "status": "success",
            "answer": formatted["answer"],
            "sql": sql,
            "rows": rows,
            "rowCount": len(rows),
            "chartSpec": formatted["chartSpec"],
            "attempts": attempts,
            "selectedTables": selector.selected_table_names,
            "searchedTables": selector.selected_table_names,
            "executionTimeMs": elapsed,
            "metadataContext": retrieval.to_dict(),
            "tableSelection": selector.to_dict(),
            "sqlGeneration": plan.to_dict(),
            "orchestration": request_plan,
            "llmSynthesis": formatted.get("llmSynthesis"),
            "llmUsage": usage,
            "workflowGraph": build_model2_workflow_graph(
                question=question,
                target=target,
                status="success",
                datasource=retrieval.datasource,
                selected_tables=selector.selected_table_names,
                execution_time_ms=elapsed,
            ),
            "warnings": retrieval.warnings,
        }

    async def _request_plan(
        self,
        question: str,
        target: str,
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        llm_slots: dict[str, Any] = {}
        if self.settings.llm_configured:
            try:
                llm_slots = await self.llm.classify_request_slots(question)
            except Exception:
                llm_slots = {}
        return build_request_plan(
            question,
            memory=memory,
            target=target,
            llm_slots=llm_slots,
        )

    async def _refine_until_success(
        self,
        *,
        question: str,
        failed_sql: str,
        database_error: str,
        selector_tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        connector: Any,
        principal: Principal,
        request_plan: dict[str, Any],
        attempts: list[dict[str, Any]],
        emit: EventSink,
    ) -> tuple[str, list[dict[str, Any]], str]:
        last_sql = failed_sql
        last_error = database_error
        error_history: list[dict[str, Any]] = []
        for retry in range(self.settings.agent_retry_count):
            error_history.append({
                "attempt": retry + 1,
                "failedSql": last_sql,
                "databaseError": last_error,
            })
            emit({"type": "stage", "stage": "model2.refiner", "message": f"Repairing SQL from database feedback (retry {retry + 1})"})
            correction = await self.refiner.refine(
                question=question,
                failed_sql=last_sql,
                database_error=last_error,
                tables=selector_tables,
                datasource=datasource,
                limit=limit,
                llm=self.llm,
                request_plan=request_plan,
                error_history=error_history,
            )
            fixed_sql = str(correction.get("sql") or "").strip()
            if not fixed_sql:
                last_error = str(correction.get("error") or "Refiner did not return SQL")
                continue
            try:
                prepared_sql = _prepare_sql_candidate(
                    fixed_sql,
                    limit=limit,
                    datasource=datasource,
                    tables=selector_tables,
                    principal=principal,
                )
                emit({"type": "sql", "sql": prepared_sql, "intent": _intent(question), "datasource": datasource})
                rows = await connector.query(prepared_sql)
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "phase": "refinement",
                        "correction": retry + 1,
                        "sql": prepared_sql,
                        "status": "success",
                        "rowCount": len(rows),
                        "errorClassification": correction.get("errorClassification"),
                    }
                )
                return prepared_sql, rows, ""
            except Exception as exc:
                last_sql = fixed_sql
                last_error = str(exc)
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "phase": "refinement",
                        "correction": retry + 1,
                        "sql": fixed_sql,
                        "status": "error",
                        "error": last_error,
                        "errorClassification": correction.get("errorClassification"),
                    }
                )
        return last_sql, [], last_error

    def _early_result(
        self,
        *,
        question: str,
        target: str,
        status: str,
        answer: str,
        request_plan: dict[str, Any],
        relevancy: dict[str, Any],
        started: float,
        clarification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        elapsed = round((perf_counter() - started) * 1000)
        return {
            "agent": "model2_hybrid_text2sql",
            "model": "model2",
            "question": question,
            "target": target,
            "intent": "clarification",
            "status": status,
            "answer": answer,
            "clarification": clarification,
            "rows": [],
            "rowCount": 0,
            "attempts": [],
            "selectedTables": [],
            "orchestration": request_plan,
            "relevancy": relevancy,
            "executionTimeMs": elapsed,
            "workflowGraph": build_model2_workflow_graph(
                question=question,
                target=target,
                status=status,
                datasource=target,
                selected_tables=[],
                execution_time_ms=elapsed,
                failed_stage="model2_relevancy" if status == "error" else None,
            ),
        }

    def _error_result(
        self,
        *,
        question: str,
        target: str,
        datasource: str,
        selected_tables: list[str],
        answer: str,
        error: str,
        started: float,
        failed_stage: str,
        request_plan: dict[str, Any] | None = None,
        retrieval: dict[str, Any] | None = None,
        selector: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        attempts: list[dict[str, Any]] | None = None,
        sql: str | None = None,
    ) -> dict[str, Any]:
        elapsed = round((perf_counter() - started) * 1000)
        return {
            "agent": "model2_hybrid_text2sql",
            "model": "model2",
            "question": question,
            "target": datasource,
            "intent": "error",
            "status": "error",
            "answer": answer,
            "error": error,
            "sql": sql,
            "rows": [],
            "rowCount": 0,
            "attempts": attempts or [],
            "selectedTables": selected_tables,
            "metadataContext": retrieval,
            "tableSelection": selector,
            "sqlGeneration": plan,
            "orchestration": request_plan or {},
            "executionTimeMs": elapsed,
            "workflowGraph": build_model2_workflow_graph(
                question=question,
                target=target,
                status="error",
                datasource=datasource,
                selected_tables=selected_tables,
                execution_time_ms=elapsed,
                failed_stage=failed_stage,
            ),
        }


def _merge_usage(*parts: dict[str, Any]) -> dict[str, Any]:
    prompt = 0
    completion = 0
    cost = 0.0
    model = None
    provider = None
    for part in parts:
        if not isinstance(part, dict):
            continue
        usage = part.get("usage") if isinstance(part.get("usage"), dict) else part
        prompt += int(usage.get("prompt_tokens") or usage.get("promptTokens") or 0)
        completion += int(usage.get("completion_tokens") or usage.get("completionTokens") or 0)
        cost += float(part.get("totalCostUsd") or usage.get("estimatedCostUsd") or 0)
        model = part.get("model") or usage.get("model") or model
        provider = part.get("provider") or usage.get("provider") or provider
    total = prompt + completion
    return {
        "promptTokens": prompt,
        "completionTokens": completion,
        "totalTokens": total,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "estimatedCostUsd": round(cost, 8),
        "model": model,
        "provider": provider,
    }
