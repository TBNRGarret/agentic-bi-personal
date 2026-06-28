from __future__ import annotations

from typing import Any

from ...services.llm import LlmService
from ...services.llm_prompts import SQL_GENERATION_PROMPT_NOTE
from .complexity_assessor import ComplexityAssessor
from .contracts import DecompositionPlan, SelectorResult


class DecomposerAgent:
    """MAC-SQL-style decomposition with hybrid rule-based + LLM gating and expansion."""

    """
    Overview
    1. Natural language question
    2. Query planer
        a. If simple question -> direct sql -> generate sql -> execute sql
        b. If complex question -> decompose into sub-questions -> solve step-by-step ->  Optimized SQL Plan (top 2 sql candidates) -> orchestration

    
    Split a complex BI question into sub-questions and solve them step-by-step 
    1. Determistic planing
    2. ComplexityAssessor.assess_hybrid(): evaluate the complexity of the question and decide whether to decompose or not
        a. if it is simple -> simple plan -> create sub-questions by rule-based method -> check deterministic audit plan again -> use llm to generate sql -> refine sql -> return 1 candidate sql 
        b. if it is complex -> decomposed plan -> create many sub-questions by llm -> solve each question step-by-step -> merge all results -> create 2 sql candidates -> use orchestrator to try 2 options.
    """
    def __init__(self, complexity_assessor: ComplexityAssessor | None = None) -> None:
        self.complexity_assessor = complexity_assessor or ComplexityAssessor()

    async def plan(
        self,
        question: str,
        *,
        selector: SelectorResult,
        datasource: str,
        limit: int,
        llm: LlmService,
        conversation: list[dict[str, Any]] | None,
        memory: dict[str, Any] | None,
        request_plan: dict[str, Any],
        join_paths: list[dict[str, Any]] | None = None,
        join_plan: dict[str, Any] | None = None,
    ) -> DecompositionPlan:
        join_paths = join_paths or []
        join_plan = join_plan or {}
        audit_complexity = {
            "level": "simple",
            "score": 0.0,
            "signals": ["deterministic DWH audit detail query"],
            "recommendedSteps": 1,
            "strategy": "deterministic_short_circuit",
        }
        audit_sub_questions = _rule_based_sub_questions(
            question,
            selector,
            join_paths=join_paths,
            join_plan=join_plan,
            complexity=audit_complexity,
        )
        audit_request_plan = _enriched_request_plan(
            request_plan,
            selector=selector,
            join_paths=join_paths,
            join_plan=join_plan,
            complexity=audit_complexity,
            sub_questions=audit_sub_questions,
            strategy="dwh_agent_query_audit_detail",
        )
        audit_plan = _deterministic_audit_plan(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            request_plan=audit_request_plan,
            sub_questions=audit_sub_questions,
            complexity=audit_complexity,
            strategy="dwh_agent_query_audit_detail",
        )
        if audit_plan:
            return audit_plan
        complexity = await self.complexity_assessor.assess_hybrid(
            question,
            selector=selector,
            join_paths=join_paths,
            request_plan=request_plan,
            llm=llm,
        )
        if complexity.level == "simple" or not llm.settings.llm_configured:
            return await self._simple_plan(
                question,
                selector=selector,
                datasource=datasource,
                limit=limit,
                llm=llm,
                conversation=conversation,
                memory=memory,
                request_plan=request_plan,
                join_paths=join_paths,
                join_plan=join_plan,
                complexity=complexity.to_dict(),
            )
        return await self._decomposed_plan(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            llm=llm,
            conversation=conversation,
            memory=memory,
            request_plan=request_plan,
            join_paths=join_paths,
            join_plan=join_plan,
            complexity=complexity.to_dict(),
        )

    async def _simple_plan(
        self,
        question: str,
        *,
        selector: SelectorResult,
        datasource: str,
        limit: int,
        llm: LlmService,
        conversation: list[dict[str, Any]] | None,
        memory: dict[str, Any] | None,
        request_plan: dict[str, Any],
        join_paths: list[dict[str, Any]],
        join_plan: dict[str, Any],
        complexity: dict[str, Any],
    ) -> DecompositionPlan:
        sub_questions = _rule_based_sub_questions(
            question,
            selector,
            join_paths=join_paths,
            join_plan=join_plan,
            complexity=complexity,
        )
        enriched_plan = _enriched_request_plan(
            request_plan,
            selector=selector,
            join_paths=join_paths,
            join_plan=join_plan,
            complexity=complexity,
            sub_questions=sub_questions,
            strategy="simple_mac_sql_solver",
        )
        audit_plan = _deterministic_audit_plan(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            request_plan=enriched_plan,
            sub_questions=sub_questions,
            complexity=complexity,
            strategy="dwh_agent_query_audit_detail",
        )
        if audit_plan:
            return audit_plan
        if selector.selected_tables and llm.settings.llm_configured:
            solver_res = await llm.solve_sub_question(
                sub_question=sub_questions[-1] if sub_questions else {"question": question},
                question=question,
                tables=selector.selected_tables,
                datasource=datasource,
                limit=limit,
                request_plan=enriched_plan,
            )
            raw = await llm.refine_decomposed_sql(
                question=question,
                accumulated_sql=str(solver_res.get("sql") or ""),
                tables=selector.selected_tables,
                datasource=datasource,
                limit=limit,
                conversation=conversation,
                memory=memory,
                candidate_count=2,
                request_plan=enriched_plan,
            )
            if raw.get("status") == "success":
                candidates = _candidates(raw)
                sql = str(raw.get("sql") or "")
                if not sql and candidates:
                    sql = str(candidates[0].get("sql") or "")
                return DecompositionPlan(
                    status="success",
                    sql=sql,
                    candidates=candidates,
                    sub_questions=sub_questions,
                    selected_tables=selector.selected_table_names,
                    visualization=raw.get("visualization") or {},
                    confidence=0.78,
                    rationale=str(raw.get("rationale") or "MAC-SQL simple solver-refiner over Model 2 selected schema"),
                    source="llm_model2_mac_sql_simple",
                    raw_plan=raw,
                    complexity=complexity,
                    intermediate_results=_planned_intermediate_results(sub_questions),
                    decomposition_strategy="simple_mac_sql_solver",
                    complexity_strategy=complexity.get("strategy") or "rule_based",
                    hybrid_signals=complexity.get("signals") or [],
                    chain_of_thought=sub_questions,
                )
        return _fallback_plan(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            request_plan=enriched_plan,
            sub_questions=sub_questions,
            complexity=complexity,
            strategy="simple_mac_sql_deterministic_fallback",
            join_plan=join_plan,
        )

    async def _decomposed_plan(
        self,
        question: str,
        *,
        selector: SelectorResult,
        datasource: str,
        limit: int,
        llm: LlmService,
        conversation: list[dict[str, Any]] | None,
        memory: dict[str, Any] | None,
        request_plan: dict[str, Any],
        join_paths: list[dict[str, Any]],
        join_plan: dict[str, Any],
        complexity: dict[str, Any],
    ) -> DecompositionPlan:
        decomposition = await llm.decompose_sql_question(
            question=question,
            tables=selector.selected_tables,
            datasource=datasource,
            request_plan=request_plan,
            complexity=complexity,
            join_paths=join_paths,
            max_steps=int(complexity.get("recommendedSteps") or 3),
        )
        if decomposition.get("status") == "success":
            sub_questions = list(decomposition.get("subQuestions") or [])
            strategy = "mac_sql_decomposed"
        else:
            sub_questions = _rule_based_sub_questions(
                question,
                selector,
                join_paths=join_paths,
                join_plan=join_plan,
                complexity=complexity,
            )
            strategy = "rule_based_mac_sql_fallback"

        intermediate_results = _planned_intermediate_results(sub_questions)
        enriched_plan = _enriched_request_plan(
            request_plan,
            selector=selector,
            join_paths=join_paths,
            join_plan=join_plan,
            complexity=complexity,
            sub_questions=sub_questions,
            strategy=strategy,
        )

        audit_plan = _deterministic_audit_plan(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            request_plan=enriched_plan,
            sub_questions=sub_questions,
            complexity=complexity,
            strategy="dwh_agent_query_audit_detail",
        )
        if audit_plan:
            return audit_plan

        previous_sql = ""
        for sq in sub_questions:
            if selector.selected_tables and llm.settings.llm_configured:
                step_res = await llm.solve_sub_question(
                    sub_question=sq,
                    question=question,
                    tables=selector.selected_tables,
                    datasource=datasource,
                    limit=limit,
                    previous_sql=previous_sql,
                    request_plan=enriched_plan,
                )
                if step_res.get("status") == "success" and step_res.get("sql"):
                    previous_sql = str(step_res.get("sql") or "")

        raw = await llm.refine_decomposed_sql(
            question=question,
            accumulated_sql=previous_sql,
            tables=selector.selected_tables,
            datasource=datasource,
            limit=limit,
            conversation=conversation,
            memory=memory,
            candidate_count=2,
            request_plan=enriched_plan,
        )
        if raw.get("status") == "success":
            candidates = _candidates(raw)
            sql = str(raw.get("sql") or "")
            if not sql and candidates:
                sql = str(candidates[0].get("sql") or "")
            return DecompositionPlan(
                status="success",
                sql=sql,
                candidates=candidates,
                sub_questions=sub_questions,
                selected_tables=selector.selected_table_names,
                visualization=raw.get("visualization") or {},
                confidence=0.82 if strategy == "mac_sql_decomposed" else 0.62,
                rationale=str(raw.get("rationale") or "Model 2 MAC-SQL decomposed step-by-step plan"),
                source="llm_model2_mac_sql_decomposer",
                raw_plan={"decomposition": decomposition, "sqlPlan": raw},
                complexity=complexity,
                intermediate_results=intermediate_results,
                decomposition_strategy=strategy,
                complexity_strategy=complexity.get("strategy") or "rule_based",
                hybrid_signals=complexity.get("signals") or [],
                chain_of_thought=sub_questions,
            )
        return _fallback_plan(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            request_plan=enriched_plan,
            sub_questions=sub_questions,
            complexity=complexity,
            strategy=f"{strategy}_deterministic_sql_fallback",
            raw_plan={"decomposition": decomposition, "sqlPlan": raw},
            join_plan=join_plan,
        )


