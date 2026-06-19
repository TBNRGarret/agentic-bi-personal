from __future__ import annotations

import asyncio
import re
import unicodedata
from time import perf_counter
from typing import Any, Callable

from ..config import Settings
from ..connectors.clickhouse import ClickHouseConnector
from ..connectors.datahub import DataHubConnector
from ..connectors.postgres import PostgresConnector
from ..services.llm import LlmService
from ..services.context_manager import contextual_question
from ..security import Principal
from .chart_spec import infer_chart_spec
from .request_orchestrator import (
    build_request_plan,
    clarification_for,
    medium_route_note,
    route_confidence_level,
)
from .row_security import apply_row_security
from .sql_safety import validate_readonly_sql
from .table_selector import TableSelector

EventSink = Callable[[dict[str, Any]], None]


class MonitoringAgent:
    """Metadata-first SQL agent with datasource routing and bounded correction."""

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
        self.table_selector = TableSelector(settings)

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
        principal = principal or Principal(
            user_id="system",
            username="system",
            role="admin",
            team="platform",
        )
        effective_question = contextual_question(question, memory or {})

        llm_slots: dict[str, Any] = {}
        if self.llm and self.settings.llm_configured:
            try:
                classifier = getattr(self.llm, "classify_request_slots", None)
                if classifier:
                    llm_slots = await classifier(effective_question)
            except Exception:
                llm_slots = {}
        request_plan = build_request_plan(
            effective_question,
            memory=memory,
            target=target,
            llm_slots=llm_slots,
        )
        intent_slot = request_plan["slots"]["intent_type"]
        intent = str(intent_slot.get("value") or "")
        if not llm_slots and self.llm and self.settings.llm_configured:
            try:
                intent = await self.llm.classify_intent(effective_question)
                intent_slot.update(
                    {
                        "value": intent,
                        "confidence": 0.8,
                        "source": "llm_intent",
                    }
                )
                request_plan["missingSlots"] = [
                    slot
                    for slot in request_plan["missingSlots"]
                    if slot != "intent_type"
                ]
                request_plan["nextMissingSlot"] = (
                    request_plan["missingSlots"][0]
                    if request_plan["missingSlots"]
                    else None
                )
            except Exception:
                intent = ""
        intent = intent or _intent(effective_question)

        clarification = clarification_for(request_plan)
        if clarification:
            return {
                "agent": "metadata_sql_agent",
                "question": question,
                "target": target,
                "intent": intent,
                "status": "needs_clarification",
                "answer": str(clarification["question"]),
                "clarification": clarification,
                "orchestration": request_plan,
                "rows": [],
                "rowCount": 0,
                "attempts": [],
                "selectedTables": [],
                "executionTimeMs": round((perf_counter() - started) * 1000),
            }

        emit({"type": "stage", "stage": "metadata", "message": "Fetching live schema"})

        datasource = _route_datasource(question, target)
        if datasource == "dwh" and self.postgres is None:
            datasource = "clickhouse"
        if datasource == "dwh" and not principal.is_admin:
            return _error_result(
                question,
                datasource,
                "Operational PostgreSQL data requires the admin role.",
                "postgres_schema",
                [],
                started,
            )

        if datasource == "dwh" and self.postgres is not None:
            metadata_tables = await self.postgres.describe_tables(("ops",))
            metadata_source = "postgres_schema"
        else:
            list_tables = getattr(self.metadata, "list_tables", None)
            if callable(list_tables):
                metadata_tables = await list_tables()
            else:
                metadata_tables = await self.metadata.relevant_tables(
                    effective_question,
                    limit=50,
                )
            metadata_source = str(
                getattr(self.metadata, "source_name", "metadata")
            )
        emit(
            {
                "type": "stage",
                "stage": "metadata",
                "message": (
                    f"Found {len(metadata_tables)} relevant table(s) "
                    f"from {metadata_source}"
                ),
            }
        )

        if not metadata_tables:
            metadata_source = f"{datasource}_fallback"
            if datasource == "dwh" and self.postgres:
                metadata_tables = await self.postgres.describe_tables(("ops",))
            else:
                metadata_tables = await self.clickhouse.describe_tables(
                    tuple(self.settings.clickhouse_analytics_schema_list)
                )

        candidate_tables = _tables_for_datasource(metadata_tables, datasource)
        if not candidate_tables:
            return _error_result(
                question,
                datasource,
                (
                    "No matching operational table was found in PostgreSQL."
                    if datasource == "dwh"
                    else "No matching curated or detail table was found in the "
                    "live metadata catalog."
                ),
                metadata_source,
                metadata_tables,
                started,
            )
        selection = await self.table_selector.select(
            effective_question,
            candidate_tables,
            datasource=datasource,
            min_tables=2,
            max_tables=5,
        )
        ranked_tables = selection.ranked_tables
        selection_confidence = selection.confidence
        confidence_level = selection.confidence_level
        selected = selection.primary_table
        if not selected:
            return _error_result(
                question,
                datasource,
                "No queryable table was found in the live metadata catalog.",
                metadata_source,
                metadata_tables,
                started,
            )

        emit(
            {
                "type": "stage",
                "stage": "routing",
                "message": (
                    f"Table selector chose {datasource}:{selected['tableName']} "
                    f"from {len(selection.tables)} table(s) "
                    f"(confidence {selection_confidence})"
                ),
            }
        )
        emit(
            {
                "type": "stage",
                "stage": "planning",
                "message": "Generating SQL from live columns",
            }
        )
        planning_tables = selection.tables
        requested_candidate_count = _sql_candidate_count(
            effective_question,
            ranked_tables,
            self.settings.agent_sql_candidate_count,
        )
        requested_dimensions = _request_dimensions(request_plan)
        coverage_dimensions = _request_coverage(request_plan)
        execution_limit = (
            self.settings.max_query_rows if coverage_dimensions else limit
        )
        table_selection = selection.to_dict()
        route_note = (
            medium_route_note(selected["tableName"], selection_confidence)
            if confidence_level == "medium"
            else None
        )
        if route_note:
            table_selection["routeNote"] = route_note
        plan = await self.llm.generate_sql(
            question=effective_question,
            tables=planning_tables,
            datasource=datasource,
            limit=execution_limit,
            conversation=conversation,
            memory=memory,
            candidate_count=requested_candidate_count,
            request_plan=request_plan,
        )
        candidate_sqls: list[str] = []
        if plan.get("status") == "success":
            candidate_sqls = [
                sql
                for sql in _plan_sql_candidates(plan)
                if _plan_matches_intent(
                    effective_question,
                    sql,
                    intent=intent,
                    request_plan=request_plan,
                )
            ]
            if candidate_sqls:
                sql = candidate_sqls[0]
                generation_source = (
                    "llm_multi_candidate"
                    if len(candidate_sqls) > 1
                    else "llm"
                )
            else:
                sql = _generate_sql(
                    effective_question,
                    selected,
                    execution_limit,
                    datasource,
                    request_plan=request_plan,
                )
                candidate_sqls = [sql]
                generation_source = "deterministic_semantic_fallback"
        else:
            sql = _generate_sql(
                effective_question,
                selected,
                execution_limit,
                datasource,
                request_plan=request_plan,
            )
            candidate_sqls = [sql]
            generation_source = "deterministic_fallback"

        attempts: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        last_error = ""
        correction_sources: list[str] = []
        correction_plans: list[dict[str, Any]] = []
        candidate_evaluations: list[dict[str, Any]] = []
        connector = self.postgres if datasource == "dwh" else self.clickhouse
        if connector is None:
            return _error_result(
                question,
                datasource,
                "The selected datasource is not configured.",
                metadata_source,
                planning_tables,
                started,
            )

        successful_candidates: list[dict[str, Any]] = []
        execution_succeeded = False
        last_failed_sql = sql
        for candidate_index, candidate_sql in enumerate(candidate_sqls):
            candidate = candidate_sql
            try:
                candidate = _prepare_sql_candidate(
                    candidate,
                    limit=execution_limit,
                    datasource=datasource,
                    tables=planning_tables,
                    principal=principal,
                )
                emit(
                    {
                        "type": "stage",
                        "stage": "guardrail",
                        "message": (
                            "SQL passed read-only, known-table, row-limit, "
                            "and row-security checks"
                        ),
                    }
                )
                emit(
                    {
                        "type": "sql",
                        "sql": candidate,
                        "intent": intent,
                        "datasource": datasource,
                    }
                )
                emit(
                    {
                        "type": "stage",
                        "stage": "execute",
                        "message": (
                            f"Evaluating SQL candidate {candidate_index + 1}/"
                            f"{len(candidate_sqls)}"
                        ),
                    }
                )
                candidate_rows = await connector.query(candidate)
                emit(
                    {
                        "type": "stage",
                        "stage": "execute",
                        "message": (
                            f"Candidate {candidate_index + 1} returned "
                            f"{len(candidate_rows)} row(s)"
                        ),
                    }
                )
                candidate_score, score_reasons = _score_sql_candidate(
                    effective_question,
                    candidate,
                    candidate_rows,
                    candidate_index,
                    intent=intent,
                    request_plan=request_plan,
                    limit=execution_limit,
                )
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "phase": "candidate",
                        "sql": candidate,
                        "status": "success",
                        "rowCount": len(candidate_rows),
                        "score": candidate_score,
                    }
                )
                evaluation = {
                    "sql": candidate,
                    "status": "success",
                    "rowCount": len(candidate_rows),
                    "score": candidate_score,
                    "reasons": score_reasons,
                }
                candidate_evaluations.append(evaluation)
                successful_candidates.append(
                    {**evaluation, "rows": candidate_rows}
                )
            except Exception as exc:
                last_error = str(exc)
                last_failed_sql = candidate
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "phase": "candidate",
                        "sql": candidate,
                        "status": "error",
                        "error": last_error,
                    }
                )
                candidate_evaluations.append(
                    {
                        "sql": candidate,
                        "status": "error",
                        "error": last_error,
                    }
                )
                emit(
                    {
                        "type": "stage",
                        "stage": "guardrail",
                        "message": (
                            f"Candidate {candidate_index + 1} was rejected: "
                            f"{str(exc)[:180]}"
                        ),
                        "status": "error",
                    }
                )

        if successful_candidates:
            winner = max(
                successful_candidates,
                key=lambda item: item["score"],
            )
            sql = str(winner["sql"])
            rows = list(winner["rows"])
            execution_succeeded = True
        else:
            sql = last_failed_sql
            for correction_number in range(self.settings.agent_retry_count):
                emit(
                    {
                        "type": "stage",
                        "stage": "correct",
                        "message": "Correcting SQL from database feedback",
                    }
                )
                correction_tables = _narrow_tables_for_sql(
                    last_failed_sql,
                    planning_tables,
                )
                correction = await self.llm.correct_sql(
                    question=effective_question,
                    failed_sql=last_failed_sql,
                    database_error=last_error,
                    tables=correction_tables,
                    datasource=datasource,
                    limit=limit,
                    request_plan=request_plan,
                )
                correction_plans.append(correction)
                if (
                    correction.get("status") == "success"
                    and _plan_matches_intent(
                        effective_question,
                        str(correction.get("sql") or ""),
                        intent=intent,
                        request_plan=request_plan,
                    )
                ):
                    sql = str(correction["sql"])
                    correction_sources.append("llm")
                else:
                    sql = _generate_sql(
                        effective_question,
                        selected,
                        execution_limit,
                        datasource,
                        request_plan=request_plan,
                    )
                    correction_sources.append("deterministic_semantic_fallback")
                candidate = sql
                try:
                    candidate = _prepare_sql_candidate(
                        candidate,
                        limit=execution_limit,
                        datasource=datasource,
                        tables=planning_tables,
                        principal=principal,
                    )
                    emit(
                        {
                            "type": "sql",
                            "sql": candidate,
                            "intent": intent,
                            "datasource": datasource,
                        }
                    )
                    rows = await connector.query(candidate)
                    attempts.append(
                        {
                            "attempt": len(attempts) + 1,
                            "phase": "correction",
                            "correction": correction_number + 1,
                            "sql": candidate,
                            "status": "success",
                        }
                    )
                    sql = candidate
                    execution_succeeded = True
                    break
                except Exception as exc:
                    last_error = str(exc)
                    last_failed_sql = candidate
                    attempts.append(
                        {
                            "attempt": len(attempts) + 1,
                            "phase": "correction",
                            "correction": correction_number + 1,
                            "sql": candidate,
                            "status": "error",
                            "error": last_error,
                        }
                    )
                    emit(
                        {
                            "type": "stage",
                            "stage": "guardrail",
                            "message": (
                                f"Correction {correction_number + 1} failed: "
                                f"{str(exc)[:180]}"
                            ),
                            "status": "error",
                        }
                    )

        fallback_trace: list[dict[str, Any]] = []
        if execution_succeeded and rows and not _rows_match_requested_metrics(
            rows, effective_question, sql
        ):
            emit(
                {
                    "type": "stage",
                    "stage": "fallback",
                    "message": (
                        "The result did not contain the requested metric; "
                        "searching a more detailed data layer"
                    ),
                }
            )
            rows = []
        if execution_succeeded and not rows and _should_expand_empty_result(
            effective_question
        ):
            expanded = await _expand_empty_llm_usage_result(
                question=effective_question,
                limit=execution_limit,
                principal=principal,
                clickhouse=self.clickhouse,
                postgres=self.postgres,
                planning_tables=planning_tables,
                event_sink=emit,
            )
            fallback_trace = expanded["trace"]
            for fallback_attempt in expanded["attempts"]:
                attempts.append(
                    {**fallback_attempt, "attempt": len(attempts) + 1}
                )
            if expanded["found"]:
                sql = expanded["sql"]
                rows = expanded["rows"]
                selected = expanded["table"]
                planning_tables = _append_unique_table(
                    planning_tables, expanded["table"]
                )

        elapsed = round((perf_counter() - started) * 1000)
        selected_table_names = _referenced_table_names(sql, planning_tables)
        selected_table_names = selected_table_names or [selected["tableName"]]
        searched_table_names = list(selected_table_names)
        for fallback_step in fallback_trace:
            fallback_table = str(fallback_step.get("table") or "")
            if fallback_table and fallback_table not in searched_table_names:
                searched_table_names.append(fallback_table)
        if not rows:
            selected_table_names = searched_table_names
        selected_table_details = [
            table
            for table in planning_tables
            if table.get("tableName") in selected_table_names
        ]
        if not execution_succeeded:
            return {
                "agent": "metadata_sql_agent",
                "question": question,
                "target": datasource,
                "status": "error",
                "answer": "The query could not be completed after the correction loop.",
                "error": last_error,
                "sql": sql,
                "rows": [],
                "rowCount": 0,
                "attempts": attempts,
                "selectedTables": selected_table_names,
                "executionTimeMs": elapsed,
                "metadataContext": {
                    "source": metadata_source,
                    "tables": [_compact_table(selected)],
                },
                "sqlGeneration": {
                    "source": generation_source,
                    "correctionSources": correction_sources,
                    "requestedCandidateCount": requested_candidate_count,
                    "candidateEvaluations": candidate_evaluations,
                    "fallbackTrace": fallback_trace,
                    "planner": plan,
                },
                "tableSelection": table_selection,
            }

        emit(
            {
                "type": "stage",
                "stage": "chart",
                "message": "Inferring a visualization from the result shape",
            }
        )
        requested_chart_type = _requested_chart_type(effective_question)
        planned_chart_spec = (
            plan.get("visualization")
            if plan.get("status") == "success"
            else None
        )
        chart_spec = infer_chart_spec(
            rows,
            intent,
            requested_chart_type,
            planned_chart_spec,
            _preferred_chart_metrics(effective_question, rows),
            requested_dimensions=requested_dimensions,
        )
        emit(
            {
                "type": "stage",
                "stage": "chart",
                "message": (
                    f"Selected {chart_spec.get('type', 'table')} chart: "
                    f"{chart_spec.get('reason', 'compatible result shape')}"
                ),
            }
        )
        fallback_answer = _answer_text(rows, ", ".join(selected_table_names))
        if include_synthesis:
            emit(
                {
                    "type": "stage",
                    "stage": "synthesize",
                    "message": "Synthesizing a grounded answer",
                }
            )
            try:
                llm_synthesis = await asyncio.wait_for(
                    self.llm.synthesize(
                        question=question,
                        sql=sql,
                        rows=rows,
                        table_names=selected_table_names,
                        tables=selected_table_details,
                        conversation=conversation,
                        memory=memory,
                        chart_spec=chart_spec,
                        delta_sink=(
                            lambda text: emit({"type": "delta", "text": text})
                            if event_sink is not None
                            else None
                        ),
                    ),
                    timeout=max(0.05, self.settings.agent_synthesis_timeout_seconds),
                )
            except TimeoutError:
                llm_synthesis = {
                    "status": "timeout",
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "answer": None,
                    "error": (
                        "LLM synthesis timed out after "
                        f"{self.settings.agent_synthesis_timeout_seconds:g}s; "
                        "using deterministic SQL result summary."
                    ),
                }
            except Exception as exc:
                llm_synthesis = {
                    "status": "failed",
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "answer": None,
                    "error": (
                        f"LLM synthesis failed: {str(exc)[:180]}; "
                        "using deterministic SQL result summary."
                    ),
                }
        else:
            llm_synthesis = {
                "status": "skipped",
                "provider": "disabled_for_request",
                "model": None,
                "answer": None,
            }
        answer = (
            str(llm_synthesis.get("answer"))
            if llm_synthesis.get("status") == "success"
            else fallback_answer
        )
        usage = _merge_usage(plan, *correction_plans, llm_synthesis)
        return {
            "agent": "metadata_sql_agent",
            "question": question,
            "target": datasource,
            "route": {
                "datasource": datasource,
                "schema": selected.get("database"),
                "reason": _route_reason(question, datasource, target),
            },
            "intent": intent,
            "status": "success",
            "answer": answer,
            "sql": sql,
            "rows": rows,
            "rowCount": len(rows),
            "chartSpec": chart_spec,
            "attempts": attempts,
            "selectedTables": selected_table_names,
            "searchedTables": searched_table_names,
            "routeNote": route_note,
            "executionTimeMs": elapsed,
            "metadataContext": {
                "source": metadata_source,
                "tables": [_compact_table(table) for table in planning_tables],
            },
            "conversationLength": len(conversation or []),
            "memory": memory or {},
            "sqlGeneration": {
                "source": generation_source,
                "correctionSources": correction_sources,
                "requestedCandidateCount": requested_candidate_count,
                "requestedDimensions": requested_dimensions,
                "coverageDimensions": coverage_dimensions,
                "requestedLimit": limit,
                "effectiveLimit": execution_limit,
                "candidateEvaluations": candidate_evaluations,
                "fallbackTrace": fallback_trace,
                "selectionStrategy": (
                    "execution_guided"
                    if len(candidate_evaluations) > 1
                    else "single_candidate"
                ),
                "planner": plan,
            },
            "tableSelection": table_selection,
            "orchestration": request_plan,
            "llmSynthesis": llm_synthesis,
            "llmUsage": usage,
        }


