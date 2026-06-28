"""Partition analysis specialist agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .partition_manager import PartitionManager


# Type aliases (imported from multi_agent at runtime to avoid circular imports)
AgentTask = Any
AgentResponse = Any
Evidence = Any
EventSink = Any


class PartitionAgent:
    """Specialist agent that analyzes data partitioning strategy and health.

    This agent inspects ClickHouse table partitions, reports on partition
    distribution, detects skew and gaps, and suggests optimization strategies.
    """

    def __init__(
        self,
        clickhouse: Any,
        settings: Any | None = None,
        *,
        metadata: Any | None = None,
    ) -> None:
        self.clickhouse = clickhouse
        self.settings = settings
        self.metadata = metadata
        self.partition_manager = PartitionManager(
            settings=settings,
            metadata=metadata,
            clickhouse=clickhouse,
        )

    async def run(
        self,
        task: Any,
        *,
        question: str,
        target: str,
        limit: int,
        conversation: list[dict[str, Any]],
        memory: dict[str, Any],
        principal: Any | None,
        include_synthesis: bool,
        event_sink: Any,
    ) -> Any:
        """Analyze partitions for tables matching the user's question."""
        # Late imports to avoid circular dependency
        from .multi_agent import (
            AgentResponse,
            AgentTask,
            Evidence,
            _resolve_specialist_table,
        )

        del target, conversation, memory, principal, include_synthesis
        emit = event_sink or (lambda _: None)

        emit({
            "type": "stage",
            "stage": "partition_agent",
            "message": "Analyzing data partition strategy",
        })

        # DataHub is the catalog authority; ClickHouse is only a live fallback.
        tables = await self._load_tables()
        if not tables:
            return AgentResponse(
                task_id=task.task_id,
                agent="partition_agent",
                status="partial",
                text="Partition agent could not load table metadata.",
                evidence=[],
                artifacts=[{
                    "type": "partition_analysis",
                    "result": {
                        "tables": [],
                        "partitionInfos": [],
                        "healthReports": [],
                    },
                }],
                confidence=0.2,
                warnings=["No table metadata available for partition analysis."],
            )

        # Analyze partitions for each table
        slots = task.context.get("slots") or {} if hasattr(task, "context") else {}
        days = _parse_time_range(slots)

        partition_infos: list[dict[str, Any]] = []
        health_reports: list[dict[str, Any]] = []
        table_summaries: list[dict[str, Any]] = []

        for table in tables[:8]:  # Limit analysis to 8 tables
            table_name = str(table.get("tableName") or "")
            info = self.partition_manager.resolve_partitions(table, days)
            partition_infos.append(info.to_dict())

            # Read only live ClickHouse partition statistics; never fabricate health data.
            partition_stats = []
            if self.clickhouse is not None:
                try:
                    partition_stats = await self.partition_manager.get_partition_stats(table_name)
                except Exception:
                    partition_stats = []

            health = self.partition_manager.analyze_partition_health(table, partition_stats)
            health_reports.append(health.to_dict())

            table_summaries.append({
                "tableName": table_name,
                "partitionKey": info.partition_key,
                "partitionType": info.partition_type,
                "activePartitionCount": health.total_partitions,
                "prunedPartitionCount": len(info.pruned_partitions),
                "healthStatus": (
                    "unknown"
                    if health.total_partitions == 0
                    else "healthy"
                    if health.coverage_pct >= 90 and not health.skewed_partitions
                    else "warning"
                    if health.coverage_pct >= 70
                    else "critical"
                ),
            })

        # Build summary text
        text = self._build_summary(table_summaries, health_reports)

        evidence = [
            Evidence(
                source=str(getattr(self.metadata, "source_name", "datahub")),
                reference="partition_analysis",
                summary=f"Analyzed partitions for {len(table_summaries)} table(s).",
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
        ]

        return AgentResponse(
            task_id=task.task_id,
            agent="partition_agent",
            status="success",
            text=text,
            evidence=evidence,
            artifacts=[{
                "type": "partition_analysis",
                "result": {
                    "tables": table_summaries,
                    "partitionInfos": partition_infos,
                    "healthReports": health_reports,
                    "timeRangeDays": days,
                    "strategy": {
                        "vsf_mart": "Monthly partitions by toYYYYMM(metric_date/date)",
                        
                    },
                },
            }],
            confidence=0.85,
            warnings=[],
        )

    async def _load_tables(self) -> list[dict[str, Any]]:
        """Load table metadata from DataHub, with ClickHouse only as fallback."""
        if self.metadata is not None:
            list_fn = getattr(self.metadata, "list_tables", None)
            if callable(list_fn):
                try:
                    tables = await list_fn()
                    if tables:
                        return [
                            t for t in tables
                            if str(t.get("database", "")).lower() in {"vsf_mart"}
                        ]
                except Exception:
                    pass

        if self.clickhouse is not None:
            describe_fn = getattr(self.clickhouse, "describe_tables", None)
            if callable(describe_fn):
                try:
                    tables = await describe_fn(("vsf_mart",))
                    return tables or []
                except Exception:
                    pass

        return []

    def _build_summary(
        self,
        summaries: list[dict[str, Any]],
        reports: list[dict[str, Any]],
    ) -> str:
        if not summaries:
            return "No tables found for partition analysis."

        lines: list[str] = [
            f"Partition analysis completed for {len(summaries)} table(s)."
        ]

        healthy = sum(1 for s in summaries if s["healthStatus"] == "healthy")
        warning = sum(1 for s in summaries if s["healthStatus"] == "warning")
        critical = sum(1 for s in summaries if s["healthStatus"] == "critical")

        if healthy:
            lines.append(f"✅ {healthy} table(s) with healthy partitions.")
        if warning:
            lines.append(f"⚠️ {warning} table(s) with partition warnings.")
        if critical:
            lines.append(f"🔴 {critical} table(s) with critical partition issues.")

        for report in reports:
            if report.get("recommendations"):
                for rec in report["recommendations"][:2]:
                    lines.append(f"  → {report['tableName']}: {rec}")

        return " ".join(lines)


def _parse_time_range(slots: dict[str, Any]) -> int:
    """Parse time range from slots, default 30 days."""
    time_slot = slots.get("time_range")
    if isinstance(time_slot, dict):
        value = time_slot.get("value")
    else:
        value = time_slot
    if not value:
        return 30
    text = str(value).lower().strip()
    import re
    match = re.search(r"(\d+)\s*(d|day|days|h|hour|hours|w|week|weeks)", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit in {"h", "hour", "hours"}:
            return max(1, amount // 24)
        if unit in {"w", "week", "weeks"}:
            return amount * 7
        return amount
    return 30

