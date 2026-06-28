"""Partition metadata management and partition-aware query optimization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any


@dataclass
class PartitionInfo:
    """Describes the partition layout of a ClickHouse table."""

    table_name: str
    partition_key: str
    partition_type: str  # "monthly" | "daily" | "custom"
    active_partitions: list[str] = field(default_factory=list)
    pruned_partitions: list[str] = field(default_factory=list)
    estimated_rows: int = 0
    filter_clause: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tableName": self.table_name,
            "partitionKey": self.partition_key,
            "partitionType": self.partition_type,
            "activePartitions": self.active_partitions,
            "prunedPartitions": self.pruned_partitions,
            "estimatedRows": self.estimated_rows,
            "filterClause": self.filter_clause,
            "stats": self.stats,
        }


@dataclass
class PartitionHealthReport:
    """Health analysis of partition distribution."""

    table_name: str
    total_partitions: int
    empty_partitions: list[str] = field(default_factory=list)
    skewed_partitions: list[dict[str, Any]] = field(default_factory=list)
    coverage_pct: float = 100.0
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tableName": self.table_name,
            "totalPartitions": self.total_partitions,
            "emptyPartitions": self.empty_partitions,
            "skewedPartitions": self.skewed_partitions,
            "coveragePct": round(self.coverage_pct, 2),
            "recommendations": self.recommendations,
        }


# Default partition strategies for DataHub-published ClickHouse analytics schemas.
_PARTITION_STRATEGIES: dict[str, dict[str, str]] = {
    "vsf_mart": {"key": "date", "expression": "toYYYYMM(date)", "type": "monthly"},
}

# Known time columns for auto-detection
_TIME_COLUMNS = {
    "metric_date": "monthly",
    "event_time": "daily",
    "event_date": "daily",
    "date": "monthly",
    "ingested_at": "daily",
    "created_at": "daily",
    "started_at": "daily",
    "timestamp": "daily",
}


class PartitionManager:
    """Manages partition metadata and generates partition-aware filters."""

    def __init__(
        self,
        settings: Any | None = None,
        metadata: Any | None = None,
        clickhouse: Any | None = None,
    ) -> None:
        self.settings = settings
        self.metadata = metadata
        self.clickhouse = clickhouse
        self._partition_cache: dict[str, PartitionInfo] = {}

    def detect_partition_key(self, table: dict[str, Any]) -> dict[str, str] | None:
        """Detect partition key from table metadata or column naming conventions.

        Returns dict with 'key', 'expression', 'type' or None if not detectable.
        """
        # 0. Check pre-extracted partition_info
        if "partition_info" in table and isinstance(table["partition_info"], dict):
            info = table["partition_info"]
            if "key" in info and "strategy" in info:
                return {
                    "key": info["key"],
                    "expression": info.get("expression") or _partition_expression(info["key"], info["strategy"]),
                    "type": info["strategy"],
                }

        # 1. Check DataHub-ingested tags for explicit partition info
        tags = table.get("tags") or []
        for tag in tags:
            tag_fqn = str(tag.get("tagFQN") if isinstance(tag, dict) else tag or "")
            lower_tag = tag_fqn.lower()
            if "partition" in lower_tag:
                # Parse tag like "partition:monthly:metric_date"
                parts = tag_fqn.split(":")
                if len(parts) >= 3:
                    return {
                        "key": parts[2],
                        "expression": _partition_expression(parts[2], parts[1]),
                        "type": parts[1],
                    }
                if len(parts) >= 2:
                    return {
                        "key": parts[1],
                        "expression": _partition_expression(parts[1], "monthly"),
                        "type": "monthly",
                    }

        # 2. Check schema tier for default strategy
        schema = _table_schema(table)
        if schema in _PARTITION_STRATEGIES:
            strategy = _PARTITION_STRATEGIES[schema]
            columns = _column_names(table)
            if strategy["key"] in columns:
                return dict(strategy)

        # 3. Auto-detect from column names
        columns = _column_names(table)
        for column_name, partition_type in _TIME_COLUMNS.items():
            if column_name in columns:
                return {
                    "key": column_name,
                    "expression": _partition_expression(column_name, partition_type),
                    "type": partition_type,
                }

        return None

    def resolve_partitions(
        self,
        table: dict[str, Any],
        time_range_days: int = 30,
    ) -> PartitionInfo:
        """Resolve partition layout and generate optimized filter clause."""
        table_name = str(table.get("tableName") or "")
        partition_key = self.detect_partition_key(table)

        if partition_key is None:
            return PartitionInfo(
                table_name=table_name,
                partition_key="",
                partition_type="none",
                filter_clause="",
            )

        key = partition_key["key"]
        p_type = partition_key["type"]
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=time_range_days)

        if p_type == "monthly":
            active = _monthly_partitions(start_date, end_date)
            all_recent = _monthly_partitions(
                end_date - timedelta(days=180), end_date
            )
        elif p_type == "daily":
            active = _daily_partitions(start_date, end_date)
            all_recent = _daily_partitions(
                end_date - timedelta(days=90), end_date
            )
        else:
            active = []
            all_recent = []

        pruned = [p for p in all_recent if p not in active]

        filter_clause = _partition_filter_clause(key, p_type, start_date, end_date)

        return PartitionInfo(
            table_name=table_name,
            partition_key=partition_key["expression"],
            partition_type=p_type,
            active_partitions=active,
            pruned_partitions=pruned,
            estimated_rows=0,
            filter_clause=filter_clause,
            stats={
                "partitionCount": len(active),
                "prunedCount": len(pruned),
                "timeRangeDays": time_range_days,
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )

    def generate_partition_filter(self, info: PartitionInfo) -> str:
        """Return the WHERE clause for partition pruning."""
        return info.filter_clause

    async def get_partition_stats(self, table_name: str) -> list[dict[str, Any]]:
        """List active partitions with stats (rows, size) from ClickHouse."""
        if not self.clickhouse or not hasattr(self.clickhouse, "query"):
            return []

        parts = table_name.split(".", 1)
        if len(parts) != 2:
            return []
        db_name, tbl_name = parts

        sql = f"""
            SELECT 
                partition AS partition_id, 
                sum(rows) AS rows, 
                sum(bytes_on_disk) AS size_bytes 
            FROM system.parts 
            WHERE database = '{db_name}' AND table = '{tbl_name}' AND active = 1 
            GROUP BY partition_id 
            ORDER BY partition_id DESC
        """
        try:
            rows = await self.clickhouse.query(sql)
            return [
                {
                    "partition_id": str(row.get("partition_id", "")),
                    "rows": int(row.get("rows", 0)),
                    "size_bytes": int(row.get("size_bytes", 0)),
                }
                for row in rows
            ]
        except Exception:
            return []

    def analyze_partition_health(
        self,
        table: dict[str, Any],
        partition_stats: list[dict[str, Any]],
    ) -> PartitionHealthReport:
        """Analyze partition distribution for skew and gaps."""
        table_name = str(table.get("tableName") or "")
        total = len(partition_stats)

        if not partition_stats:
            return PartitionHealthReport(
                table_name=table_name,
                total_partitions=0,
                coverage_pct=0.0,
                recommendations=["No partition data available for analysis."],
            )

        row_counts = [int(p.get("rows", 0)) for p in partition_stats]
        total_rows = sum(row_counts)
        mean_rows = total_rows / len(row_counts) if row_counts else 0

        # Detect empty partitions
        empty = [
            str(p.get("partition_id", "unknown"))
            for p in partition_stats
            if int(p.get("rows", 0)) == 0
        ]

        # Detect skewed partitions (> 2x mean)
        skewed = []
        if mean_rows > 0:
            for p in partition_stats:
                rows = int(p.get("rows", 0))
                ratio = rows / mean_rows
                if ratio > 2.0 or (ratio < 0.3 and rows > 0):
                    skewed.append({
                        "partitionId": str(p.get("partition_id", "unknown")),
                        "rows": rows,
                        "ratio": round(ratio, 2),
                        "type": "oversized" if ratio > 2.0 else "undersized",
                    })

        # Coverage
        non_empty = total - len(empty)
        coverage = (non_empty / total * 100) if total > 0 else 0.0

        # Recommendations
        recommendations: list[str] = []
        if empty:
            recommendations.append(
                f"{len(empty)} empty partition(s) detected — potential ingestion gap."
            )
        if skewed:
            oversized = [s for s in skewed if s["type"] == "oversized"]
            if oversized:
                recommendations.append(
                    f"{len(oversized)} oversized partition(s) detected "
                    f"(>{2.0}x average). Consider sub-partitioning."
                )
        if not recommendations:
            recommendations.append("Partition distribution looks healthy.")

        return PartitionHealthReport(
            table_name=table_name,
            total_partitions=total,
            empty_partitions=empty,
            skewed_partitions=skewed,
            coverage_pct=coverage,
            recommendations=recommendations,
        )


def _partition_expression(column: str, partition_type: str) -> str:
    """Generate ClickHouse partition expression."""
    if partition_type == "monthly":
        return f"toYYYYMM({column})"
    if partition_type == "daily":
        return f"toYYYYMMDD({column})"
    return f"toYYYYMM({column})"


def _partition_filter_clause(
    key: str, partition_type: str, start: date, end: date
) -> str:
    """Generate an optimized WHERE clause for partition pruning."""
    if partition_type == "monthly":
        # Align to month boundaries for optimal pruning
        month_start = start.replace(day=1)
        return f"{key} >= '{month_start.isoformat()}'"
    elif partition_type == "daily":
        return f"{key} >= '{start.isoformat()}'"
    return f"{key} >= '{start.isoformat()}'"


def _monthly_partitions(start: date, end: date) -> list[str]:
    """List monthly partition IDs (YYYYMM) covering a date range."""
    partitions: list[str] = []
    current = start.replace(day=1)
    while current <= end:
        partition_id = current.strftime("%Y%m")
        if partition_id not in partitions:
            partitions.append(partition_id)
        # Advance to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return partitions


def _daily_partitions(start: date, end: date) -> list[str]:
    """List daily partition IDs (YYYYMMDD) covering a date range."""
    partitions: list[str] = []
    current = start
    while current <= end:
        partitions.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return partitions


def _table_schema(table: dict[str, Any]) -> str:
    schema = str(
        table.get("database")
        or table.get("schema")
        or table.get("databaseSchema")
        or ""
    ).strip()
    if schema:
        return schema.lower()
    table_name = str(table.get("tableName") or "")
    return table_name.split(".", 1)[0].lower() if "." in table_name else ""


def _column_names(table: dict[str, Any]) -> set[str]:
    return {
        str(col.get("name") or "").strip().lower()
        for col in table.get("columns") or []
        if str(col.get("name") or "").strip()
    }
