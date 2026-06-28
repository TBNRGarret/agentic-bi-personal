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
        fallback_answer = _fallback_answer(question, rows, table_names)
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


def _fallback_answer(question: str, rows: list[dict[str, Any]], table_names: list[str]) -> str:
    source = ", ".join(table_names) if table_names else "selected tables"
    if not rows:
        return f"Khong co dong du lieu nao khop voi yeu cau hien tai tu {source}."

    ranking = _rank_rows_for_question(question, rows)
    if ranking:
        label_key, metric_key, ranked_rows = ranking
        top = ranked_rows[0]
        top_label = _display_value(top.get(label_key))
        top_value = _display_number(top.get(metric_key))
        metric_label = metric_key.replace("_", " ")
        answer = (
            f"Ket qua cao nhat la **{top_label}** voi **{metric_label} = {top_value}** "
            f"tren {len(rows)} dong ket qua."
        )
        if len(ranked_rows) > 1:
            runners = ", ".join(
                f"{_display_value(row.get(label_key))} ({_display_number(row.get(metric_key))})"
                for row in ranked_rows[1:4]
            )
            answer += f" Cac muc tiep theo: {runners}."
        return answer

    return f"Da tra ve {len(rows)} dong ket qua tu {source}."


def _rank_rows_for_question(
    question: str,
    rows: list[dict[str, Any]],
) -> tuple[str, str, list[dict[str, Any]]] | None:
    if not rows:
        return None
    columns = list(rows[0].keys())
    numeric_columns = [column for column in columns if any(_to_number(row.get(column)) is not None for row in rows)]
    if not numeric_columns:
        return None

    label_columns = [column for column in columns if column not in numeric_columns]
    if not label_columns:
        return None

    question_l = question.lower()
    metric_key = _best_metric_column(question_l, numeric_columns)
    label_key = _best_label_column(label_columns)
    reverse = not any(term in question_l for term in ("lowest", "least", "min", "minimum", "thap nhat", "it nhat", "nho nhat"))
    ranked_rows = sorted(
        rows,
        key=lambda row: _to_number(row.get(metric_key)) if _to_number(row.get(metric_key)) is not None else float("-inf"),
        reverse=reverse,
    )
    return label_key, metric_key, ranked_rows


def _best_metric_column(question: str, numeric_columns: list[str]) -> str:
    preferences = [
        (("cost", "usd", "chi phi", "spend"), ("cost", "usd", "amount")),
        (("token", "tokens"), ("token",)),
        (("request", "count", "calls", "so luong"), ("request", "count", "call")),
        (("latency", "duration", "delay"), ("latency", "duration", "ms")),
        (("error", "fail", "loi"), ("error", "fail")),
    ]
    lowered = {column: column.lower() for column in numeric_columns}
    for question_terms, column_terms in preferences:
        if any(term in question for term in question_terms):
            for column, lower in lowered.items():
                if any(term in lower for term in column_terms):
                    return column
    for preferred in ("total_cost_usd", "cost_usd", "total_tokens", "request_count"):
        for column, lower in lowered.items():
            if preferred == lower:
                return column
    return numeric_columns[0]


def _best_label_column(label_columns: list[str]) -> str:
    lowered = {column: column.lower() for column in label_columns}
    for preferred in ("source_app", "app", "application", "service", "provider", "model", "name"):
        for column, lower in lowered.items():
            if preferred == lower or lower.endswith(f"_{preferred}"):
                return column
    return label_columns[0]


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _display_number(value: Any) -> str:
    number = _to_number(value)
    if number is None:
        return _display_value(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    if abs(number) >= 1:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _display_value(value: Any) -> str:
    text = str(value if value is not None else "unknown").strip()
    return text or "unknown"
