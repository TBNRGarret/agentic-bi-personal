import pytest
from app.agents.query_decomposer import (
    decompose_query_intent,
    compose_sql,
    QueryIntent,
    SortIntent,
)

SERVER_MART_COLUMNS = {
    "date", "source_app_id", "model", "request_count", "total_tokens",
    "total_cost_usd", "error_count", "avg_latency_ms", "p95_latency_ms",
}

def test_decompose_superlative_model_cost():
    intent = decompose_query_intent(
        "Which model contributed most to cost in 30 days",
        SERVER_MART_COLUMNS,
    )
    assert "cost" in intent.metrics
    assert "model" in intent.dimensions
    assert intent.time_window_days == 30
    assert intent.sort is not None
    assert intent.sort.metric == "cost"
    assert intent.sort.direction == "desc"
    assert intent.question_type == "top_contributor"

def test_compose_superlative_model_cost():
    intent = QueryIntent(
        metrics=["cost"],
        dimensions=["model"],
        time_window_days=30,
        sort=SortIntent(metric="cost", direction="desc"),
        question_type="top_contributor",
    )
    sql = compose_sql("vsf_mart.gold_app_daily_cost", SERVER_MART_COLUMNS, intent)
    
    assert "model" in sql
    assert "round(sum(total_cost_usd), 6) AS total_cost_usd" in sql
    assert "FROM vsf_mart.gold_app_daily_cost" in sql
    assert "GROUP BY model" in sql
    assert "ORDER BY total_cost_usd DESC" in sql
    assert "date >= today() - 30" in sql

def test_decompose_trend():
    intent = decompose_query_intent(
        "show request and cost trend for 30 days",
        SERVER_MART_COLUMNS,
    )
    assert "cost" in intent.metrics
    assert "request_count" in intent.metrics
    assert "time" in intent.dimensions
    assert intent.time_window_days == 30
    assert intent.question_type == "trend"

def test_compose_trend():
    intent = QueryIntent(
        metrics=["request_count", "cost"],
        dimensions=["time"],
        time_window_days=30,
        question_type="trend",
    )
    sql = compose_sql("vsf_mart.gold_app_daily_cost", SERVER_MART_COLUMNS, intent)
    
    assert "date AS metric_date" in sql
    assert "sum(request_count) AS request_count" in sql
    assert "round(sum(total_cost_usd), 6) AS total_cost_usd" in sql
    assert "GROUP BY date" in sql
    assert "ORDER BY metric_date" in sql

def test_decompose_multi_dimension():
    # Simulate orchestrator request plan output
    plan = {
        "slots": {
            "dimensions": {"value": ["time", "model", "source_app"]},
        }
    }
    intent = decompose_query_intent(
        "cost tung model theo tung ngay cua tung source app",
        SERVER_MART_COLUMNS,
        request_plan=plan,
    )
    assert "cost" in intent.metrics
    assert intent.dimensions == ["time", "model", "source_app"]

def test_compose_multi_dimension():
    intent = QueryIntent(
        metrics=["cost"],
        dimensions=["time", "model", "source_app"],
        time_window_days=30,
        question_type="comparison",
    )
    sql = compose_sql("vsf_mart.gold_app_daily_cost", SERVER_MART_COLUMNS, intent)
    
    assert "date AS metric_date" in sql
    assert "model" in sql
    assert "source_app_id AS source_app" in sql
    assert "GROUP BY date, model, source_app_id" in sql
    # order by metric_date since time is a dimension
    assert "ORDER BY metric_date" in sql

def test_decompose_filters():
    intent = decompose_query_intent(
        "cost of model gpt-4o for source hoang_ai_chat",
        SERVER_MART_COLUMNS,
    )
    assert intent.filters["model"] == "gpt-4o"
    assert intent.filters["source_app"] == "hoang_ai_chat"

def test_superlative_entity_words_are_not_filters():
    obs_columns = {
        "metric_date", "source_app", "service_name",
        "error_log_count", "avg_span_duration_ms", "p95_span_duration_ms",
    }

    source_intent = decompose_query_intent("top source errors", obs_columns)
    assert source_intent.dimensions == ["source_app"]
    assert source_intent.filters == {}

    service_intent = decompose_query_intent(
        "which service has highest latency",
        obs_columns,
    )
    assert service_intent.dimensions == ["service_name"]
    assert service_intent.filters == {}


def test_compose_filters():
    intent = QueryIntent(
        metrics=["cost"],
        dimensions=["time"],
        time_window_days=30,
        filters={"model": "gpt-4o", "source_app": "hoang_ai_chat"},
    )
    sql = compose_sql("vsf_mart.gold_app_daily_cost", SERVER_MART_COLUMNS, intent)
    assert "positionCaseInsensitiveUTF8(model, 'gpt-4o') > 0" in sql
    assert "positionCaseInsensitiveUTF8(source_app_id, 'hoang_ai_chat') > 0" in sql
