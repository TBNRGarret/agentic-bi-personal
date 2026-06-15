"""
Tests for DataHub metadata client (metadata/client/datahub_client.py).

Tests use mock HTTP responses to verify client logic without a live DataHub instance.
For integration tests against a real DataHub, set DATAHUB_GMS_URL and run with:
    pytest tests/test_datahub_client.py -v -k "not mock"
"""

import json
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SEARCH_RESPONSE = {
    "data": {
        "search": {
            "searchResults": [
                {
                    "entity": {
                        "urn": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)",
                        "name": "fct_llm_interactions",
                        "platform": {"name": "clickhouse"},
                        "editableProperties": None,
                        "properties": {
                            "description": "Conversations turn list enriched with SigNoz errors and Langfuse metadata"
                        },
                    }
                },
                {
                    "entity": {
                        "urn": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.gold_app_daily_cost,PROD)",
                        "name": "gold_app_daily_cost",
                        "platform": {"name": "clickhouse"},
                        "editableProperties": {"description": "Daily aggregated LLM metrics"},
                        "properties": {"description": ""},
                    }
                },
            ]
        }
    }
}

SAMPLE_SCHEMA_RESPONSE = {
    "data": {
        "dataset": {
            "schemaMetadata": {
                "fields": [
                    {"fieldPath": "interaction_id", "type": "STRING", "description": "Primary key", "nullable": False},
                    {"fieldPath": "model", "type": "STRING", "description": "LLM model name", "nullable": False},
                    {"fieldPath": "total_tokens", "type": "NUMBER", "description": "Total token count", "nullable": True},
                    {"fieldPath": "domain", "type": "STRING", "description": "Domain category", "nullable": True},
                ]
            }
        }
    }
}

SAMPLE_FRESHNESS_RESPONSE = {
    "data": {
        "dataset": {
            "lastIngested": 1749301234000
        }
    }
}

SAMPLE_LINEAGE_RESPONSE = {
    "data": {
        "searchAcrossLineage": {
            "searchResults": [
                {"entity": {"urn": "urn:li:dataset:(...,vsf_stg.stg_user_interactions,PROD)", "name": "stg_user_interactions"}},
                {"entity": {"urn": "urn:li:dataset:(...,vsf_raw.user_interactions,PROD)", "name": "user_interactions"}},
            ]
        }
    }
}


# ---------------------------------------------------------------------------
# Mock helper
# ---------------------------------------------------------------------------

def _mock_httpx_post(response_data: dict):
    """Create a mock for httpx.post that returns a specific JSON response."""
    mock_response = MagicMock()
    mock_response.json.return_value = response_data
    mock_response.raise_for_status = MagicMock()
    return mock_response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListTables:
    def test_list_tables_defaults_to_vsf_mart_schema(self):
        import inspect

        from metadata.client.datahub_client import DataHubClient

        signature = inspect.signature(DataHubClient.list_tables)

        assert signature.parameters["schema"].default == "vsf_mart"

    @patch("metadata.client.datahub_client.httpx.post")
    def test_list_tables_returns_correct_structure(self, mock_post):
        mock_post.return_value = _mock_httpx_post(SAMPLE_SEARCH_RESPONSE)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        tables = client.list_tables(platform="clickhouse", schema="vsf_mart")

        assert len(tables) == 2
        assert tables[0]["name"] == "fct_llm_interactions"
        assert tables[0]["platform"] == "clickhouse"
        assert "urn" in tables[0]
        assert tables[1]["name"] == "gold_app_daily_cost"

    @patch("metadata.client.datahub_client.httpx.post")
    def test_list_tables_uses_editable_description_first(self, mock_post):
        mock_post.return_value = _mock_httpx_post(SAMPLE_SEARCH_RESPONSE)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        tables = client.list_tables()

        # First table has no editableProperties, falls back to properties.description
        assert "enriched" in tables[0]["description"].lower()
        # Second table has editableProperties.description set
        assert "aggregated" in tables[1]["description"].lower()

    @patch("metadata.client.datahub_client.httpx.post")
    def test_list_tables_empty_response(self, mock_post):
        empty_response = {"data": {"search": {"searchResults": []}}}
        mock_post.return_value = _mock_httpx_post(empty_response)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        tables = client.list_tables()

        assert tables == []