def _deterministic_audit_plan(
    question: str,
    *,
    selector: SelectorResult,
    datasource: str,
    limit: int,
    request_plan: dict[str, Any],
    sub_questions: list[dict[str, Any]],
    complexity: dict[str, Any],
    strategy: str,
) -> DecompositionPlan | None:
    sql = _dwh_agent_query_audit_sql(
        question,
        selector=selector,
        datasource=datasource,
        limit=limit,
    )
    if not sql:
        return None
    return DecompositionPlan(
        status="success",
        sql=sql,
        candidates=[
            {
                "sql": sql,
                "rationale": "deterministic DWH audit detail SQL for expensive agent queries",
            }
        ],
        sub_questions=sub_questions,
        selected_tables=selector.selected_table_names,
        confidence=0.84,
        rationale=(
            "Deterministic Postgres audit query over ops.agent_query_audit; "
            "orders by estimated cost and total tokens without aggregating away trace/query detail."
        ),
        source="deterministic_model2_dwh_audit",
        raw_plan={"status": "deterministic", "requestPlan": request_plan},
        complexity=complexity,
        intermediate_results=_planned_intermediate_results(sub_questions),
        decomposition_strategy=strategy,
        complexity_strategy=complexity.get("strategy") or "rule_based",
        hybrid_signals=complexity.get("signals") or [],
        chain_of_thought=sub_questions,
    )



