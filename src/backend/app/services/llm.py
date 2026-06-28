from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

import httpx

from ..config import Settings
from .llm_prompts import COMPLEXITY_ASSESSMENT_SYSTEM_PROMPT, DECOMPOSITION_SYSTEM_PROMPT


@dataclass(frozen=True)
class LlmResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    cost_source: str


class LlmService:
    """Small OpenAI-compatible client for grounded monitoring synthesis."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate_sql(
        self,
        *,
        question: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        conversation: list[dict[str, Any]] | None = None,
        memory: dict[str, Any] | None = None,
        candidate_count: int = 1,
        request_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._sql_plan(
            question=question,
            tables=tables,
            datasource=datasource,
            limit=limit,
            conversation=conversation,
            memory=memory,
            candidate_count=candidate_count,
            request_plan=request_plan,
        )

    async def correct_sql(
        self,
        *,
        question: str,
        failed_sql: str,
        database_error: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        request_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._sql_plan(
            question=question,
            tables=tables,
            datasource=datasource,
            limit=limit,
            failed_sql=failed_sql,
            database_error=database_error,
            request_plan=request_plan,
        )

    async def decompose_sql_question(
        self,
        *,
        question: str,
        tables: list[dict[str, Any]],
        datasource: str,
        request_plan: dict[str, Any],
        complexity: dict[str, Any],
        join_paths: list[dict[str, Any]],
        max_steps: int,
    ) -> dict[str, Any]:
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "fallbackReason": "LLM API key is not configured.",
            }
        context = {
            "question": question,
            "datasource": datasource,
            "structuredRequest": request_plan,
            "complexity": complexity,
            "maxSteps": max(2, min(max_steps, 4)),
            "tables": [_compact_table(table) for table in tables[:8]],
            "joinPaths": join_paths[:5],
        }
        try:
            response = await self._complete(
                DECOMPOSITION_SYSTEM_PROMPT,
                json.dumps(context, ensure_ascii=False, default=str)[:16000],
                timeout_seconds=self.settings.llm_sql_timeout_seconds,
                max_retries=1,
            )
            parsed = _parse_json_object(response.content)
            sub_questions = _parse_sub_questions(parsed.get("sub_questions"))
            if not sub_questions:
                raise ValueError("LLM decomposition did not return sub_questions")
            return {
                "status": str(parsed.get("status") or "success"),
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "strategy": str(parsed.get("strategy") or "llm_decomposition"),
                "subQuestions": sub_questions[: max(2, min(max_steps, 4))],
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "error": _safe_error(exc),
            }

    async def solve_sub_question(
        self,
        *,
        sub_question: dict[str, Any],
        question: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        previous_sql: str | None = None,
        request_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """MAC-SQL step-by-step solver phase for a specific sub-question."""
        enriched_plan = {
            **(request_plan or {}),
            "macSqlCurrentSubQuestion": sub_question,
            "macSqlPreviousSql": previous_sql,
            "macSqlInstruction": f"Solve sub-question: {sub_question.get('question')}. Build upon previous SQL if present.",
        }
        return await self._sql_plan(
            question=f"{question} (Focus on sub-question: {sub_question.get('question')})",
            tables=tables,
            datasource=datasource,
            limit=limit,
            candidate_count=1,
            request_plan=enriched_plan,
        )

    async def refine_decomposed_sql(
        self,
        *,
        question: str,
        accumulated_sql: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        conversation: list[dict[str, Any]] | None = None,
        memory: dict[str, Any] | None = None,
        candidate_count: int = 2,
        request_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """MAC-SQL refiner/synthesizer phase to verify and optimize accumulated step-by-step SQL."""
        enriched_plan = {
            **(request_plan or {}),
            "macSqlAccumulatedSql": accumulated_sql,
            "macSqlInstruction": "Refine, verify against schema, and synthesize final pristine SQL and visualization plan.",
        }
        return await self._sql_plan(
            question=question,
            tables=tables,
            datasource=datasource,
            limit=limit,
            conversation=conversation,
            memory=memory,
            candidate_count=candidate_count,
            request_plan=enriched_plan,
        )

    async def smart_repair_sql(
        self,
        *,
        question: str,
        failed_sql: str,
        heuristic_sql: str,
        database_error: str,
        error_category: str,
        suggested_fix: str,
        strategy_hint: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        request_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dedicated smart LLM repair strategy for SQL self-correction."""
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "fallbackReason": "LLM API key is not configured.",
            }

        dialect = "ClickHouse SQL" if datasource == "clickhouse" else "PostgreSQL SQL"
        system_prompt = (
            f"You are an expert database healer and SQL refiner for {dialect}. "
            "Your task is to repair a failed SQL query that produced a database error. "
            "Return JSON only with keys sql, candidates, selectedTables, rationale, visualization. "
            "visualization must be an object with type, x, y, series, title, rationale. "
            "Put the fully repaired, correct SQL in sql, and provide up to 2 candidates with sql and rationale. "
            "Carefully analyze the database error, the error category, the suggested fix, and the strategy hint. "
            "Examine the live table schemas provided in the context to verify every table and column name. "
            "Ensure all non-aggregated columns are included in GROUP BY, ensure correct explicit type casting, "
            "and verify that all tables are fully qualified. Never use SELECT *, comments, DDL, DML, or multiple statements. "
            "Keep the result bounded by the requested limit."
        )
        context: dict[str, Any] = {
            "question": question,
            "datasource": datasource,
            "limit": limit,
            "failedSql": failed_sql,
            "heuristicSql": heuristic_sql,
            "databaseError": database_error,
            "errorCategory": error_category,
            "suggestedFix": suggested_fix,
            "strategyHint": strategy_hint,
            "tables": [_compact_table(table) for table in tables[:8]],
            "structuredRequest": request_plan or {},
        }
        try:
            response = await self._complete(
                system_prompt,
                json.dumps(context, ensure_ascii=False, default=str)[:20000],
                timeout_seconds=self.settings.llm_sql_timeout_seconds,
                max_retries=self.settings.llm_sql_max_retries,
            )
            plan = _parse_json_object(response.content)
            sql = str(plan.get("sql") or "").strip()
            candidates = _parse_sql_candidates(plan, sql)
            if not sql:
                sql = candidates[0]["sql"] if candidates else ""
            if not sql:
                raise ValueError("LLM smart repair plan did not contain sql")
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "sql": sql,
                "candidates": candidates[:2],
                "selectedTables": plan.get("selectedTables") or [],
                "rationale": str(plan.get("rationale") or "Smart SQL repair completed successfully"),
                "visualization": _parse_visualization_plan(plan.get("visualization")),
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "error": _safe_error(exc),
            }

    async def assess_complexity(
        self,
        *,
        question: str,
        tables: list[dict[str, Any]],
        join_paths: list[dict[str, Any]],
        rule_based_assessment: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "fallbackReason": "LLM API key is not configured.",
            }
        context = {
            "question": question,
            "ruleBasedAssessment": rule_based_assessment,
            "tables": [_compact_table(table) for table in tables[:8]],
            "joinPaths": join_paths[:5],
        }
        try:
            response = await self._complete(
                COMPLEXITY_ASSESSMENT_SYSTEM_PROMPT,
                json.dumps(context, ensure_ascii=False, default=str)[:16000],
                timeout_seconds=self.settings.llm_sql_timeout_seconds,
                max_retries=1,
            )
            parsed = _parse_json_object(response.content)
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "level": parsed.get("level") or rule_based_assessment.get("level"),
                "score": parsed.get("score") or rule_based_assessment.get("score"),
                "signals": parsed.get("signals") or [],
                "recommendedSteps": parsed.get("recommended_steps") or parsed.get("recommendedSteps") or rule_based_assessment.get("recommendedSteps"),
                "rationale": parsed.get("rationale") or "",
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "error": _safe_error(exc),
            }

    async def _sql_plan(
        self,
        *,
        question: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        conversation: list[dict[str, Any]] | None = None,
        memory: dict[str, Any] | None = None,
        failed_sql: str | None = None,
        database_error: str | None = None,
        candidate_count: int = 1,
        request_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "fallbackReason": "LLM API key is not configured.",
            }

        dialect = "ClickHouse SQL" if datasource == "clickhouse" else "PostgreSQL SQL"
        bounded_candidate_count = max(1, min(int(candidate_count), 3))
        system_prompt = (
            f"You generate one read-only {dialect} query from live metadata. "
            "Return JSON only with keys sql, candidates, selectedTables, rationale, "
            "visualization. visualization must be an object with type, x, y, series, "
            "title, rationale. type must be one of table, line, bar, pie, scatter, "
            "histogram, box, heatmap. Use the exact SQL output aliases for x, y, and "
            "series; y and series may be a string or an array. When two or more "
            "categorical dimensions must distinguish the same measure, put all of "
            "them in series so the renderer can create a composite series key. "
            "Honor an explicitly requested "
            "chart type. Choose table when no chart is useful. "
            "The current question has priority over conversation history. Never carry "
            "a previous chart type into a new request unless the current question asks "
            "for it. The visualization must describe the current SQL output exactly. "
            f"Put the best query in sql and return up to {bounded_candidate_count} "
            "meaningfully different candidates as objects with sql and rationale. "
            "Use only listed tables and columns. Fully qualify every physical table. "
            "Include every requested metric and every structuredRequest dimension "
            "in SELECT and GROUP BY. A temporal dimension plus a numeric measure is "
            "chartable as a line chart even when additional categorical dimensions "
            "exist. Use table only when the returned shape truly cannot be charted. "
            "For dimensions listed in structuredRequest coverage, do not add TOP-N, "
            "category filters, or ordering/limits that omit members of that dimension. "
            "When the question names "
            "a model, service, source, or other entity value, add a case-insensitive "
            "filter for that value. "
            "Never use SELECT *, comments, DDL, DML, settings, system commands, or "
            "multiple statements. Keep the result bounded by the requested limit."
        )
        context: dict[str, Any] = {
            "question": question,
            "datasource": datasource,
            "limit": limit,
            "tables": [_compact_table(table) for table in tables[:8]],
            "conversation": _compact_conversation(conversation or []),
            "structuredMemory": memory or {},
            "structuredRequest": request_plan or {},
            "candidateCount": bounded_candidate_count,
        }
        if failed_sql is not None:
            context["failedSql"] = failed_sql
            context["databaseError"] = (database_error or "")[:2000]
            context["instruction"] = (
                "Correct the failed query using the database error and the live schema."
            )
        try:
            response = await self._complete(
                system_prompt,
                json.dumps(context, ensure_ascii=False, default=str)[:20000],
                timeout_seconds=self.settings.llm_sql_timeout_seconds,
                max_retries=self.settings.llm_sql_max_retries,
            )
            plan = _parse_json_object(response.content)
            sql = str(plan.get("sql") or "").strip()
            candidates = _parse_sql_candidates(plan, sql)
            if not sql:
                sql = candidates[0]["sql"] if candidates else ""
            if not sql:
                raise ValueError("LLM SQL plan did not contain sql")
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "sql": sql,
                "candidates": candidates[:bounded_candidate_count],
                "selectedTables": plan.get("selectedTables") or [],
                "rationale": str(plan.get("rationale") or ""),
                "visualization": _parse_visualization_plan(
                    plan.get("visualization")
                ),
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "error": _safe_error(exc),
            }

    async def synthesize(
        self,
        *,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        table_names: list[str],
        tables: list[dict[str, Any]],
        conversation: list[dict[str, Any]] | None = None,
        memory: dict[str, Any] | None = None,
        chart_spec: dict[str, Any] | None = None,
        delta_sink: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
                "fallbackReason": "LLM API key is not configured.",
            }

        context = {
            "selectedTables": table_names,
            "availableColumns": {
                str(table.get("tableName") or ""): [
                    str(column.get("name") or "")
                    for column in table.get("columns") or []
                ]
                for table in tables
            },
            "sql": sql,
            "rowCount": len(rows),
            "rows": rows[:50],
            "conversation": _compact_conversation(conversation or []),
            "structuredMemory": memory or {},
            "chartSpec": chart_spec or {},
        }
        system_prompt = (
            "Ban la tro ly phan tich monitoring va Agentic BI. "
            "Tra loi bang tieng Viet tu nhien, ngan gon, dung Markdown. "
            "Chi dua ra so lieu co trong context; khong tu tao du lieu. "
            "Khong duoc suy ra rang bang thieu cot chi vi SQL khong SELECT cot do; "
            "availableColumns la schema duy nhat co tham quyen, con rows chi la "
            "projection cua truy van. Ket qua rong chi co nghia la khong tim thay "
            "ban ghi khop dieu kien; khong duoc noi chi phi bang 0, khong duoc ket "
            "luan schema thieu cot, va khong yeu cau nguoi dung tu cung cap SQL. "
            "Neu co du lieu, neu ket luan chinh va 1-3 nhan xet co ich. "
            "Neu cau hoi chi la chao hoi hoac huong dan, hay tra loi truc tiep. "
            "Khong lap lai SQL tru khi nguoi dung hoi. UI da tu ve bieu do theo "
            "chartSpec; khong xuat ma Vega-Lite, JSON chart, HTML, hoac huong dan "
            "nguoi dung sao chep ma sang cong cu ve bieu do khac. SQL trong context "
            "da duoc thuc thi; neu rows co du lieu thi khong duoc noi can chay lai "
            "truy van, khong de xuat SQL thay cho ket qua, va khong noi du lieu hien "
            "tai khong phu hop voi chartSpec."
        )
        prompt = (
            f"Cau hoi cua nguoi dung: {question}\n\n"
            "Du lieu truy van va hoi thoai:\n"
            f"{json.dumps(context, ensure_ascii=False, default=str)[:16000]}"
        )

        try:
            response = (
                await self._complete_stream(system_prompt, prompt, delta_sink)
                if delta_sink is not None
                else await self._complete(system_prompt, prompt)
            )
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
                "error": _safe_error(exc),
            }

        return {
            "status": "success",
            "provider": self.settings.llm_provider,
            "model": self.settings.llm_model,
            "answer": response.content,
            "usage": {
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.prompt_tokens + response.completion_tokens,
            },
            "latencyMs": response.latency_ms,
            "totalCostUsd": response.estimated_cost_usd,
            "costSource": response.cost_source,
            "streamed": delta_sink is not None,
        }

    async def plan_agents(
        self,
        *,
        question: str,
        target: str,
        limit: int,
        slots: dict[str, Any],
        conversation: list[dict[str, Any]] | None = None,
        memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Select specialist agents semantically; no keyword route fallback."""
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "reason": "LLM API key is not configured.",
            }

        system_prompt = (
            "You are the semantic control-plane planner for a multi-agent BI system. "
            "Return JSON only. Choose the smallest useful set of agents for the "
            "request, based on meaning rather than exact keywords. Allowed intents: "
            "chart, insight, forecast, qa, dashboard, full_report. Allowed agents: "
            "chart_agent, insight_agent, forecast_agent, qa_agent. Responsibilities: "
            "chart_agent does NL2SQL and chart-ready query results; insight_agent "
            "analyzes observability root-cause signals; forecast_agent fits forward "
            "projections from historical metric series; qa_agent checks ingestion, "
            "audit, and data-health signals. Return keys status, intent, agents, "
            "confidence, rationale. confidence is 0..1. Use dashboard for overview "
            "requiring insight+chart+qa, and full_report when the user asks for a "
            "complete report requiring all agents."
        )
        context = {
            "question": question,
            "target": target,
            "limit": limit,
            "slots": slots,
            "conversation": _compact_conversation(conversation or []),
            "structuredMemory": memory or {},
        }
        try:
            response = await self._complete(
                system_prompt,
                json.dumps(context, ensure_ascii=False, default=str)[:12000],
                timeout_seconds=self.settings.llm_sql_timeout_seconds or 10.0,
                max_retries=1,
            )
            plan = _parse_json_object(response.content)
            return {
                "status": str(plan.get("status") or "success"),
                "intent": str(plan.get("intent") or ""),
                "agents": plan.get("agents") or [],
                "confidence": plan.get("confidence"),
                "rationale": str(plan.get("rationale") or ""),
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "reason": _safe_error(exc),
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
            }

    async def summarize_agent_responses(
        self,
        *,
        question: str,
        responses: list[dict[str, Any]],
    ) -> dict[str, Any] | str | None:
        if not self.settings.llm_configured:
            return None

        compact_responses = []
        for response in responses:
            compact_responses.append(
                {
                    "agent": response.get("agent"),
                    "status": response.get("status"),
                    "confidence": response.get("confidence"),
                    "text": response.get("text"),
                    "warnings": response.get("warnings") or [],
                    "evidence": response.get("evidence") or [],
                    "artifacts": [
                        {
                            "type": artifact.get("type"),
                            "result": artifact.get("result"),
                        }
                        for artifact in response.get("artifacts") or []
                    ][:3],
                }
            )
        system_prompt = (
            "Ban la Summarizer Agent cua he thong BI multi-agent. "
            "Tra loi bang tieng Viet, ngan gon, chi dung thong tin trong context. "
            "Khong tao so lieu moi. Neu co warning, neu ro canh bao."
        )
        try:
            response = await self._complete(
                system_prompt,
                json.dumps(
                    {
                        "question": question,
                        "responses": compact_responses,
                    },
                    ensure_ascii=False,
                    default=str,
                )[:16000],
                timeout_seconds=self.settings.llm_timeout_seconds,
                max_retries=1,
            )
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": response.content.strip(),
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception:
            return None

    async def interpret_insight(
        self,
        *,
        question: str,
        findings: list[dict[str, Any]],
        anomalies: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        dbt_runs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Synthesize observability insights, anomaly root causes, and recommended actions using LLM."""
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
            }

        system_prompt = (
            "Bạn là Insight Agent - chuyên gia phân tích dữ liệu giám sát hệ thống (Observability).\n"
            "Hãy phân tích kết quả truy vấn, các phát hiện (findings) và các điểm bất thường (anomalies) được cung cấp.\n"
            "Nếu có dữ liệu về các lần chạy server pipeline/DAG/dbt (dbt_runs), hãy tìm mối tương quan (correlation) xem "
            "các lần chạy pipeline này (đặc biệt là các lần chạy thất bại hoặc chạy ngay trước thời điểm xảy ra anomaly) "
            "có phải là nguyên nhân gây ra các điểm bất thường hay không.\n"
            "Nêu rõ các vấn đề chính (latency/error/traffic), phỏng đoán nguyên nhân gốc rễ (root cause), "
            "và đề xuất các giải pháp khắc phục ngắn hạn (short-term) và dài hạn (long-term) rõ ràng.\n"
            "Hãy trả lời bằng tiếng Việt tự nhiên, ngắn gọn và định dạng Markdown chuyên nghiệp."
        )

        prompt = (
            f"Câu hỏi của người dùng: {question}\n\n"
            "Danh sách findings:\n"
            f"{json.dumps(findings[:5], ensure_ascii=False)}\n\n"
            "Danh sách anomalies:\n"
            f"{json.dumps(anomalies[:5], ensure_ascii=False)}\n\n"
            "Mẫu dữ liệu hàng đầu:\n"
            f"{json.dumps(rows[:10], ensure_ascii=False)}"
        )

        if dbt_runs:
            prompt += (
                "\n\nLịch sử chạy pipeline server gần đây:\n"
                f"{json.dumps(dbt_runs[:5], ensure_ascii=False, default=str)}"
            )

        try:
            response = await self._complete(system_prompt, prompt)
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": response.content,
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
                "error": _safe_error(exc),
            }

    async def interpret_forecast(
        self,
        *,
        question: str,
        target_metric: str,
        unit: str,
        history: list[dict[str, Any]],
        forecast: list[dict[str, Any]],
        trend_prediction: dict[str, Any],
    ) -> dict[str, Any]:
        """Synthesize forecasting trends, model comparisons, and warnings using LLM."""
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
            }

        system_prompt = (
            "Bạn là Forecast Agent - chuyên gia dự báo chỉ số hệ thống (Time-series Forecasting).\n"
            "Hãy diễn giải kết quả dự báo của chuỗi thời gian được cung cấp.\n"
            "Giải thích mô hình được chọn, phân tích xu hướng tiếp theo (trend direction, expected change), "
            "đánh giá mức độ tin cậy của dự báo và đưa ra nhận định/cảnh báo nếu chỉ số có nguy cơ vượt ngưỡng.\n"
            "Hãy trả lời bằng tiếng Việt tự nhiên, ngắn gọn và định dạng Markdown chuyên nghiệp."
        )

        prompt = (
            f"Câu hỏi của người dùng: {question}\n"
            f"Metric mục tiêu: {target_metric} (Đơn vị: {unit})\n\n"
            f"Dự đoán xu hướng: {json.dumps(trend_prediction, ensure_ascii=False)}\n\n"
            "Lịch sử gần đây (5 điểm cuối):\n"
            f"{json.dumps(history[-5:], ensure_ascii=False)}\n\n"
            "Kết quả dự báo (7 điểm kế tiếp):\n"
            f"{json.dumps(forecast[:7], ensure_ascii=False)}"
        )

        try:
            response = await self._complete(system_prompt, prompt)
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": response.content,
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
                "error": _safe_error(exc),
            }

    async def interpret_qa(
        self,
        *,
        question: str,
        issues: list[dict[str, Any]],
        ingestion_rows: list[dict[str, Any]],
        audit_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Synthesize data quality QA results and operation warnings using LLM."""
        if not self.settings.llm_configured:
            return {
                "status": "disabled",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
            }

        system_prompt = (
            "Bạn là QA Agent - chuyên gia kiểm định chất lượng dữ liệu và sức khỏe hệ thống (Data Quality & System Health QA).\n"
            "Hãy phân tích các lỗi về chất lượng dữ liệu tải (ingestion health) và lỗi chạy truy vấn của agent (agent query audit).\n"
            "Đánh giá mức độ nghiêm trọng của các vấn đề (issues), chỉ ra các nguồn dữ liệu/ứng dụng bị ảnh hưởng chính, "
            "và đề xuất hành động kiểm tra khắc phục cho đội ngũ vận hành.\n"
            "Hãy trả lời bằng tiếng Việt tự nhiên, ngắn gọn và định dạng Markdown chuyên nghiệp."
        )

        prompt = (
            f"Câu hỏi của người dùng: {question}\n\n"
            "Các vấn đề phát hiện (issues):\n"
            f"{json.dumps(issues, ensure_ascii=False)}\n\n"
            "Dữ liệu tải Ingestion (5 hàng đầu):\n"
            f"{json.dumps(ingestion_rows[:5], ensure_ascii=False)}\n\n"
            "Dữ liệu Audit Query (5 hàng đầu):\n"
            f"{json.dumps(audit_rows[:5], ensure_ascii=False)}"
        )

        try:
            response = await self._complete(system_prompt, prompt)
            return {
                "status": "success",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": response.content,
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.prompt_tokens + response.completion_tokens,
                },
                "latencyMs": response.latency_ms,
                "totalCostUsd": response.estimated_cost_usd,
                "costSource": response.cost_source,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "answer": None,
                "error": _safe_error(exc),
            }

    async def classify_intent(
        self,
        question: str,
    ) -> str:
        """Classify the user's intent using LLM."""
        if not self.settings.llm_configured:
            return "lookup"

        system_prompt = (
            "You are an intent classifier for a database querying system. "
            "Your task is to classify the user's intent into one of the following 4 categories:\n"
            "1. dashboard: the user wants an overview/dashboard containing multiple aspects, or explicitly asks for 'dashboard' / 'tong quan'.\n"
            "2. time_series: the user wants to see trend, daily details, change over time, or time-series data.\n"
            "3. aggregation: the user wants to group data by a category (e.g. by model, by service, top items, etc.) or compare categories.\n"
            "4. lookup: the user wants to find specific details, look up individual records, or simple queries that don't fit the above categories.\n\n"
            "Respond with only a single word matching the category: dashboard, time_series, aggregation, or lookup. "
            "Do not include any other explanation, formatting, or punctuation."
        )
        prompt = f"User Question: {question}"
        try:
            response = await self._complete(
                system_prompt,
                prompt,
                timeout_seconds=self.settings.llm_sql_timeout_seconds or 10.0,
                max_retries=1,
            )
            intent = response.content.strip().lower()
            import re
            intent = re.sub(r"[^a-z_]", "", intent)
            if intent in {"dashboard", "time_series", "aggregation", "lookup"}:
                return intent
            return "lookup"
        except Exception:
            return "lookup"

    async def classify_request_slots(
        self,
        question: str,
    ) -> dict[str, Any]:
        """Extract semantic request slots; deterministic rules remain the fallback."""
        if not self.settings.llm_configured:
            return {}

        system_prompt = (
            "Extract semantic slots for an analytics request. Return JSON only. "
            "The top-level key must be slots. slots must contain intent_type, "
            "domain, metric, time_range, layer, dimensions, and coverage. Each slot is an object with "
            "value and confidence from 0 to 1. Allowed intent_type values are "
            "dashboard, time_series, aggregation, lookup. Typical domains are "
            "observability, llm_usage, ingestion, agent_cost, data_quality. "
            "Use agent_cost only for Agentic BI/chat/query/audit/platform requests. "
            "Use llm_usage for LLM generation/model/token/provider requests. "
            "Use observability for trace/span/service/backend requests. If the user "
            "only says request(s) without a domain, leave domain null and set metric "
            "to request_count so the orchestrator can ask a clarification instead "
            "of guessing a table. metric may be a string or array; include "
            "request_count for request volume questions. layer is mart, staging, raw, or auto. "
            "dimensions is an array chosen from time, model, source_app, service_name, "
            "provider, environment, status, user. Extract every grouping/grain explicitly "
            "requested, including repeated Vietnamese forms such as 'từng/theo'. "
            "coverage is the subset of dimensions whose members must not be omitted, "
            "for requests such as all/every/không bỏ sót/toàn bộ. "
            "Use null when the user did not provide enough evidence. Understand "
            "Vietnamese and English semantically; do not rely on exact keywords."
        )
        try:
            response = await self._complete(
                system_prompt,
                json.dumps({"question": question}, ensure_ascii=False),
                timeout_seconds=self.settings.llm_sql_timeout_seconds or 10.0,
                max_retries=1,
            )
            parsed = _parse_json_object(response.content)
            return parsed if isinstance(parsed.get("slots"), dict) else {}
        except Exception:
            return {}

    async def _complete_stream(
        self,
        system_prompt: str,
        prompt: str,
        delta_sink: Callable[[str], None],
    ) -> LlmResponse:
        last_error: Exception | None = None
        for attempt in range(max(1, self.settings.llm_max_retries)):
            emitted = False

            def forward(delta: str) -> None:
                nonlocal emitted
                emitted = True
                delta_sink(delta)

            try:
                return await self._request_stream(
                    system_prompt,
                    prompt,
                    forward,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {429, 500, 502, 503, 504}:
                    break
            if emitted:
                break
            if attempt + 1 < max(1, self.settings.llm_max_retries):
                await asyncio.sleep(min(0.7 * (2**attempt), 4))
        raise last_error or RuntimeError("LLM streaming request failed")

    async def _complete(
        self,
        system_prompt: str,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> LlmResponse:
        last_error: Exception | None = None
        retries = max(
            1,
            self.settings.llm_max_retries
            if max_retries is None
            else max_retries,
        )
        for attempt in range(retries):
            try:
                return await self._request(
                    system_prompt,
                    prompt,
                    timeout_seconds=timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {429, 500, 502, 503, 504}:
                    break
            if attempt + 1 < retries:
                await asyncio.sleep(min(0.7 * (2**attempt), 4))
        raise last_error or RuntimeError("LLM request failed")

    async def _request(
        self,
        system_prompt: str,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> LlmResponse:
        provider = self.settings.llm_provider
        if provider == "openrouter":
            url = (
                f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
            )
            headers = {
                "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                "HTTP-Referer": self.settings.openrouter_site_url,
                "X-Title": self.settings.openrouter_app_name,
            }
        elif provider == "openai":
            url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
            headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        started = perf_counter()
        timeout = httpx.Timeout(
            max(
                5.0,
                self.settings.llm_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds,
            ),
            connect=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": self.settings.llm_model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
        response.raise_for_status()
        body = response.json()
        content = (
            body.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM provider returned an empty response")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = int(
            usage.get("prompt_tokens") or _estimate_tokens(system_prompt + prompt)
        )
        completion_tokens = int(
            usage.get("completion_tokens") or _estimate_tokens(content)
        )
        provider_cost = _number_or_none(
            usage.get("cost")
            or usage.get("total_cost")
            or body.get("cost")
        )
        return LlmResponse(
            content=content.strip(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=round((perf_counter() - started) * 1000),
            estimated_cost_usd=provider_cost
            if provider_cost is not None
            else _estimate_cost(
                self.settings.llm_model, prompt_tokens, completion_tokens
            ),
            cost_source="provider" if provider_cost is not None else "estimated",
        )

    async def _request_stream(
        self,
        system_prompt: str,
        prompt: str,
        delta_sink: Callable[[str], None],
    ) -> LlmResponse:
        url, headers = self._provider_request()
        started = perf_counter()
        timeout = httpx.Timeout(
            max(5.0, self.settings.llm_timeout_seconds),
            connect=10.0,
        )
        chunks: list[str] = []
        usage: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json={
                    "model": self.settings.llm_model,
                    "temperature": 0.2,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    event = json.loads(payload)
                    event_usage = event.get("usage")
                    if isinstance(event_usage, dict):
                        usage = event_usage
                    delta = (
                        event.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if isinstance(delta, str) and delta:
                        chunks.append(delta)
                        delta_sink(delta)
        content = "".join(chunks).strip()
        if not content:
            raise ValueError("LLM provider returned an empty streamed response")
        prompt_tokens = int(
            usage.get("prompt_tokens") or _estimate_tokens(system_prompt + prompt)
        )
        completion_tokens = int(
            usage.get("completion_tokens") or _estimate_tokens(content)
        )
        provider_cost = _number_or_none(
            usage.get("cost") or usage.get("total_cost")
        )
        return LlmResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=round((perf_counter() - started) * 1000),
            estimated_cost_usd=provider_cost
            if provider_cost is not None
            else _estimate_cost(
                self.settings.llm_model, prompt_tokens, completion_tokens
            ),
            cost_source="provider" if provider_cost is not None else "estimated",
        )

    def _provider_request(self) -> tuple[str, dict[str, str]]:
        provider = self.settings.llm_provider
        if provider == "openrouter":
            return (
                f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions",
                {
                    "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                    "HTTP-Referer": self.settings.openrouter_site_url,
                    "X-Title": self.settings.openrouter_app_name,
                },
            )
        if provider == "openai":
            return (
                f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                {"Authorization": f"Bearer {self.settings.openai_api_key}"},
            )
        raise ValueError(f"Unsupported LLM provider: {provider}")


def _compact_conversation(
    conversation: list[dict[str, Any]],
) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for item in conversation[-6:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            compact.append({"role": role, "content": content[:1000]})
    return compact


def _compact_table(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "tableName": table.get("tableName"),
        "description": table.get("description"),
        "tags": table.get("tags") or [],
        "owners": table.get("owners") or [],
        "retrievalReasons": table.get("retrievalReasons") or [],
        "columns": [
            {
                "name": column.get("name"),
                "type": column.get("type"),
                "description": column.get("description"),
            }
            for column in table.get("columns") or []
        ],
    }


def _parse_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1]
        candidate = candidate.rsplit("```", 1)[0].strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")
    parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed


def _parse_sql_candidates(
    plan: dict[str, Any], primary_sql: str
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(sql: Any, rationale: Any = "") -> None:
        candidate_sql = str(sql or "").strip()
        normalized = " ".join(candidate_sql.lower().split())
        if not candidate_sql or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(
            {
                "sql": candidate_sql,
                "rationale": str(rationale or ""),
            }
        )

    add(primary_sql, plan.get("rationale"))
    raw_candidates = plan.get("candidates")
    if isinstance(raw_candidates, list):
        for candidate in raw_candidates:
            if isinstance(candidate, dict):
                add(candidate.get("sql"), candidate.get("rationale"))
            else:
                add(candidate)
    return candidates


def _parse_visualization_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    chart_type = str(value.get("type") or "").strip().lower()
    allowed_types = {
        "table",
        "line",
        "bar",
        "pie",
        "scatter",
        "histogram",
        "box",
        "heatmap",
    }
    if chart_type not in allowed_types:
        return {}
    y_value = value.get("y")
    if isinstance(y_value, list):
        y: str | list[str] | None = [
            str(item).strip()
            for item in y_value[:4]
            if str(item).strip()
        ]
    else:
        y = str(y_value).strip() if y_value is not None else None
    series_value = value.get("series")
    if isinstance(series_value, list):
        series: str | list[str] | None = [
            str(item).strip()
            for item in series_value[:4]
            if str(item).strip()
        ]
    else:
        series = str(series_value).strip() if series_value is not None else None
    return {
        "type": chart_type,
        "x": str(value.get("x") or "").strip() or None,
        "y": y,
        "series": series or None,
        "title": str(value.get("title") or "").strip()[:200] or None,
        "rationale": str(value.get("rationale") or "").strip()[:500] or None,
    }


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model.lower().endswith(":free"):
        return 0
    input_rate = 5.0 if "gpt-4" in model.lower() else 0.15
    output_rate = 15.0 if "gpt-4" in model.lower() else 0.6
    return round(
        prompt_tokens / 1_000_000 * input_rate
        + completion_tokens / 1_000_000 * output_rate,
        8,
    )


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "LLM provider rejected the API key or permissions."
        if status == 429:
            return "LLM provider rate limit or quota was exceeded."
        return f"LLM provider returned HTTP {status}."
    if isinstance(exc, httpx.TimeoutException):
        return "LLM provider timed out."
    if isinstance(exc, httpx.TransportError):
        return "Could not connect to the LLM provider."
    return str(exc)[:300]
