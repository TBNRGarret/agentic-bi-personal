"""Shared semantic domain scoring, vocabulary, and intent inference.

Consolidates duplicate lexical rules and semantic domain scoring across
Model 1 (monitoring_agent), Model 2 (hybrid selector/decomposer), and table_selector.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_STOP_TERMS: set[str] = {
    "and",
    "the",
    "show",
    "what",
    "which",
    "with",
    "cua",
    "cho",
    "voi",
    "hay",
}

_CAPABILITY_COLUMNS: dict[str, set[str]] = {
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
        "severity",
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


def normalize_text(value: str) -> str:
    """Normalize text removing accents and standardizing terminology."""
    ascii_value = "".join(
        character
        for character in unicodedata.normalize("NFD", str(value or ""))
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


def semantic_domain(text: str) -> str | None:
    """Infer high-level semantic domain (agent, llm, trace, log, ingestion)."""
    normalized = normalize_text(text)
    if "log" in normalized:
        return "log"
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


def asks_latency_root_cause(text: str) -> bool:
    has_latency = any(
        term in text
        for term in ("latency", "duration", "response time", "slow")
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


def asks_agent_cost(text: str) -> bool:
    return any(
        term in text
        for term in (
            "agent cost",
            "cost agent",
            "chi phi agent",
            "agent spend",
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


def asks_log_error_detail(text: str) -> bool:
    normalized = normalize_text(text)
    has_log_error = "log" in normalized and any(
        term in normalized
        for term in ("error", "failed", "failure", "fatal", "critical")
    )
    if not has_log_error:
        return False
    asks_detail = any(
        term in normalized
        for term in (
            "message",
            "detail",
            "details",
            "underlying",
            "ve gi",
            "la gi",
            "noi dung",
            "show",
            "xem",
            "liet ke",
            "what",
            "which",
        )
    )
    asks_count = any(
        term in normalized
        for term in ("count", "so luong", "bao nhieu", "trend", "tong quan", "overview")
    )
    return asks_detail or not asks_count


def question_capabilities(question: str) -> list[str]:
    text = normalize_text(question)
    capabilities: list[str] = []
    terms = (
        ("time", ("trend", "daily", "day", "week", "ngay", "tuan")),
        ("service", ("service", "dich vu")),
        ("source", ("source", "app", "owner", "environment")),
        ("model", ("model", "mo hinh")),
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
    if asks_latency_root_cause(text):
        for capability in ("trace", "service", "error"):
            if capability not in capabilities:
                capabilities.append(capability)
    return capabilities


def semantic_domain_column_score(
    text: str,
    columns: set[str],
    reasons: list[str],
) -> float:
    score = 0.0
    domain = semantic_domain(text)
    capabilities = set(question_capabilities(text))
    profiles = {
        "agent": {"query_count", "user_id", "username", "estimated_cost_usd"},
        "log": {"event_time", "severity", "message", "trace_id", "span_id", "source_app", "service_name"},
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
    elif "request_count" in question_capabilities(text):
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


def infer_intent(question: str) -> str:
    """Infer intent category (dashboard, time_series, aggregation, lookup)."""
    text = normalize_text(question)
    if "dashboard" in text or "tong quan" in text:
        return "dashboard"
    if any(term in text for term in ("trend", "daily", "ngay", "over time")):
        return "time_series"
    if any(term in text for term in ("top", "by ", "theo ")):
        return "aggregation"
    if asks_latency_root_cause(text):
        return "aggregation"
    if any(
        term in text
        for term in ("service", "source app", "source owner", "model cost")
    ):
        return "aggregation"
    return "lookup"


def requested_chart_type(question: str) -> str | None:
    text = normalize_text(question)
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


def window_days(text: str) -> int:
    match = re.search(r"(\d+)\s*(day|days|d|ngay)", text)
    if match:
        return max(1, min(int(match.group(1)), 365))
    if re.search(r"\b(24h|today|hom nay)\b", text):
        return 1
    if re.search(r"\b(7d|week|tuan)\b", text):
        return 7
    return 30


# Aliases for backwards compatibility with private function names
_normalize = normalize_text
_semantic_domain = semantic_domain
_asks_latency_root_cause = asks_latency_root_cause
_asks_agent_cost = asks_agent_cost
_asks_log_error_detail = asks_log_error_detail
_question_capabilities = question_capabilities
_semantic_domain_column_score = semantic_domain_column_score
_intent = infer_intent
_requested_chart_type = requested_chart_type
_window_days = window_days