class TestGetColumns:
    @patch("metadata.client.datahub_client.httpx.post")
    def test_get_columns_returns_correct_fields(self, mock_post):
        mock_post.return_value = _mock_httpx_post(SAMPLE_SCHEMA_RESPONSE)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        columns = client.get_columns("urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)")

        assert len(columns) == 4
        assert columns[0]["name"] == "interaction_id"
        assert columns[0]["type"] == "STRING"
        assert columns[0]["nullable"] is False
        assert columns[2]["name"] == "total_tokens"
        assert columns[3]["name"] == "domain"

    @patch("metadata.client.datahub_client.httpx.post")
    def test_get_columns_empty_schema(self, mock_post):
        empty_schema = {"data": {"dataset": {"schemaMetadata": {"fields": []}}}}
        mock_post.return_value = _mock_httpx_post(empty_schema)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        columns = client.get_columns("urn:test")

        assert columns == []


class TestGetFreshness:
    @patch("metadata.client.datahub_client.httpx.post")
    def test_get_freshness_returns_timestamp(self, mock_post):
        mock_post.return_value = _mock_httpx_post(SAMPLE_FRESHNESS_RESPONSE)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        freshness = client.get_freshness("urn:test")

        assert freshness["last_updated_ms"] == 1749301234000
        assert freshness["last_updated_iso"] is not None
        assert "2025" in freshness["last_updated_iso"] or "2026" in freshness["last_updated_iso"]

    @patch("metadata.client.datahub_client.httpx.post")
    def test_get_freshness_null_timestamp(self, mock_post):
        null_freshness = {"data": {"dataset": {"lastIngested": None}}}
        mock_post.return_value = _mock_httpx_post(null_freshness)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        freshness = client.get_freshness("urn:test")

        assert freshness["last_updated_ms"] is None
        assert freshness["last_updated_iso"] is None


class TestGetLineage:
    @patch("metadata.client.datahub_client.httpx.post")
    def test_get_lineage_returns_upstream(self, mock_post):
        mock_post.return_value = _mock_httpx_post(SAMPLE_LINEAGE_RESPONSE)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        lineage = client.get_lineage(
            "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.fct_llm_interactions,PROD)"
        )

        assert lineage["entity"] == "fct_llm_interactions"
        assert "stg_user_interactions" in lineage["upstream"]
        assert "user_interactions" in lineage["upstream"]


class TestGetSchemaContextForQuestion:
    @patch("metadata.client.datahub_client.httpx.post")
    def test_schema_context_contains_table_and_columns(self, mock_post):
        # First call: list_tables, Second call: get_columns for each table
        mock_post.side_effect = [
            _mock_httpx_post({"data": {"search": {"searchResults": []}}}),
            _mock_httpx_post(SAMPLE_SEARCH_RESPONSE),
            _mock_httpx_post(SAMPLE_SCHEMA_RESPONSE),
            _mock_httpx_post(SAMPLE_SCHEMA_RESPONSE),
        ]

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        context = client.get_schema_context_for_question("Total tokens per model?")

        assert "fct_llm_interactions" in context
        assert "interaction_id" in context
        assert "total_tokens" in context

    @patch("metadata.client.datahub_client.httpx.post")
    def test_schema_context_empty_tables(self, mock_post):
        empty_response = {"data": {"search": {"searchResults": []}}}
        mock_post.return_value = _mock_httpx_post(empty_response)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()
        context = client.get_schema_context_for_question("anything?")

        assert context == ""


class TestGraphQLErrorHandling:
    @patch("metadata.client.datahub_client.httpx.post")
    def test_graphql_error_raises_runtime_error(self, mock_post):
        error_response = {
            "errors": [{"message": "Field 'invalid' does not exist"}]
        }
        mock_post.return_value = _mock_httpx_post(error_response)

        from metadata.client.datahub_client import DataHubClient
        client = DataHubClient()

        with pytest.raises(RuntimeError, match="DataHub GraphQL error"):
            client.list_tables()