def _fallback_plan(
    question: str,
    *,
    selector: SelectorResult,
    datasource: str,
    limit: int,
    request_plan: dict[str, Any],
    sub_questions: list[dict[str, Any]],
    complexity: dict[str, Any],
    strategy: str,
    raw_plan: dict[str, Any] | None = None,
    join_plan: dict[str, Any] | None = None,
) -> DecompositionPlan:
    sql = ""
    if selector.selected_tables:
        sql = _dwh_agent_query_audit_sql(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
        )
    if selector.selected_tables and not sql and join_plan and len(selector.selected_tables) > 1:
        sql = _join_aware_fallback_sql(
            question,
            selector=selector,
            datasource=datasource,
            limit=limit,
            request_plan=request_plan,
            join_plan=join_plan,
        )
    if selector.selected_tables and not sql:
        from ..query_decomposer import compose_sql, decompose_query_intent

        table = selector.selected_tables[0]
        name = str(table.get("tableName") or "")
        columns = {str(column.get("name")) for column in table.get("columns") or [] if column.get("name")}
        intent = decompose_query_intent(question, columns, request_plan)
        sql = compose_sql(name, columns, intent, limit=limit)
    return DecompositionPlan(
        status="fallback" if sql else "failed",
        sql=sql,
        candidates=[{"sql": sql, "rationale": "deterministic decompose-to-solve fallback"}] if sql else [],
        sub_questions=sub_questions,
        selected_tables=selector.selected_table_names,
        confidence=0.45 if sql else 0.0,
        rationale="Deterministic decompose-to-solve fallback from query intent and selected table metadata",
        source="deterministic_model2_fallback",
        raw_plan=raw_plan or {"status": "fallback"},
        complexity=complexity,
        intermediate_results=_planned_intermediate_results(sub_questions),
        decomposition_strategy=strategy,
        complexity_strategy=complexity.get("strategy") or "rule_based",
        hybrid_signals=complexity.get("signals") or [],
        chain_of_thought=sub_questions,
    )



