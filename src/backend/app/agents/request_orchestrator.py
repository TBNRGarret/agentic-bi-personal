from __future__ import annotations

import re
import unicodedata
from typing import Any


SLOT_ORDER = ("intent_type", "domain", "metric", "time_range", "layer")
OPTIONAL_SLOTS = ("dimensions", "coverage")


def build_request_plan(
    question: str,
    *,
    memory: dict[str, Any] | None = None,
    target: str = "auto",
    llm_slots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _normalize(question)
    memory = memory or {}
    slots = {
        "intent_type": _intent_slot(text),
        "domain": _domain_slot(text),
        "metric": _metric_slot(text),
        "time_range": _time_range_slot(text, memory),
        "layer": _layer_slot(text, target),
        "dimensions": _dimension_slot(text),
        "coverage": _coverage_slot(text),
    }
    _merge_llm_slots(slots, llm_slots or {})
    _reconcile_slots(slots, text)
    if slots["intent_type"].get("value") in {"dashboard", "full_report"}:
        if slots["domain"].get("value") in (None, "", []):
            slots["domain"] = _slot("all", 0.7, "dashboard_default")
        if slots["metric"].get("value") in (None, "", []):
            slots["metric"] = _slot(["health"], 0.7, "dashboard_default")
    clarification_flow = any(
        marker in text
        for marker in ("intent ", "domain ", "metric ", "time range ", "layer ")
    )
    if clarification_flow:
        for name in ("time_range", "layer"):
            if slots[name].get("source") == "default":
                slots[name] = _slot(None, 0.0, "missing")

    missing = [
        name
        for name in SLOT_ORDER
        if slots[name].get("value") in (None, "", [])
    ]
    confidences = [
        float(slot.get("confidence") or 0)
        for slot in slots.values()
        if slot.get("value") not in (None, "", [])
    ]
    return {
        "slots": slots,
        "slotOrder": list(SLOT_ORDER),
        "missingSlots": missing,
        "nextMissingSlot": missing[0] if missing else None,
        "confidence": round(sum(confidences) / len(confidences), 4)
        if confidences
        else 0.0,
        "strategy": (
            "llm_semantic_with_rule_fallback"
            if llm_slots
            else "semantic_rule_fallback"
        ),
        "clarificationFlow": clarification_flow,
    }


def clarification_for(plan: dict[str, Any]) -> dict[str, Any] | None:
    slot = plan.get("nextMissingSlot")
    if not slot:
        return None
    prompts = {
        "intent_type": {
            "question": "Bạn muốn xem theo cách nào?",
            "options": [
                _option("Tổng quan", "intent dashboard", "Nhiều góc nhìn trong một dashboard."),
                _option("Xu hướng", "intent time series", "Theo dõi thay đổi theo thời gian."),
                _option("So sánh", "intent aggregation", "So sánh theo service, model hoặc nguồn."),
                _option("Chi tiết", "intent lookup", "Xem các bản ghi hoặc kết quả cụ thể."),
            ],
        },
        "domain": {
            "question": "Bạn muốn phân tích domain nào?",
            "options": [
                _option("Observability", "domain observability", "Latency, lỗi, trace và service."),
                _option("LLM usage", "domain llm usage", "Token, model, generation và chi phí."),
                _option("Ingestion", "domain ingestion", "Batch, pipeline và dữ liệu nạp."),
                _option("Agent cost", "domain agent cost", "Chi phí truy vấn của Agentic BI."),
            ],
        },
        "metric": {
            "question": "Metric chính bạn muốn xem là gì?",
            "options": [
                _option("Latency", "metric latency", "Độ trễ trung bình hoặc p95."),
                _option("Errors", "metric errors", "Số lỗi hoặc tỷ lệ lỗi."),
                _option("Cost", "metric cost", "Chi phí theo model hoặc agent."),
                _option("Tokens", "metric tokens", "Prompt, completion hoặc total tokens."),
            ],
        },
        "time_range": {
            "question": "Bạn muốn xem trong khoảng thời gian nào?",
            "options": [
                _option("24 giờ", "time range 24h", "Dữ liệu gần nhất."),
                _option("7 ngày", "time range 7 days", "Xu hướng ngắn hạn."),
                _option("30 ngày", "time range 30 days", "Mặc định cho phân tích xu hướng."),
            ],
        },
        "layer": {
            "question": "Bạn muốn dùng lớp dữ liệu nào?",
            "options": [
                _option("Tự động", "layer auto", "Ưu tiên Gold rồi fallback xuống lớp chi tiết."),
                _option("Tổng hợp", "layer gold", "Dữ liệu Gold/Mart đã tổng hợp."),
                _option("Chi tiết", "layer silver", "Dữ liệu Silver theo event hoặc trace."),
            ],
        },
    }
    prompt = prompts[str(slot)]
    return {
        "tool": "request_orchestrator",
        "slot": slot,
        "question": prompt["question"],
        "reason": f"Missing required slot: {slot}",
        "options": prompt["options"],
    }


def route_confidence_level(confidence: float) -> str:
    if confidence >= 0.72:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"


def medium_route_note(table_name: str, confidence: float) -> str:
    return (
        f"Tạm định tuyến vào {table_name} với confidence {confidence:.2f}; "
        "kết quả có thể thay đổi nếu bạn làm rõ domain hoặc layer."
    )


def _slot(value: Any, confidence: float, source: str) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": round(confidence, 4),
        "source": source,
    }


