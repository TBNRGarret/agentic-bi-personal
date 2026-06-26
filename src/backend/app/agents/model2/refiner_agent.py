from __future__ import annotations

from typing import Any

from ...services.llm import LlmService


class RefinerAgent:
    """MAC-SQL Refiner + QueryWeaver Healer pattern."""

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
    ) -> dict[str, Any]:
        correction = await llm.correct_sql(
            question=question,
            failed_sql=failed_sql,
            database_error=database_error,
            tables=tables,
            datasource=datasource,
            limit=limit,
            request_plan=request_plan,
        )
        if correction.get("status") == "success":
            return {**correction, "source": "model2_refiner"}
        return {
            "status": "failed",
            "source": "model2_refiner",
            "error": correction.get("error") or correction.get("fallbackReason") or "SQL refinement failed",
            "raw": correction,
        }