def _dwh_agent_query_audit_sql(
    question: str,
    *,
    selector: SelectorResult,
    datasource: str,
    limit: int,
) -> str:
    if datasource != "dwh":
        return ""
    table = next(
        (
            item
            for item in selector.selected_tables
            if str(item.get("tableName") or "") == "ops.agent_query_audit"
        ),
        None,
    )
    if not table:
        return ""
    columns = {
        str(column.get("name"))
        for column in table.get("columns") or []
        if column.get("name")
    }
    text = _normalized(question)
    if not _asks_expensive_agent_queries(text, columns):
        return ""
    return _expensive_agent_queries_sql(
        str(table.get("tableName") or "ops.agent_query_audit"),
        columns,
        limit,
        _window_days(text),
    )


def _asks_expensive_agent_queries(text: str, columns: set[str]) -> bool:
    has_audit_shape = {"query_id", "question", "token_usage", "created_at"} <= columns
    if not has_audit_shape:
        return False
    cost_terms = ("expensive", "cost", "costly", "spend", "usd", "ton", "dat")
    query_terms = (
        "agent query",
        "agent queries",
        "agent request",
        "agent requests",
        "query audit",
        "queries",
        "truy van",
    )
    return any(term in text for term in cost_terms) and any(
        term in text for term in query_terms
    )


