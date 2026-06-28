from __future__ import annotations

import asyncio

from app.connectors.datahub import DataHubConnector


class FakeSettings:
    clickhouse_analytics_schema_list = ["vsf_mart"]


class FakeClickHouse:
    async def describe_tables(self, schemas):
        assert tuple(schemas) == ("vsf_mart",)
        return [
            {
                "tableName": "vsf_mart.gold_app_daily_cost",
                "database": "vsf_mart",
                "name": "gold_app_daily_cost",
                "datasource": "clickhouse",
                "description": "live description",
                "columns": [
                    {"name": "date", "type": "Date"},
                    {"name": "model", "type": "String"},
                    {"name": "total_cost_usd", "type": "Decimal(38, 8)"},
                ],
            }
        ]


def test_datahub_connector_keeps_only_live_clickhouse_tables_and_columns() -> None:
    async def run():
        connector = DataHubConnector(FakeSettings(), FakeClickHouse())

        async def fake_datahub_tables():
            return [
                {
                    "urn": "urn:live",
                    "tableName": "vsf_mart.gold_app_daily_cost",
                    "database": "vsf_mart",
                    "name": "gold_app_daily_cost",
                    "datasource": "clickhouse",
                    "description": "catalog description",
                    "columns": [{"name": "date", "type": "Date"}],
                    "tags": [{"tagFQN": "curated"}],
                },
                {
                    "urn": "urn:stale",
                    "tableName": "vsf_mart.agg_daily_llm_metrics",
                    "database": "vsf_mart",
                    "name": "agg_daily_llm_metrics",
                    "datasource": "clickhouse",
                    "columns": [{"name": "metric_date", "type": "Date"}],
                },
            ]

        connector._datahub_tables = fake_datahub_tables
        return await connector.list_tables(refresh=True)

    tables = asyncio.run(run())

    assert [table["tableName"] for table in tables] == ["vsf_mart.gold_app_daily_cost"]
    assert {column["name"] for column in tables[0]["columns"]} >= {"model", "total_cost_usd"}
    assert tables[0]["description"] == "catalog description"
    assert tables[0]["urn"] == "urn:live"
