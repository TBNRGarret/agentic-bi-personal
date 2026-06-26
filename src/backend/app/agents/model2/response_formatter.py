from __future__ import annotations

import asyncio
from typing import Any, Callable

from ...services.llm import LlmService
from ..chart_spec import infer_chart_spec

EventSink = Callable[[dict[str, Any]], None]


class ResponseFormatter:
    """QueryWeaver-style final formatter adapted to Agentic BI chart output."""

    async def format(
        self,
        *,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        table_names: list[str],
        tables: list[dict[str, Any]],
        llm: LlmService,
        intent: str,
        planned_visualization: dict[str, Any] | None,
        conversation: list[dict[str, Any]] | None,
        memory: dict[str, Any] | None,
        include_synthesis: bool,
        event_sink: EventSink | None,
        synthesis_timeout_seconds: float,
    ) -> dict[str, Any]:
        chart_spec = infer_chart_spec(
            rows,
            intent,
            None,
            planned_visualization,
        )
        fallback_answer = _fallback_answer(rows, table_names)
        streamed_chunks: list[str] = []

        def capture_delta(text: str) -> None:
            streamed_chunks.append(text)
            if event_sink is not None:
                event_sink({"type": "delta", "text": text})

        if include_synthesis:
            try:
                synthesis = await asyncio.wait_for(
                    llm.synthesize(
                        question=question,
                        sql=sql,
                        rows=rows,
                        table_names=table_names,
                        tables=tables,
                        conversation=conversation,
                        memory=memory,
                        chart_spec=chart_spec,
                        delta_sink=capture_delta if event_sink is not None else None,
                    ),
                    timeout=max(0.05, synthesis_timeout_seconds),
                )
            except TimeoutError:
                synthesis = {
                    "status": "timeout",
                    "answer": None,
                    "error": "Model 2 answer synthesis timed out; using deterministic summary.",
                }
            except Exception as exc:
                synthesis = {
                    "status": "failed",
                    "answer": None,
                    "error": f"Model 2 answer synthesis failed: {str(exc)[:180]}",
                }
        else:
            synthesis = {"status": "skipped", "answer": None}

        partial = "".join(streamed_chunks).strip()
        if partial and synthesis.get("status") != "success":
            synthesis = {**synthesis, "answer": partial, "streamed": True}
        answer = str(synthesis.get("answer") or "").strip() or fallback_answer
        return {
            "answer": answer,
            "chartSpec": chart_spec,
            "llmSynthesis": synthesis,
        }


def _fallback_answer(rows: list[dict[str, Any]], table_names: list[str]) -> str:
    source = ", ".join(table_names) if table_names else "selected tables"
    if rows:
        return f"Returned {len(rows)} row(s) from {source}."
    return f"No rows matched the current request from {source}."