def _plan_sql_candidates(plan: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        sql = str(value or "").strip()
        normalized = " ".join(sql.lower().split())
        if sql and normalized not in seen:
            seen.add(normalized)
            candidates.append(sql)

    add(plan.get("sql"))
    raw_candidates = plan.get("candidates")
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates:
            add(candidate.get("sql") if isinstance(candidate, dict) else candidate)
    return candidates


def _prepare_sql_candidate(
    sql: str,
    *,
    limit: int,
    datasource: str,
    tables: list[dict[str, Any]],
    principal: Principal,
) -> str:
    candidate = validate_readonly_sql(sql, limit)
    _validate_known_tables(candidate, tables)
    candidate = apply_row_security(
        candidate,
        dialect="postgres" if datasource == "dwh" else "clickhouse",
        tables=tables,
        principal=principal,
    )
    return validate_readonly_sql(candidate, limit)


def _score_sql_candidate(
    question: str,
    sql: str,
    rows: list[dict[str, Any]],
    position: int,
    intent: str | None = None,
    request_plan: dict[str, Any] | None = None,
    limit: int | None = None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if _plan_matches_intent(question, sql, intent=intent, request_plan=request_plan):
        score += 60
        reasons.append("matches requested semantic shape")
    if rows:
        score += 25
        reasons.append("returned rows")
    else:
        score -= 5
        reasons.append("empty result")

    keys = {str(key).lower() for row in rows[:5] for key in row}
    if intent is None:
        intent = _intent(question)
    if intent == "time_series" and any(
        "date" in key or "time" in key for key in keys
    ):
        score += 10
        reasons.append("time dimension present")
    if intent == "aggregation" and re.search(r"\bgroup by\b", sql, re.IGNORECASE):
        score += 8
        reasons.append("aggregation grain present")
    if any(
        isinstance(value, (int, float))
        for row in rows[:5]
        for value in row.values()
    ):
        score += 4
        reasons.append("numeric measure present")
    requested_dimensions = _request_dimensions(request_plan)
    if requested_dimensions:
        normalized_sql = re.sub(r"\s+", " ", sql.lower())
        grouping = _sql_grouping(normalized_sql)
        matched_dimensions = [
            dimension
            for dimension in requested_dimensions
            if _sql_has_dimension(normalized_sql, grouping, dimension)
        ]
        missing_dimensions = [
            dimension
            for dimension in requested_dimensions
            if dimension not in matched_dimensions
        ]
        if matched_dimensions:
            score += len(matched_dimensions) * 8
            reasons.append(
                "requested dimensions present: " + ", ".join(matched_dimensions)
            )
        if missing_dimensions:
            score -= len(missing_dimensions) * 25
            reasons.append(
                "missing requested dimensions: " + ", ".join(missing_dimensions)
            )
    score -= position * 0.25
    return round(score, 3), reasons


async def _expand_empty_llm_usage_result(
    *,
    question: str,
    limit: int,
    principal: Principal,
    clickhouse: ClickHouseConnector,
    postgres: PostgresConnector | None,
    planning_tables: list[dict[str, Any]],
    event_sink: EventSink | None = None,
) -> dict[str, Any]:
    emit = event_sink or (lambda _: None)
    trace: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    emit(
        {
            "type": "stage",
            "stage": "fallback",
            "message": "Gold returned no rows; searching the Silver detail layer",
        }
    )
    silver_table = next(
        (
            table
            for table in planning_tables
            if str(table.get("tableName") or "")
            == "silver.silver_llm_generations"
        ),
        None,
    )
    if silver_table is None:
        silver_tables = await clickhouse.describe_tables(("silver",))
        silver_table = next(
            (
                table
                for table in silver_tables
                if str(table.get("tableName") or "")
                == "silver.silver_llm_generations"
            ),
            None,
        )
        if silver_table:
            planning_tables = _append_unique_table(planning_tables, silver_table)
    if silver_table:
        silver_sql = _llm_usage_sql(question, silver_table, limit)
        try:
            candidate = _prepare_sql_candidate(
                silver_sql,
                limit=limit,
                datasource="clickhouse",
                tables=planning_tables,
                principal=principal,
            )
            silver_rows = await clickhouse.query(candidate)
            emit(
                {
                    "type": "stage",
                    "stage": "fallback",
                    "message": (
                        "Silver search returned "
                        f"{len(silver_rows)} row(s) from {silver_table['tableName']}"
                    ),
                }
            )
            attempts.append(
                {
                    "attempt": 0,
                    "phase": "empty_result_fallback",
                    "layer": "silver",
                    "sql": candidate,
                    "status": "success",
                    "rowCount": len(silver_rows),
                }
            )
            trace.append(
                {
                    "layer": "silver",
                    "table": silver_table["tableName"],
                    "rowCount": len(silver_rows),
                }
            )
            if silver_rows:
                return {
                    "found": True,
                    "sql": candidate,
                    "rows": silver_rows,
                    "table": silver_table,
                    "trace": trace,
                    "attempts": attempts,
                }
        except Exception as exc:
            trace.append(
                {
                    "layer": "silver",
                    "table": silver_table["tableName"],
                    "error": str(exc),
                }
            )

    return {
        "found": False,
        "sql": "",
        "rows": [],
        "table": {},
        "trace": trace,
        "attempts": attempts,
    }


def _append_unique_table(
    tables: list[dict[str, Any]], table: dict[str, Any]
) -> list[dict[str, Any]]:
    name = str(table.get("tableName") or "")
    if any(str(item.get("tableName") or "") == name for item in tables):
        return tables
    return [*tables, table]


def _narrow_tables_for_sql(
    sql: str, tables: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    referenced = set(_referenced_table_names(sql, tables))
    if not referenced:
        return tables[:3]
    return [
        table
        for table in tables
        if str(table.get("tableName") or "") in referenced
    ]


def _table_selection_confidence(
    ranked_tables: list[dict[str, Any]],
) -> float:
    if not ranked_tables:
        return 0.0
    top = float(ranked_tables[0].get("score") or 0)
    second = (
        float(ranked_tables[1].get("score") or 0)
        if len(ranked_tables) > 1
        else 0.0
    )
    margin_confidence = max(0.0, top - second) / max(abs(top), 1.0)
    retrieval_confidence = float(
        ranked_tables[0]["table"].get("retrievalConfidence") or 0
    )
    return round(
        min(1.0, max(retrieval_confidence, 0.45 + margin_confidence * 0.55)),
        4,
    )


def _sql_candidate_count(
    question: str,
    ranked_tables: list[dict[str, Any]],
    configured_count: int,
) -> int:
    maximum = max(1, min(int(configured_count), 3))
    if maximum == 1:
        return 1
    text = _normalize(question)
    capabilities = _question_capabilities(question)
    complex_intent = len(capabilities) >= 2 or any(
        term in text
        for term in (
            "compare",
            "correlation",
            "join",
            "other services",
            "ratio",
            "versus",
        )
    )
    low_confidence = _table_selection_confidence(ranked_tables) < 0.62
    return maximum if complex_intent or low_confidence else 1


def _route_datasource(question: str, target: str) -> str:
    requested = target.lower().strip()
    if requested in {"dwh", "postgres", "postgresql"}:
        return "dwh"
    if requested == "clickhouse":
        return "clickhouse"
    text = _normalize(question)
    operational = (
        "query audit",
        "agent audit",
        "dbt run",
        "dbt test result",
        "ops table",
        "operational table",
        "dwh table",
        "dwh database",
        "postgres",
        "postgresql",
    )
    return "dwh" if any(term in text for term in operational) else "clickhouse"


def _tables_for_datasource(
    tables: list[dict[str, Any]], datasource: str
) -> list[dict[str, Any]]:
    if datasource == "dwh":
        return [
            table
            for table in tables
            if str(table.get("database") or table.get("schema") or "").lower()
            == "ops"
        ]
    return [
        table
        for table in tables
        if str(table.get("database") or table.get("schema") or "").lower()
        in {"gold", "silver", "vsf_mart", "vsf_stg"}
    ]


def _select_table(
    question: str,
    tables: list[dict[str, Any]],
    datasource: str,
) -> dict[str, Any] | None:
    ranked = _rank_tables(question, tables, datasource)
    return ranked[0]["table"] if ranked else None


def _prioritize_table(
    selected: dict[str, Any], tables: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [selected, *[table for table in tables if table is not selected]]


def _rank_tables(
    question: str,
    tables: list[dict[str, Any]],
    datasource: str,
) -> list[dict[str, Any]]:
    text = _normalize(question)
    capabilities = _question_capabilities(question)
    ranked: list[dict[str, Any]] = []
    for position, table in enumerate(tables):
        name = str(table.get("tableName") or "").lower()
        description = _normalize(str(table.get("description") or ""))
        columns = {
            str(column.get("name") or "").lower()
            for column in table.get("columns") or []
        }
        score = max(0, 20 - position)
        reasons: list[str] = []
        retrieval_score = float(table.get("retrievalScore") or 0)
        if retrieval_score:
            score += min(30, retrieval_score * 2)
            reasons.extend(
                str(reason)
                for reason in table.get("retrievalReasons") or []
            )
        for capability in capabilities:
            matched = sorted(_CAPABILITY_COLUMNS[capability] & columns)
            if matched:
                score += 18 if capability in {"latency", "error"} else 12
                reasons.append(f"{capability}: {', '.join(matched[:3])}")
            else:
                score -= 12 if capability in {"latency", "error"} else 5
        lexical_terms = {
            term
            for term in text.split()
            if len(term) > 2 and term not in {"compare", "other", "show"}
        }
        lexical_hits = sum(
            term in f"{name} {description} {' '.join(columns)}"
            for term in lexical_terms
        )
        score += lexical_hits * 2
        if lexical_hits:
            reasons.append(f"{lexical_hits} metadata keyword match(es)")
        schema = str(table.get("database") or "")
        if datasource == "clickhouse":
            if _asks_latency_root_cause(text):
                has_latency = bool(_CAPABILITY_COLUMNS["latency"] & columns)
                dimensions = {
                    "service_name",
                    "source_app",
                    "source_app_id",
                } & columns
                if has_latency and dimensions:
                    score += 70 if schema in {"gold", "vsf_mart"} else 50
                    reasons.append(
                        "latency root-cause analysis needs a service/source dimension"
                    )
                if "llm_usage" in name and not dimensions:
                    score -= 70
                    reasons.append(
                        "LLM usage lacks a service/source root-cause dimension"
                    )
            elif _asks_error_only_latency(text):
                if {
                    "event_time",
                    "service_name",
                    "duration_ms",
                    "span_status",
                } <= columns:
                    score += 60
                    reasons.append("error-span detail supports conditional latency")
                if schema in {"gold", "vsf_mart"}:
                    score -= 10
            elif schema in {"gold", "vsf_mart"}:
                score += 10
                reasons.append("curated analytics aggregate")
            score += _semantic_domain_column_score(text, columns, reasons)
        if datasource == "dwh":
            fragment_weights = (
                (("audit", "request", "query"), "agent_query_audit"),
                (("dbt", "quality", "test result"), "dbt_run"),
            )
            for terms, fragment in fragment_weights:
                if fragment in name and any(term in text for term in terms):
                    score += 50
                    reasons.append(f"operational intent matches {fragment}")
        ranked.append(
            {
                "table": table,
                "score": score,
                "reasons": reasons or ["metadata relevance order"],
            }
        )
    ranked.sort(
        key=lambda item: (
            item["score"],
            str(item["table"].get("tableName") or ""),
        ),
        reverse=True,
    )
    return ranked


def _semantic_domain_column_score(
    text: str,
    columns: set[str],
    reasons: list[str],
) -> float:
    score = 0.0
    domain = _semantic_domain(text)
    capabilities = set(_question_capabilities(text))
    profiles = {
        "agent": {"query_count", "user_id", "username", "estimated_cost_usd"},
        "llm": {
            "generation_count",
            "request_count",
            "total_requests",
            "model",
            "source_app",
            "source_app_id",
            "total_cost_usd",
        },
        "trace": {
            "trace_count",
            "span_count",
            "service_name",
            "source_app",
            "source_app_id",
        },
        "ingestion": {"batch_count", "loaded_batch_count", "loaded_row_count"},
    }
    if domain and domain in profiles:
        matches = sorted(profiles[domain] & columns)
        if matches:
            score += min(36.0, len(matches) * 9.0)
            reasons.append(
                f"semantic domain columns ({domain}): {', '.join(matches[:4])}"
            )
        else:
            score -= 18.0
            reasons.append(f"missing semantic domain columns for {domain}")
    elif "request_count" in _question_capabilities(text):
        score -= 8.0
        reasons.append("request domain is ambiguous without agent/LLM/trace context")
    if "cost" in capabilities and domain != "agent":
        generic_cost_matches = sorted(
            {
                "total_cost_usd",
                "cost_usd",
                "source_app",
                "source_app_id",
                "generation_count",
                "request_count",
                "total_requests",
            }
            & columns
        )
        agent_cost_matches = sorted({"estimated_cost_usd", "query_count", "user_id"} & columns)
        if generic_cost_matches:
            score += min(28.0, len(generic_cost_matches) * 7.0)
            reasons.append(
                "generic cost columns: " + ", ".join(generic_cost_matches[:4])
            )
        if agent_cost_matches and not generic_cost_matches:
            score -= 14.0
            reasons.append("agent audit cost columns need explicit agent intent")
    return score


def _semantic_domain(text: str) -> str | None:
    normalized = _normalize(text)
    if any(
        term in normalized
        for term in (
            "agent",
            "chat",
            "query audit",
            "agent audit",
            "platform",
            "backend agent",
        )
    ):
        return "agent"
    if any(
        term in normalized
        for term in ("llm", "model", "token", "generation", "provider", "completion")
    ):
        return "llm"
    if any(
        term in normalized
        for term in ("trace", "span", "service", "observability", "monitoring")
    ):
        return "trace"
    if any(term in normalized for term in ("ingestion", "batch", "loaded")):
        return "ingestion"
    return None


_CAPABILITY_COLUMNS = {
    "time": {
        "metric_date",
        "date",
        "event_time",
        "timestamp",
        "started_at",
        "created_at",
        "finished_at",
    },
    "service": {"service_name"},
    "source": {"source_app", "source_app_id", "source_owner", "source_env"},
    "model": {"model"},
    "latency": {
        "duration_ms",
        "latency_ms",
        "avg_latency_ms",
        "p95_latency_ms",
        "avg_span_duration_ms",
        "p95_span_duration_ms",
    },
    "error": {
        "span_status",
        "trace_status",
        "status",
        "error_count",
        "error_log_count",
        "error_span_count",
        "failed_batch_count",
        "failed_requests",
        "error_rate",
        "error_rate_percent",
    },
    "cost": {"cost_usd", "total_cost_usd", "estimated_cost_usd"},
    "tokens": {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "total_tokens_used",
    },
    "request_count": {
        "query_count",
        "generation_count",
        "request_count",
        "total_requests",
        "trace_count",
        "span_count",
    },
    "ingestion": {
        "batch_count",
        "loaded_batch_count",
        "loaded_row_count",
        "quarantined_batch_count",
    },
}


def _question_capabilities(question: str) -> list[str]:
    text = _normalize(question)
    capabilities: list[str] = []
    terms = (
        ("time", ("trend", "daily", "day", "week", "ngay", "tuan")),
        ("service", ("service",)),
        ("source", ("source", "app", "owner", "environment")),
        ("model", ("model",)),
        ("latency", ("latency", "duration", "p95", "response time", "slow")),
        ("error", ("error", "failed", "failure")),
        ("cost", ("cost", "chi phi")),
        ("tokens", ("token",)),
        ("request_count", ("request", "requests", "so request", "query count")),
        ("ingestion", ("ingestion", "batch", "loaded", "quarantine")),
    )
    for capability, keywords in terms:
        if any(keyword in text for keyword in keywords):
            capabilities.append(capability)
    if _asks_latency_root_cause(text):
        for capability in ("service", "source"):
            if capability not in capabilities:
                capabilities.append(capability)
    return capabilities


def _sql_grouping_for_dimensions(
    request_plan: dict[str, Any] | None,
    columns: set[str],
    *,
    default_dimensions: list[str],
) -> list[dict[str, str]]:
    dimensions = _request_dimensions(request_plan) or default_dimensions
    groups: list[dict[str, str]] = []
    seen_aliases: set[str] = set()

    def add(dimension: str, select: str, group: str, alias: str) -> None:
        if alias in seen_aliases:
            return
        seen_aliases.add(alias)
        groups.append(
            {
                "dimension": dimension,
                "select": select,
                "group": group,
                "alias": alias,
            }
        )

    for dimension in dimensions:
        if dimension == "time":
            if "metric_date" in columns:
                add(dimension, "metric_date", "metric_date", "metric_date")
            elif "date" in columns:
                add(dimension, "date AS metric_date", "date", "metric_date")
            elif "event_time" in columns:
                quote = chr(39)
                add(
                    dimension,
                    f"toDate(event_time, {quote}UTC{quote}) AS metric_date",
                    "metric_date",
                    "metric_date",
                )
            elif "timestamp" in columns:
                add(dimension, "toDate(timestamp) AS metric_date", "metric_date", "metric_date")
            elif "started_at" in columns:
                add(dimension, "toDate(started_at) AS metric_date", "metric_date", "metric_date")
            elif "created_at" in columns:
                add(dimension, "toDate(created_at) AS metric_date", "metric_date", "metric_date")
        elif dimension == "source_app":
            if "source_app" in columns:
                add(dimension, "source_app", "source_app", "source_app")
            elif "source_app_id" in columns:
                add(dimension, "source_app_id AS source_app", "source_app_id", "source_app")
        elif dimension == "service_name" and "service_name" in columns:
            add(dimension, "service_name", "service_name", "service_name")
        elif dimension == "model" and "model" in columns:
            add(dimension, "model", "model", "model")
        elif dimension == "provider" and "provider" in columns:
            add(dimension, "provider", "provider", "provider")
        elif dimension == "environment" and "environment" in columns:
            add(dimension, "environment", "environment", "environment")
        elif dimension == "status" and "status" in columns:
            add(dimension, "status", "status", "status")
        elif dimension == "user":
            if "user_id" in columns:
                add(dimension, "user_id", "user_id", "user_id")
            elif "username" in columns:
                add(dimension, "username", "username", "username")
    return groups


def _time_filter_column(columns: set[str]) -> str | None:
    for column in ("metric_date", "date", "event_time", "timestamp", "started_at", "created_at"):
        if column in columns:
            return column
    return None


def _time_window_condition(column: str, days: int) -> str:
    if column in {"metric_date", "date"}:
        return f"{column} >= today() - {days}"
    return f"{column} >= now() - INTERVAL {days} DAY"


def _aggregate_grouped_sql(
    *,
    table_name: str,
    groups: list[dict[str, str]],
    aggregate: str,
    where: str,
    limit: int,
    metric_alias: str,
) -> str:
    select_list = ", ".join(group["select"] for group in groups)
    group_by = ", ".join(group["group"] for group in groups)
    aliases = [group["alias"] for group in groups]
    if any(group["dimension"] == "time" for group in groups):
        order_by = ", ".join(aliases)
    else:
        order_by = ", ".join([f"{metric_alias} DESC", *aliases])
    return (
        f"SELECT {select_list}, {aggregate} FROM {table_name} "
        f"WHERE {where} GROUP BY {group_by} ORDER BY {order_by} "
        f"LIMIT {min(limit, 1000)}"
    )


def _generate_sql(
    question: str,
    table: dict[str, Any],
    limit: int,
    datasource: str,
    request_plan: dict[str, Any] | None = None,
) -> str:
    if datasource == "dwh":
        return _generate_postgres_sql(question, table, limit)
    name = str(table["tableName"])
    columns = {str(column.get("name")) for column in table.get("columns") or []}
    text = _normalize(question)
    days = _window_days(text)

    if "date" in columns and {
        "request_count",
        "total_requests",
        "total_tokens",
        "total_tokens_used",
        "total_cost_usd",
        "error_count",
        "failed_requests",
    } & columns:
        return _server_mart_sql(question, table, limit, request_plan)

    if "cost" in _question_capabilities(question) and (
        {"metric_date", "model", "total_cost_usd"} <= columns
        or {"date", "model", "total_cost_usd"} <= columns
        or {"event_time", "model", "cost_usd"} <= columns
    ):
        return _llm_cost_sql(question, table, limit, request_plan)

    if "request_count" in _question_capabilities(question):
        request_sql = _request_count_sql(question, table, limit, request_plan)
        if request_sql:
            return request_sql

    if {
        "event_time",
        "service_name",
        "duration_ms",
        "span_status",
    } <= columns:
        if _asks_latency_root_cause(text):
            dimensions = ["service_name"]
            if "source_app" in columns:
                dimensions.append("source_app")
            grain = ", ".join(dimensions)
            return (
                f"SELECT {grain}, count() AS span_count, "
                "round(avg(duration_ms), 2) AS avg_latency_ms, "
                "round(quantile(0.95)(duration_ms), 2) AS p95_latency_ms "
                f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY "
                f"AND duration_ms IS NOT NULL GROUP BY {grain} "
                "ORDER BY p95_latency_ms DESC, span_count DESC"
            )
        return (
            "SELECT service_name, count() AS span_count, "
            "countIf(span_status = 'error') AS error_span_count, "
            "round(avgIf(duration_ms, span_status = 'error'), 2) "
            "AS error_avg_latency_ms, "
            "round(quantileIf(0.95)(duration_ms, span_status = 'error'), 2) "
            "AS error_p95_latency_ms, "
            "round(avg(duration_ms), 2) AS overall_avg_latency_ms, "
            "round(quantile(0.95)(duration_ms), 2) AS overall_p95_latency_ms "
            f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY "
            "GROUP BY service_name "
            "ORDER BY error_p95_latency_ms DESC, error_span_count DESC"
        )

    if {"metric_date", "model", "total_tokens", "total_cost_usd"} <= columns:
        if any(term in text for term in ("top model", "model nao", "by model", "theo model")):
            return (
                "SELECT model, sum(total_tokens) AS total_tokens, "
                "round(sum(total_cost_usd), 6) AS total_cost_usd, "
                f"sum(error_count) AS error_count FROM {name} "
                f"WHERE metric_date >= today() - {days} GROUP BY model "
                "ORDER BY total_cost_usd DESC"
            )
        if "error" in text:
            return (
                "SELECT metric_date, sum(error_count) AS error_count, "
                f"sum(success_count) AS success_count FROM {name} "
                f"WHERE metric_date >= today() - {days} "
                "GROUP BY metric_date ORDER BY metric_date"
            )
        if "latency" in text:
            return (
                "SELECT metric_date, round(avg(avg_latency_ms), 2) AS avg_latency_ms, "
                f"round(avg(p95_latency_ms), 2) AS p95_latency_ms FROM {name} "
                f"WHERE metric_date >= today() - {days} "
                "GROUP BY metric_date ORDER BY metric_date"
            )
        return (
            "SELECT metric_date, sum(generation_count) AS generation_count, "
            "sum(total_tokens) AS total_tokens, "
            f"round(sum(total_cost_usd), 6) AS total_cost_usd FROM {name} "
            f"WHERE metric_date >= today() - {days} "
            "GROUP BY metric_date ORDER BY metric_date"
        )

    if {"metric_date", "trace_count", "error_count", "success_count"} <= columns:
        if _asks_latency_root_cause(text) and "source_app" in columns:
            return (
                "SELECT source_app, sum(trace_count) AS trace_count, "
                "round(avg(avg_latency_ms), 2) AS avg_latency_ms, "
                f"round(avg(p95_latency_ms), 2) AS p95_latency_ms FROM {name} "
                f"WHERE metric_date >= today() - {days} GROUP BY source_app "
                "ORDER BY p95_latency_ms DESC, trace_count DESC"
            )
        if any(term in text for term in ("source", "app", "owner", "environment")):
            return (
                "SELECT source_app, trace_count, error_count, "
                "round(error_count / greatest(trace_count, 1) * 100, 2) "
                "AS error_rate_pct FROM ("
                "SELECT source_app, sum(trace_count) AS trace_count, "
                f"sum(error_count) AS error_count FROM {name} "
                f"WHERE metric_date >= today() - {days} GROUP BY source_app"
                ") ORDER BY trace_count DESC"
            )
        return (
            "SELECT metric_date, sum(trace_count) AS trace_count, "
            "sum(success_count) AS success_count, sum(error_count) AS error_count, "
            f"round(avg(p95_latency_ms), 2) AS p95_latency_ms FROM {name} "
            f"WHERE metric_date >= today() - {days} "
            "GROUP BY metric_date ORDER BY metric_date"
        )

    if {"metric_date", "service_name", "log_count", "span_count"} <= columns:
        if any(term in text for term in ("trend", "daily", "ngay", "over time")):
            return (
                "SELECT metric_date, sum(log_count) AS log_count, "
                "sum(error_log_count) AS error_log_count, sum(span_count) AS span_count, "
                f"sum(error_span_count) AS error_span_count FROM {name} "
                f"WHERE metric_date >= today() - {days} "
                "GROUP BY metric_date ORDER BY metric_date"
            )
        order_by = (
            "p95_span_duration_ms DESC, span_count DESC"
            if _asks_latency_root_cause(text)
            else "error_span_count DESC, error_log_count DESC"
        )
        dimensions = ["service_name"]
        if _asks_latency_root_cause(text) and "source_app" in columns:
            dimensions.append("source_app")
        grain = ", ".join(dimensions)
        return (
            f"SELECT {grain}, sum(log_count) AS log_count, "
            "sum(error_log_count) AS error_log_count, sum(span_count) AS span_count, "
            "sum(error_span_count) AS error_span_count, "
            "round(avg(avg_span_duration_ms), 2) AS avg_span_duration_ms, "
            f"round(avg(p95_span_duration_ms), 2) AS p95_span_duration_ms FROM {name} "
            f"WHERE metric_date >= today() - {days} GROUP BY {grain} "
            f"ORDER BY {order_by}"
        )

    if {"metric_date", "batch_count", "loaded_batch_count"} <= columns:
        group_column = "source_app" if "source_app" in columns else "metric_date"
        return (
            f"SELECT {group_column}, sum(batch_count) AS batch_count, "
            "sum(loaded_batch_count) AS loaded_batch_count, "
            "sum(failed_batch_count) AS failed_batch_count, "
            "sum(quarantined_batch_count) AS quarantined_batch_count, "
            f"sum(loaded_row_count) AS loaded_row_count FROM {name} "
            f"WHERE metric_date >= today() - {days} GROUP BY {group_column} "
            "ORDER BY loaded_row_count DESC"
        )
    return _correction_sql(table, limit)


def _request_count_sql(
    question: str,
    table: dict[str, Any],
    limit: int,
    request_plan: dict[str, Any] | None = None,
) -> str | None:
    name = str(table["tableName"])
    columns = {str(column.get("name")) for column in table.get("columns") or []}
    days = _window_days(_normalize(question))
    for request_column in ("request_count", "total_requests"):
        if {"date", request_column} <= columns:
            return (
                f"SELECT date AS metric_date, sum({request_column}) AS request_count "
                f"FROM {name} WHERE date >= today() - {days} "
                "GROUP BY metric_date ORDER BY metric_date "
                f"LIMIT {min(limit, 1000)}"
            )
    if {"metric_date", "query_count"} <= columns:
        return (
            "SELECT metric_date, sum(query_count) AS request_count "
            f"FROM {name} WHERE metric_date >= today() - {days} "
            "GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    if {"metric_date", "generation_count"} <= columns:
        return (
            "SELECT metric_date, sum(generation_count) AS request_count "
            f"FROM {name} WHERE metric_date >= today() - {days} "
            "GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    if {"metric_date", "trace_count"} <= columns:
        return (
            "SELECT metric_date, sum(trace_count) AS request_count "
            f"FROM {name} WHERE metric_date >= today() - {days} "
            "GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    if {"metric_date", "span_count"} <= columns:
        return (
            "SELECT metric_date, sum(span_count) AS request_count "
            f"FROM {name} WHERE metric_date >= today() - {days} "
            "GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    if {"event_time", "generation_status"} <= columns:
        return (
            "SELECT toDate(event_time, 'UTC') AS metric_date, count() AS request_count "
            f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY "
            "GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    if {"event_time", "span_status"} <= columns:
        return (
            "SELECT toDate(event_time, 'UTC') AS metric_date, count() AS request_count "
            f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY "
            "GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    return None


def _generate_postgres_sql(
    question: str, table: dict[str, Any], limit: int
) -> str:
    name = str(table["tableName"])
    columns = {str(column.get("name")) for column in table.get("columns") or []}
    projection_preferences = (
        "run_id",
        "query_id",
        "batch_id",
        "status",
        "command",
        "source_app",
        "target_table",
        "row_count",
        "loaded_row_count",
        "execution_time_ms",
        "started_at",
        "finished_at",
        "created_at",
        "error_message",
    )
    projection = [column for column in projection_preferences if column in columns]
    if not projection:
        projection = [str(column.get("name")) for column in table.get("columns") or []][:12]
    order_column = next(
        (column for column in ("created_at", "started_at", "ingested_at", "run_id") if column in columns),
        None,
    )
    sql = f"SELECT {', '.join(projection)} FROM {name}"
    text = _normalize(question)
    if "failed" in text and "status" in columns:
        sql += " WHERE lower(status) IN ('failed', 'error')"
    if order_column:
        sql += f" ORDER BY {order_column} DESC"
    return f"{sql} LIMIT {min(limit, 100)}"


def _llm_cost_sql(
    question: str,
    table: dict[str, Any],
    limit: int,
    request_plan: dict[str, Any] | None = None,
) -> str:
    name = str(table["tableName"])
    columns = {str(column.get("name")) for column in table.get("columns") or []}
    days = _window_days(_normalize(question))
    requested_model = _requested_model(question)
    model_filter = ""
    if requested_model:
        escaped_model = _sql_literal(requested_model)
        model_filter = (
            " AND positionCaseInsensitiveUTF8(model, "
            f"'{escaped_model}') > 0"
        )

    cost_column = "total_cost_usd" if "total_cost_usd" in columns else "cost_usd" if "cost_usd" in columns else ""
    default_dimensions = (
        ["model"]
        if _asks_model_comparison(question) and not requested_model
        else ["time", "model"]
    )
    groups = _sql_grouping_for_dimensions(
        request_plan,
        columns,
        default_dimensions=default_dimensions,
    )
    time_column = _time_filter_column(columns)
    if cost_column and groups and time_column:
        where = _time_window_condition(time_column, days) + model_filter
        return _aggregate_grouped_sql(
            table_name=name,
            groups=groups,
            aggregate=f"round(sum({cost_column}), 6) AS total_cost_usd",
            where=where,
            limit=limit,
            metric_alias="total_cost_usd",
        )

    if {"date", "model", "total_cost_usd"} <= columns:
        if _asks_model_comparison(question) and not requested_model:
            return (
                "SELECT model, round(sum(total_cost_usd), 6) AS total_cost_usd "
                f"FROM {name} WHERE date >= today() - {days} "
                "GROUP BY model ORDER BY total_cost_usd DESC "
                f"LIMIT {min(limit, 1000)}"
            )
        return (
            "SELECT date AS metric_date, model, "
            "round(sum(total_cost_usd), 6) AS total_cost_usd "
            f"FROM {name} WHERE date >= today() - {days}"
            f"{model_filter} GROUP BY metric_date, model "
            f"ORDER BY metric_date, model LIMIT {min(limit, 1000)}"
        )
    if {"metric_date", "model", "total_cost_usd"} <= columns:
        if _asks_model_comparison(question) and not requested_model:
            return (
                "SELECT model, round(sum(total_cost_usd), 6) AS total_cost_usd "
                f"FROM {name} WHERE metric_date >= today() - {days} "
                "GROUP BY model ORDER BY total_cost_usd DESC "
                f"LIMIT {min(limit, 1000)}"
            )
        return (
            "SELECT metric_date, model, "
            "round(sum(total_cost_usd), 6) AS total_cost_usd "
            f"FROM {name} WHERE metric_date >= today() - {days}"
            f"{model_filter} GROUP BY metric_date, model "
            f"ORDER BY metric_date, model LIMIT {min(limit, 1000)}"
        )
    if {"event_time", "model", "cost_usd"} <= columns:
        if _asks_model_comparison(question) and not requested_model:
            return (
                "SELECT model, round(sum(cost_usd), 6) AS total_cost_usd "
                f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY "
                "GROUP BY model ORDER BY total_cost_usd DESC "
                f"LIMIT {min(limit, 1000)}"
            )
        return (
            "SELECT toDate(event_time, 'UTC') AS metric_date, model, "
            "round(sum(cost_usd), 6) AS total_cost_usd "
            f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY"
            f"{model_filter} GROUP BY metric_date, model "
            f"ORDER BY metric_date, model LIMIT {min(limit, 1000)}"
        )
    return _correction_sql(table, limit)


def _server_mart_sql(
    question: str,
    table: dict[str, Any],
    limit: int,
    request_plan: dict[str, Any] | None = None,
) -> str:
    name = str(table["tableName"])
    columns = {str(column.get("name")) for column in table.get("columns") or []}
    text = _normalize(question)
    days = _window_days(text)
    if any(term in text for term in ("by model", "theo model", "top model")) and "model" in columns:
        dimension = "model"
        projection = "model"
    elif any(term in text for term in ("source", "app", "environment")) and "source_app_id" in columns:
        dimension = "source_app_id"
        projection = "source_app_id AS source_app"
    else:
        dimension = "date"
        projection = "date AS metric_date"

    aggregates: list[str] = []
    request_column = next(
        (column for column in ("request_count", "total_requests") if column in columns),
        None,
    )
    token_column = next(
        (column for column in ("total_tokens", "total_tokens_used") if column in columns),
        None,
    )
    error_column = next(
        (column for column in ("error_count", "failed_requests") if column in columns),
        None,
    )
    if request_column:
        aggregates.append(f"sum({request_column}) AS request_count")
    if token_column:
        aggregates.append(f"sum({token_column}) AS total_tokens")
    if "total_cost_usd" in columns:
        aggregates.append("round(sum(total_cost_usd), 6) AS total_cost_usd")
    if error_column:
        aggregates.append(f"sum({error_column}) AS error_count")
    if "avg_latency_ms" in columns:
        aggregates.append("round(avg(avg_latency_ms), 2) AS avg_latency_ms")
    if "p95_latency_ms" in columns:
        aggregates.append("round(avg(p95_latency_ms), 2) AS p95_latency_ms")
    if not aggregates:
        return _correction_sql(table, limit)
    requested_groups = _sql_grouping_for_dimensions(
        request_plan,
        columns,
        default_dimensions=[],
    )
    time_column = _time_filter_column(columns)
    if requested_groups and time_column:
        metric_alias = aggregates[0].split(" AS ")[-1]
        return _aggregate_grouped_sql(
            table_name=name,
            groups=requested_groups,
            aggregate=", ".join(aggregates),
            where=_time_window_condition(time_column, days),
            limit=limit,
            metric_alias=metric_alias,
        )
    order_by = "metric_date" if dimension == "date" else aggregates[0].split(" AS ")[-1] + " DESC"
    return (
        f"SELECT {projection}, {', '.join(aggregates)} FROM {name} "
        f"WHERE date >= today() - {days} GROUP BY {dimension} "
        f"ORDER BY {order_by} LIMIT {min(limit, 1000)}"
    )


def _llm_usage_sql(
    question: str, table: dict[str, Any], limit: int
) -> str:
    capabilities = set(_question_capabilities(question))
    name = str(table["tableName"])
    columns = {str(column.get("name")) for column in table.get("columns") or []}
    days = _window_days(_normalize(question))
    requested_model = _requested_model(question)
    model_filter = ""
    if requested_model and "model" in columns:
        model_filter = (
            " AND positionCaseInsensitiveUTF8(model, "
            f"'{_sql_literal(requested_model)}') > 0"
        )
    if "latency" in capabilities:
        if {"metric_date", "avg_latency_ms", "p95_latency_ms"} <= columns:
            return (
                "SELECT metric_date, round(avg(avg_latency_ms), 2) AS avg_latency_ms, "
                "round(avg(p95_latency_ms), 2) AS p95_latency_ms "
                f"FROM {name} WHERE metric_date >= today() - {days}"
                f"{model_filter} GROUP BY metric_date ORDER BY metric_date "
                f"LIMIT {min(limit, 1000)}"
            )
        if {"event_time", "latency_ms"} <= columns:
            return (
                "SELECT toDate(event_time, 'UTC') AS metric_date, "
                "round(avg(latency_ms), 2) AS avg_latency_ms, "
                "round(quantile(0.95)(latency_ms), 2) AS p95_latency_ms "
                f"FROM {name} WHERE event_time >= now() - INTERVAL {days} DAY "
                "AND latency_ms IS NOT NULL"
                f"{model_filter} GROUP BY metric_date ORDER BY metric_date "
                f"LIMIT {min(limit, 1000)}"
            )
    if "tokens" in capabilities and {"event_time", "total_tokens"} <= columns:
        return (
            "SELECT toDate(event_time, 'UTC') AS metric_date, "
            f"sum(total_tokens) AS total_tokens FROM {name} "
            f"WHERE event_time >= now() - INTERVAL {days} DAY"
            f"{model_filter} GROUP BY metric_date ORDER BY metric_date "
            f"LIMIT {min(limit, 1000)}"
        )
    return _llm_cost_sql(question, table, limit)


def _requested_model(question: str) -> str | None:
    match = re.search(
        r"\bmodel\s*(?:=|:|named\s+|la\s+|là\s+)?"
        r"[`'\"]?([a-z0-9][a-z0-9._/:+\-]{1,127})",
        question,
        re.IGNORECASE,
    )
    if not match:
        return None
    candidate = match.group(1).rstrip("`'\".,;:?")
    if not any(character.isdigit() for character in candidate) and not any(
        separator in candidate for separator in ("-", "/", ".", ":")
    ):
        return None
    return candidate


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _should_expand_empty_result(question: str) -> bool:
    capabilities = set(_question_capabilities(question))
    return bool(capabilities & {"cost", "tokens", "latency"}) and (
        "model" in capabilities
        or "llm" in _normalize(question)
        or "latency" in capabilities
    )


def _rows_match_requested_metrics(
    rows: list[dict[str, Any]], question: str, sql: str = ""
) -> bool:
    if not rows:
        return True
    keys = {str(key).lower() for row in rows[:10] for key in row}
    normalized_sql = _normalize(sql)
    has_generic_value_alias = "y" in keys or "value" in keys
    capabilities = set(_question_capabilities(question))
    checks = {
        "latency": any("latency" in key or "duration" in key for key in keys)
        or (
            has_generic_value_alias
            and ("latency" in normalized_sql or "duration" in normalized_sql)
        ),
        "cost": any("cost" in key for key in keys)
        or (
            has_generic_value_alias
            and (
                "cost" in normalized_sql
                or "estimated_cost_usd" in normalized_sql
                or "total_cost_usd" in normalized_sql
            )
        ),
        "tokens": any("token" in key for key in keys)
        or (has_generic_value_alias and "token" in normalized_sql),
        "request_count": any(
            term in key
            for key in keys
            for term in ("request", "query_count", "generation_count", "trace_count", "span_count")
        )
        or (
            has_generic_value_alias
            and any(
                term in normalized_sql
                for term in ("query_count", "generation_count", "trace_count", "span_count")
            )
        ),
    }
    requested = [name for name in checks if name in capabilities]
    return all(checks[name] for name in requested)


def _request_dimensions(request_plan: dict[str, Any] | None) -> list[str]:
    return _request_slot_dimensions(request_plan, "dimensions")


def _request_coverage(request_plan: dict[str, Any] | None) -> list[str]:
    return _request_slot_dimensions(request_plan, "coverage")


def _request_slot_dimensions(
    request_plan: dict[str, Any] | None,
    slot_name: str,
) -> list[str]:
    if not isinstance(request_plan, dict):
        return []
    slots = request_plan.get("slots")
    if not isinstance(slots, dict):
        return []
    slot = slots.get(slot_name)
    if not isinstance(slot, dict):
        return []
    value = slot.get("value")
    raw_values = value if isinstance(value, list) else [value]
    aliases = {
        "date": "time",
        "day": "time",
        "metric_date": "time",
        "event_time": "time",
        "source": "source_app",
        "source_app_id": "source_app",
        "app": "source_app",
        "service": "service_name",
        "user_id": "user",
        "username": "user",
    }
    allowed = {
        "time",
        "model",
        "source_app",
        "service_name",
        "provider",
        "environment",
        "status",
        "user",
    }
    dimensions: list[str] = []
    for raw in raw_values:
        normalized = str(raw or "").strip().lower().replace(" ", "_")
        dimension = aliases.get(normalized, normalized)
        if dimension in allowed and dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def _preferred_chart_metrics(
    question: str, rows: list[dict[str, Any]]
) -> list[str]:
    keys = [str(key) for row in rows[:1] for key in row]
    capabilities = set(_question_capabilities(question))
    if "latency" in capabilities:
        return [
            key
            for key in keys
            if "latency" in key.lower() or "duration" in key.lower()
        ]
    if "cost" in capabilities:
        return [key for key in keys if "cost" in key.lower()]
    if "tokens" in capabilities:
        return [key for key in keys if "token" in key.lower()]
    if "request_count" in capabilities:
        return [
            key
            for key in keys
            if any(
                term in key.lower()
                for term in (
                    "request",
                    "query_count",
                    "generation_count",
                    "trace_count",
                    "span_count",
                )
            )
        ]
    if "error" in capabilities:
        return [key for key in keys if "error" in key.lower()]
    return []


def _asks_model_comparison(question: str) -> bool:
    text = _normalize(question)
    return any(
        term in text
        for term in (
            "cac model",
            "all model",
            "all models",
            "by model",
            "theo model",
            "top model",
            "models",
        )
    )


def _correction_sql(table: dict[str, Any], limit: int) -> str:
    columns = [
        str(column.get("name"))
        for column in table.get("columns") or []
        if column.get("name")
    ][:12]
    projection = ", ".join(columns) if columns else "1 AS no_columns"
    return f"SELECT {projection} FROM {table['tableName']} LIMIT {min(limit, 100)}"


def _validate_known_tables(sql: str, tables: list[dict[str, Any]]) -> None:
    allowed = {str(table.get("tableName") or "").lower() for table in tables}
    for reference in re.findall(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)", sql, re.IGNORECASE
    ):
        normalized = reference.lower()
        if "." in normalized and normalized not in allowed:
            raise ValueError(f"SQL references table outside live metadata: {reference}")


def _referenced_table_names(
    sql: str, tables: list[dict[str, Any]]
) -> list[str]:
    allowed = {
        str(table.get("tableName") or "").lower(): str(table.get("tableName") or "")
        for table in tables
    }
    names: list[str] = []
    for reference in re.findall(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)", sql, re.IGNORECASE
    ):
        canonical = allowed.get(reference.lower())
        if canonical and canonical not in names:
            names.append(canonical)
    return names


def _window_days(text: str) -> int:
    match = re.search(r"(\d+)\s*(day|days|d|ngay)", text)
    if match:
        return max(1, min(int(match.group(1)), 365))
    if re.search(r"\b(24h|today|hom nay)\b", text):
        return 1
    if re.search(r"\b(7d|week|tuan)\b", text):
        return 7
    return 30


def _intent(question: str) -> str:
    text = _normalize(question)
    if "dashboard" in text or "tong quan" in text:
        return "dashboard"
    if any(term in text for term in ("trend", "daily", "ngay", "over time")):
        return "time_series"
    if any(term in text for term in ("top", "by ", "theo ")):
        return "aggregation"
    if _asks_latency_root_cause(text):
        return "aggregation"
    if any(
        term in text
        for term in ("service", "source app", "source owner", "model cost")
    ):
        return "aggregation"
    return "lookup"


def _requested_chart_type(question: str) -> str | None:
    text = _normalize(question)
    chart_terms = (
        ("pie", ("pie chart", "bieu do tron", "donut chart")),
        ("line", ("line chart", "bieu do duong")),
        ("bar", ("bar chart", "column chart", "bieu do cot")),
        ("scatter", ("scatter chart", "scatter plot", "bieu do phan tan")),
        ("histogram", ("histogram", "bieu do tan suat")),
        ("box", ("box plot", "boxplot", "bieu do hop")),
        ("table", ("table view", "dang bang", "bang du lieu")),
    )
    for chart_type, keywords in chart_terms:
        if any(keyword in text for keyword in keywords):
            return chart_type
    return None


def _plan_matches_intent(
    question: str,
    sql: str,
    intent: str | None = None,
    request_plan: dict[str, Any] | None = None,
) -> bool:
    if intent is None:
        intent = _intent(question)
    normalized = re.sub(r"\s+", " ", sql.lower())
    group_by_match = re.search(
        r"\bgroup by\s+(.+?)(?:\border by\b|\blimit\b|$)",
        normalized,
    )
    grouping = group_by_match.group(1) if group_by_match else ""
    if intent in {"time_series", "aggregation"}:
        if not grouping:
            return False
    text = _normalize(question)
    capabilities = set(_question_capabilities(question))
    if (
        "cost" in capabilities
        and not _asks_agent_cost(text)
        and "gold.gold_agent_cost_daily" in normalized
    ):
        return False
    if "cost" in capabilities and not any(
        term in normalized
        for term in ("cost_usd", "total_cost_usd", "estimated_cost_usd")
    ):
        return False
    requested_model = _requested_model(question)
    if requested_model and (
        "model" not in normalized
        or requested_model.lower() not in normalized
    ):
        return False
    if intent == "time_series" and not any(
        term in grouping
        for term in ("metric_date", "date", "event_time", "timestamp", "start_time", "started_at", "created_at", "ingested_at")
    ):
        return False
    exact_metric_date = re.search(
        r"(?:\b\w+\.)?metric_date\s*=\s*"
        r"(?:today\s*\(\s*\)|current_date|toDate\s*\()",
        normalized,
    )
    explicit_single_window = re.search(
        r"\b(today|yesterday|hom nay|hom qua|24h|1\s*(?:day|d|ngay))\b",
        text,
    )
    if exact_metric_date and not explicit_single_window:
        return False
    if "latency" in text and not any(
        term in normalized
        for term in (
            "latency_ms",
            "duration_ms",
            "avg_span_duration_ms",
            "p95_span_duration_ms",
        )
    ):
        return False
    if _asks_latency_root_cause(text):
        if not any(
            term in normalized
            for term in (
                "latency_ms",
                "duration_ms",
                "avg_span_duration_ms",
                "p95_span_duration_ms",
            )
        ):
            return False
        if not any(
            dimension in grouping for dimension in ("service_name", "source_app")
        ):
            return False
    if _asks_error_only_latency(text) and not all(
        term in normalized
        for term in ("silver_system_traces", "span_status", "duration_ms")
    ):
        return False
    if (
        "service" in text
        and any(term in text for term in ("compare", "other", "by service"))
        and (
            "service_name" not in normalized or "service_name" not in grouping
        )
    ):
        return False
    if any(
        term in text
        for term in ("by source app", "theo source app", "by source", "theo source")
    ):
        if "source_app" not in grouping:
            return False
        if intent != "time_series" and "metric_date" in grouping:
            return False
    for dimension in _request_dimensions(request_plan):
        if not _sql_has_dimension(normalized, grouping, dimension):
            return False
    return True


def _sql_grouping(normalized_sql: str) -> str:
    group_by_match = re.search(
        r"\bgroup by\s+(.+?)(?:\border by\b|\blimit\b|$)",
        normalized_sql,
    )
    return group_by_match.group(1) if group_by_match else ""


def _sql_has_dimension(normalized_sql: str, grouping: str, dimension: str) -> bool:
    terms = {
        "time": (
            "metric_date",
            "date",
            "event_time",
            "timestamp",
            "started_at",
            "created_at",
            "todate",
        ),
        "model": ("model",),
        "source_app": ("source_app", "source_app_id"),
        "service_name": ("service_name",),
        "provider": ("provider",),
        "environment": ("environment", "source_env"),
        "status": ("status", "generation_status", "span_status"),
        "user": ("user_id", "username", "user"),
    }.get(dimension, (dimension,))
    if grouping and any(term in grouping for term in terms):
        return True
    ordinal_grouping = bool(re.search(r"\bgroup by\s+(?:\d+\s*,\s*)*\d+\b", normalized_sql))
    return ordinal_grouping and any(term in normalized_sql for term in terms)


def _asks_error_only_latency(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "error latency",
            "latency of error",
            "latency for error",
            "latency of failed",
            "failed request latency",
        )
    )


def _asks_latency_root_cause(text: str) -> bool:
    has_latency = any(
        term in text for term in ("latency", "duration", "response time", "slow")
    )
    asks_origin = any(
        term in text
        for term in (
            "why",
            "tai sao",
            "nguon goc",
            "nguyen nhan",
            "root cause",
            "where is",
            "where does",
            "from where",
            "tai dau",
            "o dau",
        )
    )
    return has_latency and asks_origin


def _asks_agent_cost(text: str) -> bool:
    return any(
        term in text
        for term in (
            "agent cost",
            "cost agent",
            "chi phi agent",
            "query cost",
            "request cost",
            "chat cost",
            "cost per query",
            "chi phi truy van",
            "chi phi request",
            "agentic bi cost",
            "platform cost",
            "backend agent cost",
        )
    )


def _route_reason(question: str, datasource: str, requested: str) -> str:
    if requested.lower() != "auto":
        return f"The caller explicitly requested {datasource}."
    if datasource == "dwh":
        return "Operational intent is served from PostgreSQL ops tables."
    return "Analytical intent is served from curated ClickHouse Gold/Silver tables."


def _answer_text(rows: list[dict[str, Any]], table_name: str) -> str:
    if not rows:
        return f"No matching data was found in {table_name}."
    return (
        f"Returned {len(rows)} row(s) from {table_name} using live catalog metadata. "
        "The table or chart below contains the detailed result."
    )


def _compact_table(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "tableName": table.get("tableName"),
        "datasource": table.get("datasource"),
        "description": table.get("description"),
        "updatedAt": table.get("updatedAt"),
        "retrievalScore": table.get("retrievalScore"),
        "retrievalConfidence": table.get("retrievalConfidence"),
        "retrievalReasons": table.get("retrievalReasons") or [],
        "columns": table.get("columns") or [],
    }


def _merge_usage(*responses: dict[str, Any]) -> dict[str, Any]:
    prompt = completion = total = 0
    cost = 0.0
    provider = model = None
    cost_source = None
    for response in responses:
        usage = response.get("usage") or {}
        response_prompt = int(usage.get("prompt_tokens") or 0)
        response_completion = int(usage.get("completion_tokens") or 0)
        response_total = int(usage.get("total_tokens") or 0)
        prompt += response_prompt
        completion += response_completion
        total += response_total
        cost += float(response.get("totalCostUsd") or 0)
        if response_total or response_prompt or response_completion:
            provider = response.get("provider") or provider
            model = response.get("model") or model
            cost_source = response.get("costSource") or cost_source
    return {
        "provider": provider,
        "model": model,
        "promptTokens": prompt,
        "completionTokens": completion,
        "totalTokens": total or prompt + completion,
        "estimatedCostUsd": round(cost, 8),
        "costSource": cost_source or "estimated",
    }


def _error_result(
    question: str,
    datasource: str,
    message: str,
    metadata_source: str,
    tables: list[dict[str, Any]],
    started: float,
) -> dict[str, Any]:
    return {
        "agent": "metadata_sql_agent",
        "question": question,
        "target": datasource,
        "status": "error",
        "answer": message,
        "rows": [],
        "rowCount": 0,
        "attempts": [],
        "executionTimeMs": round((perf_counter() - started) * 1000),
        "metadataContext": {"source": metadata_source, "tables": tables},
    }


def _normalize(value: str) -> str:
    ascii_value = "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d").replace("Đ", "D")
    return (
        ascii_value.replace("\u0111", "d").replace("\u0110", "D").lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("chi phi", "cost")
        .replace("do tre", "latency")
        .replace("cham", "slow")
        .replace("loi", "error")
    )