def _expensive_agent_queries_sql(
    table_name: str,
    columns: set[str],
    limit: int,
    days: int,
) -> str:
    trace_expr = "NULL::text AS trace_id"
    span_expr = "NULL::text AS span_id"
    if "metadata" in columns:
        trace_expr = (
            "COALESCE(metadata->>'trace_id', metadata->>'otel_trace_id', "
            "metadata->>'langfuse_trace_id') AS trace_id"
        )
        span_expr = "COALESCE(metadata->>'span_id', metadata->>'otel_span_id') AS span_id"
    model_expr = "NULL::text AS model"
    total_tokens_expr = "0::bigint AS total_tokens"
    cost_expr = "0::numeric AS estimated_cost_usd"
    if "token_usage" in columns:
        model_expr = "NULLIF(token_usage->>'model', '') AS model"
        total_tokens_expr = "COALESCE((token_usage->>'totalTokens')::bigint, 0) AS total_tokens"
        cost_expr = (
            "round(COALESCE((token_usage->>'estimatedCostUsd')::numeric, 0), 8) "
            "AS estimated_cost_usd"
        )
    projection = [
        "query_id",
        trace_expr,
        span_expr,
        "created_at",
        "username" if "username" in columns else "NULL::text AS username",
        "endpoint" if "endpoint" in columns else "NULL::text AS endpoint",
        "status" if "status" in columns else "NULL::text AS status",
        model_expr,
        total_tokens_expr,
        cost_expr,
        "execution_time_ms"
        if "execution_time_ms" in columns
        else "NULL::bigint AS execution_time_ms",
        "left(question, 240) AS question"
        if "question" in columns
        else "NULL::text AS question",
        "left(generated_sql, 500) AS generated_sql"
        if "generated_sql" in columns
        else "NULL::text AS generated_sql",
    ]
    where = [f"created_at >= CURRENT_DATE - {max(1, min(days, 365))}"]
    if "token_usage" in columns:
        where.append("token_usage <> '{}'::jsonb")
    select_list = ", ".join(projection)
    where_clause = " AND ".join(where)
    return (
        f"SELECT {select_list} FROM {table_name} "
        f"WHERE {where_clause} "
        "ORDER BY estimated_cost_usd DESC, total_tokens DESC, created_at DESC "
        f"LIMIT {min(limit, 100)}"
    )


def _window_days(text: str) -> int:
    import re

    match = re.search(r"(\d+)\s*(day|days|d|ngay)", text)
    if match:
        return max(1, min(int(match.group(1)), 365))
    if "today" in text or "hom nay" in text or "24h" in text:
        return 1
    if "week" in text or "tuan" in text or "7d" in text:
        return 7
    return 30


def _join_aware_fallback_sql(
    question: str,
    *,
    selector: SelectorResult,
    datasource: str,
    limit: int,
    request_plan: dict[str, Any],
    join_plan: dict[str, Any],
) -> str:
    paths = join_plan.get("selectedPaths") or []
    if not paths:
        return ""
    path = paths[0]
    steps = path.get("joinSteps") or []
    if not steps:
        return ""
    tables_by_name = {str(table.get("tableName") or ""): table for table in selector.selected_tables}
    aliases: dict[str, str] = {}

    def alias(name: str) -> str:
        if name not in aliases:
            aliases[name] = f"t{len(aliases) + 1}"
        return aliases[name]

    ordered_tables = [str(table) for table in path.get("tables") or [] if str(table)]
    if not ordered_tables:
        ordered_tables = [selector.selected_table_names[0]]
    root = ordered_tables[0]
    alias(root)
    join_clauses: list[str] = []
    for step in steps:
        left = str(step.get("leftTable") or "")
        right = str(step.get("rightTable") or "")
        columns = [str(column) for column in step.get("columns") or [] if str(column)]
        if not left or not right or not columns:
            return ""
        left_alias = alias(left)
        right_alias = alias(right)
        conditions = [f"{left_alias}.{column} = {right_alias}.{column}" for column in columns]
        join_clauses.append(f"JOIN {right} AS {right_alias} ON {' AND '.join(conditions)}")

    metric = _join_metric_expression(question, tables_by_name, aliases)
    dimensions = _join_dimension_expressions(question, tables_by_name, aliases)
    select_exprs = dimensions + [metric[0]]
    group_by = [expr.split(" AS ")[0] for expr in dimensions]
    where = _join_time_filter(question, tables_by_name, aliases, datasource)
    sql = f"SELECT {', '.join(select_exprs)} FROM {root} AS {aliases[root]} " + " ".join(join_clauses)
    if where:
        sql += f" WHERE {where}"
    if group_by:
        sql += f" GROUP BY {', '.join(group_by)} ORDER BY {metric[1]} DESC"
    else:
        sql += f" ORDER BY {metric[1]} DESC"
    sql += f" LIMIT {min(limit, 1000)}"
    return sql