def _intent_slot(text: str) -> dict[str, Any]:
    groups = (
        (
            "full_report",
            ("full report", "bao cao day du", "báo cáo đầy đủ"),
        ),
        ("dashboard", ("dashboard", "tong quan", "overview", "intent dashboard")),
        (
            "time_series",
            (
                "trend",
                "xu huong",
                "daily",
                "theo ngay",
                "over time",
                "tang hay giam",
                "bieu do duong",
                "line chart",
                "time series",
                "intent time series",
            ),
        ),
        (
            "aggregation",
            (
                "compare",
                "so sanh",
                "top ",
                " theo service",
                " theo model",
                " theo source",
                "nguon goc",
                "nguyen nhan",
                "root cause",
                "phan tich",
                "analyze",
                "bieu do tron",
                "pie chart",
                "vi sao",
                "tai sao",
                "why",
                "aggregation",
                "intent aggregation",
            ),
        ),
        (
            "lookup",
            (
                "show",
                "xem",
                "liet ke",
                "chi tiet",
                "detail",
                "which",
                "what",
                "where",
                " nao",
                "bao nhieu",
                "co ",
                "lookup",
                "intent lookup",
            ),
        ),
    )
    for value, terms in groups:
        if any(term in text for term in terms):
            return _slot(value, 0.86, "semantic_rules")
    return _slot(None, 0.0, "missing")


def _domain_slot(text: str) -> dict[str, Any]:
    request_terms = ("request", "requests", "so request", "query count")
    agent_terms = ("agent", "chat", "query audit", "agent audit", "platform")
    llm_terms = ("llm", "model", "token", "generation", "provider", "completion")
    trace_terms = ("trace", "span", "observability", "monitoring", "telemetry")
    if any(term in text for term in request_terms):
        if any(term in text for term in agent_terms):
            return _slot("agent_cost", 0.88, "semantic_rules")
        if any(term in text for term in llm_terms):
            return _slot("llm_usage", 0.88, "semantic_rules")
        if any(term in text for term in trace_terms):
            return _slot("observability", 0.88, "semantic_rules")
        return _slot(None, 0.0, "ambiguous_request_domain")
    groups = (
        (
            "agent_cost",
            (
                "agent cost",
                "query cost",
                "chi phi truy van",
                "agentic bi cost",
                "agent request",
                "chat request",
            ),
        ),
        (
            "llm_usage",
            ("llm", "model", "token", "generation", "provider", "completion", "cost", "price", "usd", "money", "chi phi"),
        ),
        (
            "observability",
            (
                "latency",
                "do tre",
                "cham",
                "slow",
                "error",
                "loi",
                "service",
                "trace",
                "span",
                "log",
                "observability",
                "observe",
                "monitoring",
                "telemetry",
            ),
        ),
        (
            "ingestion",
            ("ingestion", "pipeline", "batch", "etl", "loaded", "quarantine"),
        ),
        (
            "agent_operations",
            ("query audit", "agent audit", "dbt run", "request audit"),
        ),
        ("data_quality", ("data quality", "dbt test", "chat luong du lieu")),
    )
    for value, terms in groups:
        if any(term in text for term in terms):
            return _slot(value, 0.88, "semantic_rules")
    return _slot(None, 0.0, "missing")


