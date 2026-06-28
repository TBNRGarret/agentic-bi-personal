from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import Settings


class ClickHouseConnector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def health(self) -> bool:
        try:
            rows = await self.query("SELECT 1 AS ok")
            return bool(rows and rows[0].get("ok") == 1)
        except Exception:
            return False

    async def query(self, sql: str) -> list[dict[str, Any]]:
        statement = sql.strip().rstrip(";")
        if not statement:
            return []
        timeout = httpx.Timeout(
            connect=5,
            read=self.settings.clickhouse_query_timeout_seconds + 2,
            write=5,
            pool=5,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"http://{self.settings.clickhouse_host}:{self.settings.clickhouse_port}/",
                params={
                    "database": self.settings.clickhouse_database,
                    "readonly": 1,
                    "max_execution_time": self.settings.clickhouse_query_timeout_seconds,
                    "max_result_rows": self.settings.max_query_rows,
                    "result_overflow_mode": "break",
                },
                auth=(self.settings.clickhouse_user, self.settings.clickhouse_password),
                content=f"{statement}\nFORMAT JSON",
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            if response.is_error:
                detail = response.text.strip() or response.reason_phrase
                raise RuntimeError(
                    f"ClickHouse query failed ({response.status_code}): {detail[:2000]}"
                )
            payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError("ClickHouse JSON response did not contain a data array")
        types = {
            str(item.get("name")): str(item.get("type") or "")
            for item in payload.get("meta") or []
        }
        return [
            {
                key: _convert_value(value, types.get(key, ""))
                for key, value in row.items()
            }
            for row in data
            if isinstance(row, dict)
        ]

    async def describe_tables(
        self, schemas: tuple[str, ...] = ("vsf_mart",)
    ) -> list[dict[str, Any]]:
        schema_values = ", ".join(f"'{schema}'" for schema in schemas)
        rows = await self.query(
            "SELECT database, table, name, type, position "
            "FROM system.columns "
            f"WHERE database IN ({schema_values}) "
            "ORDER BY database, table, position"
        )
        tables: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = f"{row['database']}.{row['table']}"
            table = tables.setdefault(
                key,
                {
                    "tableName": key,
                    "database": row["database"],
                    "name": row["table"],
                    "datasource": "clickhouse",
                    "columns": [],
                },
            )
            table["columns"].append({"name": row["name"], "type": row["type"]})
        return list(tables.values())


def _convert_value(value: Any, type_name: str) -> Any:
    if value is None or not isinstance(value, str):
        return value
    normalized = type_name.replace("Nullable(", "").rstrip(")")
    if normalized.startswith(("Int", "UInt")):
        try:
            return int(value)
        except ValueError:
            return value
    if normalized.startswith(("Float", "Decimal")):
        try:
            return float(Decimal(value))
        except (InvalidOperation, ValueError):
            return value
    return value
