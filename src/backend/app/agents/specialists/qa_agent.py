"""QA specialist agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...config import Settings
from ...security import Principal
from ...services.llm import LlmService
from ..table_selector import TableSelector
from .specialist_utils import (
    AgentResponse,
    AgentStatus,
    AgentTask,
    Evidence,
    EventSink,
    _clickhouse_query,
    _clickhouse_where_clause,
    _extract_entity_filters,
    _filter_warnings,
    _llm_usage_from_result,
    _metadata_evidence,
    _normalize_qa_row,
    _parse_days,
    _qa_summary,
    _resolve_specialist_table,
    _run_llm_synthesis,
    _slot_value,
    _sql_evidence,
    _table_column_names,
    _to_int,
)


class QaAgent:
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
        filters = _extract_entity_filters(question)

        required_cols = {
            "metric_date",
            "source_app",
            "batch_count",
            "failed_batch_count",
            "quarantined_batch_count",
            "loaded_row_count",
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
                "QA agent could not find a live ingestion health table "
                "with the required columns."
            )
            return AgentResponse(
                task_id=task.task_id,
                agent="qa_agent",
                status="partial",
                text=text,
                evidence=[_metadata_evidence(resolution, "ingestion health")],
                artifacts=[
                    {
                        "type": "qa_health",
                        "result": {
                            "selectedTables": [],
                            "sql": None,
                            "auditSql": None,
                            "rows": [],
                            "auditRows": [],
                            "issues": [],
                            "timeRangeDays": days,
                            "appliedFilters": filters,
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
        table_columns = _table_column_names(resolution.table)
        filter_warnings = _filter_warnings(filters, {"source_app"}, table_columns)

        where_clause = _clickhouse_where_clause(
            days=days,
            filters=filters,
            allowed_columns={"source_app"} & table_columns,
        )
        sql = f"""
            SELECT
                source_app,
                batch_count,
                failed_batch_count,
                quarantined_batch_count,
                loaded_row_count,
                round(failed_batch_count / greatest(batch_count, 1) * 100, 2) AS failed_batch_rate_pct
            FROM (
                SELECT
                    source_app,
                    sum(batch_count) AS batch_count,
                    sum(failed_batch_count) AS failed_batch_count,
                    sum(quarantined_batch_count) AS quarantined_batch_count,
                    sum(loaded_row_count) AS loaded_row_count
                FROM {chosen_table}
                WHERE {where_clause}
                GROUP BY source_app
            )
            ORDER BY failed_batch_count DESC, quarantined_batch_count DESC
            LIMIT 20
        """
        rows, executed_sql = await _clickhouse_query(
            self.clickhouse,
            sql,
            limit=min(limit, 50),
            principal=principal,
        )
        normalized = [_normalize_qa_row(row) for row in rows]
        audit_rows: list[dict[str, Any]] = []
        audit_sql = ""
        if self.postgres is not None:
            audit_sql = (
                "SELECT status, count(*) AS query_count "
                "FROM ops.agent_query_audit "
                f"WHERE created_at >= now() - interval '{days} days' "
                "GROUP BY status "
                "ORDER BY query_count DESC "
                "LIMIT 20"
            )
            try:
                audit_rows = await self.postgres.query(audit_sql)
            except Exception:
                audit_rows = []

        failed_batches = sum(row["failed_batch_count"] for row in normalized)
        quarantined_batches = sum(row["quarantined_batch_count"] for row in normalized)
        query_failures = sum(
            _to_int(row.get("query_count"))
            for row in audit_rows
            if str(row.get("status") or "").lower() not in {"success", "completed", "ok"}
        )
        issues = []
        if failed_batches:
            issues.append(
                {
                    "severity": "high",
                    "signal": "failed_batches",
                    "count": failed_batches,
                }
            )
        if quarantined_batches:
            issues.append(
                {
                    "severity": "medium",
                    "signal": "quarantined_batches",
                    "count": quarantined_batches,
                }
            )
        if query_failures:
            issues.append(
                {
                    "severity": "medium",
                    "signal": "agent_query_failures_24h",
                    "count": query_failures,
                }
            )
        status: AgentStatus = "success" if rows or audit_rows else "partial"
        text = _qa_summary(issues, normalized, audit_rows)
        usage = {}
        warnings = [*resolution.warnings, *filter_warnings]
        if include_synthesis and self.llm:
            llm_result, synthesis_warning = await _run_llm_synthesis(
                self.llm.interpret_qa(
                    question=question,
                    issues=issues,
                    ingestion_rows=normalized,
                    audit_rows=audit_rows,
                ),
                settings=self.settings,
                label="QA agent",
            )
            if llm_result.get("status") == "success" and llm_result.get("answer"):
                text = llm_result["answer"]
                usage = _llm_usage_from_result(llm_result)
            elif synthesis_warning:
                warnings.append(synthesis_warning)

        if status != "success":
            warnings.append("No QA health rows were returned.")
        return AgentResponse(
            task_id=task.task_id,
            agent="qa_agent",
            status=status,
            text=text,
            evidence=[
                _sql_evidence(executed_sql, len(rows), chosen_table)
            ]
            + (
                [
                    Evidence(
                        source="sql",
                        reference=audit_sql,
                        summary=f"Returned {len(audit_rows)} audit status rows from ops.agent_query_audit",
                        observed_at=datetime.now(timezone.utc).isoformat(),
                    )
                ]
                if audit_sql
                else []
            ),
            artifacts=[
                {
                    "type": "qa_health",
                    "result": {
                        "selectedTables": [
                            chosen_table,
                            *(
                                ["ops.agent_query_audit"]
                                if audit_sql
                                else []
                            ),
                        ],
                        "sql": executed_sql,
                        "auditSql": audit_sql or None,
                        "rows": normalized,
                        "auditRows": audit_rows,
                        "issues": issues,
                        "timeRangeDays": days,
                        "appliedFilters": filters,
                        "metadataSource": resolution.metadata_source,
                        "tableSelection": resolution.table_selection,
                        "requiredColumns": resolution.required_columns,
                    },
                }
            ],
            confidence=0.78 if status == "success" else 0.4,
            warnings=warnings,
            usage=usage,
        )
