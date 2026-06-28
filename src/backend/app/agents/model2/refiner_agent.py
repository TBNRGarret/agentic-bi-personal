from __future__ import annotations

import re
from typing import Any

from ...services.llm import LlmService
from .error_classifier import ErrorClassification, ErrorClassifier
from .utils import table_name


class RefinerAgent:
    """MAC-SQL Refiner + QueryWeaver Healer pattern with deterministic-first repair."""

    """
    Overview
    1. Receives failed SQL + database error from orchestrator
    2. ErrorClassifier.classify() -> categorize error into 6 types
        a. unknown_column, missing_table, syntax_error
        b. aggregation_error, type_mismatch, general_database_error
    3. Build deterministic repair candidates (no LLM needed)
        a. clean_sql: remove markdown wrappers
        b. schema_replacement: fuzzy match bad column/table -> correct schema name
        c. repair_group_by: auto-add missing non-aggregated columns
        d. repair_syntax: balance quotes/parentheses
        e. repair_type_mismatch: add dialect casts
        f. constrain: add table qualification + limit
    4. If confidence >= 0.8 -> return deterministic candidate immediately
    5. If confidence < 0.8 -> call LLM smart_repair/correct_sql with error context
    6. Merge deterministic + LLM candidates, deduplicate, return top 5

    Deterministic-first: avoids LLM cost for common, predictable errors.
    """

    def __init__(self, error_classifier: ErrorClassifier | None = None) -> None:
        self.error_classifier = error_classifier or ErrorClassifier()

    def _build_repair_candidates(
        self,
        failed_sql: str,
        classification: ErrorClassification,
        *,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        error_history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build ordered deterministic repair candidates before any LLM call."""
        cleaned = _clean_sql(failed_sql)
        if not cleaned:
            return []

        candidates: list[dict[str, Any]] = []
        self._add_candidate(
            candidates,
            cleaned,
            strategy="clean_sql",
            rationale="Removed markdown wrappers and trailing statement separators.",
        )

        replacement_sql = self._apply_schema_replacements(cleaned, classification)
        self._add_candidate(
            candidates,
            replacement_sql,
            strategy=f"{classification.category}_schema_replacement",
            rationale=classification.suggested_fix,
        )

        if classification.category == "aggregation_error":
            self._add_candidate(
                candidates,
                _repair_group_by(replacement_sql),
                strategy="repair_group_by",
                rationale="Added missing non-aggregated SELECT expressions to GROUP BY.",
            )

        if classification.category == "syntax_error":
            self._add_candidate(
                candidates,
                _repair_syntax(replacement_sql),
                strategy="repair_syntax",
                rationale="Balanced common syntax mistakes without changing query intent.",
            )

        if classification.category in {"type_mismatch", "general_database_error"}:
            self._add_candidate(
                candidates,
                _repair_type_mismatch(replacement_sql, datasource),
                strategy="repair_type_mismatch",
                rationale="Normalized common type/date comparison patterns for the target dialect.",
            )

        constrained = _constrain_sql(
            candidates[-1]["sql"] if candidates else replacement_sql,
            tables=tables,
            datasource=datasource,
            limit=limit,
        )
        self._add_candidate(
            candidates,
            constrained,
            strategy="schema_and_limit_guard",
            rationale="Applied known table qualification and result limit guard.",
        )

        for previous in error_history[-3:]:
            previous_sql = str(previous.get("failedSql") or "").strip()
            if previous_sql:
                self._add_candidate(
                    candidates,
                    _avoid_repeated_failure(constrained, previous_sql),
                    strategy="avoid_repeated_failure",
                    rationale="Avoided returning the exact SQL shape that already failed.",
                )

        return candidates[:5]

    def _apply_schema_replacements(self, failed_sql: str, classification: ErrorClassification) -> str:
        """Apply deterministic regex/rule-based quick fixes directly to failed SQL."""
        sql = failed_sql.strip()
        if not sql:
            return sql

        if classification.category == "unknown_column":
            match = re.search(r"Replace unknown column '([^']+)' with valid schema column '([^']+)'", classification.suggested_fix)
            if match:
                bad_col, good_col = match.group(1), match.group(2)
                sql = re.sub(rf"\b{re.escape(bad_col)}\b", good_col, sql)

        elif classification.category == "missing_table":
            match = re.search(r"Replace invalid table '([^']+)' with valid schema table '([^']+)'", classification.suggested_fix)
            if match:
                bad_table, good_table = match.group(1), match.group(2)
                sql = re.sub(rf"\b{re.escape(bad_table)}\b", good_table, sql)

        return sql

    def _add_candidate(
        self,
        candidates: list[dict[str, Any]],
        sql: str,
        *,
        strategy: str,
        rationale: str,
    ) -> None:
        normalized = _normalize_sql_for_compare(sql)
        if not normalized:
            return
        if any(_normalize_sql_for_compare(item["sql"]) == normalized for item in candidates):
            return
        candidates.append({"sql": sql.strip(), "strategy": strategy, "rationale": rationale})

    async def refine(
        self,
        *,
        question: str,
        failed_sql: str,
        database_error: str,
        tables: list[dict[str, Any]],
        datasource: str,
        limit: int,
        llm: LlmService,
        request_plan: dict[str, Any],
        error_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        error_history = error_history or []
        classification = self.error_classifier.classify(
            database_error,
            tables=tables,
            datasource=datasource,
        )

        deterministic_candidates = self._build_repair_candidates(
            failed_sql,
            classification,
            tables=tables,
            datasource=datasource,
            limit=limit,
            error_history=error_history,
        )
        heuristic_sql = deterministic_candidates[-1]["sql"] if deterministic_candidates else _clean_sql(failed_sql)

        strategy_hints = {
            "unknown_column": "Examine the provided schema tables. Only select columns that explicitly exist in the table definitions.",
            "missing_table": "Verify table names against the schema. Make sure every table in FROM/JOIN is fully qualified with its database/schema prefix.",
            "type_mismatch": "Check ClickHouse/PostgreSQL specific casting rules. Use explicit casts (e.g., toDateTime(), toInt64(), ::text) for function arguments and comparisons.",
            "aggregation_error": "Ensure all non-aggregated select expressions are fully included in the GROUP BY clause.",
            "syntax_error": "Verify correct comma separation, balanced quotes/parentheses, and valid dialect syntax.",
        }
        domain_hint = strategy_hints.get(classification.category, "Ensure SQL adheres strictly to target database dialect and schema.")
        join_validation = _join_validation_context(request_plan)
        join_hint = _join_validation_hint(join_validation)
        if join_hint:
            domain_hint = f"{domain_hint} {join_hint}"

        enriched_plan = {
            **request_plan,
            "refinerErrorClassification": classification.to_dict(),
            "refinerErrorHistory": error_history,
            "refinerHeuristicSql": heuristic_sql,
            "refinerDeterministicCandidates": deterministic_candidates,
            "refinerRepairStrategyHint": domain_hint,
            "refinerJoinValidation": join_validation,
        }

        if deterministic_candidates and classification.confidence >= 0.8:
            return {
                "status": "success",
                "source": "model2_refiner",
                "sql": heuristic_sql,
                "candidates": deterministic_candidates,
                "rationale": "Deterministic refiner repair generated schema-aware candidate SQL before LLM repair.",
                "errorClassification": classification.to_dict(),
                "errorHistoryCount": len(error_history),
                "heuristicRepaired": heuristic_sql != failed_sql,
                "repairStrategy": "deterministic_first",
            }

        if hasattr(llm, "smart_repair_sql"):
            correction = await llm.smart_repair_sql(
                question=question,
                failed_sql=failed_sql,
                heuristic_sql=heuristic_sql,
                database_error=database_error,
                error_category=classification.category,
                suggested_fix=classification.suggested_fix,
                strategy_hint=domain_hint,
                tables=tables,
                datasource=datasource,
                limit=limit,
                request_plan=enriched_plan,
            )
        else:
            enhanced_error = (
                f"{database_error}\n\n"
                f"[Smart Refiner Suggestion: {classification.suggested_fix}]\n"
                f"[Refiner Strategy Hint: {domain_hint}]"
            )
            correction = await llm.correct_sql(
                question=question,
                failed_sql=heuristic_sql or failed_sql,
                database_error=enhanced_error,
                tables=tables,
                datasource=datasource,
                limit=limit,
                request_plan=enriched_plan,
            )

        if correction.get("status") == "success":
            merged_candidates = deterministic_candidates + [
                candidate
                for candidate in correction.get("candidates", [])
                if isinstance(candidate, dict)
            ]
            return {
                **correction,
                "source": "model2_refiner",
                "candidates": merged_candidates[:5],
                "errorClassification": classification.to_dict(),
                "errorHistoryCount": len(error_history),
                "heuristicRepaired": heuristic_sql != failed_sql,
                "repairStrategy": "llm_after_deterministic_candidates",
            }
        return {
            "status": "failed",
            "source": "model2_refiner",
            "error": correction.get("error") or correction.get("fallbackReason") or "SQL refinement failed",
            "errorClassification": classification.to_dict(),
            "errorHistoryCount": len(error_history),
            "candidates": deterministic_candidates,
            "raw": correction,
        }


def _join_validation_context(request_plan: dict[str, Any]) -> dict[str, Any]:
    direct = request_plan.get("model2JoinValidation")
    if isinstance(direct, dict):
        return direct
    join_plan = request_plan.get("model2JoinPlan")
    if isinstance(join_plan, dict) and isinstance(join_plan.get("validation"), dict):
        return join_plan["validation"]
    return {}


def _join_validation_hint(validation: dict[str, Any]) -> str:
    if not validation:
        return ""
    parts: list[str] = []
    required = [str(item) for item in validation.get("requiredJoinConditions") or [] if str(item)]
    if required:
        parts.append(
            "Re-check graph join path and preserve these explicit JOIN conditions, rewriting fully qualified table names to SQL aliases when needed: "
            + "; ".join(required[:5])
            + "."
        )
    missing = validation.get("missingJoinConditions") or []
    if missing:
        parts.append(
            "If a required relationship lacks an explicit key, inspect table schemas before choosing a join condition."
        )
    if validation.get("noCrossJoin"):
        parts.append("Do not use CROSS JOIN or comma joins.")
    if validation.get("validateGrainBeforeAggregation"):
        parts.append("Validate table grain before aggregation so joins do not duplicate metric rows.")
    return " ".join(parts)


def _clean_sql(sql: str) -> str:
    cleaned = str(sql or "").strip()
    cleaned = re.sub(r"^```sql\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned.rstrip(";").strip()


def _normalize_sql_for_compare(sql: str) -> str:
    return re.sub(r"\s+", " ", _clean_sql(sql)).lower()


def _repair_group_by(sql: str) -> str:
    select_match = re.search(r"\bSELECT\b(?P<select>.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return sql
    non_aggregated = []
    for expression in _split_select_expressions(select_match.group("select")):
        expr = re.sub(r"\s+AS\s+[`\"]?[a-zA-Z_][\w]*[`\"]?$", "", expression.strip(), flags=re.IGNORECASE)
        if not expr or re.search(r"\b(count|sum|avg|min|max|quantile|uniq|any)\s*\(", expr, re.IGNORECASE):
            continue
        if re.match(r"^[0-9'\"`]", expr):
            continue
        non_aggregated.append(expr)
    if not non_aggregated:
        return sql
    group_by = ", ".join(dict.fromkeys(non_aggregated))
    if re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE):
        return re.sub(
            r"\bGROUP\s+BY\b(?P<group>.*?)(?=\bORDER\s+BY\b|\bLIMIT\b|$)",
            lambda match: "GROUP BY " + _merge_group_by(match.group("group"), non_aggregated) + " ",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    insert_at = re.search(r"\bORDER\s+BY\b|\bLIMIT\b", sql, re.IGNORECASE)
    if insert_at:
        return f"{sql[:insert_at.start()].rstrip()} GROUP BY {group_by} {sql[insert_at.start():].lstrip()}"
    return f"{sql} GROUP BY {group_by}"


def _split_select_expressions(select_clause: str) -> list[str]:
    expressions: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in select_clause:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if char == "," and depth == 0:
            expressions.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        expressions.append("".join(current).strip())
    return expressions


def _merge_group_by(existing_group: str, required: list[str]) -> str:
    existing = [item.strip() for item in existing_group.split(",") if item.strip()]
    merged = list(dict.fromkeys(existing + required))
    return ", ".join(merged)


def _repair_syntax(sql: str) -> str:
    repaired = re.sub(r",\s*(FROM|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT)\b", r" \1", sql, flags=re.IGNORECASE)
    missing = repaired.count("(") - repaired.count(")")
    if missing > 0:
        repaired += ")" * missing
    return repaired


def _repair_type_mismatch(sql: str, datasource: str) -> str:
    if datasource != "clickhouse":
        return sql
    repaired = re.sub(
        r"\b(now|today)\s*\(\s*\)\s*-\s*interval\s+'?(\d+)'?\s+(day|hour|month|week)s?\b",
        lambda match: f"{match.group(1)}() - INTERVAL {match.group(2)} {match.group(3).upper()}",
        sql,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\bdate\s*\(\s*([^)]+)\s*\)", r"toDate(\1)", repaired, flags=re.IGNORECASE)


def _constrain_sql(sql: str, *, tables: list[dict[str, Any]], datasource: str, limit: int) -> str:
    constrained = _clean_sql(sql)
    table_lookup = {
        table_name(table).split(".")[-1].lower(): table_name(table)
        for table in tables
        if table_name(table)
    }
    for short_name, qualified_name in table_lookup.items():
        constrained = re.sub(
            rf"\b(FROM|JOIN)\s+[`\"]?{re.escape(short_name)}[`\"]?\b",
            rf"\1 {qualified_name}",
            constrained,
            flags=re.IGNORECASE,
        )
    if limit > 0 and not re.search(r"\bLIMIT\s+\d+\b", constrained, re.IGNORECASE):
        constrained = f"{constrained} LIMIT {limit}"
    if datasource == "clickhouse":
        constrained = constrained.replace("::date", "")
    return constrained


def _avoid_repeated_failure(sql: str, previous_sql: str) -> str:
    if _normalize_sql_for_compare(sql) != _normalize_sql_for_compare(previous_sql):
        return sql
    return re.sub(r"\bLIMIT\s+\d+\b", "LIMIT 50", sql, flags=re.IGNORECASE)
