from metadata.mcp_service import DataHubMCPService


class FakeDataHubClient:
    def __init__(self):
        self.tables = [
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)",
                "name": "fct_llm_interactions",
                "platform": "clickhouse",
                "schema": "vsf_mart",
                "platform_instance": "agentic_clickhouse",
                "description": "LLM interaction fact table with tokens and model cost",
                "tags": [{"tagFQN": "gold"}],
            },
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.data_quality_results,PROD)",
                "name": "data_quality_results",
                "platform": "clickhouse",
                "schema": "vsf_mart",
                "platform_instance": "agentic_clickhouse",
                "description": "Data quality check results",
                "tags": [],
            },
        ]

    def list_tables(self, platform="clickhouse", schema="vsf_mart", platform_instance="agentic_clickhouse"):
        return self.tables

    def get_columns(self, dataset_urn):
        return [
            {"name": "event_date", "type": "DATE", "description": "", "nullable": False},
            {"name": "model", "type": "STRING", "description": "", "nullable": False},
            {"name": "total_tokens", "type": "NUMBER", "description": "", "nullable": True},
        ]

    def get_freshness(self, dataset_urn):
        return {
            "last_updated_ms": 1749301234000,
            "last_updated_iso": "2025-06-07T10:20:34+00:00",
            "latest_dq_status": "pass",
        }

    def get_lineage(self, dataset_urn, depth=3):
        return {
            "entity": "fct_llm_interactions",
            "upstream": ["stg_llm_interactions"],
            "requested_depth": depth,
        }


def test_search_tables_returns_compact_relevant_cards():
    service = DataHubMCPService(client=FakeDataHubClient())

    result = service.search_tables("token cost by model", limit=1)

    assert result["schema"] == "vsf_mart"
    assert len(result["tables"]) == 1
    assert result["tables"][0]["name"] == "fct_llm_interactions"
    assert "customProperties" not in result["tables"][0]


def test_get_table_context_for_question_adds_columns_and_freshness():
    service = DataHubMCPService(client=FakeDataHubClient())

    result = service.get_table_context_for_question("token cost by model", limit=1)

    table = result["tables"][0]
    assert table["name"] == "fct_llm_interactions"
    assert table["columns"][1]["name"] == "model"
    assert table["freshness"]["latest_dq_status"] == "pass"


def test_list_schema_fields_returns_mcp_field_contract():
    service = DataHubMCPService(client=FakeDataHubClient())

    result = service.list_schema_fields("urn:test")

    assert result["urn"] == "urn:test"
    assert result["fields"][0]["name"] == "event_date"
    assert result["field_count"] == 3
    assert result["truncated"] is False


def test_get_lineage_clamps_depth():
    service = DataHubMCPService(client=FakeDataHubClient())

    result = service.get_lineage("urn:test", depth=99)

    assert result["requested_depth"] == 5


def test_health_returns_metadata_count():
    service = DataHubMCPService(client=FakeDataHubClient())

    result = service.health()

    assert result == {
        "status": "ok",
        "platform": "clickhouse",
        "schema": "vsf_mart",
        "table_count": 2,
    }