def _metric_slot(text: str) -> dict[str, Any]:
    mappings = (
        ("latency", ("latency", "do tre", "cham", "slow", "duration", "p95")),
        ("error", ("error", "loi", "failed", "failure")),
        ("cost", ("cost", "chi phi", "spend", "usd")),
        ("tokens", ("token", "usage")),
        ("request_count", ("request", "requests", "so request", "query count")),
        ("throughput", ("throughput", "loaded row")),
        ("health", ("health", "suc khoe")),
        ("trace_count", ("trace count", "so trace")),
    )
    values = [
        value
        for value, terms in mappings
        if any(term in text for term in terms)
    ]
    return _slot(values, 0.9, "semantic_rules") if values else _slot(None, 0.0, "missing")


def _dimension_slot(text: str) -> dict[str, Any]:
    dimensions: list[str] = []
    mappings = (
        (
            "time",
            (
                "theo ngay",
                "tung ngay",
                "moi ngay",
                "hang ngay",
                "per day",
                "each day",
                "daily",
                "by day",
                "over time",
                "trend",
                "theo gio",
                "tung gio",
                "hourly",
                "by hour",
            ),
        ),
        ("model", ("theo model", "tung model", "moi model", "by model", "per model", "each model", "models")),
        (
            "source_app",
            (
                "source app",
                "source_app",
                "source app id",
                "source_app_id",
                "theo source",
                "tung source",
                "by source",
                "per source",
                "each source",
            ),
        ),
        ("service_name", ("theo service", "tung service", "by service", "per service", "each service")),
        ("provider", ("theo provider", "tung provider", "by provider", "per provider")),
        ("environment", ("theo environment", "tung environment", "by environment", "per environment")),
        ("status", ("theo status", "tung status", "by status", "per status")),
        ("user", ("theo user", "tung user", "by user", "per user")),
    )
    for dimension, terms in mappings:
        if any(term in text for term in terms):
            dimensions.append(dimension)
    return (
        _slot(dimensions, 0.92, "semantic_rules")
        if dimensions
        else _slot([], 0.0, "not_requested")
    )


def _coverage_slot(text: str) -> dict[str, Any]:
    dimensions = _dimension_slot(text).get("value") or []
    coverage: list[str] = []
    global_all = any(
        phrase in text
        for phrase in (
            "khong bo sot",
            "khong thieu",
            "day du tat ca",
            "toan bo",
            "without missing",
            "include all",
            "do not omit",
        )
    )
    dimension_terms = {
        "model": ("model", "models"),
        "source_app": ("source", "source app", "sources"),
        "service_name": ("service", "services"),
        "provider": ("provider", "providers"),
        "environment": ("environment", "environments"),
        "status": ("status", "statuses"),
        "user": ("user", "users"),
    }
    specific_coverage = {
        dimension
        for dimension in dimensions
        for term in dimension_terms.get(dimension, ())
        if any(
            phrase in text
            for phrase in (
                f"khong bo sot {term}",
                f"khong thieu {term}",
                f"day du {term}",
                f"include all {term}",
                f"do not omit {term}",
                f"without missing {term}",
            )
        )
    }
    for dimension in dimensions:
        terms = dimension_terms.get(dimension, ())
        dimension_all = any(
            phrase in text
            for term in terms
            for phrase in (
                f"tat ca {term}",
                f"moi {term}",
                f"all {term}",
                f"every {term}",
            )
        )
        if dimension == "time":
            continue
        if dimension in specific_coverage or dimension_all:
            coverage.append(dimension)
        elif global_all and not specific_coverage and any(term in text for term in terms):
            coverage.append(dimension)
    if global_all and not coverage:
        coverage = [dimension for dimension in dimensions if dimension != "time"]
    return (
        _slot(coverage, 0.95, "semantic_rules")
        if coverage
        else _slot([], 0.0, "not_requested")
    )


