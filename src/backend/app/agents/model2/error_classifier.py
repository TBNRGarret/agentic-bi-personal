from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorClassification:
    category: str
    message: str
    suggested_fix: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": self.message,
            "suggestedFix": self.suggested_fix,
            "confidence": self.confidence,
        }


class ErrorClassifier:
    """Classifies database errors and suggests fuzzy-matched quick fixes."""

    def classify(
        self,
        database_error: str,
        tables: list[dict[str, Any]],
        datasource: str = "clickhouse",
    ) -> ErrorClassification:
        err = str(database_error or "").strip()
        err_lower = err.lower()

        # Gather all valid table and column names from schema context
        available_columns: dict[str, list[str]] = {}
        all_columns: set[str] = set()
        all_tables: set[str] = set()

        for table in tables:
            t_name = str(table.get("tableName") or "")
            if t_name:
                all_tables.add(t_name)
                cols = [str(c.get("name") or "") for c in table.get("columns") or []]
                available_columns[t_name] = cols
                all_columns.update(cols)

        # 1. Unknown Column / Missing Column
        if any(p in err_lower for p in ("unknown column", "column not found", "does not exist", "no such column", "missing column", "cannot identify column")):
            # Try to extract column name using regex
            # e.g., Unknown column 'foobar' in 'field list' or column "foobar" does not exist
            match = re.search(r"missing\s+column\s*:?\s+['\"]?([a-zA-Z0-9_]+)['\"]?", err, re.IGNORECASE)
            if not match:
                match = re.search(r"unknown\s+column\s+['\"]?([a-zA-Z0-9_]+)['\"]?", err, re.IGNORECASE)
            if not match:
                match = re.search(r"column\s+['\"]?([a-zA-Z0-9_]+)['\"]?", err, re.IGNORECASE)
            if not match:
                match = re.search(r"['\"]([a-zA-Z0-9_]+)['\"]", err)

            if match:
                bad_col = match.group(1)
                # Find closest match
                matches = difflib.get_close_matches(bad_col, all_columns, n=2, cutoff=0.6)
                if matches:
                    fix = f"Replace unknown column '{bad_col}' with valid schema column '{matches[0]}'"
                    return ErrorClassification(
                        category="unknown_column",
                        message=err,
                        suggested_fix=fix,
                        confidence=0.85,
                    )
                return ErrorClassification(
                    category="unknown_column",
                    message=err,
                    suggested_fix=f"Remove or replace invalid column '{bad_col}'; ensure correct table qualification.",
                    confidence=0.70,
                )
            return ErrorClassification(
                category="unknown_column",
                message=err,
                suggested_fix="Check SELECT and WHERE clauses for incorrect column names against available schema.",
                confidence=0.60,
            )

        # 2. Missing Table / Unknown Table / Table not found
        if any(p in err_lower for p in ("table not found", "unknown table", "relation does not exist", "table does not exist")):
            match = re.search(r"(?:table|relation)\s+['\"]?([a-zA-Z0-9_.]+)['\"]?", err, re.IGNORECASE)
            if not match:
                match = re.search(r"['\"]([a-zA-Z0-9_.]+)['\"]", err)

            if match:
                bad_table = match.group(1).split(".")[-1]  # strip schema prefix if any
                matches = difflib.get_close_matches(bad_table, all_tables, n=2, cutoff=0.5)
                if matches:
                    fix = f"Replace invalid table '{bad_table}' with valid schema table '{matches[0]}'"
                    return ErrorClassification(
                        category="missing_table",
                        message=err,
                        suggested_fix=fix,
                        confidence=0.85,
                    )
            return ErrorClassification(
                category="missing_table",
                message=err,
                suggested_fix="Ensure all tables in FROM and JOIN clauses are fully qualified and exist in the provided schema.",
                confidence=0.70,
            )

        # 3. Type Mismatch / Syntax / Function / Operator
        if any(p in err_lower for p in ("type mismatch", "invalid input syntax", "cannot convert", "different types", "operator does not exist", "function does not exist", "argument of function")):
            return ErrorClassification(
                category="type_mismatch",
                message=err,
                suggested_fix="Check data types in WHERE/HAVING conditions, explicit type casting (e.g., ::text, toDateTime(), toInt64()), and function arguments.",
                confidence=0.75,
            )

        # 4. Group By / Aggregation Error
        if any(p in err_lower for p in ("not in group by", "must appear in the group by", "aggregate function", "column must be in group by", "unaggregated column")):
            return ErrorClassification(
                category="aggregation_error",
                message=err,
                suggested_fix="Ensure all non-aggregated columns in SELECT are included in the GROUP BY clause.",
                confidence=0.90,
            )

        # 5. Syntax Error
        if any(p in err_lower for p in ("syntax error", "you have an error in your sql syntax", "unexpected token", "mismatched parentheses")):
            return ErrorClassification(
                category="syntax_error",
                message=err,
                suggested_fix="Inspect SQL query for missing commas, mismatched quotes, unbalanced parentheses, or trailing semicolons.",
                confidence=0.80,
            )

        # Default fallback
        return ErrorClassification(
            category="general_database_error",
            message=err,
            suggested_fix="Analyze the database error message and ensure the SQL strictly complies with the target database dialect and provided schema.",
            confidence=0.50,
        )