def _join_metric_expression(
    question: str,
    tables_by_name: dict[str, dict[str, Any]],
    aliases: dict[str, str],
) -> tuple[str, str]:
    text = _normalized(question)
    metric_candidates = (
        ("cost", ("total_cost_usd", "estimated_cost_usd", "cost_usd"), "total_cost_usd"),
        ("revenue", ("revenue", "total_revenue", "total_amount", "amount"), "total_revenue"),
        ("token", ("total_tokens", "input_tokens", "output_tokens"), "total_tokens"),
        ("latency", ("latency_ms", "avg_latency_ms", "p95_latency_ms"), "avg_latency_ms"),
    )
    for signal, columns, alias_name in metric_candidates:
        if signal not in text and not (signal == "revenue" and "doanh thu" in text):
            continue
        located = _find_column(columns, tables_by_name, aliases)
        if located:
            table_alias, column = located
            return f"round(sum({table_alias}.{column}), 6) AS {alias_name}", alias_name
    located = _find_column(("request_count", "query_count", "id"), tables_by_name, aliases)
    if located and located[1] != "id":
        table_alias, column = located
        return f"sum({table_alias}.{column}) AS request_count", "request_count"
    return "count(*) AS row_count", "row_count"


def _join_dimension_expressions(
    question: str,
    tables_by_name: dict[str, dict[str, Any]],
    aliases: dict[str, str],
) -> list[str]:
    text = _normalized(question)
    candidates: list[tuple[str, tuple[str, ...]]] = []
    if "publisher" in text:
        candidates.append(("publisher", ("publisher_name", "publisher", "publisher_id")))
    if "source app" in text or "source_app" in text or "app" in text:
        candidates.append(("source_app", ("source_app", "source_app_id")))
    if "model" in text:
        candidates.append(("model", ("model",)))
    if "provider" in text:
        candidates.append(("provider", ("provider",)))
    dimensions: list[str] = []
    seen_aliases: set[str] = set()
    for output_name, columns in candidates:
        located = _find_column(columns, tables_by_name, aliases)
        if not located:
            continue
        table_alias, column = located
        if output_name in seen_aliases:
            continue
        seen_aliases.add(output_name)
        dimensions.append(f"{table_alias}.{column} AS {output_name}")
    return dimensions[:4]


def _join_time_filter(
    question: str,
    tables_by_name: dict[str, dict[str, Any]],
    aliases: dict[str, str],
    datasource: str,
) -> str:
    text = _normalized(question)
    days = 30
    if "7" in text or "week" in text or "tuan" in text:
        days = 7
    located = _find_column(("date", "metric_date", "created_at", "event_time", "started_at"), tables_by_name, aliases)
    if not located:
        return ""
    table_alias, column = located
    if datasource == "dwh":
        return f"{table_alias}.{column} >= CURRENT_DATE - {days}"
    if column in {"created_at", "event_time", "started_at"}:
        return f"{table_alias}.{column} >= now() - INTERVAL {days} DAY"
    return f"{table_alias}.{column} >= today() - {days}"


def _find_column(
    candidates: tuple[str, ...],
    tables_by_name: dict[str, dict[str, Any]],
    aliases: dict[str, str],
) -> tuple[str, str] | None:
    table_columns = {
        table_name_value: {str(column.get("name") or "") for column in table.get("columns") or []}
        for table_name_value, table in tables_by_name.items()
        if aliases.get(table_name_value)
    }
    for candidate in candidates:
        for table_name_value, columns in table_columns.items():
            if candidate in columns:
                return aliases[table_name_value], candidate
    return None


