from __future__ import annotations

from typing import Any

from ...services.llm import LlmService
from ...services.llm_prompts import SQL_GENERATION_PROMPT_NOTE
from .complexity_assessor import ComplexityAssessor
from .contracts import DecompositionPlan, SelectorResult


class DecomposerAgent:
    """MAC-SQL-style decomposition with hybrid rule-based + LLM gating and expansion."""

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
    ) -> DecompositionPlan:
        join_paths = join_paths or []
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
        complexity: dict[str, Any],
    ) -> DecompositionPlan:
        sub_questions = _rule_based_sub_questions(
            question,
            selector,
            join_paths=join_paths,
            complexity=complexity,
        )
        enriched_plan = _enriched_request_plan(
            request_plan,
            selector=selector,
            join_paths=join_paths,
            complexity=complexity,
            sub_questions=sub_questions,
            strategy="single_shot",
        )
        if selector.selected_tables and llm.settings.llm_configured:
            raw = await llm.generate_sql(
                question=question,
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
                    rationale=str(raw.get("rationale") or "LLM SQL planner over Model 2 selected schema"),
                    source="llm_model2_single_shot",
                    raw_plan=raw,
                    complexity=complexity,
                    intermediate_results=_planned_intermediate_results(sub_questions),
                    decomposition_strategy="single_shot",
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
            strategy="single_shot_deterministic_fallback",
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
            strategy = "llm_decomposed"
        else:
            sub_questions = _rule_based_sub_questions(
                question,
                selector,
                join_paths=join_paths,
                complexity=complexity,
            )
            strategy = "rule_based_decomposition_fallback"

        intermediate_results = _planned_intermediate_results(sub_questions)
        enriched_plan = _enriched_request_plan(
            request_plan,
            selector=selector,
            join_paths=join_paths,
            complexity=complexity,
            sub_questions=sub_questions,
            strategy=strategy,
        )
        raw = await llm.generate_sql(
            question=question,
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
                confidence=0.82 if strategy == "llm_decomposed" else 0.62,
                rationale=str(raw.get("rationale") or "Model 2 decomposed SQL plan"),
                source="llm_model2_decomposer",
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
) -> DecompositionPlan:
    if selector.selected_tables:
        from ..monitoring_agent import _generate_sql

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
        sub_questions=sub_questions,
        selected_tables=selector.selected_table_names,
        confidence=0.45 if sql else 0.0,
        rationale="Deterministic SQL fallback from selected table metadata",
        source="deterministic_model2_fallback",
        raw_plan=raw_plan or {"status": "fallback"},
        complexity=complexity,
        intermediate_results=_planned_intermediate_results(sub_questions),
        decomposition_strategy=strategy,
        complexity_strategy=complexity.get("strategy") or "rule_based",
        hybrid_signals=complexity.get("signals") or [],
        chain_of_thought=sub_questions,
    )


def _enriched_request_plan(
    request_plan: dict[str, Any],
    *,
    selector: SelectorResult,
    join_paths: list[dict[str, Any]],
    complexity: dict[str, Any],
    sub_questions: list[dict[str, Any]],
    strategy: str,
) -> dict[str, Any]:
    return {
        **request_plan,
        "model2SchemaPrompt": selector.schema_prompt,
        "model2Selector": selector.to_dict(),
        "model2JoinPaths": join_paths,
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
