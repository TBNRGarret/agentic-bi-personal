"""Insight specialist agent."""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...security import Principal
from ...services.llm import LlmService
from ..table_selector import TableSelector
from .specialist_utils import (
    AgentResponse,
    AgentTask,
    EventSink,
    _clickhouse_query,
    _clickhouse_where_clause,
    _detect_anomalies,
    _extract_entity_filters,
    _filter_warnings,
    _insight_order_clause,
    _insight_summary,
    _llm_usage_from_result,
    _median,
    _merge_anomaly_sets,
    _metadata_evidence,
    _normalize_insight_row,
    _parse_days,
    _resolve_specialist_table,
    _run_llm_synthesis,
    _slot_value,
    _sql_evidence,
    _table_column_names,
)


class InsightAgent:
    def __init__(
        self,
        clickhouse: Any,
        postgres: Any | None = None,
        settings: Settings | None = None,
        llm: LlmService | None = None,
        *,
        metadata: Any | None = None,
    ) -> None:
        self.clickhouse = clickhouse
        self.postgres = postgres
        self.settings = settings
        self.llm = llm
        self.metadata = metadata
        self.table_selector = TableSelector(settings) if settings is not None else None

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
        del target, conversation, memory, event_sink
        slots = task.context.get("slots") or {}
        days = _parse_days(_slot_value(slots, "time_range"), default=7)
        dbt_runs = []
        if self.postgres is not None:
            try:
                dbt_runs = await self.postgres.query(
                    "SELECT run_id, status, started_at, finished_at "
                    "FROM ops.dbt_runs "
                    "WHERE started_at >= now() - interval '%s days' "
                    "ORDER BY started_at DESC "
                    "LIMIT 10",
                    (days,),
                )
            except Exception:
                dbt_runs = []
        if not dbt_runs:
            try:
                dbt_runs = await self.clickhouse.query(
                    "SELECT toString(batch_id) AS run_id, status, "
                    "load_started_at AS started_at, load_finished_at AS finished_at "
                    "FROM vsf_raw.raw_ingestion_batches "
                    f"WHERE created_at >= now() - INTERVAL {days} DAY "
                    "ORDER BY created_at DESC LIMIT 10"
                )
            except Exception:
                dbt_runs = []

        required_cols = {
            "metric_date",
            "service_name",
            "source_app",
            "span_count",
            "error_span_count",
            "avg_span_duration_ms",
            "p95_span_duration_ms",
        }
        resolution = await _resolve_specialist_table(
            question,
            clickhouse=self.clickhouse,
            metadata=self.metadata,
            table_selector=self.table_selector,
            required_columns=required_cols,
        )
        if resolution.table is None or resolution.table_name is None:
            text = (
                "Insight agent could not find a live observability table "
                "with the required columns."
            )
            return AgentResponse(
                task_id=task.task_id,
                agent="insight_agent",
                status="partial",
                text=text,
                evidence=[_metadata_evidence(resolution, "observability")],
                artifacts=[
                    {
                        "type": "root_cause_analysis",
                        "result": {
                            "rows": [],
                            "selectedTables": [],
                            "sql": None,
                            "findings": [],
                            "anomalies": [],
                            "dbtRuns": dbt_runs,
                            "timeRangeDays": days,
                            "appliedFilters": {},
                            "metadataSource": resolution.metadata_source,
                            "tableSelection": resolution.table_selection,
                            "requiredColumns": resolution.required_columns,
                        },
                    }
                ],
                confidence=0.2,
                warnings=resolution.warnings or [text],
            )
        chosen_table = resolution.table_name

        filters = _extract_entity_filters(question)
        table_columns = _table_column_names(resolution.table)
        filter_warnings = _filter_warnings(
            filters,
            {"service_name", "source_app"},
            table_columns,
        )
        where_clause = _clickhouse_where_clause(
            days=days,
            filters=filters,
            allowed_columns={"service_name", "source_app"} & table_columns,
        )
        order_clause = _insight_order_clause(question, slots)
        sql = f"""
            SELECT
                service_name,
                source_app,
                span_count,
                error_span_count,
                round(error_span_count / greatest(span_count, 1) * 100, 2) AS error_rate_pct,
                avg_latency_ms,
                p95_latency_ms
            FROM (
                SELECT
                    service_name,
                    source_app,
                    sum(span_count) AS span_count,
                    sum(error_span_count) AS error_span_count,
                    round(avg(avg_span_duration_ms), 2) AS avg_latency_ms,
                    round(avg(p95_span_duration_ms), 2) AS p95_latency_ms
                FROM {chosen_table}
                WHERE {where_clause}
                GROUP BY service_name, source_app
            )
            ORDER BY {order_clause}
            LIMIT 20
        """
        rows, executed_sql = await _clickhouse_query(
            self.clickhouse,
            sql,
            limit=min(limit, 50),
            principal=principal,
        )
        if not rows:
            return AgentResponse(
                task_id=task.task_id,
                agent="insight_agent",
                status="partial",
                text="Insight agent did not find observability rows for the current window.",
                evidence=[_sql_evidence(executed_sql, 0, chosen_table)],
                artifacts=[
                    {
                        "type": "root_cause_analysis",
                        "result": {
                            "rows": [],
                            "selectedTables": [chosen_table],
                            "sql": executed_sql,
                            "timeRangeDays": days,
                            "appliedFilters": filters,
                            "metadataSource": resolution.metadata_source,
                            "tableSelection": resolution.table_selection,
                            "requiredColumns": resolution.required_columns,
                        },
                    }
                ],
                confidence=0.35,
                warnings=[
                    *resolution.warnings,
                    *filter_warnings,
                    "No observability rows were returned for insight analysis.",
                ],
            )
        normalized = [_normalize_insight_row(row) for row in rows]
        top_latency = max(normalized, key=lambda row: row["p95_latency_ms"])
        top_error = max(normalized, key=lambda row: row["error_rate_pct"])
        latency_values = [row["p95_latency_ms"] for row in normalized]
        median_latency = _median(latency_values)
        latency_ratio = (
            top_latency["p95_latency_ms"] / median_latency
            if median_latency > 0
            else 1.0
        )
        latency_anomalies = _detect_anomalies(
            normalized,
            "p95_latency_ms",
            method="z_score",
            z_threshold=2.0,
        )
        error_anomalies = _detect_anomalies(
            normalized,
            "error_rate_pct",
            method="z_score",
            z_threshold=2.0,
        )
        volume_anomalies = _detect_anomalies(
            normalized,
            "span_count",
            method="z_score",
            z_threshold=2.0,
        )
        anomalies = _merge_anomaly_sets(
            [
                ("latency", latency_anomalies),
                ("errors", error_anomalies),
                ("traffic", volume_anomalies),
            ]
        )
        findings = []
        if top_latency["p95_latency_ms"] > 0:
            findings.append(
                {
                    "driver": "latency",
                    "service": top_latency["service_name"],
                    "source_app": top_latency["source_app"],
                    "p95_latency_ms": top_latency["p95_latency_ms"],
                    "relative_to_median": round(latency_ratio, 2),
                }
            )
        if top_error["error_rate_pct"] > 0:
            findings.append(
                {
                    "driver": "errors",
                    "service": top_error["service_name"],
                    "source_app": top_error["source_app"],
                    "error_rate_pct": top_error["error_rate_pct"],
                    "error_span_count": top_error["error_span_count"],
                }
            )
        for anomaly in anomalies[:5]:
            findings.append(
                {
                    "driver": f"{anomaly['metric']} anomaly",
                    "service": anomaly["service_name"],
                    "source_app": anomaly["source_app"],
                    "anomaly_type": anomaly["anomaly_type"],
                    "z_score": anomaly.get("z_score"),
                    "severity": anomaly["severity"],
                }
            )
        summary = _insight_summary(
            top_latency,
            top_error,
            latency_ratio,
            anomalies,
        )
        text = summary
        usage = {}
        warnings = [*resolution.warnings, *filter_warnings]
        if include_synthesis and self.llm:
            llm_result, synthesis_warning = await _run_llm_synthesis(
                self.llm.interpret_insight(
                    question=question,
                    findings=findings,
                    anomalies=anomalies,
                    rows=normalized,
                    dbt_runs=dbt_runs,
                ),
                settings=self.settings,
                label="Insight agent",
            )
            if llm_result.get("status") == "success" and llm_result.get("answer"):
                text = llm_result["answer"]
                usage = _llm_usage_from_result(llm_result)
            elif synthesis_warning:
                warnings.append(synthesis_warning)

        return AgentResponse(
            task_id=task.task_id,
            agent="insight_agent",
            status="success",
            text=text,
            evidence=[
                _sql_evidence(
                    executed_sql,
                    len(rows),
                    chosen_table,
                )
            ],
            artifacts=[
                {
                    "type": "root_cause_analysis",
                    "result": {
                        "selectedTables": [chosen_table],
                        "sql": executed_sql,
                        "rows": normalized,
                        "findings": findings,
                        "anomalies": anomalies,
                        "dbtRuns": dbt_runs,
                        "anomalyDetection": {
                            "method": "z_score",
                            "threshold": 2.0,
                            "metrics": [
                                "p95_latency_ms",
                                "error_rate_pct",
                                "span_count",
                            ],
                        },
                        "timeRangeDays": days,
                        "appliedFilters": filters,
                        "metadataSource": resolution.metadata_source,
                        "tableSelection": resolution.table_selection,
                        "requiredColumns": resolution.required_columns,
                    },
                }
            ],
            confidence=0.88 if anomalies else 0.82 if findings else 0.55,
            warnings=warnings,
            usage=usage,
        )