def _time_range_slot(text: str, memory: dict[str, Any]) -> dict[str, Any]:
    match = re.search(r"\b(\d+)\s*(h|hour|hours|d|day|days|week|weeks|ngay|tuan)\b", text)
    if match:
        unit = match.group(2)
        normalized = (
            "h"
            if unit in {"h", "hour", "hours"}
            else "d"
            if unit in {"d", "day", "days", "ngay"}
            else "w"
        )
        return _slot(f"{match.group(1)}{normalized}", 0.95, "explicit")
    if any(term in text for term in ("today", "hom nay", "24h")):
        return _slot("24h", 0.95, "explicit")
    if any(term in text for term in ("last week", "tuan truoc")):
        return _slot("previous_week", 0.95, "explicit")
    if memory.get("timeframe"):
        return _slot(str(memory["timeframe"]), 0.78, "memory")
    return _slot("30d", 0.55, "default")


def _layer_slot(text: str, target: str) -> dict[str, Any]:
    if any(term in text for term in ("raw", "du lieu tho")):
        return _slot("silver", 0.75, "raw_disabled")
    if any(term in text for term in ("silver", "staging", "event detail", "trace detail")):
        return _slot("silver", 0.94, "explicit")
    if any(term in text for term in ("gold", "mart", "tong hop", "aggregate")):
        return _slot("gold", 0.94, "explicit")
    if target.lower() in {"dwh", "postgres", "postgresql"}:
        return _slot("auto", 0.7, "target")
    return _slot("auto", 0.55, "default")


def _merge_llm_slots(
    slots: dict[str, dict[str, Any]], llm_slots: dict[str, Any]
) -> None:
    raw_slots = llm_slots.get("slots")
    if not isinstance(raw_slots, dict):
        raw_slots = llm_slots
    for name in (*SLOT_ORDER, *OPTIONAL_SLOTS):
        candidate = raw_slots.get(name) if isinstance(raw_slots, dict) else None
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("value")
        confidence = float(candidate.get("confidence") or 0)
        if name in {"dimensions", "coverage"}:
            value = _canonical_dimension_list(value)
        current = slots[name]
        conflicting_explicit_rule = (
            name == "domain"
            and current.get("source") == "semantic_rules"
            and current.get("value") not in (None, "", [])
            and value != current.get("value")
            and confidence < float(current.get("confidence") or 0) + 0.15
        )
        if conflicting_explicit_rule:
            continue
        if value not in (None, "", []) and (
            current.get("value") in (None, "", [])
            or confidence > float(current.get("confidence") or 0)
        ):
            slots[name] = _slot(value, confidence, "llm_semantic")


def _reconcile_slots(slots: dict[str, dict[str, Any]], text: str) -> None:
    dimensions = _canonical_dimension_list(slots["dimensions"].get("value"))
    coverage = [
        dimension
        for dimension in _canonical_dimension_list(slots["coverage"].get("value"))
        if dimension in dimensions
    ]
    slots["dimensions"]["value"] = dimensions
    slots["coverage"]["value"] = coverage
    if any(term in text for term in ("dashboard", "tong quan", "overview", "intent dashboard")):
        slots["intent_type"] = _slot("dashboard", 0.96, "semantic_rules")
    intent = str(slots["intent_type"].get("value") or "")
    explicitly_non_temporal_chart = any(
        term in text
        for term in ("pie chart", "bieu do tron", "donut", "bar chart", "bieu do cot")
    )
    if "time" in dimensions and intent in {"aggregation", "lookup", ""} and not explicitly_non_temporal_chart:
        slots["intent_type"] = _slot("time_series", 0.94, "semantic_shape")


def _canonical_dimension_list(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    aliases = {
        "date": "time",
        "day": "time",
        "time_range": "time",
        "metric_date": "time",
        "event_time": "time",
        "source": "source_app",
        "source_app_id": "source_app",
        "app": "source_app",
        "service": "service_name",
        "user_id": "user",
        "username": "user",
    }
    allowed = {
        "time",
        "model",
        "source_app",
        "service_name",
        "provider",
        "environment",
        "status",
        "user",
    }
    dimensions: list[str] = []
    for raw in raw_values:
        normalized = str(raw or "").strip().lower().replace(" ", "_")
        dimension = aliases.get(normalized, normalized)
        if dimension in allowed and dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def _option(label: str, suffix: str, description: str) -> dict[str, str]:
    return {
        "label": label,
        "value": suffix,
        "questionSuffix": suffix,
        "description": description,
    }


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    ascii_value = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", ascii_value.replace("đ", "d")).strip()
