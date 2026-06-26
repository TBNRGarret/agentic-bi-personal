from __future__ import annotations

from typing import Any

from ...services.llm import LlmService
from ..monitoring_agent import _generate_sql
from .contracts import DecompositionPlan, SelectorResult


class DecomposerAgent:
    """MAC-SQL-style decomposition wrapper over the existing SQL planner."""

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
    ) -> DecompositionPlan:
        if selector.selected_tables and llm.settings.llm_configured:
            raw = await llm.generate_sql(
                question=question,
                tables=selector.selected_tables,
                datasource=datasource,
                limit=limit,
                conversation=conversation,
                memory=memory,
                candidate_count=2,
                request_plan={
                    **request_plan,
                    "model2SchemaPrompt": selector.schema_prompt,
                    "model2Selector": selector.to_dict(),
                },
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
                    sub_questions=_deterministic_sub_questions(question, selector),
                    selected_tables=selector.selected_table_names,
                    visualization=raw.get("visualization") or {},
                    confidence=0.78,
                    rationale=str(raw.get("rationale") or "LLM SQL planner over Model 2 selected schema"),
                    source="llm_model2_decomposer",
                    raw_plan=raw,
                )

        if selector.selected_tables:
            sql = _generate_sql(
                question,
                selector.selected_tables[0],
                limit,
                datasource,
                request_plan=request_plan,
            )
        else:
            sql = ""
        return DecompositionPlan(
            status="success" if sql else "failed",
            sql=sql,
            candidates=[{"sql": sql, "rationale": "deterministic fallback"}] if sql else [],
            sub_questions=_deterministic_sub_questions(question, selector),
            selected_tables=selector.selected_table_names,
            confidence=0.45 if sql else 0.0,
            rationale="Deterministic SQL fallback from selected table metadata",
            source="deterministic_model2_fallback",
            raw_plan={"status": "fallback"},
        )


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


def _deterministic_sub_questions(
    question: str,
    selector: SelectorResult,
) -> list[dict[str, Any]]:
    tables = ", ".join(selector.selected_table_names) or "selected tables"
    return [
        {
            "id": "sq1",
            "question": "Identify the relevant governed BI tables and columns.",
            "evidence": tables,
        },
        {
            "id": "sq2",
            "question": "Translate the user request into one read-only SQL statement.",
            "evidence": question,
        },
        {
            "id": "sq3",
            "question": "Execute and refine the SQL if the database returns an error.",
            "evidence": "bounded correction loop",
        },
    ]