def _normalized(value: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", value.lower())
    return (
        "".join(character for character in text if not unicodedata.combining(character))
        .replace("đ", "d")
        .replace("Đ", "D")
        .replace("_", " ")
    )


def _enriched_request_plan(
    request_plan: dict[str, Any],
    *,
    selector: SelectorResult,
    join_paths: list[dict[str, Any]],
    join_plan: dict[str, Any],
    complexity: dict[str, Any],
    sub_questions: list[dict[str, Any]],
    strategy: str,
) -> dict[str, Any]:
    return {
        **request_plan,
        "model2SchemaPrompt": selector.schema_prompt,
        "model2Selector": selector.to_dict(),
        "model2JoinPaths": join_paths,
        "model2JoinPlan": join_plan,
        "model2JoinHintBlock": join_plan.get("hintBlock") if isinstance(join_plan, dict) else "",
        "model2JoinValidation": (join_plan.get("validation") or {}) if isinstance(join_plan, dict) else {},
        "model2Complexity": complexity,
        "model2SubQuestions": sub_questions,
        "model2DecompositionStrategy": strategy,
        "model2SqlGenerationNote": SQL_GENERATION_PROMPT_NOTE,
    }


def _candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(sql_value: Any, rationale: Any = "") -> None:
        sql = str(sql_value or "").strip()
        normalized = " ".join(sql.lower().split())
        if not sql or normalized in seen:
            return
        seen.add(normalized)
        candidates.append({"sql": sql, "rationale": str(rationale or "")})

    add(raw.get("sql"), raw.get("rationale"))
    for item in raw.get("candidates") or []:
        if isinstance(item, dict):
            add(item.get("sql"), item.get("rationale"))
        else:
            add(item)
    return candidates


def _rule_based_sub_questions(
    question: str,
    selector: SelectorResult,
    *,
    join_paths: list[dict[str, Any]],
    join_plan: dict[str, Any] | None = None,
    complexity: dict[str, Any],
) -> list[dict[str, Any]]:
    tables = ", ".join(selector.selected_table_names) or "selected tables"
    join_evidence = _join_path_evidence(join_paths)
    steps = [
        {
            "id": "sq1",
            "question": "Identify the relevant governed BI tables and columns.",
            "dependsOn": [],
            "expectedOutput": tables,
            "tables": selector.selected_table_names,
            "joinPath": [],
            "evidence": tables,
        }
    ]
    next_id = 2
    if join_evidence:
        steps.append(
            {
                "id": f"sq{next_id}",
                "question": "Connect selected tables using graph join paths.",
                "dependsOn": ["sq1"],
                "expectedOutput": join_evidence,
                "tables": selector.selected_table_names,
                "joinPath": (join_paths[0].get("tables") or []) if join_paths else [],
                "joinPlan": (join_plan or {}).get("selectedPaths", [])[:1],
                "evidence": join_evidence,
            }
        )
        next_id += 1
    if complexity.get("level") in {"moderate", "complex"}:
        steps.append(
            {
                "id": f"sq{next_id}",
                "question": "Apply requested metrics, dimensions, ranking, or comparisons.",
                "dependsOn": [steps[-1]["id"]],
                "expectedOutput": "aggregated intermediate result",
                "tables": selector.selected_table_names,
                "joinPath": [],
                "evidence": ", ".join(complexity.get("signals") or []),
            }
        )
        next_id += 1
    steps.append(
        {
            "id": f"sq{next_id}",
            "question": "Translate the request into one read-only SQL statement.",
            "dependsOn": [steps[-1]["id"]],
            "expectedOutput": "final SQL result shape",
            "tables": selector.selected_table_names,
            "joinPath": [],
            "evidence": question,
        }
    )
    return steps[: max(2, min(int(complexity.get("recommendedSteps") or 3), 4))]


def _planned_intermediate_results(
    sub_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.get("id") or f"sq{index}"),
            "status": "planned",
            "question": str(item.get("question") or ""),
            "expectedOutput": str(item.get("expectedOutput") or item.get("expected_output") or ""),
            "dependsOn": item.get("dependsOn") or item.get("depends_on") or [],
            "tables": item.get("tables") or [],
            "joinPath": item.get("joinPath") or item.get("join_path") or [],
        }
        for index, item in enumerate(sub_questions, 1)
    ]


def _join_path_evidence(join_paths: list[dict[str, Any]]) -> str:
    if not join_paths:
        return ""
    path = join_paths[0]
    tables = path.get("tables") or []
    bridge_tables = path.get("bridgeTables") or []
    pieces = [" -> ".join(str(table) for table in tables)]
    if bridge_tables:
        pieces.append("bridge: " + ", ".join(str(table) for table in bridge_tables))
    return "; ".join(piece for piece in pieces if piece)
