from __future__ import annotations

from typing import Any

from ...services.llm import LlmService
from .error_classifier import ErrorClassifier


class RefinerAgent:
    """MAC-SQL Refiner + QueryWeaver Healer pattern with accumulative error context."""

    def __init__(self, error_classifier: ErrorClassifier | None = None) -> None:
        self.error_classifier = error_classifier or ErrorClassifier()

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

        enriched_plan = {
            **request_plan,
            "refinerErrorClassification": classification.to_dict(),
            "refinerErrorHistory": error_history,
        }

        # Enhance the database error string with the suggested fix for direct attention
        enhanced_error = (
            f"{database_error}\n\n"
            f"[Smart Refiner Suggestion: {classification.suggested_fix}]"
        )

        correction = await llm.correct_sql(
            question=question,
            failed_sql=failed_sql,
            database_error=enhanced_error,
            tables=tables,
            datasource=datasource,
            limit=limit,
            request_plan=enriched_plan,
        )
        if correction.get("status") == "success":
            return {
                **correction,
                "source": "model2_refiner",
                "errorClassification": classification.to_dict(),
                "errorHistoryCount": len(error_history),
            }
        return {
            "status": "failed",
            "source": "model2_refiner",
            "error": correction.get("error") or correction.get("fallbackReason") or "SQL refinement failed",
            "errorClassification": classification.to_dict(),
            "errorHistoryCount": len(error_history),
            "raw": correction,
        }
